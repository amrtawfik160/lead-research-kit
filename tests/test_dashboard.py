import json
import sys
import tempfile
import unittest
from pathlib import Path

from lead_research.quality import HEADERS

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dashboard'))
from api.sample import seed_sample  # noqa: E402
from api.snapshot import Dashboard, clean_rows  # noqa: E402


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

    def test_daily_leads_have_six_core_columns(self):
        payload = self.dash.leads('2026-10-20')
        self.assertGreaterEqual(len(payload['leads']), 10)
        first = payload['leads'][0]
        for header in HEADERS:
            self.assertIn(header, first)
        self.assertIn('Email Risk', first)
        self.assertTrue(payload['markdown'].startswith('Leads 2026-10-20'))
        self.assertTrue(payload['report']['advice'] or payload['report']['delivered'] >= payload['report']['target'])

    def test_hold_uses_store(self):
        self.dash.hold('example-robin', 'stale_leadership_claim', 'https://example.net/team')
        contact = self.dash.contact('example-robin')['contact']
        self.assertTrue(contact['held'])
        self.assertEqual(contact['hold_reason'], 'stale_leadership_claim')

    def test_provider_toggle_writes_config(self):
        before = {p['id']: p for p in self.dash.providers()['providers']}
        self.assertTrue(before['hunter']['enabled'])
        self.dash.set_enabled('hunter', False)
        after = {p['id']: p for p in self.dash.providers()['providers']}
        self.assertFalse(after['hunter']['enabled'])
        self.assertFalse(after['hunter']['key_present'])
        self.assertTrue(after['zerobounce']['key_present'] is False or after['zerobounce']['enabled'])

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


if __name__ == '__main__':
    unittest.main()
