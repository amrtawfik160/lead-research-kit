import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from lead_research.models import PolicyError
from lead_research.quality import HEADERS

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dashboard'))
from api.sample import seed_sample  # noqa: E402
from api import snapshot  # noqa: E402
from api.snapshot import Dashboard, clean_rows, resolve_workspace, update_settings, validate_workspace_path  # noqa: E402


class SampleWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'sample'
        seed_sample(self.root)
        self.dash = Dashboard(self.root, sample=False)

    def tearDown(self):
        self.tmp.cleanup()

    def test_seed_uses_kit_store_and_history(self):
        overview = self.dash.overview()
        self.assertTrue(overview['status']['initialized'])
        self.assertTrue(overview['status']['audit_ok'])
        self.assertGreaterEqual(overview['status']['contacts'], 4)
        self.assertGreater(overview['history']['delivered_keys'], 0)
        self.assertGreater(overview['history']['domains_known'], 0)
        self.assertTrue(overview['latest_daily'])
        self.assertEqual(overview['latest_daily']['date'], '2026-10-20')
        self.assertGreaterEqual(overview['latest_daily']['delivered'], 10)

    def test_contacts_and_evidence_are_real_records(self):
        rows = {r['id']: r for r in self.dash.contacts()['contacts']}
        self.assertIn('example-alex', rows)
        self.assertEqual(rows['example-alex']['email'], 'alex@example.org')
        self.assertTrue(rows['example-jordan']['held'])
        detail = self.dash.contact('example-alex')
        self.assertTrue(detail['evidence'])
        self.assertEqual(detail['evidence'][0]['classification'], 'public_person_linked')
        self.assertTrue(detail['candidates'])
        self.assertEqual(detail['assessments']['alex@example.org']['assessment'], 'provider_valid')

    def test_research_queue_skips_evidenced_and_held(self):
        queue_ids = {r['id'] for r in self.dash.research()['queue']}
        self.assertNotIn('example-alex', queue_ids)
        self.assertNotIn('example-jordan', queue_ids)
        self.assertTrue(queue_ids)

    def test_daily_list_prefers_executed_batch_over_preview(self):
        preview = self.root / 'daily' / '2026-10-20-preview'
        preview.mkdir(parents=True)
        (preview / 'report.json').write_text(json.dumps({
            'date': '2026-10-20', 'execute': False, 'target': 10, 'delivered': 0, 'shortfall': 10,
        }))
        batches = self.dash.daily_list()
        self.assertEqual(batches[0]['date'], '2026-10-20')
        self.assertTrue(batches[0]['execute'])
        self.assertGreaterEqual(batches[0]['delivered'], 10)
        names = [row['date'] for row in batches]
        self.assertIn('2026-10-20-preview', names)
        self.assertGreater(names.index('2026-10-20-preview'), names.index('2026-10-20'))

    def test_daily_leads_have_six_core_columns(self):
        payload = self.dash.leads('2026-10-20')
        self.assertGreaterEqual(len(payload['leads']), 10)
        first = payload['leads'][0]
        for header in HEADERS:
            self.assertIn(header, first)
        self.assertIn('Email Risk', first)
        self.assertTrue(payload['markdown'].startswith('Leads 2026-10-20'))
        self.assertTrue(payload['report']['advice'] or payload['report']['delivered'] >= payload['report']['target'])

    def test_sample_has_team_list_and_pinned_filters(self):
        team = self.dash.team_view()
        self.assertTrue(team['joined'])
        self.assertGreaterEqual(len(team['members']), 2)
        self.assertGreaterEqual(team['leads_count'], 1)
        self.assertTrue(any(m.get('name') == 'Jordan Sample' for m in team['members']))
        casey = next(row for row in team['leads'] if row.get('Email') == 'casey@harborrealty.example')
        self.assertEqual(casey['Found By'], 'Jordan Sample')
        self.assertEqual(casey['Area'], 'Texas')
        filters = self.dash.filters_view()
        self.assertGreaterEqual(len(filters['pinned']), 1)
        names = {item['name'] for item in filters['filters']}
        self.assertIn('Austin contacted', names)
        payload = self.dash.leads('2026-10-20')
        self.assertTrue(payload['pinned_filters'])
        jane = next(row for row in payload['leads'] if row.get('Email') == 'jane@harborrealty.example')
        self.assertEqual(jane.get('Found By'), 'Operator')
        team_leads = self.dash.leads('team')
        self.assertEqual(team_leads['date'], 'team')
        self.assertGreaterEqual(len(team_leads['leads']), 1)
        dates = [row['date'] for row in payload['batches']]
        self.assertIn('team', dates)

    def test_hold_uses_store(self):
        self.dash.hold('example-robin', 'stale_leadership_claim', 'https://example.net/team')
        contact = self.dash.contact('example-robin')['contact']
        self.assertTrue(contact['held'])
        self.assertEqual(contact['hold_reason'], 'stale_leadership_claim')

    def test_same_day_merge_keeps_overview_and_daily_counts(self):
        from lead_research.configuration import default_config
        from lead_research.daily import History, run_daily
        from lead_research.store import Store

        def person(first, last, domain, title='Owner'):
            return {'full_name': f'{first} {last}', 'title': title, 'email': f'{first.lower()}@{domain}',
                    'linkedin_url': None, 'evidence_url': f'https://{domain}/team', 'method': 'mailto'}

        def site(domain, people):
            return {'domain': domain, 'status': 'ok', 'company': {'name': 'Harbor Realty'},
                    'people': people, 'pattern': None, 'pages_fetched': 2, 'errors': []}

        work = Path(self.tmp.name) / 'merge-ws'
        work.mkdir()
        store = Store(work / 'research.sqlite3')
        self.addCleanup(store.close)
        history = History(work / 'lead-history.sqlite3')
        history.save_sites([site('harbor.example', [
            person('Jordan', 'Blake', 'harbor.example'),
            person('Casey', 'Morgan', 'harbor.example', 'President'),
            person('Riley', 'Quinn', 'harbor.example', 'Managing Broker'),
        ])])
        history.close()
        (work / 'providers.json').write_text(json.dumps(default_config()))
        first = run_daily(work, store, default_config(), target=1, sources=('build',), execute=True, day='2026-01-01')
        second = run_daily(work, store, default_config(), target=3, sources=('build',), execute=True, day='2026-01-01')
        self.assertEqual((first['delivered'], second['delivered'], second['this_run'], second['prior_delivered']), (1, 3, 2, 1))
        dash = Dashboard(work, sample=False)
        overview = dash.overview()
        self.assertEqual(overview['latest_daily']['date'], '2026-01-01')
        self.assertEqual(overview['latest_daily']['delivered'], 3)
        self.assertEqual(overview['latest_daily']['target'], 3)
        self.assertTrue(overview['latest_daily']['execute'])
        self.assertEqual(overview['report']['this_run'], 2)
        self.assertEqual(overview['report']['prior_delivered'], 1)
        daily = dash.daily('2026-01-01')
        self.assertEqual(len(daily['leads']), 3)
        self.assertIn('This run added 2', daily['markdown'])
        names = {row['Full Name'] for row in daily['leads']}
        self.assertIn('Jordan Blake', names)
        folders = [p.name for p in (work / 'daily').iterdir() if p.is_dir()]
        self.assertEqual(folders, ['2026-01-01'])
        self.assertTrue((work / 'daily' / '2026-01-01' / 'runs').is_dir())

    def test_provider_toggle_writes_config(self):
        before = {p['id']: p for p in self.dash.providers()['providers']}
        self.assertTrue(before['hunter']['enabled'])
        self.dash.set_enabled('hunter', False)
        after = {p['id']: p for p in self.dash.providers()['providers']}
        self.assertFalse(after['hunter']['enabled'])
        self.assertFalse(after['hunter']['key_present'])
        self.assertTrue(after['zerobounce']['key_present'] is False or after['zerobounce']['enabled'])

    def test_overview_charts_use_workspace_batches(self):
        charts = self.dash.overview()['charts']
        dates = [row['date'] for row in charts['leads_per_day']]
        self.assertIn('2026-10-20', dates)
        self.assertIn('2026-10-19', dates)
        self.assertGreaterEqual(charts['leads_per_day'][-1]['delivered'], 10)
        self.assertGreaterEqual(charts['verification']['rate'], 0)
        self.assertTrue(charts['sources'])
        self.assertTrue(any(row['id'] == 'hunter' for row in charts['provider_quota']))
        self.assertIn('budget_spent', charts)

    def test_ops_sidecar_does_not_rewrite_leads_csv(self):
        saved = self.dash.update_ops({
            'key': 'jane@harborrealty.example',
            'stage': 'replied',
            'owner': 'A. Sample',
            'notes': 'Follow up after the intro.',
        })
        self.assertEqual(saved['ops']['stage'], 'replied')
        self.assertEqual((self.root / 'veinore-ops.json').stat().st_mode & 0o777, 0o600)
        jane = next(row for row in self.dash.leads('2026-10-20')['leads'] if row.get('Email') == 'jane@harborrealty.example')
        self.assertEqual(jane['Stage'], 'replied')
        self.assertEqual(jane['Owner'], 'A. Sample')
        csv_text = (self.root / 'daily' / '2026-10-20' / 'leads.csv').read_text(encoding='utf-8')
        self.assertNotIn('Follow up after the intro.', csv_text)
        self.assertNotIn('A. Sample', csv_text)
        for header in HEADERS:
            self.assertIn(header, jane)

    def test_unknown_lead_stage_is_rejected(self):
        with self.assertRaises(PolicyError) as caught:
            self.dash.update_ops({'key': 'jane@harborrealty.example', 'stage': 'won'})
        self.assertEqual(str(caught.exception), 'unknown_lead_stage')

    def test_daily_run_defaults_to_preview(self):
        report = self.dash.daily_run(target=3)
        self.assertFalse(report['execute'])
        self.assertEqual(report['target'], 3)
        leads_path = str((report.get('files') or {}).get('leads') or '')
        self.assertIn('-preview', leads_path)
        preview_dir = self.root / 'daily' / (report['date'] + '-preview')
        self.assertTrue(preview_dir.is_dir())

    def test_daily_run_caps_target(self):
        report = self.dash.daily_run(target=999)
        self.assertEqual(report['target'], 200)
        self.assertFalse(report['execute'])

    def test_duplicate_warnings_across_batches(self):
        other = self.root / 'daily' / '2026-10-18'
        other.mkdir()
        (other / 'report.json').write_text(json.dumps({
            'date': '2026-10-18', 'execute': True, 'target': 1, 'delivered': 1,
        }), encoding='utf-8')
        (other / 'leads.csv').write_text(
            'Full Name,Title,Company,Company Website,LinkedIn URL,Email\n'
            'Jane Sample,Founder,Harbor Realty Group,harborrealty.example,,jane@harborrealty.example\n',
            encoding='utf-8',
        )
        warnings = self.dash.daily('2026-10-20')['duplicates']
        jane = [row for row in warnings if row['email'] == 'jane@harborrealty.example']
        self.assertTrue(jane)
        self.assertEqual(jane[0]['kind'], 'across_batches')
        self.assertEqual(jane[0]['also_in'], '2026-10-18')

    def test_budget_view_preserves_attested_pools(self):
        view = self.dash.budget_view()
        pools = {(row['provider'], row['pool']) for row in view['budgets']}
        self.assertIn(('hunter', 'credits'), pools)
        self.assertTrue(any(row.get('remaining') for row in view['budgets']))
        zb = next((row for row in view['budgets'] if row['provider'] == 'zerobounce'), None)
        self.assertIsNotNone(zb)
        self.assertEqual(zb['spent'], '1')
        self.assertLess(zb['spent_units'], 10_000_000)
        charts = self.dash.overview()['charts']
        spent = next(row for row in charts['budget_spent'] if row['provider'] == 'zerobounce')
        self.assertEqual(spent['spent'], '1')

    def test_jobs_include_synthetic_verify(self):
        attempts = self.dash.jobs()['attempts']
        self.assertTrue(any(a['provider'] == 'zerobounce' and a['state'] == 'completed' for a in attempts))
        self.assertTrue(all('url' not in a and 'headers' not in a for a in attempts))

    def test_history_source_quality_and_patterns(self):
        view = self.dash.history_view()
        self.assertIn('bay.example', view['patterns'])
        self.assertTrue(view['source_quality'])
        self.assertTrue(view['refills'])
        self.assertTrue(view['suppressed'])


class CleanApiTests(unittest.TestCase):
    def test_clean_rows_calls_quality_engine(self):
        result = clean_rows([
            {
                'Full Name': 'Jane Sample', 'Title': 'Founder & CEO', 'Company': 'Harbor Realty Group',
                'Company Website': 'harborrealty.example', 'LinkedIn URL': 'https://www.linkedin.com/in/jane-sample/',
                'Email': 'jane@harborrealty.example',
            },
            {
                'Full Name': 'Not A Person LLC', 'Title': 'Agent', 'Company': 'Random Association',
                'Email': 'info@example.org',
            },
        ])
        self.assertEqual(result['summary']['input_rows'], 2)
        self.assertGreaterEqual(result['summary']['clean'], 1)
        self.assertGreaterEqual(result['summary']['rejected'], 1)
        self.assertEqual(result['clean'][0]['full_name'], 'Jane Sample')


class WorkspaceSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._env = {
            'LEAD_RESEARCH_WORKSPACE': os.environ.get('LEAD_RESEARCH_WORKSPACE'),
            'LEAD_RESEARCH_SAMPLE': os.environ.get('LEAD_RESEARCH_SAMPLE'),
        }
        os.environ.pop('LEAD_RESEARCH_WORKSPACE', None)
        os.environ.pop('LEAD_RESEARCH_SAMPLE', None)
        self._paths = (snapshot.SETTINGS_PATH, snapshot.DATA_DIR, snapshot.SAMPLE_DIR)
        snapshot.DATA_DIR = self.root / 'data'
        snapshot.SAMPLE_DIR = self.root / 'data' / 'sample-workspace'
        snapshot.SETTINGS_PATH = self.root / 'data' / 'settings.json'
        self.real = self.root / 'real-workspace'
        self.real.mkdir()

    def tearDown(self):
        snapshot.SETTINGS_PATH, snapshot.DATA_DIR, snapshot.SAMPLE_DIR = self._paths
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def test_default_resolution_is_sample(self):
        path, sample = resolve_workspace({})
        self.assertTrue(sample)
        self.assertEqual(path, snapshot.SAMPLE_DIR.resolve())

    def test_explicit_sample_off_requires_a_directory(self):
        with self.assertRaises(PolicyError) as caught:
            resolve_workspace({'sample': False})
        self.assertEqual(str(caught.exception), 'workspace_required')
        missing = self.root / 'missing'
        with self.assertRaises(PolicyError) as caught:
            validate_workspace_path(missing)
        self.assertEqual(str(caught.exception), 'workspace_not_found')
        with self.assertRaises(PolicyError) as caught:
            validate_workspace_path(str(self.real / '..' / 'escaped'))
        self.assertEqual(str(caught.exception), 'invalid_workspace_path')

    def test_real_workspace_round_trip_keeps_the_path_in_sample_mode(self):
        saved = update_settings({'workspace': str(self.real)})
        self.assertFalse(saved['sample'])
        self.assertEqual(saved['workspace'], str(self.real.resolve()))
        self.assertFalse(saved['env_locked'])
        self.assertFalse(saved['initialized'])
        again = update_settings({'sample': True})
        self.assertTrue(again['sample'])
        self.assertEqual(again['settings']['workspace'], str(self.real.resolve()))
        path, sample = resolve_workspace(again['settings'])
        self.assertTrue(sample)
        self.assertEqual(path, snapshot.SAMPLE_DIR.resolve())

    def test_bad_path_is_not_saved(self):
        with self.assertRaises(PolicyError):
            update_settings({'workspace': str(self.root / 'nope')})
        self.assertFalse(snapshot.SETTINGS_PATH.exists())

    def test_environment_workspace_overrides_saved_settings(self):
        other = self.root / 'from-env'
        other.mkdir()
        os.environ['LEAD_RESEARCH_WORKSPACE'] = str(other)
        saved = update_settings({'workspace': str(self.real)})
        self.assertTrue(saved['env_locked'])
        self.assertEqual(saved['workspace'], str(other.resolve()))
        self.assertEqual(saved['settings']['workspace'], str(self.real.resolve()))
        self.assertFalse(saved['sample'])


class ReleaseAllowsDashboardTooling(unittest.TestCase):
    def test_package_json_is_tooling_not_contact_data(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'release_check', Path(__file__).resolve().parents[1] / 'scripts' / 'release_check.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'dashboard').mkdir()
            (root / 'dashboard' / 'package.json').write_text(json.dumps({'name': 'dashboard', 'private': True}))
            (root / 'a.py').write_text('print(1)\n')
            result = module.check(root)
            self.assertTrue(result['passed'], result)

    def test_dashboard_brand_png_is_not_contact_data(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'release_check', Path(__file__).resolve().parents[1] / 'scripts' / 'release_check.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            public = root / 'dashboard' / 'public'
            public.mkdir(parents=True)
            shots = root / 'docs' / 'screenshots'
            shots.mkdir(parents=True)
            (public / 'favicon-32.png').write_bytes(b'\x89PNG\r\n\x1a\n')
            (shots / 'overview.png').write_bytes(b'\x89PNG\r\n\x1a\n')
            (root / 'notes.png').write_bytes(b'\x89PNG\r\n\x1a\n')
            (root / 'a.py').write_text('print(1)\n')
            result = module.check(root)
            rules = {item['path']: item['rule'] for item in result['findings']}
            self.assertNotIn('dashboard/public/favicon-32.png', rules)
            self.assertNotIn('docs/screenshots/overview.png', rules)
            self.assertEqual(rules.get('notes.png'), 'unexpected_binary')


class DashboardSafetyTests(unittest.TestCase):
    def test_api_refuses_non_loopback_bind(self):
        import server as dashboard_server
        with self.assertRaises(SystemExit) as ctx:
            dashboard_server.main(['--host', '0.0.0.0'])
        self.assertIn('loopback', str(ctx.exception))

    def test_provider_rows_expose_env_names_not_values(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / 'sample'
        seed_sample(root)
        os.environ['HUNTER_API_KEY'] = 'SUPERSECRETKEYVALUE'
        self.addCleanup(lambda: os.environ.pop('HUNTER_API_KEY', None))
        payload = Dashboard(root, sample=False).providers()
        blob = json.dumps(payload)
        self.assertNotIn('SUPERSECRETKEYVALUE', blob)
        hunter = next(row for row in payload['providers'] if row['id'] == 'hunter')
        self.assertEqual(hunter['auth_env'], ['HUNTER_API_KEY'])
        self.assertTrue(hunter['key_present'])
        self.assertNotIn('api_key', hunter)
        self.assertNotIn('url', hunter)


if __name__ == '__main__':
    unittest.main()
