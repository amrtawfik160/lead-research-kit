import tempfile
import unittest
from pathlib import Path

from lead_research.models import Outcome
from lead_research.policy import input_key, normalize_job
from lead_research.resolve import (company_key, firm_core, pick_domain, resolution_jobs, resolution_queries,
                                   resolution_query, resolve_from_store, verify_domain)
from lead_research.store import Store
from lead_research.webfetch import FetchResult

TX_FIRM = {'organization': 'Harborlight Homes LLC', 'city': '', 'state': 'TX', 'county': 'Bexar', 'phone': '',
           'license_number': '9000001-BB', 'source': 'tx-trec', 'decision_maker': 'Jasper D Quillon'}
NY_FIRM = {'organization': 'Corraway Realty Group LLC', 'city': 'New York', 'state': 'NY', 'county': '',
           'phone': '', 'license_number': '10490000001', 'source': 'ny-dos', 'decision_maker': 'Nova Fixture'}
DBA_FIRM = {'organization': 'Crestwave Property Solutions DBA Rentwave', 'city': 'Dallas', 'state': 'TX',
            'county': 'Dallas', 'license_number': '9000005-BB', 'source': 'co-dora', 'decision_maker': 'X Y'}


def result(link, title='', snippet=''):
    return {'title': title, 'link': link, 'snippet': snippet}


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        page = self.pages.get(url)
        if page is None:
            return FetchResult(url, url, None, '', '', error='network_error')
        final, text = page if isinstance(page, tuple) else (url, page)
        return FetchResult(url, final, 200, 'text/html', text)


class QueryTests(unittest.TestCase):
    def test_firm_core(self):
        self.assertEqual(firm_core('Harborlight Homes LLC'), 'Harborlight Homes')
        self.assertEqual(firm_core('NNN Properties of Texas, INC.'), 'NNN Properties of Texas')
        self.assertEqual(firm_core('Crestwave Property Solutions DBA Rentwave'), 'Rentwave')
        self.assertEqual(firm_core('Prairiefox Realty, L.L.C.'), 'Prairiefox Realty')

    def test_queries_are_deterministic(self):
        self.assertEqual(resolution_query(TX_FIRM), '"Harborlight Homes" Bexar County Texas real estate')
        self.assertEqual(resolution_query(NY_FIRM), '"Corraway Realty Group" New York NY real estate')
        self.assertEqual(resolution_query(DBA_FIRM), '"Rentwave" Dallas TX real estate')
        self.assertEqual(resolution_query({'organization': 'LLC', 'state': 'TX'}), '')
        pairs = resolution_queries([TX_FIRM, NY_FIRM, dict(TX_FIRM)])
        self.assertEqual([k for k, _ in pairs], ['tx-trec:9000001-BB', 'ny-dos:10490000001'])
        self.assertEqual(company_key({'organization': 'Harborlight Homes LLC', 'state': 'TX', 'source': 'x'}),
                         'x:harborlight homes llc|TX')

    def test_jobs_match_existing_format(self):
        jobs = resolution_jobs([TX_FIRM, NY_FIRM])
        self.assertEqual(jobs[0], {'provider': 'serper', 'operation': 'search',
                                   'payload': {'query': '"Harborlight Homes" Bexar County Texas real estate'}})
        brave = resolution_jobs([TX_FIRM], provider='brave')
        self.assertEqual(brave[0]['payload'], {'query': '"Harborlight Homes" Bexar County Texas real estate',
                                               'count': 10})
        self.assertEqual(normalize_job(brave[0])['payload']['count'], 10)
        with self.assertRaises(Exception):
            resolution_jobs([TX_FIRM], provider='hunter')


class PickTests(unittest.TestCase):
    def test_skips_portals_and_picks_own_site(self):
        results = [
            result('https://www.zillow.com/profile/harborlight', 'Harborlight Homes - Zillow', 'San Antonio TX'),
            result('https://www.facebook.com/harborlighthomes', 'Harborlight Homes | Facebook'),
            result('https://www.bizapedia.com/tx/harborlight-homes-llc.html', 'Harborlight Homes LLC in TX'),
            result('https://www.trec.texas.gov/x', 'Harborlight Homes license'),
            result('https://www.harborlighthomes.example/about', 'Harborlight Homes | Bexar County Real Estate',
                   'Serving Bexar County, Texas'),
        ]
        pick = pick_domain(TX_FIRM, results)
        self.assertEqual(pick['domain'], 'harborlighthomes.example')
        self.assertGreaterEqual(pick['confidence'], 0.9)
        self.assertIn('county', pick['reason'])

    def test_franchise_parent_skipped_unless_brand(self):
        results = [result('https://www.kw.com/agent/corraway', 'Corraway Realty Group - Keller Williams', 'New York')]
        self.assertIsNone(pick_domain(NY_FIRM, results))
        branded = dict(NY_FIRM, organization='Keller Williams Corraway LLC')
        self.assertEqual(pick_domain(branded, [result('https://kw.com/corraway', 'Keller Williams Corraway')])['domain'],
                         'kw.com')

    def test_threshold_and_unrelated_domains(self):
        unrelated = [result('https://www.bestnyhomes.example/', 'Corraway Realty Group reviews', 'New York NY')]
        self.assertIsNone(pick_domain(NY_FIRM, unrelated))
        partial = [result('https://corraway-nyc.example/', 'Welcome', '')]
        weak = pick_domain(NY_FIRM, partial, threshold=0.0)
        self.assertEqual(weak['domain'], 'corraway-nyc.example')
        self.assertIsNone(pick_domain(NY_FIRM, partial, threshold=0.99))
        self.assertIsNone(pick_domain(NY_FIRM, []))
        self.assertIsNone(pick_domain(NY_FIRM, [{'title': 'no link'}, 'junk', result('not a url')]))

    def test_dba_brand_and_acronym(self):
        pick = pick_domain(DBA_FIRM, [result('https://rentwave.example/', 'Rentwave Property Management', 'Dallas')])
        self.assertEqual(pick['domain'], 'rentwave.example')
        acronym = {'organization': 'Silver Bay Real Estate Partners LLC', 'city': 'Austin', 'state': 'TX'}
        pick = pick_domain(acronym, [result('https://sbrep.example/', 'Silver Bay Real Estate Partners', 'Austin, TX')])
        self.assertEqual(pick['domain'], 'sbrep.example')


class VerifyTests(unittest.TestCase):
    def test_verify_domain(self):
        fetcher = FakeFetcher({
            'https://harborlighthomes.example/': '<html><head><title>Harborlight Homes | San Antonio</title></head>'
                                                 '<body>Welcome</body></html>',
            'https://rentwave.example/': '<html><head><title>Home</title><meta property="og:site_name" '
                                         'content="Rentwave"></head><body></body></html>',
            'https://corraway.example/': '<html><title>Home</title><body><p>Corraway is a NYC brokerage</p></body></html>',
            'https://moved.example/': ('https://www.zillow.com/x', '<title>Harborlight</title>'),
            'https://other.example/': '<html><title>Totally different</title><body>nothing</body></html>',
        })
        ok, evidence = verify_domain(TX_FIRM, 'harborlighthomes.example', fetcher)
        self.assertTrue(ok)
        self.assertEqual(evidence['where'], 'title')
        self.assertTrue(verify_domain(DBA_FIRM, 'rentwave.example', fetcher)[0])
        ok, evidence = verify_domain(NY_FIRM, 'corraway.example', fetcher)
        self.assertTrue(ok)
        self.assertEqual(evidence['where'], 'body')
        ok, evidence = verify_domain(TX_FIRM, 'moved.example', fetcher)
        self.assertEqual((ok, evidence['reason']), (False, 'redirected_off_domain'))
        ok, evidence = verify_domain(TX_FIRM, 'other.example', fetcher)
        self.assertEqual((ok, evidence['reason']), (False, 'name_not_on_homepage'))
        ok, evidence = verify_domain(TX_FIRM, 'down.example', fetcher)
        self.assertEqual((ok, evidence['reason']), (False, 'network_error'))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'db.sqlite3')

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _complete(self, job, results, provider='serper'):
        payload = normalize_job(job)['payload']
        attempt = self.store.reserve(provider, 'search', input_key('search', payload), payload, {'queries': '1'},
                                     provider)
        self.store.mark_sent(attempt['id'], at=1.0)
        self.store.finish(attempt['id'], Outcome('results' if results else 'not_found',
                                                 fields={'prospects': [], 'results': results}))
        return attempt['id']

    def test_resolve_from_store_round_trip(self):
        self.store.confirm_budget('serper', 'queries', '3', 'synthetic free')
        unresolved = dict(NY_FIRM, organization='Quietbrook Realty LLC', license_number='10490000009')
        jobs = resolution_jobs([TX_FIRM, NY_FIRM, unresolved])
        tx_id = self._complete(jobs[0], [
            result('https://www.zillow.com/x', 'Harborlight Homes - Zillow'),
            result('https://www.harborlighthomes.example/', 'Harborlight Homes', 'Bexar County, Texas')])
        self._complete(jobs[1], [result('https://www.yelp.com/biz/corraway', 'Corraway Realty Group - Yelp')])
        fresh = dict(NY_FIRM, organization='Neverasked Realty LLC', license_number='1')
        out = resolve_from_store(self.store, [TX_FIRM, NY_FIRM, fresh])
        self.assertEqual(out[0]['company_website'], 'harborlighthomes.example')
        self.assertEqual(out[0]['resolution']['status'], 'resolved')
        self.assertEqual(out[0]['resolution']['attempt_ids'], [tx_id])
        self.assertEqual(out[0]['resolution']['evidence_link'], 'https://www.harborlighthomes.example/')
        self.assertEqual(out[1]['company_website'], '')
        self.assertEqual(out[1]['resolution']['status'], 'no_confident_domain')
        self.assertEqual(out[2]['resolution']['status'], 'not_searched')
        self.assertEqual(TX_FIRM.get('company_website'), None)  # inputs are not mutated
        self.assertEqual(resolve_from_store(self.store, [TX_FIRM], provider='brave')[0]['company_website'], '')
        fetcher = FakeFetcher({'https://harborlighthomes.example/': '<title>Something else</title>'})
        checked = resolve_from_store(self.store, [TX_FIRM], fetcher=fetcher)[0]
        self.assertEqual(checked['company_website'], '')
        self.assertEqual(checked['resolution']['status'], 'verification_failed')


if __name__ == '__main__':
    unittest.main()
