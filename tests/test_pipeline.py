import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lead_research.cli import main
from lead_research.models import Outcome
from lead_research.pipeline import build, domains_from, merge_sources, write_build
from lead_research.quality import HEADERS
from lead_research.store import Store


def site(domain='harborrealty.example', people=None, pattern=None, name='Harbor Realty Group'):
    return {'domain': domain, 'status': 'ok', 'company': {'name': name}, 'pattern': pattern,
            'people': people if people is not None else [], 'pages_fetched': 2, 'errors': []}


PATTERN = {'pattern': '{first}.{last}', 'support': 3, 'total': 3, 'confidence': 1.0}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_domains_from_prefers_website_and_skips_personal_and_franchise(self):
        rows = [{'Company Website': 'https://www.harborrealty.example/team', 'Company': 'Harbor Realty Group'},
                {'Email': 'jane@ridgeline.example'},
                {'Email': 'someone@' 'gmail.com'},
                {'Company Website': 'kw.com', 'Company': 'Source Real Estate'},
                {'domain': 'harborrealty.example'}]
        self.assertEqual(domains_from(rows), ['harborrealty.example', 'ridgeline.example'])

    def test_site_people_become_leads_with_published_email(self):
        people = [{'full_name': 'Jordan Blake', 'title': 'Founder & CEO', 'email': 'jordan.blake@harborrealty.example',
                   'linkedin_url': 'https://www.linkedin.com/in/jordan-blake-example/', 'evidence_url': 'https://harborrealty.example/team'}]
        result = build(site_results=[site(people=people)])
        self.assertEqual(len(result['clean']), 1)
        row = result['clean'][0]
        self.assertEqual(row['email'], 'jordan.blake@harborrealty.example')
        self.assertEqual(row['email_source'], 'website_published')

    def test_site_email_fills_import_missing_email_and_linkedin(self):
        imports = [{'Full Name': 'Jordan Blake', 'Title': 'CEO', 'Company': 'Harbor Realty Group',
                    'Company Website': 'harborrealty.example', 'LinkedIn URL': '', 'Email': ''}]
        people = [{'full_name': 'Jordan Blake', 'title': 'Chief Executive Officer', 'email': 'jordan.blake@harborrealty.example',
                   'linkedin_url': 'https://www.linkedin.com/in/jordan-blake-example/', 'evidence_url': 'https://harborrealty.example/team'}]
        result = build(imports=imports, site_results=[site(people=people)])
        self.assertEqual(len(result['clean']), 1)
        self.assertEqual(result['clean'][0]['email'], 'jordan.blake@harborrealty.example')
        self.assertTrue(result['clean'][0]['linkedin_url'].endswith('/jordan-blake-example/'))

    def test_pattern_guess_is_candidate_not_email(self):
        imports = [{'Full Name': 'Casey Morgan', 'Title': 'President', 'Company': 'Harbor Realty Group',
                    'Company Website': 'harborrealty.example', 'LinkedIn URL': 'https://www.linkedin.com/in/casey-morgan-example/'}]
        result = build(imports=imports, site_results=[site(pattern=PATTERN)])
        self.assertEqual(result['clean'][0]['email'], '')
        self.assertEqual(result['candidates'][0]['Candidate Email'], 'casey.morgan@harborrealty.example')

    def test_weak_pattern_is_ignored(self):
        imports = [{'Full Name': 'Casey Morgan', 'Title': 'President', 'Company': 'Harbor Realty Group',
                    'Company Website': 'harborrealty.example', 'LinkedIn URL': 'https://www.linkedin.com/in/casey-morgan-example/'}]
        weak = {'pattern': '{first}', 'support': 1, 'total': 1, 'confidence': 1.0}
        self.assertEqual(build(imports=imports, site_results=[site(pattern=weak)])['candidates'], [])

    def test_verified_guess_is_promoted(self):
        store = Store(self.root / 'db.sqlite3')
        try:
            store.confirm_budget('hunter', 'credits', '1', 'synthetic', attested_free=True)
            store.confirm_budget('hunter', 'verify_ops', '1', 'synthetic', attested_free=True)
            attempt = store.reserve('hunter', 'verify', 'k1', {'email': 'casey.morgan@harborrealty.example'}, {'verify_ops': '1'}, 'hunter')
            store.mark_sent(attempt['id'], at=1.0)
            store.finish(attempt['id'], Outcome('provider_valid', 'casey.morgan@harborrealty.example'))
            imports = [{'Full Name': 'Casey Morgan', 'Title': 'President', 'Company': 'Harbor Realty Group',
                        'Company Website': 'harborrealty.example', 'LinkedIn URL': 'https://www.linkedin.com/in/casey-morgan-example/'}]
            result = build(imports=imports, site_results=[site(pattern=PATTERN)], store=store)
            self.assertEqual(result['clean'][0]['email'], 'casey.morgan@harborrealty.example')
            self.assertEqual(result['clean'][0]['email_source'], 'pattern_verified')
            self.assertEqual(result['candidates'], [])
        finally:
            store.close()

    def test_catch_all_guess_stays_candidate(self):
        store = Store(self.root / 'db.sqlite3')
        try:
            store.confirm_budget('hunter', 'verify_ops', '1', 'synthetic', attested_free=True)
            attempt = store.reserve('hunter', 'verify', 'k1', {'email': 'casey.morgan@harborrealty.example'}, {'verify_ops': '1'}, 'hunter')
            store.mark_sent(attempt['id'], at=1.0)
            store.finish(attempt['id'], Outcome('catch_all', 'casey.morgan@harborrealty.example', ('catch_all',)))
            imports = [{'Full Name': 'Casey Morgan', 'Title': 'President', 'Company': 'Harbor Realty Group',
                        'Company Website': 'harborrealty.example', 'LinkedIn URL': 'https://www.linkedin.com/in/casey-morgan-example/'}]
            result = build(imports=imports, site_results=[site(pattern=PATTERN)], store=store)
            self.assertEqual(result['clean'][0]['email'], '')
            self.assertEqual(len(result['candidates']), 1)
        finally:
            store.close()

    def test_hunter_prospect_and_xray_merge_by_linkedin(self):
        prospects = [{'full_name': 'Jordan Blake', 'title': 'Founder', 'company': 'Harbor Realty Group',
                      'linkedin_url': 'https://www.linkedin.com/in/jordan-blake-example/', 'source': 'xray'},
                     {'full_name': 'Jordan Blake', 'title': 'Founder', 'company': 'Harbor Realty Group',
                      'company_website': 'harborrealty.example', 'email': 'jordan@harborrealty.example',
                      'email_verification': 'valid', 'linkedin_url': 'https://www.linkedin.com/in/jordan-blake-example/',
                      'source': 'hunter', 'email_source': 'hunter_domain_search'}]
        result = build(prospects=prospects)
        self.assertEqual(result['summary']['duplicates_merged'], 1)
        self.assertEqual(result['clean'][0]['email'], 'jordan@harborrealty.example')

    def test_role_mailbox_from_site_is_not_person_email(self):
        people = [{'full_name': 'Jordan Blake', 'title': 'Owner', 'email': 'info@harborrealty.example',
                   'evidence_url': 'https://harborrealty.example/team'}]
        rows = merge_sources(site_results=[site(people=people)])
        self.assertFalse(rows[0].get('email'))

    def test_write_build_outputs_exact_headers_and_jobs(self):
        imports = [{'Full Name': 'Casey Morgan', 'Title': 'President', 'Company': 'Harbor Realty Group',
                    'Company Website': 'harborrealty.example', 'LinkedIn URL': 'https://www.linkedin.com/in/casey-morgan-example/'}]
        written = write_build(build(imports=imports, site_results=[site(pattern=PATTERN)]), self.root / 'out')
        with open(written['paths']['clean'], encoding='utf-8') as handle:
            self.assertEqual(next(csv.reader(handle)), HEADERS)
        jobs = json.loads(Path(written['paths']['verify_jobs']).read_text())
        self.assertEqual(jobs[0]['payload']['email'], 'casey.morgan@harborrealty.example')
        self.assertEqual(Path(written['paths']['email_candidates']).stat().st_mode & 0o777, 0o600)


class PipelineCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('network')):
            code = main(['--workspace', str(self.root / 'ws'), *args])
        return code, json.loads(out.getvalue())

    def write_csv(self, rows):
        path = self.root / 'in.csv'
        with path.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=HEADERS)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_clean_needs_no_workspace(self):
        path = self.write_csv([{'Full Name': 'Casey Morgan', 'Title': 'Founder & CEO', 'Company': 'Harbor Realty Group',
                                'Company Website': 'harborrealty.example', 'LinkedIn URL': 'https://www.linkedin.com/in/casey-morgan-example/',
                                'Email': 'casey@harborrealty.example'}])
        code, result = self.call('clean', str(path), '--output', str(self.root / 'out'))
        self.assertEqual(code, 0)
        self.assertEqual(result['summary']['clean'], 1)
        self.assertFalse((self.root / 'ws').exists())

    def test_crawl_plans_without_network(self):
        path = self.write_csv([{'Full Name': 'Casey Morgan', 'Company Website': 'harborrealty.example'}])
        code, result = self.call('crawl', str(path), '--output', str(self.root / 'crawl.json'))
        self.assertTrue(result['dry_run'])
        self.assertEqual(result['domains'], 1)
        self.assertFalse((self.root / 'crawl.json').exists())

    def test_jobs_commands(self):
        _, xray = self.call('jobs', 'xray', '--states', 'TX', '--titles', 'CEO,Founder')
        self.assertTrue(all(j['provider'] == 'serper' and 'site:linkedin.com/in' in j['payload']['query'] for j in xray))
        _, disc = self.call('jobs', 'hunter-discover', '--states', 'TX,FL', '--headcounts', '1-10')
        self.assertEqual(len(disc), 2)
        _, osm = self.call('jobs', 'osm', '--areas', 'Austin:TX')
        self.assertEqual(osm[0]['operation'], 'places')

    def test_build_from_crawl_file(self):
        self.call('init')
        crawl = self.root / 'crawl.json'
        crawl.write_text(json.dumps({'sites': [site(people=[{'full_name': 'Jordan Blake', 'title': 'Owner',
                                                              'email': 'jordan@harborrealty.example',
                                                              'evidence_url': 'https://harborrealty.example/team'}])]}))
        code, result = self.call('build', '--crawl', str(crawl), '--output', str(self.root / 'out'))
        self.assertEqual(code, 0)
        self.assertEqual(result['summary']['with_work_email'], 1)


if __name__ == '__main__':
    unittest.main()
