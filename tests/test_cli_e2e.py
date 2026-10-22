"""CLI-only journey. No dashboard process. Offline fixtures only."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lead_research.cli import main, parser as parser_fn
from lead_research.daily import write_daily
from lead_research.targeting import DEFAULT_ICP_TITLES, DEFAULT_INDUSTRY_KEYWORDS


def _record(name, title, email, domain, state='TX'):
    return {
        'full_name': name,
        'title': title,
        'company': 'Harbor Commercial Real Estate',
        'company_website': domain,
        'linkedin_url': '',
        'email': email,
        'city': 'Austin',
        'state': state,
        'email_source': 'website_published',
        'source': 'website_published',
        'tier': 'A',
        'score': 90,
        'issues': [],
        'email_risk': 'low',
        'email_verification': 'valid',
    }


class CliOnlyJourneyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name) / 'alice'
        self.other = Path(self.tmp.name) / 'jess'
        self.shared = Path(self.tmp.name) / 'shared'

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, workspace, *args):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), patch(
            'urllib.request.OpenerDirector.open', side_effect=AssertionError('live network forbidden'),
        ):
            code = main(['--workspace', str(workspace), '--json', *args])
        text = stdout.getvalue()
        payload = json.loads(text) if text.strip() else {}
        err = stderr.getvalue()
        if err.strip():
            try:
                payload.setdefault('_stderr', json.loads(err.strip().splitlines()[-1]))
            except Exception:
                payload.setdefault('_stderr_raw', err)
        return code, payload

    def test_schema_lists_new_commands(self):
        code, catalog = self.call(self.work, 'schema')
        self.assertEqual(code, 0)
        names = {row['name'] for row in catalog['commands']}
        for name in ('ops set', 'ops get', 'ops list', 'leads list', 'leads export',
                     'workspace show', 'filter save', 'daily', 'jobs list', 'schema'):
            self.assertIn(name, names)
        self.assertIn('0', catalog['exit_codes'])
        self.assertIn('2', catalog['exit_codes'])
        self.assertTrue(catalog['non_interactive'])
        code2, alias = self.call(self.work, 'commands')
        self.assertEqual(code2, 0)
        self.assertEqual(len(alias['commands']), len(catalog['commands']))

    def test_full_cli_journey_without_dashboard(self):
        self.assertEqual(self.call(self.work, 'init')[0], 0)
        code, joined = self.call(
            self.work, 'team', 'join',
            '--name', 'Ada', '--area-kind', 'icp', '--area', 'US CRE decision makers',
            '--shared', str(self.shared),
        )
        self.assertEqual(code, 0)
        self.assertEqual(joined['me']['area']['value'], 'US CRE decision makers')

        write_daily([
            _record('Casey Morgan', 'CEO', 'casey@harbor.example', 'harbor.example'),
            _record('Riley Stone', 'Intern', 'riley@harbor.example', 'harbor.example'),
            _record('Pat Quinn', 'Managing Partner', 'pat@harbor.example', 'harbor.example'),
        ], self.work / 'daily' / '2026-10-22')
        self.assertEqual(self.call(self.work, 'team', 'export', '--shared', str(self.shared))[0], 0)

        titles = ','.join(DEFAULT_ICP_TITLES)
        industry = ','.join(DEFAULT_INDUSTRY_KEYWORDS)
        code, saved = self.call(
            self.work, 'filter', 'save', 'US CRE decision makers 5M',
            '--country', 'US', '--revenue-min', '5M',
            '--industry', industry, '--include-titles', titles,
            '--exclude-junior', '--pin',
        )
        self.assertEqual(code, 0)
        self.assertTrue(saved['saved']['pinned'])
        self.assertEqual(saved['saved']['query']['country'], 'US')
        self.assertEqual(saved['saved']['query']['revenue_min'], 5_000_000)

        code, applied = self.call(self.work, 'filter', 'apply', 'US CRE decision makers 5M')
        self.assertEqual(code, 0)
        names = {row['Full Name'] for row in applied['leads']}
        self.assertIn('Casey Morgan', names)
        self.assertIn('Pat Quinn', names)
        self.assertNotIn('Riley Stone', names)

        code, set_ops = self.call(
            self.work, 'ops', 'set', '--email', 'casey@harbor.example',
            '--stage', 'contacted', '--owner', 'Ada', '--notes', 'Intro this week',
        )
        self.assertEqual(code, 0)
        self.assertEqual(set_ops['ops']['stage'], 'contacted')
        code, listed = self.call(self.work, 'leads', 'list', '--filter', 'US CRE decision makers 5M')
        self.assertEqual(code, 0)
        casey = next(row for row in listed['leads'] if row['Email'] == 'casey@harbor.example')
        self.assertEqual(casey['Stage'], 'contacted')
        self.assertEqual(casey['Owner'], 'Ada')
        self.assertEqual(casey['Notes'], 'Intro this week')

        export_path = Path(self.tmp.name) / 'leads.csv'
        code, exported = self.call(self.work, 'leads', 'export', '--output', str(export_path), '--date', 'team')
        self.assertEqual(code, 0)
        self.assertTrue(export_path.is_file())
        csv_text = export_path.read_text(encoding='utf-8')
        self.assertIn('Found By', csv_text)
        self.assertIn('Casey Morgan', csv_text)

        self.assertEqual(self.call(self.other, 'init')[0], 0)
        code, jess = self.call(
            self.other, 'team', 'join',
            '--name', 'Jess', '--area-kind', 'region', '--area', 'Texas',
            '--shared', str(self.shared),
        )
        self.assertEqual(code, 0)
        code, imported = self.call(self.other, 'team', 'import', '--shared', str(self.shared))
        self.assertEqual(code, 0)
        self.assertGreaterEqual(imported.get('leads_count') or imported.get('count') or 0, 1)

        self.assertEqual(self.call(self.work, 'provider', 'list')[0], 0)
        self.assertEqual(self.call(self.work, 'budget', 'list')[0], 0)
        code, shown = self.call(self.work, 'workspace', 'show')
        self.assertEqual(code, 0)
        self.assertTrue(shown['initialized'])
        self.assertIn('US CRE decision makers 5M', shown['pinned_filters'])

        code, daily = self.call(
            self.work, 'daily', '--target', '5', '--filter', 'US CRE decision makers 5M',
            '--sources', 'build',
        )
        self.assertEqual(code, 0)
        self.assertFalse(daily.get('execute'))
        self.assertEqual(daily.get('targeting', {}).get('country'), 'US')
        self.assertEqual(self.call(self.work, 'jobs', 'list')[0], 0)

    def test_help_on_every_catalog_command(self):
        from lead_research.schema import command_catalog
        catalog = command_catalog(parser_fn())
        for row in catalog['commands']:
            argv = row['name'].split() + ['--help']
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout), self.assertRaises(SystemExit) as ctx:
                parser_fn().parse_args(argv)
            self.assertEqual(ctx.exception.code, 0, f'--help failed for {row["name"]}')
            self.assertTrue(stdout.getvalue().strip())

    def test_json_errors_include_hints(self):
        from lead_research.cli import entrypoint
        err = io.StringIO()
        with patch('sys.argv', [
            'lead-research', '--workspace', str(self.work), 'ops', 'set',
            '--email', 'casey@harbor.example',
        ]), contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = entrypoint()
        self.assertEqual(code, 2)
        payload = json.loads(err.getvalue())
        self.assertEqual(payload['error'], 'ops_patch_required')
        self.assertIn('hint', payload)

        self.assertEqual(self.call(self.work, 'init')[0], 0)
        err2 = io.StringIO()
        with patch('sys.argv', [
            'lead-research', '--workspace', str(self.work), 'filter', 'save', 'bad-country',
            '--country', 'Narnia',
        ]), contextlib.redirect_stderr(err2), contextlib.redirect_stdout(io.StringIO()):
            code = entrypoint()
        self.assertEqual(code, 2)
        payload = json.loads(err2.getvalue())
        self.assertEqual(payload['error'], 'unknown_country')
        self.assertIn('ISO', payload['hint'])


if __name__ == '__main__':
    unittest.main()
