"""Named, pinned lead filters from the CLI."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lead_research.cli import main
from lead_research.daily import write_daily
from lead_research.filters import apply_filter, list_filters, match_lead, save_filter
from lead_research.models import PolicyError
from lead_research.team import join


def _record(name, email, domain):
    return {
        'full_name': name,
        'title': 'Owner',
        'company': 'Example Holdings',
        'company_website': domain,
        'linkedin_url': '',
        'email': email,
        'city': 'Austin',
        'state': 'TX',
        'email_source': 'website_published',
        'source': 'website_published',
        'tier': 'B',
        'score': 80,
        'issues': [],
        'email_risk': 'low',
        'email_verification': 'valid',
    }


class FilterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name) / 'ws'

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, *args):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), patch(
            'urllib.request.OpenerDirector.open', side_effect=AssertionError('live network forbidden'),
        ):
            code = main(['--workspace', str(self.work), '--json', *args])
        return code, json.loads(stdout.getvalue())

    def test_save_list_pin_unpin_apply_json(self):
        self.call('init')
        join(self.work, name='Ada', area_kind='region', area='Texas')
        write_daily([
            _record('Alex Morgan', 'alex@example.org', 'example.org'),
            _record('Pat Quinn', 'pat@example.net', 'example.net'),
        ], self.work / 'daily' / '2026-10-21')
        self.call('team', 'export')
        ops = self.work / 'veinore-ops.json'
        ops.write_text(json.dumps({
            'alex@example.org': {'stage': 'contacted', 'owner': 'Ada', 'notes': ''},
            'pat@example.net': {'stage': 'new', 'owner': '', 'notes': ''},
        }), encoding='utf-8')

        code, saved = self.call(
            'filter', 'save', 'austin-contacted',
            '--stage', 'contacted', '--date', '2026-10-21', '--owner', 'Ada', '--pin',
        )
        self.assertEqual(code, 0)
        self.assertTrue(saved['saved']['pinned'])
        self.assertEqual(saved['saved']['query']['stage'], 'contacted')
        self.assertEqual(len(saved['pinned']), 1)

        code, listed = self.call('filter', 'list')
        self.assertEqual(code, 0)
        self.assertEqual(listed['filters'][0]['name'], 'austin-contacted')

        code, applied = self.call('filter', 'apply', 'austin-contacted')
        self.assertEqual(code, 0)
        self.assertEqual(applied['matched'], 1)
        self.assertEqual(applied['leads'][0]['Email'], 'alex@example.org')

        code, unpinned = self.call('filter', 'unpin', 'austin-contacted')
        self.assertEqual(code, 0)
        self.assertFalse(unpinned['filter']['pinned'])
        self.assertEqual(unpinned['pinned'], [])

        code, pinned = self.call('filter', 'pin', 'austin-contacted')
        self.assertEqual(code, 0)
        self.assertTrue(pinned['filter']['pinned'])

        self.call('filter', 'save', 'team-texas', '--area', 'Texas', '--date', 'team', '--pin')
        code, team_hits = self.call('filter', 'apply', 'team-texas')
        self.assertEqual(code, 0)
        self.assertGreaterEqual(team_hits['matched'], 2)
        self.assertTrue(all(row.get('Area') == 'Texas' for row in team_hits['leads']))

    def test_unknown_filter_and_bad_name(self):
        self.call('init')
        with self.assertRaises(PolicyError) as caught:
            apply_filter(self.work, 'missing')
        self.assertEqual(str(caught.exception), 'unknown_filter')
        with self.assertRaises(PolicyError):
            save_filter(self.work, '??')

    def test_match_lead_helpers(self):
        row = {
            'Full Name': 'Alex Morgan', 'Email': 'alex@example.org', 'Stage': 'new',
            'Owner': '', 'Area': 'SaaS', 'Source': 'website_published',
            'Email Risk': 'low', 'Email Verification': 'valid', 'Found By': 'Alice',
            'Batch Date': '2026-10-21',
        }
        self.assertTrue(match_lead(row, {'verified': 'verified', 'area': 'saas'}))
        self.assertFalse(match_lead(row, {'stage': 'contacted'}))
        self.assertTrue(match_lead(row, {'owner': 'unassigned'}))
        self.assertFalse(match_lead(row, {'risk': 'high'}))
        listed = list_filters(self.work)
        self.assertEqual(listed['count'], 0)


if __name__ == '__main__':
    unittest.main()
