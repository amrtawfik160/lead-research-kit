import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from lead_research.audit import check_lead, linkedin_consistent, name_on_page, run_audit, title_on_page
from lead_research.cli import main
from lead_research.daily import History, render_report
from lead_research.results import detect_tool, import_results, row_outcome
from lead_research.webfetch import FetchResult


def lead(first, last, domain, source='website_published', title='Owner'):
    return {'full_name': f'{first} {last}', 'title': title, 'company': domain.split('.')[0].title(),
            'company_website': domain, 'email': f'{first.lower()}.{last.lower()}@{domain}',
            'linkedin_url': f'https://www.linkedin.com/in/{first.lower()}-{last.lower()}/', 'email_source': source}


def write_csv(path, headers, rows):
    with open(path, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


class DetectionTests(unittest.TestCase):
    def test_detects_common_tools(self):
        self.assertEqual(detect_tool(['email', 'first_name', 'lead_category', 'is_bounced', 'reply_count']), 'smartlead')
        self.assertEqual(detect_tool(['email', 'firstName', 'emailsBounced', 'emailsReplied', 'lastState']), 'lemlist')
        self.assertEqual(detect_tool(['Email', 'First Name', 'Email Bounced', 'Replied', 'Email Open', 'Contact Stage']), 'apollo')
        self.assertEqual(detect_tool(['Record ID', 'Email', 'Email hard bounced', 'Unsubscribed from all email']), 'hubspot')
        self.assertEqual(detect_tool(['Email', 'Lead Status', 'Opens', 'Clicks', 'Replies']), 'instantly')
        self.assertEqual(detect_tool(['email', 'status']), 'generic')

    def test_row_outcomes(self):
        cases = [
            ({'email': 'a@x.example', 'status': 'Hard Bounce'}, 'bounced'),
            ({'email': 'a@x.example', 'status': 'soft bounce'}, 'delivered'),
            ({'email': 'a@x.example', 'lead_category': 'Not Interested'}, 'not_interested'),
            ({'email': 'a@x.example', 'lead_category': 'Meeting Request', 'is_bounced': 'false'}, 'meeting'),
            ({'email': 'a@x.example', 'is_unsubscribed': 'true', 'reply_count': '2'}, 'unsubscribed'),
            ({'Email': 'a@x.example', 'Email Bounced': 'true', 'Replied': 'true'}, 'bounced'),
            ({'Email': 'a@x.example', 'Marketing emails opened': '3'}, 'engaged'),
            ({'email': 'a@x.example', 'event': 'spam complaint'}, 'complaint'),
            ({'email': 'a@x.example', 'status': 'Replied'}, 'replied'),
            ({'email': 'a@x.example', 'emailsSent': '1'}, 'delivered'),
            ({'email': 'a@x.example', 'status': 'Unqualified'}, 'not_interested'),
            ({'email': 'a@x.example', 'status': 'whatever'}, None),
        ]
        for row, expected in cases:
            self.assertEqual(row_outcome(row)[1], expected, row)
        self.assertEqual(row_outcome({'email': 'a@x.example', 'status': '-1'}, 'instantly')[1], 'bounced')


class ImportResultsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)
        self.history = History(self.work / 'lead-history.sqlite3')
        self.a = lead('Casey', 'Morgan', 'harbor.example', 'pattern_verified')
        self.b = lead('Jordan', 'Lee', 'summit.example')
        self.c = lead('Riley', 'Stone', 'bay.example')
        self.history.mark_delivered([self.a, self.b, self.c], '2026-10-17')
        self.history.save_sites([{'domain': 'harbor.example', 'status': 'ok', 'people': [], 'company': {},
                                  'pattern': {'pattern': '{first}.{last}', 'support': 3, 'total': 3, 'confidence': 1.0}}])

    def tearDown(self):
        self.history.close()
        self.tmp.cleanup()

    def rows(self):
        return [{'email': self.a['email'], 'status': 'Bounced'},
                {'email': self.b['email'], 'status': 'Unsubscribed'},
                {'email': self.c['email'], 'status': 'Interested'},
                {'email': 'stranger@else.example', 'status': 'opened'},
                {'email': '', 'status': 'Replied'},
                {'email': self.c['email'], 'status': '???'}]

    def test_import_suppresses_flags_pattern_and_is_idempotent(self):
        first = import_results(self.history, self.rows())
        self.assertEqual(first['tool'], 'generic')
        self.assertEqual(first['rows_read'], 6)
        self.assertEqual(first['matched_delivered'], 3)
        self.assertEqual(first['outcomes'], {'bounced': 1, 'unsubscribed': 1, 'interested': 1, 'engaged': 1})
        self.assertEqual(first['no_email'], 1)
        self.assertEqual(first['unrecognized'], 1)
        self.assertEqual(first['patterns_distrusted'], ['harbor.example'])
        # bounce: only the email; unsubscribe: the whole person (email, LinkedIn, name+domain)
        self.assertFalse(self.history.is_fresh({'full_name': 'Someone New', 'company_website': 'x.example', 'email': self.a['email']}))
        blocked = {r['key']: r['reason'] for r in self.history.db.execute('SELECT key, reason FROM suppressed')}
        self.assertEqual(blocked['em:' + self.a['email']], 'bounced')
        self.assertFalse(any(k.startswith(('li:casey', 'nd:')) and 'harbor' in k for k in blocked))
        self.assertEqual(blocked['li:jordan-lee'], 'unsubscribed')
        self.assertTrue(any(k.startswith('nd:') and k.endswith('summit.example') for k in blocked))
        self.assertNotIn('em:' + self.c['email'], blocked)
        self.assertEqual(first['newly_suppressed'], 4)
        # pattern no longer usable for guesses
        site = [s for s in self.history.recent_sites() if s['domain'] == 'harbor.example'][0]
        self.assertTrue(site['pattern']['distrusted'])
        self.assertEqual(site['pattern']['confidence'], 0)
        quality = self.history.source_quality()
        self.assertEqual(quality['pattern_verified']['bounced'], 1)
        self.assertEqual(quality['website_published']['replied'], 1)
        self.assertEqual(quality['website_published']['unsubscribed'], 1)

        again = import_results(self.history, self.rows())
        self.assertEqual(again['already_imported'], 6)
        self.assertEqual(again['outcomes'], {})
        self.assertEqual(again['newly_suppressed'], 0)
        self.assertEqual(self.history.source_quality(), quality)
        self.assertEqual(self.history.distrusted_patterns()['harbor.example']['bounces'], 1)
        self.history.clear_pattern('harbor.example')
        self.assertEqual(self.history.distrusted_patterns(), {})


class CliResultsTests(unittest.TestCase):
    def test_import_results_and_patterns_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            with contextlib.redirect_stdout(io.StringIO()):
                main(['--workspace', tmp, 'init'])
            h = History(work / 'lead-history.sqlite3')
            a = lead('Casey', 'Morgan', 'harbor.example', 'pattern_verified')
            c = lead('Riley', 'Stone', 'bay.example')
            h.mark_delivered([a, c], '2026-10-17')
            h.close()
            export = work / 'smartlead.csv'
            write_csv(export, ['email', 'first_name', 'lead_category', 'is_bounced', 'is_unsubscribed', 'reply_count', 'open_count'],
                      [[a['email'], 'Casey', '', 'true', 'false', '0', '0'],
                       [c['email'], 'Riley', 'Meeting Request', 'false', 'false', '1', '4']])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(['--workspace', tmp, 'history', 'import-results', str(export)]), 0)
            data = json.loads(out.getvalue())
            self.assertEqual(data['results'][0]['tool'], 'smartlead')
            self.assertEqual(data['results'][0]['outcomes'], {'bounced': 1, 'meeting': 1})
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main(['--workspace', tmp, 'history', 'patterns'])
            self.assertIn('harbor.example', json.loads(out.getvalue())['distrusted_patterns'])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main(['--workspace', tmp, 'history', 'patterns', '--clear', 'harbor.example'])
            self.assertEqual(json.loads(out.getvalue())['distrusted_patterns'], {})


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        if url in self.pages:
            return FetchResult(url, url, 200, 'text/html', self.pages[url], False, 0.0, None)
        return FetchResult(url, url, 404, 'text/html', '', False, 0.0, 'http_status')


class FakeStore:
    def __init__(self, attempts):
        self._attempts = attempts

    def attempts(self):
        return self._attempts


DETAIL_HEADERS = ['Full Name', 'Title', 'Company', 'Company Website', 'LinkedIn URL', 'Email', 'Tier', 'Score', 'Email Source', 'Source URL', 'Issues']


class AuditTests(unittest.TestCase):
    def test_helpers(self):
        self.assertTrue(name_on_page('Casey Morgan', '<p>Meet CASEY  Morgan, Broker</p>'))
        self.assertTrue(name_on_page('Casey Morgan', 'C. Morgan leads the team'))
        self.assertFalse(name_on_page('Casey Morgan', 'Jordan Morgan'))
        self.assertTrue(title_on_page('Designated Broker', 'Our designated broker is here'))
        self.assertFalse(title_on_page('Managing Partner', 'Agent'))
        self.assertIsNone(title_on_page('', 'x'))
        self.assertTrue(linkedin_consistent('Casey Morgan', 'https://www.linkedin.com/in/casey-morgan-12ab/'))
        self.assertFalse(linkedin_consistent('Casey Morgan', 'https://www.linkedin.com/in/jordan-lee/'))

    def test_run_audit_scores_and_writes_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            day = Path(tmp) / '2026-10-17'
            day.mkdir()
            rows = [
                # fully confirmed: name, title, email on page, site up, LinkedIn consistent
                ['Casey Morgan', 'Designated Broker', 'Harbor', 'harbor.example', 'https://www.linkedin.com/in/casey-morgan/',
                 'casey@harbor.example', 'A', '90', 'website_published', 'https://harbor.example/team', ''],
                # partial: email gone from page
                ['Jordan Lee', 'Principal', 'Summit', 'summit.example', '', 'jordan@summit.example', 'A', '80',
                 'website_published', 'https://summit.example/about', ''],
                # pattern_verified with a ledger pass: confirmed
                ['Riley Stone', 'President', 'Bay', 'bay.example', '', 'riley.stone@bay.example', 'B', '70',
                 'pattern_verified', 'https://bay.example/team', ''],
                # failed: site down
                ['Sam Park', 'Owner', 'Gone', 'gone.example', '', 'sam@gone.example', 'B', '60', 'website_published',
                 'https://gone.example/team', ''],
                # failed: pattern without verifier result, LinkedIn mismatch
                ['Alex Kim', 'Partner', 'Bay', 'bay.example', 'https://www.linkedin.com/in/someone-else/', 'alex.kim@bay.example',
                 'B', '60', 'pattern_verified', 'https://bay.example/team', ''],
            ]
            write_csv(day / 'leads-details.csv', DETAIL_HEADERS, rows)
            (day / 'report.json').write_text(json.dumps({'date': '2026-10-17', 'execute': True, 'target': 5, 'delivered': 5,
                                                         'files': {'leads': str(day / 'leads.csv')}}))
            fetcher = FakeFetcher({
                'https://harbor.example/': '<h1>Harbor</h1>', 'https://harbor.example/team':
                    '<div>Casey Morgan<br>Designated Broker <a href="mailto:casey@harbor.example">email</a></div>',
                'https://summit.example/': 'ok', 'https://summit.example/about': '<p>Jordan Lee, Principal</p>',
                'https://bay.example/': 'ok', 'https://bay.example/team': '<p>Riley Stone - President</p><p>Alex Kim, Partner</p>',
            })
            store = FakeStore([{'operation': 'verify', 'state': 'completed', 'provider': 'reoon',
                                'payload': {'email': 'riley.stone@bay.example'}, 'outcome': {'assessment': 'provider_valid'}}])
            report = run_audit(day, fetcher, store=store, sample=20, seed=1)
            results = {l['full_name']: l['result'] for l in report['leads']}
            self.assertEqual(results, {'Casey Morgan': 'confirmed', 'Jordan Lee': 'partial', 'Riley Stone': 'confirmed',
                                       'Sam Park': 'failed', 'Alex Kim': 'failed'})
            self.assertEqual(report['score'], {'confirmed_pct': 40.0, 'partial_pct': 20.0, 'failed_pct': 40.0})
            self.assertIn('published email no longer on the evidence page', report['top_reasons'])
            self.assertTrue((day / 'audit.json').exists())
            self.assertIn('40.0% fully confirmed', (day / 'audit.md').read_text())
            self.assertEqual(oct((day / 'audit.json').stat().st_mode & 0o777), '0o600')
            # evidence pages are fetched once each, even when shared by several leads
            self.assertEqual(fetcher.calls.count('https://bay.example/team'), 1)
            self.assertFalse(any('linkedin.com' in c for c in fetcher.calls))
            # morning report picks up the score and lists the delivery files
            text = render_report(json.loads((day / 'report.json').read_text()))
            self.assertIn('Spot-check (5 leads): 40.0% fully confirmed', text)
            self.assertIn('Delivery', text)
            self.assertIn('leads.csv', text)

    def test_sample_is_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            day = Path(tmp) / '2026-10-17'
            day.mkdir()
            rows = [[f'Person{i} Last{i}', 'Owner', 'C', f'c{i}.example', '', '', 'B', '50', '', '', ''] for i in range(50)]
            write_csv(day / 'leads-details.csv', DETAIL_HEADERS, rows)
            one = run_audit(day, FakeFetcher({}), sample=10, seed=7)
            two = run_audit(day, FakeFetcher({}), sample=10, seed=7)
            self.assertEqual([l['full_name'] for l in one['leads']], [l['full_name'] for l in two['leads']])
            self.assertEqual(one['sampled'], 10)

    def test_cli_daily_audit_alias_requires_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()):
                main(['--workspace', tmp, 'init'])
            from lead_research.cli import _daily_audit_alias
            self.assertEqual(_daily_audit_alias(['--workspace', tmp, 'daily', 'audit', '--sample', '5']),
                             ['--workspace', tmp, 'daily-audit', '--sample', '5'])
            from lead_research.models import PolicyError
            with self.assertRaises(PolicyError):
                main(['--workspace', tmp, 'daily', 'audit', '--date', '2026-01-01'])


if __name__ == '__main__':
    unittest.main()
