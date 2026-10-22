"""Team lists, territories, and two simulated teammates on separate workspaces."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lead_research.cli import entrypoint, main
from lead_research.configuration import default_config, save_config
from lead_research.daily import History, load_daily_records, run_daily, write_daily
from lead_research.models import PolicyError
from lead_research.store import Store
from lead_research.team import export_team, import_team, join, list_team, partition_owned


def _record(name, email, domain, linkedin='', title='Owner', city='Austin', state='TX'):
    return {
        'full_name': name,
        'title': title,
        'company': 'Example Holdings',
        'company_website': domain,
        'linkedin_url': linkedin,
        'email': email,
        'city': city,
        'state': state,
        'email_source': 'website_published',
        'source': 'website_published',
        'tier': 'B',
        'score': 80,
        'issues': [],
        'email_risk': 'low',
        'email_verification': 'valid',
    }


def _site(domain, people):
    return {
        'domain': domain,
        'status': 'ok',
        'company': {'name': 'Example Holdings'},
        'people': people,
        'pattern': None,
        'pages_fetched': 2,
        'errors': [],
    }


def _person(first, last, domain, title='Owner', linkedin=None):
    return {
        'full_name': f'{first} {last}',
        'title': title,
        'email': f'{first.lower()}@{domain}',
        'linkedin_url': linkedin,
        'evidence_url': f'https://{domain}/team',
        'method': 'mailto',
    }


class TeamCli:
    def __init__(self, work):
        self.work = Path(work)

    def call(self, *args):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), patch(
            'urllib.request.OpenerDirector.open', side_effect=AssertionError('live network forbidden'),
        ):
            code = main(['--workspace', str(self.work), '--json', *args])
        return code, json.loads(stdout.getvalue())


class TwoTeammateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.alice = self.root / 'alice'
        self.bob = self.root / 'bob'
        self.shared = self.root / 'shared'
        self.shared.mkdir()
        self.bundle = self.shared / 'veinore-team.json'
        for work in (self.alice, self.bob):
            TeamCli(work).call('init')

    def tearDown(self):
        self.tmp.cleanup()

    def test_join_export_import_dedupe_and_territories(self):
        alice = TeamCli(self.alice)
        bob = TeamCli(self.bob)
        code, joined = alice.call(
            'team', 'join', '--name', 'Alice', '--area-kind', 'industry', '--area', 'SaaS',
            '--shared', str(self.shared), '--team-name', 'Veinore crew',
        )
        self.assertEqual(code, 0)
        self.assertTrue(joined['joined'])
        self.assertEqual(joined['me']['area']['value'], 'SaaS')
        self.assertTrue(self.bundle.is_file())
        self.assertEqual(self.bundle.stat().st_mode & 0o777, 0o600)

        with self.assertRaises(PolicyError) as caught:
            join(self.bob, name='Bob', area_kind='industry', area='saas', shared=str(self.shared))
        self.assertEqual(str(caught.exception), 'area_already_claimed')
        self.assertTrue(caught.exception.details['conflicts'])

        err = io.StringIO()
        with patch('sys.argv', [
            'lead-research', '--workspace', str(self.bob), 'team', 'join',
            '--name', 'Bob', '--area-kind', 'industry', '--area', 'SaaS',
            '--shared', str(self.shared), '--json',
        ]), contextlib.redirect_stderr(err):
            self.assertEqual(entrypoint(), 2)
        payload = json.loads(err.getvalue())
        self.assertEqual(payload['error'], 'area_already_claimed')
        self.assertIn('different', payload['hint'])
        self.assertTrue(payload['details']['conflicts'])

        code, bob_join = bob.call(
            'team', 'join', '--name', 'Bob', '--area-kind', 'region', '--area', 'Texas',
            '--shared', str(self.shared),
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(bob_join['members']), 2)
        kinds = {(m['area']['kind'], m['area']['value']) for m in bob_join['members']}
        self.assertEqual(kinds, {('industry', 'SaaS'), ('region', 'Texas')})

        write_daily([
            _record('Alex Morgan', 'alex@example.org', 'example.org',
                    linkedin='https://www.linkedin.com/in/example-alex'),
            _record('Pat Quinn', 'pat@example.net', 'example.net'),
        ], self.alice / 'daily' / '2026-10-21')
        code, exported = alice.call('team', 'export', '--shared', str(self.shared))
        self.assertEqual(code, 0)
        self.assertGreaterEqual(exported['leads_count'], 2)
        self.assertEqual(exported['by_finder']['Alice'], 2)

        code, imported = bob.call('team', 'import', '--shared', str(self.shared))
        self.assertEqual(code, 0)
        self.assertGreaterEqual(imported['added'], 2)
        emails = {row['Email'] for row in imported['leads']}
        self.assertEqual(emails, {'alex@example.org', 'pat@example.net'})
        alex = next(row for row in imported['leads'] if row['Email'] == 'alex@example.org')
        self.assertEqual(alex['Found By'], 'Alice')
        self.assertEqual(alex['Area'], 'SaaS')

        write_daily([
            _record('Alex Morgan', 'other@example.org', 'example.org',
                    linkedin='https://www.linkedin.com/in/example-alex'),
            _record('Pat Quinn', '', 'example.net'),
            _record('Riley Stone', 'riley@example.com', 'example.com'),
        ], self.bob / 'daily' / '2026-10-21')
        code, bob_export = bob.call('team', 'export', '--shared', str(self.shared))
        self.assertEqual(code, 0)
        self.assertEqual(bob_export['leads_count'], 3)
        finders = {row['Email']: row['Found By'] for row in bob_export['leads']}
        self.assertEqual(finders['alex@example.org'], 'Alice')
        self.assertEqual(finders['riley@example.com'], 'Bob')

        code, alice_sync = alice.call('team', 'merge', str(self.bundle))
        self.assertEqual(code, 0)
        self.assertEqual(alice_sync['leads_count'], 3)
        self.assertEqual(alice_sync['duplicates'], 2)

    def test_daily_skips_leads_owned_by_the_other_teammate(self):
        join(self.alice, name='Alice', area_kind='industry', area='SaaS', shared=str(self.shared))
        join(self.bob, name='Bob', area_kind='region', area='Texas', shared=str(self.shared))
        write_daily([
            _record('Alex Morgan', 'alex@harbor.example', 'harbor.example'),
        ], self.alice / 'daily' / '2026-10-21')
        export_team(self.alice, shared=str(self.shared))
        imported = import_team(self.bob, shared=str(self.shared))
        self.assertEqual(imported['marked_owned_by_teammates'], 1)

        store = Store(self.bob / 'research.sqlite3')
        self.addCleanup(store.close)
        history = History(self.bob / 'lead-history.sqlite3')
        history.save_sites([_site('harbor.example', [
            _person('Alex', 'Morgan', 'harbor.example'),
            _person('Riley', 'Quinn', 'harbor.example', 'Managing Broker'),
        ])])
        history.close()
        report = run_daily(
            self.bob, store, default_config(), target=5, sources=('build',),
            execute=True, day='2026-10-22',
        )
        self.assertGreaterEqual(report['skipped_team_owned'], 1)
        names = {row['full_name'] for row in load_daily_records(self.bob / 'daily' / '2026-10-22')}
        self.assertNotIn('Alex Morgan', names)
        self.assertIn('Riley Quinn', names)

        fresh, skipped = partition_owned([
            _record('Alex Morgan', 'alex@harbor.example', 'harbor.example'),
            _record('New Person', 'new@harbor.example', 'harbor.example'),
        ], self.bob)
        self.assertEqual([r['full_name'] for r in skipped], ['Alex Morgan'])
        self.assertEqual([r['full_name'] for r in fresh], ['New Person'])

    def test_list_json_and_shared_folder_roundtrip(self):
        alice = TeamCli(self.alice)
        alice.call('team', 'join', '--name', 'Alice', '--area-kind', 'icp', '--area', 'Founders at brokerages')
        write_daily([_record('Jamie Frost', 'jamie@example.org', 'example.org')], self.alice / 'daily' / '2026-10-21')
        alice.call('team', 'export', '--output', str(self.root / 'bundle.json'))
        bob = TeamCli(self.bob)
        code, merged = bob.call(
            'team', 'join', '--name', 'Bob', '--area-kind', 'source', '--area', 'public records',
            '--shared', str(self.root / 'bundle.json'),
        )
        self.assertEqual(code, 0)
        self.assertGreaterEqual(merged['leads_count'], 1)
        code, listed = bob.call('team', 'list')
        self.assertEqual(code, 0)
        self.assertEqual(listed['leads'][0]['Found By'], 'Alice')
        self.assertEqual(listed['leads'][0]['Area'], 'Founders at brokerages')


class TeamJoinGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name) / 'ws'
        self.work.mkdir(parents=True)
        save_config(self.work / 'providers.json', default_config())
        Store(self.work / 'research.sqlite3').close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_export_requires_join(self):
        with self.assertRaises(PolicyError) as caught:
            export_team(self.work)
        self.assertEqual(str(caught.exception), 'team_join_required')

    def test_unknown_area_kind(self):
        with self.assertRaises(PolicyError) as caught:
            join(self.work, name='Ada', area_kind='planet', area='Earth')
        self.assertEqual(str(caught.exception), 'unknown_area_kind')

    def test_list_empty_workspace(self):
        view = list_team(self.work)
        self.assertFalse(view['joined'])
        self.assertEqual(view['leads_count'], 0)


if __name__ == '__main__':
    unittest.main()
