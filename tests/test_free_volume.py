"""Free-volume and speed extensions: Hunter Discover filters, Serper mini-batch, Exa company search,
Hunter name resolution and opt-in free-plan pools. Offline, synthetic fixtures only."""
import json
import tempfile
import unittest
from pathlib import Path

from lead_research.configuration import DEFAULT, default_config, load_config, save_config
from lead_research.discovery import (HUNTER_NAME_BATCH, collect, hunter_discover_city_jobs,
                                     hunter_discover_keyword_jobs, hunter_domain_jobs, hunter_name_resolution_jobs,
                                     serper_batch_jobs)
from lead_research.models import BudgetError, Outcome, PolicyError, ProviderError, Response
from lead_research.policy import input_key, normalize_discover_filters, normalize_job
from lead_research.providers import get_provider, hunter_free_pools_from_account
from lead_research.resolve import (exa_resolution_jobs, exa_resolution_query, firm_name_key, pick_domain,
                                   resolution_batch_jobs, resolution_query, resolve_from_hunter, resolve_from_store,
                                   searched_queries)
from lead_research.runner import Runner
from lead_research.store import Store
from lead_research.transport import MAX_RESPONSE_CEILING

ENV = {'HUNTER_API_KEY': 'fixture-secret', 'SERPER_API_KEY': 'fixture-secret', 'EXA_API_KEY': 'fixture-secret'}

TX_FIRM = {'organization': 'Harborlight Homes LLC', 'city': '', 'state': 'TX', 'county': 'Bexar',
           'license_number': '9000001-BB', 'source': 'tx-trec'}
NY_FIRM = {'organization': 'Corraway Realty Group LLC', 'city': 'New York', 'state': 'NY', 'county': '',
           'license_number': '10490000001', 'source': 'ny-dos'}
CO_FIRM = {'organization': 'Pinecrest Brokerage, Inc.', 'city': 'Denver', 'state': 'CO',
           'license_number': 'CO-1', 'source': 'co-dora'}


def serper_item(query, links):
    return {'searchParameters': {'q': query, 'num': 10}, 'credits': 1,
            'organic': [{'title': title, 'link': link, 'snippet': snippet, 'position': i}
                        for i, (link, title, snippet) in enumerate(links, 1)]}


class FakeTransport:
    def __init__(self, responses):
        self.responses, self.requests = list(responses), []

    def send(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


class DiscoverFilterTests(unittest.TestCase):
    def test_organization_filter(self):
        out = normalize_discover_filters({'organization': {'domain': ['www.Harbor.example.org', 'harbor.example.org'],
                                                           'name': ['Corraway Realty', 'corraway realty', 'Pinecrest']}})
        self.assertEqual(out['organization'], {'domain': ['harbor.example.org'], 'name': ['Corraway Realty', 'Pinecrest']})
        self.assertEqual(len(normalize_discover_filters({'organization': {'name': ['Firm %d' % i for i in range(100)]}})
                             ['organization']['name']), 100)
        for bad in ({'organization': {'name': ['Firm %d' % i for i in range(101)]}},
                    {'organization': {'domain': ['d%d.example.org' % i for i in range(101)]}},
                    {'organization': {}}, {'organization': {'name': []}}, {'organization': {'name': 'Pinecrest'}},
                    {'organization': {'domain': ['not a domain']}}, {'organization': {'name': ['<script>']}},
                    {'organization': {'similar': ['x']}}, {'similar_to': {'domain': 'example.org'}},
                    {'organization': {'name': ['Pinecrest']}, 'offset': 100}):
            with self.assertRaises(PolicyError, msg=bad):
                normalize_discover_filters(bad)

    def test_job_openings_filter(self):
        out = normalize_discover_filters({'job_openings': {'hiring': True, 'department': ['sales', 'marketing', 'sales'],
                                                           'posted_date': 'last30d',
                                                           'job_title': {'include': ['Leasing Agent']}}})
        self.assertEqual(out['job_openings'], {'hiring': True, 'department': ['marketing', 'sales'],
                                               'posted_date': 'last30d', 'job_title': {'include': ['Leasing Agent']}})
        for bad in ({'hiring': 'true'}, {'posted_date': 'last14d'}, {'department': []}, {'department': ['sa\x00les']},
                    {'hiring': False, 'posted_date': 'last7d'}, {}, {'open_roles': {'from': 1}},
                    {'job_title': {'include': ['x'] * 0}}):
            with self.assertRaises(PolicyError, msg=bad):
                normalize_discover_filters({'job_openings': bad})
        self.assertEqual(normalize_discover_filters({'job_openings': {'hiring': True}})['job_openings'], {'hiring': True})

    def test_city_requires_country_and_state_requires_us(self):
        ok = normalize_discover_filters({'headquarters_location': {'include': [{'city': 'Austin', 'country': 'US', 'state': 'TX'}]}})
        self.assertEqual(ok['headquarters_location']['include'], [{'city': 'Austin', 'country': 'US', 'state': 'TX'}])
        for bad in ([{'city': 'Austin'}], [{'city': 'Austin', 'state': 'TX'}], [{'city': 'Paris', 'country': 'FR', 'state': 'TX'}],
                    [{'city': 'A', 'country': 'US'}], [{'city': 'Austin', 'country': 'US', 'continent': 'Europe'}]):
            with self.assertRaises(PolicyError, msg=bad):
                normalize_discover_filters({'headquarters_location': {'include': bad}})

    def test_cache_identity_includes_new_filters(self):
        base = {'headquarters_location': {'include': [{'country': 'US', 'state': 'TX'}]}}
        keys = {input_key('discover', {'filters': f}) for f in (
            base, {**base, 'organization': {'name': ['Pinecrest']}}, {**base, 'organization': {'name': ['Harborlight']}},
            {**base, 'job_openings': {'hiring': True}}, {**base, 'job_openings': {'hiring': True, 'posted_date': 'last7d'}})}
        self.assertEqual(len(keys), 5)
        self.assertEqual(input_key('discover', {'filters': {'organization': {'name': ['B Firm', 'A Firm']}}}),
                         input_key('discover', {'filters': {'organization': {'name': ['a firm', 'B Firm', 'A Firm']}}}))

    def test_hunter_request_body_carries_filters(self):
        filters = {'organization': {'name': ['Pinecrest']}, 'job_openings': {'hiring': True},
                   'headquarters_location': {'include': [{'city': 'Denver', 'country': 'US', 'state': 'CO'}]}, 'limit': 100}
        req = get_provider('hunter').request('discover', {'filters': filters}, ENV)
        body = json.loads(req.body)
        self.assertEqual(body['organization'], {'name': ['Pinecrest']})
        self.assertEqual(body['job_openings'], {'hiring': True})
        self.assertEqual(body['headquarters_location']['include'][0]['city'], 'Denver')
        self.assertEqual((req.method, req.url), ('POST', 'https://api.hunter.io/v2/discover'))

    def test_domain_search_defaults_are_decision_makers(self):
        job = hunter_domain_jobs(['harbor.example.org'])[0]
        req = get_provider('hunter').request('domain_search', normalize_job(job)['payload'], ENV)
        self.assertIn('type=personal', req.url)
        self.assertIn('decision_maker=true', req.url)
        self.assertIn('required_field=full_name%2Cposition', req.url)
        bare = get_provider('hunter').request('domain_search', {'domain': 'harbor.example.org'}, ENV)
        self.assertEqual(bare.url, req.url)


class DiscoveryHelperTests(unittest.TestCase):
    def test_city_jobs(self):
        jobs = hunter_discover_city_jobs([('Austin', 'TX'), {'city': 'Denver', 'state': 'Colorado'}, ('austin', 'tx')],
                                         headcounts=('1-10', '11-50'), keywords=['brokerage'], hiring=True)
        self.assertEqual(len(jobs), 4)
        filters = jobs[0]['payload']['filters']
        self.assertEqual(filters['headquarters_location'], {'include': [{'city': 'Austin', 'country': 'US', 'state': 'TX'}]})
        self.assertEqual((filters['headcount'], filters['job_openings']), (['1-10'], {'hiring': True}))
        self.assertEqual(jobs[2]['payload']['filters']['headquarters_location']['include'][0]['state'], 'CO')
        self.assertNotIn('job_openings', hunter_discover_city_jobs([('Austin', 'TX')])[0]['payload']['filters'])
        dated = hunter_discover_city_jobs([('Austin', 'TX')], hiring={'hiring': True, 'posted_date': 'last30d'})
        self.assertEqual(dated[0]['payload']['filters']['job_openings']['posted_date'], 'last30d')
        for bad in (dict(metros=[('Austin', 'ZZ')]), dict(metros=[('Austin', 'TX')], headcounts=('2-3',)),
                    dict(metros=['Austin']), dict(metros=[('Austin', 'TX')], hiring='yes'),
                    dict(metros=[('Austin', 'TX')], hiring={'posted_date': 'last1d'})):
            with self.assertRaises(PolicyError, msg=bad):
                hunter_discover_city_jobs(**bad)
        for job in jobs:
            self.assertEqual(normalize_job(job)['operation'], 'discover')

    def test_keyword_jobs(self):
        jobs = hunter_discover_keyword_jobs(['TX', 'Texas', 'NY'], [('property management',), 'commercial brokerage',
                                                                   ['property management']])
        self.assertEqual(len(jobs), 4)
        self.assertEqual(jobs[0]['payload']['filters']['keywords'], {'include': ['property management'], 'match': 'any'})
        with self.assertRaises(PolicyError):
            hunter_discover_keyword_jobs(['TX'], [[]])

    def test_name_resolution_jobs_batch_by_state(self):
        companies = [{'organization': 'Texfirm %03d Realty LLC' % i, 'state': 'TX'} for i in range(120)]
        companies += [{'organization': 'Corraway Realty Group LLC', 'state': 'NY'},
                      {'organization': 'CORRAWAY REALTY GROUP, L.L.C.', 'state': 'NY'},
                      {'organization': 'Nostate Homes Inc'}, {'organization': 'LLC', 'state': 'TX'},
                      {'organization': 'Bad!Name Realty', 'state': 'TX'}]
        jobs = hunter_name_resolution_jobs(companies)
        self.assertEqual([len(j['payload']['filters']['organization']['name']) for j in jobs], [1, 50, 50, 20, 1])
        ny = jobs[0]['payload']['filters']
        self.assertEqual(ny['organization']['name'], ['Corraway Realty Group'])
        self.assertEqual(ny['headquarters_location'], {'include': [{'country': 'US', 'state': 'NY'}]})
        self.assertEqual(jobs[1]['payload']['filters']['headquarters_location']['include'][0]['state'], 'TX')
        self.assertNotIn('headquarters_location', jobs[-1]['payload']['filters'])
        self.assertEqual(jobs[-1]['payload']['filters']['organization']['name'], ['Nostate Homes'])
        self.assertTrue(all(len(j['payload']['filters']['organization']['name']) <= HUNTER_NAME_BATCH for j in jobs))
        for job in jobs:
            normalize_job(job)
        with self.assertRaises(PolicyError):
            hunter_name_resolution_jobs(companies, batch_size=51)

    def test_serper_batch_jobs(self):
        queries = ['q %d' % i for i in range(250)] + ['q 1', ' q  2 ']
        jobs = serper_batch_jobs(queries)
        self.assertEqual([len(j['payload']['queries']) for j in jobs], [100, 100, 50])
        self.assertEqual(jobs[0], {'provider': 'serper', 'operation': 'search_batch',
                                   'payload': {'queries': ['q %d' % i for i in range(100)]}})
        self.assertEqual([len(j['payload']['queries']) for j in serper_batch_jobs(queries, size=60)], [60, 60, 60, 60, 10])
        for bad in (0, 101, True):
            with self.assertRaises(PolicyError):
                serper_batch_jobs(queries, size=bad)
        with self.assertRaises(PolicyError):
            normalize_job({'provider': 'serper', 'operation': 'search_batch', 'payload': {'queries': ['x'] * 0}})
        with self.assertRaises(PolicyError):
            normalize_job({'provider': 'serper', 'operation': 'search_batch',
                           'payload': {'queries': ['q %d' % i for i in range(101)]}})
        with self.assertRaises(PolicyError):
            normalize_job({'provider': 'serper', 'operation': 'search_batch', 'payload': {'queries': ['x'], 'num': 10}})


class SerperBatchAdapterTests(unittest.TestCase):
    def setUp(self):
        self.serper = get_provider('serper')

    def test_request_is_one_array_post(self):
        queries = ['q %d' % i for i in range(100)]
        req = self.serper.request('search_batch', {'queries': queries}, ENV)
        body = json.loads(req.body)
        self.assertEqual((req.method, req.url, len(body)), ('POST', 'https://google.serper.dev/search', 100))
        self.assertEqual(body[0], {'q': 'q 0', 'num': 10, 'gl': 'us', 'hl': 'en'})
        self.assertEqual((req.max_response_bytes, req.timeout), (MAX_RESPONSE_CEILING, 90))
        self.assertEqual(req.headers['X-API-KEY'], 'fixture-secret')
        for bad in ({'queries': []}, {'queries': ['q %d' % i for i in range(101)]}, {'queries': 'q'},
                    {'queries': ['ok'], 'page': 2}, {'query': 'x'}):
            with self.assertRaises(PolicyError, msg=bad):
                self.serper.request('search_batch', bad, ENV)
        self.assertEqual((self.serper.min_spacing['search'], self.serper.min_spacing['search_batch']), (0.1, 0.1))

    def test_parse_and_scope(self):
        data = [serper_item('alpha', [('https://alpha.example.org/', 'Alpha Realty', 'Austin')]),
                {'error': {'message': 'bad', 'statusCode': 400}},
                serper_item('gamma', []),
                serper_item('other text', [('https://delta.example.org/', 'Delta', '')]),
                serper_item('linkedin', [('https://www.linkedin.com/in/jane-example', 'Jane Example - Founder - Alpha Realty | LinkedIn',
                                          'Austin, Texas · Founder · Alpha Realty')])]
        out = self.serper.parse('search_batch', Response(200, data))
        self.assertEqual(out.assessment, 'results')
        self.assertEqual(out.fields['batches'][1]['error'], 'provider_application_error')
        scoped = self.serper.scope_outcome('search_batch', out, {'queries': ['alpha', 'beta', 'gamma', 'delta', 'linkedin']})
        batches = scoped.fields['batches']
        self.assertEqual([b['query'] for b in batches], ['alpha', 'beta', 'gamma', 'delta', 'linkedin'])
        self.assertEqual(batches[0]['results'][0]['link'], 'https://alpha.example.org/')
        self.assertEqual(batches[2]['results'], [])
        self.assertEqual(batches[3]['error'], 'query_mismatch')
        self.assertEqual(batches[3]['results'], [])
        self.assertEqual(batches[4]['prospects'][0]['linkedin_url'], 'https://www.linkedin.com/in/jane-example/')
        self.assertEqual(self.serper.scope_outcome('search_batch', out, {'queries': ['alpha']}).error, 'invalid_response')

    def test_parse_failures(self):
        parse = lambda data, status=200, valid=True: self.serper.parse('search_batch', Response(status, data, {}, valid))
        self.assertEqual(parse({'message': 'Not enough credits', 'statusCode': 400}).error, 'provider_application_error')
        self.assertEqual(parse([{'error': 'x'}, 'junk']).error, 'provider_application_error')
        self.assertEqual(parse([]).error, 'invalid_response')
        self.assertEqual(parse([serper_item('q', [])] * 101).error, 'invalid_response')
        self.assertEqual(parse(None, valid=False).error, 'invalid_response')
        self.assertEqual(parse({'message': 'x'}, status=401).error, 'auth')
        self.assertEqual(parse([], status=429).error, 'rate_limit')
        self.assertEqual(parse([], status=503).error, 'provider_error')
        self.assertEqual(parse([serper_item('q', [])]).assessment, 'not_found')


class ExaCompanyTests(unittest.TestCase):
    def test_request_and_parse(self):
        exa = get_provider('exa')
        req = exa.request('company_search', {'query': 'Harborlight Homes real estate company in Bexar County, Texas'}, ENV)
        body = json.loads(req.body)
        self.assertEqual((body['category'], body['numResults'], body['type']), ('company', 10, 'auto'))
        self.assertEqual(req.headers['x-api-key'], 'fixture-secret')
        for bad in ({'query': 'x', 'num': 20}, {}, {'query': ''}):
            with self.assertRaises(PolicyError, msg=bad):
                exa.request('company_search', bad, ENV)
        data = {'results': [{'title': 'Harborlight Homes', 'url': 'https://harborlighthomes.example/?ref=exa', 'text': 'Bexar County'},
                            {'title': 'no url'}, {'title': 'Insecure', 'url': 'http://insecure.example.org/'}, 'junk'],
                'requestId': 'abc'}
        out = exa.parse('company_search', Response(200, data))
        self.assertEqual(out.fields, {'results': [{'title': 'Harborlight Homes', 'url': 'https://harborlighthomes.example/',
                                                   'snippet': 'Bexar County', 'position': 1}]})
        self.assertEqual(exa.parse('company_search', Response(200, {'results': []})).assessment, 'not_found')
        self.assertEqual(exa.parse('company_search', Response(200, {})).error, 'invalid_response')
        self.assertEqual(pick_domain(TX_FIRM, out.fields['results'])['domain'], 'harborlighthomes.example')

    def test_jobs(self):
        self.assertEqual(exa_resolution_query(TX_FIRM), 'Harborlight Homes real estate company in Bexar County, Texas')
        self.assertEqual(exa_resolution_query(NY_FIRM), 'Corraway Realty Group real estate company in New York, New York')
        self.assertEqual(exa_resolution_query({'organization': 'LLC'}), '')
        jobs = exa_resolution_jobs([TX_FIRM, NY_FIRM, dict(TX_FIRM)])
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0], {'provider': 'exa', 'operation': 'company_search',
                                   'payload': {'query': exa_resolution_query(TX_FIRM)}})


class FreePoolsTests(unittest.TestCase):
    def account(self, plan='Free', **requests):
        return {'data': {'plan_name': plan, 'plan_level': 0, 'requests': requests}}

    def test_separate_quotas_derive_credits(self):
        pools = hunter_free_pools_from_account(self.account(searches={'used': 5, 'available': 25},
                                                            verifications={'used': 10, 'available': 50}))
        self.assertEqual(pools, {'find_ops': '20', 'verify_ops': '40', 'credits': '40'})

    def test_remaining_wins_and_single_pool(self):
        pools = hunter_free_pools_from_account(self.account(
            credits={'used': 4.5, 'available': 50.0, 'remaining': 45.5},
            searches={'used': 4, 'available': 50, 'remaining': 45}, verifications={'used': 1, 'available': 100}))
        self.assertEqual(pools, {'find_ops': '45', 'verify_ops': '99', 'credits': '45.5'})
        inner = hunter_free_pools_from_account(self.account(searches={'used': 30, 'available': 25})['data'])
        self.assertEqual(inner, {'find_ops': '0'})

    def test_rejections(self):
        with self.assertRaisesRegex(ProviderError, 'not_free_plan'):
            hunter_free_pools_from_account(self.account(plan='Growth', searches={'used': 0, 'available': 10}))
        for bad in (self.account(), {'data': {'plan_name': 'Free'}}, None, self.account(searches={'used': 'x'})):
            with self.assertRaises(ProviderError, msg=bad):
                hunter_free_pools_from_account(bad)

    def test_pools_feed_confirm_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'ledger.db')
            for pool, amount in hunter_free_pools_from_account(self.account(
                    searches={'used': 0, 'available': 25}, verifications={'used': 1, 'available': 50})).items():
                store.confirm_budget('hunter', pool, amount, 'hunter account (accepted free plan)')
            self.assertEqual({b['pool']: b['remaining'] for b in store.budgets()},
                             {'credits': '49.5', 'find_ops': '25', 'verify_ops': '49'})
            store.close()


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'providers.json'

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, config):
        self.path.write_text(json.dumps(config))
        return self.path

    def test_defaults_and_migration(self):
        self.assertEqual(DEFAULT['providers']['serper']['charges']['search_batch'], {'queries': '1'})
        self.assertEqual(DEFAULT['providers']['serper']['per_item'], ['search_batch'])
        self.assertEqual(DEFAULT['providers']['exa']['charges']['company_search'], {'queries': '1'})
        legacy = default_config()
        del legacy['providers']['serper']['per_item']
        del legacy['providers']['serper']['charges']['search_batch']
        del legacy['providers']['exa']['charges']['company_search']
        legacy['providers']['serper']['enabled'] = True
        loaded = load_config(self.write(legacy))
        self.assertEqual(loaded['providers']['serper'], {**DEFAULT['providers']['serper'], 'enabled': True})
        save_config(self.path, default_config())
        self.assertEqual(load_config(self.path), default_config())

    def test_tampering_rejected(self):
        mutations = [
            lambda c: c['providers']['serper'].update(per_item=['search']),
            lambda c: c['providers']['serper'].update(per_item=[]),
            lambda c: c['providers']['brave'].update(per_item=['search']),
            lambda c: c['providers']['serper']['charges']['search_batch'].update(queries='0.5'),
            lambda c: c['providers']['exa']['charges']['company_search'].update(queries='2'),
        ]
        for mutate in mutations:
            config = default_config()
            mutate(config)
            with self.assertRaises(PolicyError):
                load_config(self.write(config))


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'ledger.db')
        self.config = default_config()
        for name in ('hunter', 'serper', 'exa'):
            self.config['providers'][name]['enabled'] = True

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def runner(self, responses=()):
        self.transport = FakeTransport(responses)
        return Runner(self.store, self.config, env=ENV, transport=self.transport, sleep=lambda _: None)

    def remaining(self, provider, pool):
        return next(b['remaining'] for b in self.store.budgets() if (b['provider'], b['pool']) == (provider, pool))

    def test_hundred_query_batch_costs_hundred_in_one_request(self):
        queries = ['firm %d austin tx real estate' % i for i in range(100)]
        self.store.confirm_budget('serper', 'queries', '150', 'synthetic free')
        job = serper_batch_jobs(queries)[0]
        plan = self.runner().run([job])['results'][0]
        self.assertEqual((plan['status'], plan['charges'], plan['charge_items']), ('ready', {'queries': '100'}, 100))
        response = Response(200, [serper_item(q, [('https://r%d.example.org/' % i, 'Result', '')]) for i, q in enumerate(queries)])
        report = self.runner([response]).run([job], execute=True)
        self.assertEqual(report['results'][0]['status'], 'completed')
        self.assertEqual(len(self.transport.requests), 1)
        self.assertEqual(self.remaining('serper', 'queries'), '50')
        attempt = self.store.attempts()[0]
        self.assertEqual((attempt['charges'], attempt['charge_items']), ({'queries': 100000000}, 100))
        self.assertEqual(attempt['outcome']['fields']['batches'][99]['query'], queries[99])
        again = self.runner([]).run([{**job, 'payload': {'queries': list(reversed(queries))}}], execute=True)
        self.assertEqual((again['results'][0]['status'], self.transport.requests), ('cached', []))
        # A second, different 100-query batch no longer fits the 50 remaining units.
        other = serper_batch_jobs(['other %d' % i for i in range(100)])[0]
        skipped = self.runner([]).run([other], execute=True)['results'][0]
        self.assertEqual((skipped['status'], skipped['reason']), ('skipped', 'insufficient_confirmed_free_budget'))
        self.assertEqual((self.transport.requests, self.remaining('serper', 'queries')), ([], '50'))

    def test_insufficient_budget_skips_without_request(self):
        self.store.confirm_budget('serper', 'queries', '99', 'synthetic free')
        job = serper_batch_jobs(['q %d' % i for i in range(100)])[0]
        result = self.runner([]).run([job], execute=True)['results'][0]
        self.assertEqual((result['status'], result['reason'], result['charges']),
                         ('skipped', 'insufficient_confirmed_free_budget', {'queries': '100'}))
        self.assertEqual((self.transport.requests, self.store.attempts()), ([], []))

    def test_per_item_marker_must_match_adapter(self):
        self.store.confirm_budget('serper', 'queries', '10', 'synthetic free')
        job = serper_batch_jobs(['a', 'b'])[0]
        self.config['providers']['serper']['per_item'] = []
        self.assertEqual(self.runner().run([job])['results'][0]['reason'], 'unsupported_charge')
        self.config['providers']['serper']['per_item'] = ['search_batch', 'search']
        single = {'provider': 'serper', 'operation': 'search', 'payload': {'query': 'a'}}
        self.assertEqual(self.runner().run([single])['results'][0]['reason'], 'unsupported_charge')

    def test_store_per_item_guard_and_refund(self):
        self.store.confirm_budget('serper', 'queries', '10', 'synthetic free')
        payload = {'queries': ['a', 'b', 'c']}
        key = input_key('search_batch', payload)
        for charges, items in (({'queries': '3'}, 2), ({'queries': '3'}, 101), ({'queries': '300'}, 3), ({'queries': '3'}, True)):
            with self.assertRaises(PolicyError):
                self.store.reserve('serper', 'search_batch', key, payload, charges, 'serper', items=items)
        with self.assertRaises(PolicyError):
            self.store.reserve('serper', 'search_batch', key, payload, {'queries': '3'}, 'serper')
        with self.assertRaises(BudgetError):
            self.store.reserve('serper', 'search_batch', key, payload, {'queries': '30'}, 'serper', items=30)
        attempt = self.store.reserve('serper', 'search_batch', key, payload, {'queries': '1.5'}, 'serper', items=3)
        self.assertEqual(self.remaining('serper', 'queries'), '8.5')
        self.store.cancel_reserved(attempt['id'], 'test refund')
        self.assertEqual(self.remaining('serper', 'queries'), '10')

    def test_batch_feeds_resolve_and_collect(self):
        self.store.confirm_budget('serper', 'queries', '10', 'synthetic free')
        jobs = resolution_batch_jobs([TX_FIRM, NY_FIRM, CO_FIRM])
        self.assertEqual(len(jobs), 1)
        queries = jobs[0]['payload']['queries']
        self.assertEqual(queries, [resolution_query(TX_FIRM), resolution_query(NY_FIRM), resolution_query(CO_FIRM)])
        response = Response(200, [
            serper_item(queries[0], [('https://www.zillow.com/x', 'Harborlight Homes - Zillow', ''),
                                     ('https://www.harborlighthomes.example/', 'Harborlight Homes', 'Bexar County, Texas')]),
            {'error': {'message': 'temporary', 'statusCode': 500}},
            serper_item(queries[2], [('https://www.linkedin.com/in/pat-sample', 'Pat Sample - Broker - Pinecrest Brokerage | LinkedIn',
                                      'Denver, Colorado · Broker · Pinecrest Brokerage')])])
        report = self.runner([response]).run(jobs, execute=True)
        self.assertEqual(report['results'][0]['status'], 'completed')
        self.assertEqual(self.remaining('serper', 'queries'), '7')
        out = resolve_from_store(self.store, [TX_FIRM, NY_FIRM, CO_FIRM])
        self.assertEqual((out[0]['company_website'], out[0]['resolution']['status']), ('harborlighthomes.example', 'resolved'))
        self.assertEqual(out[1]['resolution']['status'], 'not_searched')  # the failed entry can be searched again
        self.assertEqual(out[2]['resolution']['status'], 'no_confident_domain')
        self.assertEqual(searched_queries(self.store), {queries[0], queries[2]})
        retry = resolution_batch_jobs([TX_FIRM, NY_FIRM, CO_FIRM], store=self.store)
        self.assertEqual([j['payload']['queries'] for j in retry], [[queries[1]]])
        self.assertEqual(resolution_batch_jobs([TX_FIRM], store=self.store), [])
        prospects = collect(self.store)['prospects']
        self.assertEqual([p['linkedin_url'] for p in prospects], ['https://www.linkedin.com/in/pat-sample/'])
        self.assertEqual(prospects[0]['sources'][0]['query'], queries[2])

    def test_exa_company_search_resolves(self):
        self.store.confirm_budget('exa', 'queries', '2', 'synthetic free')
        jobs = exa_resolution_jobs([TX_FIRM, NY_FIRM])
        responses = [Response(200, {'results': [{'title': 'Harborlight Homes | San Antonio', 'url': 'https://harborlighthomes.example/'}]}),
                     Response(200, {'results': [{'title': 'Unrelated', 'url': 'https://www.yelp.com/biz/corraway'}]})]
        report = self.runner(responses).run(jobs, execute=True)
        self.assertEqual([r['status'] for r in report['results']], ['completed', 'completed'])
        self.assertEqual(self.remaining('exa', 'queries'), '0')
        out = resolve_from_store(self.store, [TX_FIRM, NY_FIRM, CO_FIRM], provider='exa')
        self.assertEqual(out[0]['company_website'], 'harborlighthomes.example')
        self.assertEqual(out[0]['resolution']['provider'], 'exa')
        self.assertEqual(out[1]['resolution']['status'], 'no_confident_domain')
        self.assertEqual(out[2]['resolution']['status'], 'not_searched')
        self.assertEqual(resolve_from_store(self.store, [TX_FIRM])[0]['company_website'], '')  # serper untouched

    def _discover(self, filters, companies):
        payload = normalize_job({'provider': 'hunter', 'operation': 'discover', 'payload': {'filters': filters}})['payload']
        attempt = self.store.reserve('hunter', 'discover', input_key('discover', payload), payload,
                                     {'discover_requests': '1'}, 'hunter')
        self.store.mark_sent(attempt['id'], at=1.0)
        rows = [{'domain': d, 'organization': o, 'source': 'hunter_discover'} for o, d in companies]
        self.store.finish(attempt['id'], Outcome('results', fields={'companies': rows}))
        return attempt['id']

    def test_hunter_name_resolution_end_to_end(self):
        self.store.confirm_budget('hunter', 'discover_requests', '5', 'synthetic free')
        firms = [TX_FIRM, NY_FIRM, CO_FIRM]
        jobs = hunter_name_resolution_jobs(firms)
        self.assertEqual(len(jobs), 3)
        by_state = {j['payload']['filters']['headquarters_location']['include'][0]['state']: j for j in jobs}
        responses = {
            'CO': {'data': [{'domain': 'pinecrest.example', 'organization': 'Pinecrest Brokerage'}], 'meta': {'results': 1}},
            'NY': {'data': [{'domain': 'corraway.example', 'organization': 'The Corraway Realty Group'},
                            {'domain': 'corraway-nyc.example', 'organization': 'Corraway Realty Group, LLC'}], 'meta': {}},
            'TX': {'data': [{'domain': 'harborlighthomes.example', 'organization': 'Harborlight Homes'}], 'meta': {}}}
        ordered = [Response(200, responses[j['payload']['filters']['headquarters_location']['include'][0]['state']]) for j in jobs]
        report = self.runner(ordered).run(jobs, execute=True)
        self.assertEqual([r['status'] for r in report['results']], ['completed'] * 3)
        self.assertEqual(self.remaining('hunter', 'discover_requests'), '2')
        out = resolve_from_hunter(self.store, firms + [{'organization': 'Already Set LLC', 'company_website': 'set.example.org'}])
        self.assertEqual((out[0]['company_website'], out[0]['resolution']['method']), ('harborlighthomes.example', 'hunter_discover_name'))
        self.assertTrue(out[0]['resolution']['state_checked'])
        tx_attempt = next(a['id'] for a in self.store.attempts() if a['payload']['filters']['headquarters_location']['include'][0]['state'] == 'TX')
        self.assertEqual(out[0]['resolution']['attempt_id'], tx_attempt)
        self.assertEqual((out[1]['company_website'], out[1]['resolution']['status']), ('', 'ambiguous_domain'))
        self.assertEqual(out[2]['company_website'], 'pinecrest.example')
        self.assertEqual(out[3], {'organization': 'Already Set LLC', 'company_website': 'set.example.org'})
        self.assertIn('TX', by_state)

    def test_hunter_resolution_never_guesses(self):
        self.store.confirm_budget('hunter', 'discover_requests', '5', 'synthetic free')
        self._discover({'headquarters_location': {'include': [{'country': 'US', 'state': 'TX'}]}},
                       [('Summit Homes', 'summit-tx.example'), ('Keller Williams Lakeview', 'kw.com')])
        self._discover({'organization': {'name': ['Bluejay Realty', 'Summit Homes']}},
                       [('Bluejay Realty', 'bluejay.example'), ('Summit Homes', 'summit-any.example')])
        tx_summit = {'organization': 'Summit Homes LLC', 'state': 'TX', 'source': 's', 'license_number': '1'}
        co_summit = {'organization': 'Summit Homes Inc', 'state': 'CO', 'source': 's', 'license_number': '2'}
        out = resolve_from_hunter(self.store, [tx_summit, co_summit])
        # Two firms share the name in different states: only the TX-labelled result counts for TX.
        self.assertEqual(out[0]['company_website'], 'summit-tx.example')
        self.assertEqual((out[1]['company_website'], out[1]['resolution']['status']), ('', 'state_mismatch'))
        nostate = {'organization': 'Summit Homes', 'source': 's', 'license_number': '3'}
        out = resolve_from_hunter(self.store, [tx_summit, nostate])
        self.assertEqual([r['resolution']['status'] for r in out], ['ambiguous_firm_name', 'ambiguous_firm_name'])
        dup = resolve_from_hunter(self.store, [tx_summit, dict(tx_summit, license_number='9')])
        self.assertEqual([r['company_website'] for r in dup], ['', ''])
        alone = resolve_from_hunter(self.store, [co_summit])[0]
        # Alone in CO: the unlabelled result might be the TX firm, so nothing is picked.
        self.assertEqual(alone['resolution']['status'], 'ambiguous_domain')
        franchise = resolve_from_hunter(self.store, [{'organization': 'Keller Williams Lakeview', 'state': 'TX'}])[0]
        self.assertEqual((franchise['company_website'], franchise['resolution']['status']), ('', 'blocked_domain'))
        blue = resolve_from_hunter(self.store, [{'organization': 'Bluejay Realty, LLC', 'state': 'NY'}])[0]
        self.assertEqual((blue['company_website'], blue['resolution']['state_checked']), ('bluejay.example', False))
        self.assertEqual(resolve_from_hunter(self.store, [{'organization': 'Bluejay Realty Partners', 'state': 'NY'}])[0]
                         ['resolution']['status'], 'no_match')

    def test_firm_name_key(self):
        self.assertEqual(firm_name_key('Corraway Realty Group, LLC'), firm_name_key('The Corraway Realty Group'))
        self.assertEqual(firm_name_key('Smith & Lane Realty'), firm_name_key('Smith and Lane Realty Inc.'))
        self.assertNotEqual(firm_name_key('Smith Lane Realty'), firm_name_key('Smith Lane Realty Partners'))
        self.assertEqual(firm_name_key('LLC'), '')


if __name__ == '__main__':
    unittest.main()
