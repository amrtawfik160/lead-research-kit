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


# --- Domain guessing (free, before any paid search) -------------------------------------------

from lead_research.resolve import guess_domains, resolve_by_guess, verify_guess  # noqa: E402

GUESS_TX = {'organization': 'Harbor Realty Group, LLC', 'county': 'Travis', 'state': 'TX', 'city': 'Austin',
            'source': 'tx-trec', 'license_number': '9000009-BB'}
HARBOR_HOME = ('<html><head><title>Harbor Realty Group | Austin Homes</title></head><body><h1>Find your home</h1>'
               '<p>' + 'Local brokerage serving buyers and sellers. ' * 12 + '</p>'
               '<footer>&copy; 2026 Harbor Realty Group. 100 Main St, Austin, TX 78701</footer></body></html>')


def fake_dns(existing):
    def resolve(host, port, type=None):
        if host.removeprefix('www.') in existing:
            return [(2, 1, 6, '', ('203.0.113.10', port))]
        raise OSError('nxdomain')
    return resolve


class GuessCandidateTests(unittest.TestCase):
    def test_candidates_ranked_and_bounded(self):
        domains = guess_domains(GUESS_TX)
        self.assertEqual(domains[:2], ['harborrealtygroup.com', 'harborrealty.com'])
        self.assertLessEqual(len(domains), 8)
        self.assertTrue(all(d.endswith('.com') for d in domains[:3]))
        self.assertIn('harborrealtygroup.net', domains)
        self.assertNotIn('harbor.com', domains)  # bare short word is too often someone else

    def test_and_variants_real_estate_and_extra_try(self):
        domains = guess_domains({'organization': 'The Smith & Jones Real Estate Group Inc'})
        self.assertEqual(domains[0], 'smithandjonesrealestategroup.com')
        self.assertIn('smithjonesrealestate.com', domains)
        self.assertIn('smithjonesre.com', domains)
        self.assertIn('quillonrealty.com', guess_domains({'organization': 'Quillon Partners LLC'}))

    def test_dba_first_and_generic_or_franchise_skipped(self):
        self.assertEqual(guess_domains({'organization': 'Crestwave Property Solutions DBA Rentwave'})[0], 'rentwave.com')
        self.assertEqual(guess_domains({'organization': 'Real Estate Group LLC'}), [])
        self.assertEqual(guess_domains({'organization': 'Keller Williams Realty'}), [])
        self.assertEqual(guess_domains({'organization': 'RE/MAX Elite'}), [])
        self.assertFalse(any(d in ('realty.com', 'homes.com', 'realtor.com') for d in guess_domains({'organization': 'Homes Realty'})))


class GuessVerifyTests(unittest.TestCase):
    def test_match_with_evidence(self):
        fetcher = FakeFetcher({'https://harborrealtygroup.com/': ('https://www.harborrealtygroup.com/', HARBOR_HOME)})
        ok, evidence = verify_guess(GUESS_TX, 'harborrealtygroup.com', fetcher)
        self.assertTrue(ok, evidence)
        self.assertEqual(evidence['method'], 'guess')
        self.assertIn('title', evidence['where'])
        self.assertIn('Harbor Realty Group', evidence['matched_text'])
        self.assertEqual(evidence['location'], 'city')

    def test_parked_and_placeholder_pages_rejected(self):
        pages = {
            'https://harborrealtygroup.com/': '<html><title>harborrealtygroup.com</title><body>Harbor Realty Group - '
                                              'This domain is for sale! Make an offer. HugeDomains</body></html>',
            'https://harborrealty.com/': '<html><title>Harbor Realty Group</title><body>Related searches Austin TX. '
                                         'This web page is parked FREE, courtesy of GoDaddy.</body></html>',
            'https://harborrealty.net/': '<html><title>Harbor Realty Group</title><body>Coming soon. Austin, TX</body></html>',
        }
        fetcher = FakeFetcher(pages)
        self.assertEqual(verify_guess(GUESS_TX, 'harborrealtygroup.com', fetcher)[1]['reason'], 'parked_or_for_sale')
        self.assertEqual(verify_guess(GUESS_TX, 'harborrealty.com', fetcher)[1]['reason'], 'parked_or_for_sale')
        self.assertEqual(verify_guess(GUESS_TX, 'harborrealty.net', fetcher)[1]['reason'], 'placeholder_page')

    def test_wrong_firm_rejected(self):
        pages = {
            # Same words only in body copy, not in the brand zones
            'https://harborrealtygroup.com/': '<html><title>Seaside Marine Supply</title><body><h1>Boats</h1>'
                                              'We ship to Harbor Realty Group offices in Austin TX</body></html>',
            # Right name, wrong state (an Ohio namesake)
            'https://harborrealty.com/': '<html><title>Harbor Realty Group</title><body><h1>Harbor Realty Group</h1>'
                                         'Serving Columbus, OH 43215 since 1990 with honest service and local expertise '
                                         'for every buyer and seller in central Ohio</body></html>',
            # Redirects to a portal
            'https://harborrealty.us/': ('https://www.zillow.com/profile/harbor', '<title>Harbor Realty Group</title>'),
        }
        fetcher = FakeFetcher(pages)
        self.assertEqual(verify_guess(GUESS_TX, 'harborrealtygroup.com', fetcher)[1]['reason'], 'name_not_in_brand_zones')
        self.assertEqual(verify_guess(GUESS_TX, 'harborrealty.com', fetcher)[1]['reason'], 'location_not_on_homepage')
        self.assertEqual(verify_guess(GUESS_TX, 'harborrealty.us', fetcher)[1]['reason'], 'redirected_off_domain')

    def test_resolve_by_guess_uses_dns_then_first_verified(self):
        fetcher = FakeFetcher({'https://harborrealty.com/': HARBOR_HOME,
                               'https://harborrealtygroup.net/': HARBOR_HOME})
        dns = fake_dns({'harborrealty.com', 'harborrealtygroup.net'})
        other = {'organization': 'Nowhere Ventures Realty', 'state': 'TX', 'source': 'tx-trec', 'license_number': 'X1'}
        known = {**GUESS_TX, 'license_number': 'K1', 'company_website': 'known.example'}
        rows = resolve_by_guess([GUESS_TX, other, known], fetcher, dns=dns, workers=2)
        self.assertEqual(rows[0]['company_website'], 'harborrealty.com')
        self.assertEqual(rows[0]['resolution']['status'], 'resolved')
        self.assertEqual(rows[0]['resolution']['tried'], ['harborrealty.com'])
        self.assertEqual(fetcher.urls.count('https://harborrealtygroup.net/'), 0)  # stopped at the first match
        self.assertEqual((rows[1]['company_website'], rows[1]['resolution']['status']), ('', 'no_guess_match'))
        self.assertEqual(rows[1]['resolution']['tried'], [])  # no DNS record -> no fetch
        self.assertEqual(rows[2]['company_website'], 'known.example')
        self.assertNotIn('https://nowhereventuresrealty.com/', fetcher.urls)
