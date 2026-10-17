"""v4 free-tier adapters: offline fixtures only (synthetic identities, reserved domains, fake keys)."""
import json
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from lead_research.configuration import DEFAULT, MIGRATION_ADDED_PROVIDERS, default_config, load_config
from lead_research.discovery import (collect, decode_html, encode_html, firecrawl_scrape_jobs, pdl_enrich_jobs,
                                     scraped_html, scraped_pages, search_jobs)
from lead_research.models import PolicyError, ProviderError, Response
from lead_research.policy import input_key, normalize_job, scrape_url, strict_payload
from lead_research.providers import get_provider
from lead_research.resolve import resolution_jobs, resolve_from_store
from lead_research.runner import Runner
from lead_research.store import Store
from lead_research.transport import OFFICIAL_ORIGINS
from lead_research.workflow import route_run

SECRET = 'fixture-secret'
KEYS = {'tavily': 'TAVILY_API_KEY', 'firecrawl': 'FIRECRAWL_API_KEY', 'abstract': 'ABSTRACT_EMAIL_API_KEY',
        'emailable': 'EMAILABLE_API_KEY', 'kickbox': 'KICKBOX_API_KEY', 'bouncer': 'BOUNCER_API_KEY',
        'myemailverifier': 'MYEMAILVERIFIER_API_KEY', 'pdl': 'PDL_API_KEY', 'serpapi': 'SERPAPI_API_KEY'}
ENV = {v: SECRET for v in KEYS.values()}
NEW = tuple(KEYS)
VERIFIERS = ('abstract', 'emailable', 'kickbox', 'bouncer', 'myemailverifier')
EMAIL = 'jane.example@example.org'
OTHER = 'someone.else@example.org'
QUERY = 'site:linkedin.com/in "Broker" "real estate" Austin'
LINKEDIN = 'https://www.linkedin.com/in/jane-example/'
TX_FIRM = {'organization': 'Harborlight Homes LLC', 'city': '', 'state': 'TX', 'county': 'Bexar', 'phone': '',
           'license_number': '9000001-BB', 'source': 'tx-trec', 'decision_maker': 'Jasper D Quillon'}

# --- fixtures shaped like the documented responses -------------------------------------------
TAVILY_OK = {'query': QUERY, 'answer': None, 'images': [], 'response_time': 1.1, 'results': [
    {'title': 'Jane Example - Broker Owner - Harbor Realty | LinkedIn', 'url': 'https://www.linkedin.com/in/jane-example',
     'content': 'Austin, Texas, United States. Broker Owner at Harbor Realty.', 'score': 0.9},
    {'title': 'Harbor Realty', 'url': 'https://harbor.example.net/about', 'content': 'A brokerage.', 'score': 0.5}]}
SERPAPI_OK = {'search_metadata': {'id': 'abc', 'status': 'Success'},
              'search_information': {'organic_results_state': 'Results for exact spelling'},
              'organic_results': [{'position': 1, 'title': 'Jane Example - Broker Owner - Harbor Realty | LinkedIn',
                                   'link': 'https://www.linkedin.com/in/jane-example',
                                   'snippet': 'Austin, Texas, United States. Broker Owner at Harbor Realty.'},
                                  {'position': 2, 'title': 'Harbor Realty', 'link': 'https://harbor.example.net/',
                                   'snippet': 'A brokerage.'}]}
SERPAPI_EMPTY = {'search_metadata': {'id': 'abc', 'status': 'Success'},
                 'search_information': {'organic_results_state': 'Fully empty'},
                 'error': "Google hasn't returned any results for this query."}
FIRECRAWL_SEARCH = {'success': True, 'data': {'web': [
    {'url': 'https://www.linkedin.com/in/jane-example', 'title': 'Jane Example - Broker Owner - Harbor Realty | LinkedIn',
     'description': 'Austin, Texas. Broker Owner at Harbor Realty.', 'position': 1}]}, 'creditsUsed': 2}
TEAM_HTML = '<html><body><h2>Jane Example</h2><p>Broker Owner</p><a href="https://example.org/x">x</a></body></html>'
FIRECRAWL_SCRAPE = {'success': True, 'data': {'html': TEAM_HTML, 'metadata': {
    'title': 'Team', 'sourceURL': 'https://example.org/team', 'url': 'https://example.org/team', 'statusCode': 200}}}
PDL_MATCH = {'status': 200, 'likelihood': 8, 'data': {
    'full_name': 'Jane Example', 'linkedin_url': 'linkedin.com/in/jane-example', 'job_title': 'Broker Owner',
    'job_company_name': 'Harbor Realty', 'job_company_website': 'example.org', 'location_name': True,
    'work_email': True, 'emails': True}}
PDL_MISS = {'status': 404, 'error': {'type': 'not_found', 'message': 'No records were found matching your request'}}
ABSTRACT_OK = {'email_address': EMAIL, 'suggested_correction': None,
               'email_deliverability': {'status': 'deliverable', 'status_detail': 'valid_email', 'is_format_valid': True,
                                        'is_smtp_valid': True, 'is_mx_valid': True, 'mx_records': ['mx.example.org']},
               'email_quality': {'score': 0.9, 'is_free_email': False, 'is_username_suspicious': False,
                                 'is_disposable': False, 'is_catchall': False, 'is_role': False},
               'email_risk': {'address_risk_status': 'low', 'domain_risk_status': 'low'}}
EMAILABLE_OK = {'email': EMAIL, 'state': 'deliverable', 'reason': 'accepted_email', 'accept_all': False,
                'disposable': False, 'role': False, 'free': False, 'mailbox_full': False, 'no_reply': False, 'score': 95}
KICKBOX_OK = {'result': 'deliverable', 'reason': 'accepted_email', 'role': False, 'free': False, 'disposable': False,
              'accept_all': False, 'did_you_mean': None, 'sendex': 0.9, 'email': EMAIL, 'user': 'jane.example',
              'domain': 'example.org', 'success': True, 'message': None}
BOUNCER_OK = {'email': EMAIL, 'status': 'deliverable', 'reason': 'accepted_email',
              'domain': {'name': 'example.org', 'acceptAll': 'no', 'disposable': 'no', 'free': 'no'},
              'account': {'role': 'no', 'disabled': 'no', 'fullMailbox': 'no'},
              'dns': {'type': 'MX', 'record': 'mx.example.org.'}, 'provider': 'example.org', 'score': 100,
              'toxic': 'unknown', 'toxicity': 0}
MEV_OK = {'Address': EMAIL, 'catch_all': 'false', 'Status': 'Valid', 'Disposable_Domain': 'false',
          'Role_Based': 'false', 'Free_Domain': 'false', 'Greylisted': 'false', 'Diagnosis': 'Mailbox Exists and Active'}
VERIFY_OK = {'abstract': ABSTRACT_OK, 'emailable': EMAILABLE_OK, 'kickbox': KICKBOX_OK, 'bouncer': BOUNCER_OK,
             'myemailverifier': MEV_OK}
ECHO_KEY = {'abstract': 'email_address', 'emailable': 'email', 'kickbox': 'email', 'bouncer': 'email',
            'myemailverifier': 'Address'}


def parse(provider, data, operation=None, status=200, json_valid=True):
    p = get_provider(provider)
    return p.parse(operation or p.operations[0], Response(status, data, {}, json_valid))


def query(request):
    return {k: v[0] for k, v in parse_qs(urlsplit(request.url).query).items()}


class FakeTransport:
    def __init__(self, responses):
        self.responses, self.requests = list(responses), []

    def send(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


class RegistryTests(unittest.TestCase):
    def test_defaults_origins_and_charges(self):
        expected = {'tavily': {'search': {'credits': '1'}},
                    'firecrawl': {'search': {'search_ops': '1'}, 'scrape': {'scrape_credits': '1'}},
                    'abstract': {'verify': {'requests': '1'}}, 'emailable': {'verify': {'credits': '1'}},
                    'kickbox': {'verify': {'credits': '1'}}, 'bouncer': {'verify': {'credits': '1'}},
                    'myemailverifier': {'verify': {'credits': '1'}}, 'pdl': {'person_enrich': {'credits': '1'}},
                    'serpapi': {'search': {'searches': '1'}}}
        for name in NEW:
            provider = get_provider(name)
            self.assertIn(provider.origin, OFFICIAL_ORIGINS)
            self.assertEqual(provider.independence_group, name)
            self.assertEqual(provider.auth_env, (KEYS[name],))
            entry = DEFAULT['providers'][name]
            self.assertFalse(entry['enabled'])
            self.assertEqual(entry['charges'], expected[name])
            self.assertEqual(set(entry['charges']), set(provider.operations))
            self.assertTrue(all(provider.min_spacing[op] > 0 for op in provider.operations))
            self.assertIn(name, MIGRATION_ADDED_PROVIDERS)
        self.assertEqual(get_provider('serpapi').min_spacing['search'], 72.0)  # free plan: 50 searches/hour
        self.assertEqual(get_provider('firecrawl').min_spacing['scrape'], 6.5)  # free plan: 10/min

    def test_verify_route_order(self):
        order = [p for p in DEFAULT['providers'] if 'verify' in get_provider(p).operations]
        self.assertEqual(order, ['hunter', 'verifalia', 'clearout', 'zerobounce', 'reoon', 'quickemailverification',
                                 'abstract', 'emailable', 'kickbox', 'bouncer', 'myemailverifier', 'emailvalidation',
                                 'emaillistverify', 'millionverifier'])

    def test_older_config_migrates_new_providers_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = tmp + '/providers.json'
            old = default_config()
            for name in NEW:
                del old['providers'][name]
            old['providers']['reoon']['enabled'] = True
            old['providers']['millionverifier']['enabled'] = True
            with open(path, 'w') as fh:
                json.dump(old, fh)
            loaded = load_config(path)
            self.assertEqual(list(loaded['providers']), list(DEFAULT['providers']))
            self.assertFalse(any(loaded['providers'][n]['enabled'] for n in NEW))
            self.assertTrue(loaded['providers']['reoon']['enabled'] and loaded['providers']['millionverifier']['enabled'])
            for tamper in ({'enabled': True, 'charges': {'search': {'credits': '0.5'}}},
                           {'enabled': True, 'charges': {'search': {'credits': '1'}}, 'origin': 'https://example.org'}):
                old['providers']['tavily'] = tamper
                with open(path, 'w') as fh:
                    json.dump(old, fh)
                with self.assertRaises(PolicyError):
                    load_config(path)

    def test_auth_and_secret_placement(self):
        payloads = {'tavily': {'query': QUERY}, 'serpapi': {'query': QUERY}, 'firecrawl': {'query': QUERY},
                    'pdl': {'linkedin_url': LINKEDIN}, **{v: {'email': EMAIL} for v in VERIFIERS}}
        in_url = ('serpapi', 'myemailverifier')  # the only documented auth for these two
        for name, payload in payloads.items():
            provider = get_provider(name)
            with self.assertRaisesRegex(ProviderError, '^missing_auth$'):
                provider.request(provider.operations[0], payload, {})
            request = provider.request(provider.operations[0], payload, ENV)
            self.assertNotIn(SECRET, repr(request))
            self.assertEqual(SECRET in request.url, name in in_url, name)
            if name not in in_url:
                self.assertTrue(any(SECRET in v for v in request.headers.values()), name)
            self.assertTrue(request.url.startswith(provider.origin + '/'))
        with self.assertRaisesRegex(PolicyError, '^invalid_auth$'):
            get_provider('tavily').request('search', {'query': QUERY}, {'TAVILY_API_KEY': 'a\r\nX: y'})

    def test_sandbox_keys_refused(self):
        for name in ('emailable', 'kickbox'):
            with self.assertRaisesRegex(PolicyError, '^test_api_key_refused$'):
                get_provider(name).request('verify', {'email': EMAIL}, {KEYS[name]: 'test_' + SECRET})

    def test_common_failures(self):
        for name in NEW:
            provider = get_provider(name)
            for op in provider.operations:
                for status, code in ((401, 'auth'), (403, 'auth'), (402, 'quota'), (429, 'rate_limit'), (500, 'provider_error'),
                                     (400, 'http_error')):
                    out = parse(name, {'message': SECRET}, op, status)
                    self.assertEqual((out.assessment, out.error), ('error', code), (name, op, status))
                    self.assertNotIn(SECRET, repr(out))
                for data in ([], None, {}, {'error': SECRET}, {'errors': [SECRET]}):
                    out = parse(name, data, op)
                    self.assertEqual(out.assessment, 'error', (name, op, data))
                    self.assertNotIn(SECRET, repr(out))
                self.assertEqual(parse(name, {}, op, json_valid=False).error, 'invalid_response')


class SearchAdapterTests(unittest.TestCase):
    def test_tavily_request_and_parse(self):
        job = normalize_job({'provider': 'tavily', 'operation': 'search',
                             'payload': {'query': QUERY, 'include_domains': ['www.Example.org', 'example.net']}})
        request = get_provider('tavily').request('search', job['payload'], ENV)
        body = json.loads(request.body)
        self.assertEqual((request.method, request.url), ('POST', 'https://api.tavily.com/search'))
        self.assertEqual(request.headers['Authorization'], 'Bearer ' + SECRET)
        self.assertEqual(body['search_depth'], 'basic')
        self.assertEqual(body['max_results'], 20)
        self.assertEqual(body['include_domains'], ['example.net', 'example.org'])
        self.assertFalse(body['auto_parameters'] or body['include_raw_content'] or body['include_answer'])
        self.assertEqual(json.loads(get_provider('tavily').request('search', {'query': QUERY, 'count': 5}, ENV).body)['max_results'], 5)
        out = parse('tavily', TAVILY_OK)
        self.assertEqual(out.assessment, 'results')
        self.assertEqual(out.fields['results'][0], {'title': 'Jane Example - Broker Owner - Harbor Realty | LinkedIn',
                                                    'link': 'https://www.linkedin.com/in/jane-example',
                                                    'snippet': 'Austin, Texas, United States. Broker Owner at Harbor Realty.',
                                                    'position': 1})
        self.assertEqual(out.fields['prospects'][0]['linkedin_url'], LINKEDIN)
        self.assertEqual(parse('tavily', {**TAVILY_OK, 'results': []}).assessment, 'not_found')
        self.assertEqual(parse('tavily', {'detail': {'error': SECRET}}).error, 'provider_application_error')
        self.assertEqual(parse('tavily', {'results': 'x'}).error, 'invalid_response')
        self.assertEqual(parse('tavily', {'detail': {'error': 'x'}}, status=432).error, 'quota')
        self.assertEqual(parse('tavily', {'detail': {'error': 'x'}}, status=433).error, 'quota')

    def test_tavily_payload_validation(self):
        for bad in ({'query': QUERY, 'page': 2}, {'query': QUERY, 'offset': 1}, {'query': QUERY, 'count': 21},
                    {'query': QUERY, 'search_depth': 'advanced'}, {'query': ''}, {'query': QUERY, 'include_domains': []}):
            with self.assertRaises(PolicyError, msg=bad):
                get_provider('tavily').request('search', bad, ENV)
        with self.assertRaisesRegex(PolicyError, 'paging_not_supported_by_provider'):
            normalize_job({'provider': 'tavily', 'operation': 'search', 'payload': {'query': QUERY, 'page': 2}})
        with self.assertRaisesRegex(PolicyError, 'domain_filter_not_supported_by_provider'):
            normalize_job({'provider': 'serper', 'operation': 'search',
                           'payload': {'query': QUERY, 'include_domains': ['example.org']}})
        with self.assertRaises(PolicyError):
            normalize_job({'provider': 'tavily', 'operation': 'search',
                           'payload': {'query': QUERY, 'include_domains': ['10.0.0.1']}})
        plain = input_key('search', {'query': QUERY})
        scoped = input_key('search', {'query': QUERY, 'include_domains': ['example.org']})
        self.assertNotEqual(plain, scoped)  # a domain-scoped search is a different cached observation

    def test_serpapi_request_and_parse(self):
        request = get_provider('serpapi').request('search', {'query': QUERY}, ENV)
        q = query(request)
        self.assertEqual((request.method, urlsplit(request.url).path), ('GET', '/search.json'))
        self.assertEqual({k: q[k] for k in ('engine', 'q', 'gl', 'hl', 'api_key')},
                         {'engine': 'google', 'q': QUERY, 'gl': 'us', 'hl': 'en', 'api_key': SECRET})
        self.assertNotIn('start', q)
        self.assertEqual(query(get_provider('serpapi').request('search', {'query': QUERY, 'page': 3}, ENV))['start'], '20')
        for bad in ({'query': QUERY, 'page': 11}, {'query': QUERY, 'count': 10}, {'query': QUERY, 'num': 20}):
            with self.assertRaises(PolicyError):
                get_provider('serpapi').request('search', bad, ENV)
        out = parse('serpapi', SERPAPI_OK)
        self.assertEqual(out.assessment, 'results')
        self.assertEqual([r['link'] for r in out.fields['results']],
                         ['https://www.linkedin.com/in/jane-example', 'https://harbor.example.net/'])
        self.assertEqual(out.fields['prospects'][0]['linkedin_url'], LINKEDIN)
        self.assertEqual(parse('serpapi', SERPAPI_EMPTY).assessment, 'not_found')
        no_organic = {'search_metadata': {'status': 'Success'}, 'local_results': []}
        self.assertEqual(parse('serpapi', no_organic).assessment, 'not_found')
        self.assertEqual(parse('serpapi', {**SERPAPI_OK, 'error': SECRET}).error, 'provider_application_error')
        self.assertEqual(parse('serpapi', {'search_metadata': {'status': 'Error'}}).error, 'provider_error')
        self.assertEqual(parse('serpapi', {'search_metadata': {'status': 'Processing'}}).error, 'invalid_response')
        self.assertEqual(parse('serpapi', {**SERPAPI_OK, 'organic_results': {}}).error, 'invalid_response')
        self.assertEqual(parse('serpapi', {'error': 'Your account has run out of searches.'}, status=429).error, 'rate_limit')

    def test_firecrawl_search(self):
        request = get_provider('firecrawl').request('search', {'query': QUERY, 'include_domains': ['example.org']}, ENV)
        body = json.loads(request.body)
        self.assertEqual(request.url, 'https://api.firecrawl.dev/v2/search')
        self.assertEqual(request.headers['Authorization'], 'Bearer ' + SECRET)
        self.assertEqual((body['limit'], body['sources'], body['includeDomains']), (10, ['web'], ['example.org']))
        self.assertNotIn('scrapeOptions', body)  # scraping each result would bill extra credits
        out = parse('firecrawl', FIRECRAWL_SEARCH, 'search')
        self.assertEqual(out.assessment, 'results')
        self.assertEqual(out.fields['prospects'][0]['linkedin_url'], LINKEDIN)
        self.assertEqual(parse('firecrawl', {'success': True, 'data': {'web': []}}, 'search').assessment, 'not_found')
        self.assertEqual(parse('firecrawl', {'success': False}, 'search').error, 'provider_application_error')
        self.assertEqual(parse('firecrawl', {'success': True, 'data': {'web': 'x'}}, 'search').error, 'invalid_response')
        with self.assertRaises(PolicyError):
            get_provider('firecrawl').request('search', {'query': QUERY, 'page': 2}, ENV)


class FirecrawlScrapeTests(unittest.TestCase):
    def test_url_validation(self):
        self.assertEqual(scrape_url('https://Example.org/Our Team'), 'https://example.org/Our%20Team')
        self.assertEqual(scrape_url('http://example.org:80/team?x=1#top'), 'https://example.org/team?x=1')
        self.assertEqual(scrape_url(scrape_url('https://example.org/a b')), scrape_url('https://example.org/a b'))
        for bad in ('file:///etc/passwd', 'ftp://example.org/', 'javascript:alert(1)', 'https://127.0.0.1/',
                    'http://10.0.0.5/team', 'http://[::1]/', 'https://169.254.169.254/latest', 'http://localhost/',
                    'https://intranet.local/', 'https://printer.internal/', 'https://app.localhost/', 'https://127.1/',
                    'https://0x7f.1/', 'https://user:pw@example.org/', 'https://example.org:8443/', 'https://example',
                    'https://example.org/?api_key=abc', 'https://example.org/?token=abc', 'https://example.org/a\\b',
                    'https://example.org/\nx', '', None, 'example.org/team', 'https://' + 'a' * 2050 + '.org/'):
            with self.assertRaises(PolicyError, msg=bad):
                scrape_url(bad)
        with self.assertRaisesRegex(PolicyError, 'scrape_host_not_supported'):
            scrape_url('https://www.linkedin.com/in/jane-example/')

    def test_request(self):
        provider = get_provider('firecrawl')
        request = provider.request('scrape', {'url': 'http://example.org/team'}, ENV)
        body = json.loads(request.body)
        self.assertEqual(request.url, 'https://api.firecrawl.dev/v2/scrape')
        self.assertEqual(body, {'url': 'https://example.org/team', 'formats': ['html'], 'onlyMainContent': False,
                                'parsers': [], 'timeout': 60000})
        self.assertGreater(request.max_response_bytes, 1_048_576)
        for bad in ({'url': 'http://192.168.1.1/'}, {'url': 'file:///x'}, {'url': 'https://example.org/', 'formats': ['json']}, {}):
            with self.assertRaises(PolicyError):
                provider.request('scrape', bad, ENV)
        with self.assertRaises(PolicyError):
            normalize_job({'provider': 'firecrawl', 'operation': 'scrape', 'payload': {'url': 'https://10.1.2.3/'}})
        jobs = firecrawl_scrape_jobs(['https://example.org/team', 'http://example.org/team', 'https://example.net/about'])
        self.assertEqual([j['payload']['url'] for j in jobs], ['https://example.org/team', 'https://example.net/about'])
        for job in jobs:
            self.assertEqual(normalize_job(job)['payload'], job['payload'])
        with self.assertRaises(PolicyError):
            firecrawl_scrape_jobs(['https://example.org/', 'http://127.0.0.1/'])

    def test_parse(self):
        out = parse('firecrawl', FIRECRAWL_SCRAPE, 'scrape')
        self.assertEqual(out.assessment, 'results')
        self.assertEqual(out.fields['url'], 'https://example.org/team')
        self.assertEqual(scraped_html(out), TEAM_HTML)
        self.assertNotIn('html', out.fields)
        big = parse('firecrawl', {'success': True, 'data': {'html': '<p>' + 'x' * 600_000 + '</p>', 'metadata': {}}}, 'scrape')
        self.assertTrue(big.fields['truncated'])
        self.assertEqual(len(scraped_html(big)), 500_000)
        self.assertIsNone(big.fields['url'])
        gone = parse('firecrawl', {'success': True, 'data': {'html': 'Not found', 'metadata': {'statusCode': 404}}}, 'scrape')
        self.assertEqual((gone.assessment, gone.fields['page_status']), ('not_found', 404))
        self.assertEqual(parse('firecrawl', {'success': True, 'data': {'markdown': 'x'}}, 'scrape').error, 'invalid_response')
        self.assertEqual(parse('firecrawl', {'success': False, 'error': 'Invalid URL'}, 'scrape').error,
                         'provider_application_error')
        self.assertIsNone(decode_html('not base64!'))
        self.assertIsNone(decode_html(encode_html('x' * 600_001)))
        self.assertEqual(decode_html(encode_html('é<a href="https://example.org">')), 'é<a href="https://example.org">')


class PdlTests(unittest.TestCase):
    def test_request(self):
        provider = get_provider('pdl')
        request = provider.request('person_enrich', {'linkedin_url': 'linkedin.com/in/Jane-Example'}, ENV)
        q = query(request)
        self.assertEqual(urlsplit(request.url).path, '/v5/person/enrich')
        self.assertEqual(request.headers['X-Api-Key'], SECRET)
        self.assertEqual(q['profile'], LINKEDIN)
        self.assertEqual((q['min_likelihood'], q['titlecase']), ('6', 'true'))
        self.assertNotIn('email', q['data_include'])
        named = query(provider.request('person_enrich', {'name': 'Jane Example', 'domain': 'www.example.org',
                                                         'location': 'Austin, TX'}, ENV))
        self.assertEqual((named['name'], named['company'], named['location']), ('Jane Example', 'example.org', 'Austin, TX'))
        self.assertEqual(query(provider.request('person_enrich', {'name': 'Jane Example', 'company': 'Harbor Realty'}, ENV))['company'],
                         'Harbor Realty')
        for bad in ({'name': 'Jane Example'}, {'linkedin_url': LINKEDIN, 'name': 'Jane Example'}, {'name': 'Jane', 'domain': 'example.org'},
                    {'domain': 'example.org'}, {'email': EMAIL}, {'linkedin_url': 'https://example.org/in/x'}, {}):
            with self.assertRaises(PolicyError, msg=bad):
                provider.request('person_enrich', bad, ENV)

    def test_parse(self):
        out = parse('pdl', PDL_MATCH)
        self.assertEqual(out.assessment, 'enriched')
        row = out.fields['prospect']
        self.assertEqual((row['full_name'], row['linkedin_url'], row['title'], row['company'], row['company_website']),
                         ('Jane Example', LINKEDIN, 'Broker Owner', 'Harbor Realty', 'example.org'))
        self.assertIsNone(row['location'])  # free plan returns booleans for obfuscated values
        self.assertNotIn('email', row)
        self.assertEqual(out.fields['likelihood'], 8)
        self.assertEqual(parse('pdl', PDL_MISS, status=404).assessment, 'not_found')
        self.assertEqual(parse('pdl', {'secret': SECRET}, status=404).error, 'http_error')
        self.assertEqual(parse('pdl', {'status': 402, 'error': {'type': 'payment_required'}}, status=402).error, 'quota')
        self.assertEqual(parse('pdl', {**PDL_MATCH, 'likelihood': 11}).error, 'invalid_response')
        self.assertEqual(parse('pdl', {**PDL_MATCH, 'data': {'job_title': 'X'}}).error, 'invalid_response')
        provider = get_provider('pdl')
        other = provider.scope_outcome('person_enrich', out, {'linkedin_url': 'https://www.linkedin.com/in/someone-else/'})
        self.assertEqual((other.assessment, other.fields['reason']), ('not_found', 'profile_mismatch'))
        moved = provider.scope_outcome('person_enrich', out, {'name': 'Jane Example', 'domain': 'example.net'})
        self.assertTrue(moved.fields['company_mismatch'])
        jobs = pdl_enrich_jobs([{'linkedin_url': LINKEDIN, 'lead_id': 'L1'}, {'linkedin_url': LINKEDIN},
                                {'full_name': 'Sam Sample', 'company_website': 'example.net'},
                                {'full_name': 'Kim Fixture', 'company': 'Harbor Realty'}, {'full_name': 'No Company'}])
        self.assertEqual([j['payload'] for j in jobs], [{'linkedin_url': LINKEDIN, 'lead_id': 'L1'},
                                                        {'name': 'Sam Sample', 'domain': 'example.net'},
                                                        {'name': 'Kim Fixture', 'company': 'Harbor Realty'}])
        for job in jobs:
            normalize_job(job)


class VerifierTests(unittest.TestCase):
    def test_success_and_requests(self):
        for name in VERIFIERS:
            out = parse(name, VERIFY_OK[name])
            self.assertEqual((out.assessment, out.email), ('provider_valid', EMAIL), name)
            self.assertEqual(out.risk_flags, (), name)
            self.assertNotIn('Diagnosis', json.dumps(out.fields))
        q = query(get_provider('emailable').request('verify', {'email': 'jane+tag@example.org'}, ENV))
        self.assertEqual((q['email'], q['accept_all']), ('jane+tag@example.org', 'true'))
        self.assertIn('email=jane%2Btag%40example.org', get_provider('bouncer').request('verify', {'email': 'jane+tag@example.org'}, ENV).url)
        mev = get_provider('myemailverifier').request('verify', {'email': EMAIL}, ENV)
        self.assertEqual((mev.method, urlsplit(mev.url).netloc, urlsplit(mev.url).path),
                         ('GET', 'api.myemailverifier.com', '/api/validate_single.php'))
        self.assertEqual(query(mev), {'apikey': SECRET, 'email': EMAIL})
        plus = get_provider('myemailverifier').request('verify', {'email': 'jane+tag@example.org'}, ENV)
        self.assertIn('email=jane%2Btag%40example.org', plus.url)
        self.assertEqual(urlsplit(get_provider('kickbox').request('verify', {'email': EMAIL}, ENV).url).path, '/v2/verify')
        self.assertEqual(get_provider('bouncer').request('verify', {'email': EMAIL}, ENV).headers['x-api-key'], SECRET)
        self.assertEqual(urlsplit(get_provider('abstract').request('verify', {'email': EMAIL}, ENV).url).path, '/v1/')

    def test_recipient_echo_required(self):
        for name in VERIFIERS:
            data = dict(VERIFY_OK[name])
            data.pop(ECHO_KEY[name])
            self.assertEqual(parse(name, data).error, 'invalid_response', name)
            self.assertEqual(parse(name, {**VERIFY_OK[name], ECHO_KEY[name]: 'not-an-email'}).error, 'invalid_response', name)

    def test_negative_and_risky_verdicts(self):
        cases = {
            'abstract': [({'email_deliverability': {**ABSTRACT_OK['email_deliverability'], 'status': 'undeliverable',
                                                    'status_detail': 'invalid_mailbox', 'is_smtp_valid': False}}, 'provider_invalid', ()),
                         ({'email_deliverability': {**ABSTRACT_OK['email_deliverability'], 'status': 'undeliverable',
                                                    'status_detail': 'full_mailbox'}}, 'unknown', ('inbox_full',)),
                         ({'email_deliverability': {**ABSTRACT_OK['email_deliverability'], 'is_smtp_valid': False}}, 'unknown', ()),
                         ({'email_quality': {**ABSTRACT_OK['email_quality'], 'is_catchall': True, 'is_role': True}},
                          'catch_all', ('role', 'catch_all')),
                         ({'email_deliverability': {**ABSTRACT_OK['email_deliverability'], 'status': 'unknown'}}, 'unknown', ())],
            'emailable': [({'state': 'undeliverable', 'reason': 'rejected_email'}, 'provider_invalid', ()),
                          ({'state': 'risky', 'reason': 'low_quality', 'disposable': True}, 'unknown', ('disposable', 'risky')),
                          ({'accept_all': True}, 'catch_all', ('catch_all',)),
                          ({'mailbox_full': True}, 'unknown', ('inbox_full',)),
                          ({'reason': 'something_new'}, 'unknown', ()),
                          ({'state': 'unknown', 'reason': 'timeout'}, 'unknown', ())],
            'kickbox': [({'result': 'undeliverable', 'reason': 'rejected_email'}, 'provider_invalid', ()),
                        ({'result': 'risky', 'reason': 'low_quality', 'role': True}, 'unknown', ('role', 'risky')),
                        ({'accept_all': True}, 'catch_all', ('catch_all',)),
                        ({'result': 'unknown', 'reason': 'no_connect'}, 'unknown', ())],
            'bouncer': [({'status': 'undeliverable', 'reason': 'rejected_email'}, 'provider_invalid', ()),
                        ({'status': 'risky', 'reason': 'low_deliverability',
                          'domain': {**BOUNCER_OK['domain'], 'acceptAll': 'yes'}}, 'catch_all', ('catch_all', 'risky')),
                        ({'status': 'risky', 'reason': 'low_quality', 'toxicity': 5}, 'unknown', ('risky', 'toxic')),
                        ({'account': {**BOUNCER_OK['account'], 'role': 'yes', 'fullMailbox': 'yes'}}, 'unknown', ('role', 'inbox_full')),
                        ({'account': {**BOUNCER_OK['account'], 'role': 'yes'}}, 'provider_valid', ('role',)),
                        ({'status': 'unknown', 'reason': 'timeout', 'retryAfter': '2026-10-15T10:00:00Z'}, 'unknown', ('greylisted',))],
            'myemailverifier': [({'Status': 'Invalid'}, 'provider_invalid', ()),
                                ({'Status': 'Catch All', 'catch_all': 'true'}, 'catch_all', ('catch_all',)),
                                ({'Status': 'Grey-listed', 'Greylisted': 'true'}, 'unknown', ('greylisted',)),
                                ({'Status': 'Valid', 'Role_Based': 'true', 'Disposable_Domain': 'true'}, 'provider_valid', ('disposable', 'role')),
                                ({'Status': 'Unknown'}, 'unknown', ())],
        }
        for name, items in cases.items():
            for change, assessment, flags in items:
                out = parse(name, {**VERIFY_OK[name], **change})
                self.assertEqual((out.assessment, out.risk_flags), (assessment, flags), (name, change))

    def test_app_errors_in_200_and_malformed(self):
        self.assertEqual(parse('abstract', {'error': {'message': SECRET, 'code': 'quota_reached'}}).error, 'provider_application_error')
        self.assertEqual(parse('abstract', {'error': {'message': 'x'}}, status=422).error, 'quota')
        self.assertEqual(parse('abstract', {**ABSTRACT_OK, 'email_deliverability': {'status': 'maybe'}}).error, 'invalid_response')
        self.assertEqual(parse('emailable', {'message': 'still verifying'}, status=249).error, 'provider_retry_later')
        self.assertEqual(parse('emailable', {**EMAILABLE_OK, 'state': 'duplicate'}).error, 'invalid_response')
        self.assertEqual(parse('kickbox', {'success': False, 'message': 'Invalid API Key'}).error, 'auth')
        self.assertEqual(parse('kickbox', {'success': False, 'message': 'Balance Depleted'}).error, 'quota')
        self.assertEqual(parse('kickbox', {'success': False, 'message': 'Rate Limit Exceeded'}).error, 'rate_limit')
        self.assertEqual(parse('kickbox', {**KICKBOX_OK, 'success': None}).error, 'invalid_response')
        self.assertEqual(parse('kickbox', {**KICKBOX_OK, 'result': 'valid'}).error, 'invalid_response')
        self.assertEqual(parse('bouncer', {**BOUNCER_OK, 'status': 'ok'}).error, 'invalid_response')
        for message, code in (('Unauthorized Request', 'auth'), ('Invalid API Key', 'auth'), ('You do not have enough credits.', 'quota'),
                              ('Your Account is blocked. Please contact support for more details.', 'auth'),
                              ('Too many requests. Please try again later.', 'rate_limit'), ('Unknown error occurred.', 'provider_application_error')):
            self.assertEqual(parse('myemailverifier', {'status': False, 'message': message}).error, code, message)
        self.assertEqual(parse('myemailverifier', {**MEV_OK, 'Status': 'Spamtrap'}).error, 'invalid_response')

    def test_myemailverifier_v2_flags_and_echo(self):
        numeric = {'Address': EMAIL, 'catch_all': 0, 'Disposable_Domain': 0, 'Role_Based': 0, 'Free_Domain': 0,
                   'Greylisted': 0, 'Status': 'Valid', 'Diagnosis': 'Mailbox exists and active. Safe to send (D2)'}
        out = parse('myemailverifier', numeric)
        self.assertEqual((out.assessment, out.email, out.risk_flags, out.fields), ('provider_valid', EMAIL, (), {'status': 'valid', 'free': False}))
        for change, assessment, flags in (({'catch_all': 1}, 'catch_all', ('catch_all',)),
                                          ({'Status': 'Catch-all'}, 'catch_all', ('catch_all',)),
                                          ({'Role_Based': 1, 'Disposable_Domain': '1'}, 'provider_valid', ('disposable', 'role')),
                                          ({'Greylisted': 1}, 'unknown', ('greylisted',)),
                                          ({'Status': 'Invalid', 'catch_all': 'true'}, 'provider_invalid', ('catch_all',)),
                                          ({'Status': 'Unknown'}, 'unknown', ())):
            out = parse('myemailverifier', {**numeric, **change})
            self.assertEqual((out.assessment, out.risk_flags), (assessment, flags), change)
        self.assertEqual(parse('myemailverifier', {**numeric, 'Free_Domain': 1}).fields['free'], True)
        self.assertEqual(parse('myemailverifier', {**numeric, 'Address': 'Jane.Example@Example.ORG'}).email, EMAIL)
        self.assertEqual(parse('myemailverifier', {**numeric, 'catch_all': 2}).assessment, 'provider_valid')  # unknown flag ignored


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name + '/ledger.db')
        self.config = default_config()
        for name in NEW:
            self.config['providers'][name]['enabled'] = True

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def run_jobs(self, jobs, responses):
        self.transport = FakeTransport(responses)
        return Runner(self.store, self.config, env=ENV, transport=self.transport, sleep=lambda _: None).run(jobs, execute=True)

    def test_disabled_and_unfunded_are_skipped(self):
        config = default_config()
        plan = Runner(self.store, config, env=ENV).plan([{'provider': 'tavily', 'operation': 'search', 'payload': {'query': QUERY}}])
        self.assertEqual(plan[0]['reason'], 'provider_disabled')
        plan = Runner(self.store, self.config, env=ENV).plan([{'provider': 'tavily', 'operation': 'search', 'payload': {'query': QUERY}}])
        self.assertEqual(plan[0]['reason'], 'insufficient_confirmed_free_budget')

    def test_verifier_recipient_mismatch_pauses(self):
        for name in VERIFIERS:
            pool = list(DEFAULT['providers'][name]['charges']['verify'])[0]
            self.store.confirm_budget(name, pool, '1', 'synthetic free')
            # A distinct address per provider: an errored attempt blocks re-verifying the same one.
            report = self.run_jobs([{'provider': name, 'operation': 'verify', 'payload': {'email': name + '@example.org'}}],
                                   [Response(200, {**VERIFY_OK[name], ECHO_KEY[name]: OTHER})])
            self.assertEqual(report['results'][0]['outcome']['error'], 'recipient_mismatch', name)
            self.assertTrue(self.store.provider_state(name)['paused'])

    def test_auto_route_spends_first_funded_free_verifier(self):
        self.store.confirm_budget('kickbox', 'credits', '1', 'synthetic free')
        self.store.confirm_budget('millionverifier', 'credits', '1', 'synthetic free')
        plan = route_run(self.store, self.config, [{'provider': 'auto', 'operation': 'verify', 'payload': {'email': EMAIL}}], env=ENV)
        self.assertEqual(plan['results'][0]['provider'], 'kickbox')
        self.store.confirm_budget('abstract', 'requests', '1', 'synthetic free')
        transport = FakeTransport([Response(200, ABSTRACT_OK)])
        done = route_run(self.store, self.config, [{'provider': 'auto', 'operation': 'verify', 'payload': {'email': EMAIL}}],
                         execute=True, env=ENV, transport=transport, sleep=lambda _: None)
        self.assertEqual((done['results'][0]['provider'], done['results'][0]['status']), ('abstract', 'completed'))
        self.assertEqual(self.store.assess(EMAIL)['assessment'], 'provider_valid')
        self.assertNotIn(SECRET, json.dumps(self.store.attempts()))
        self.assertEqual({b['pool']: b['remaining_units'] for b in self.store.budgets() if b['provider'] == 'abstract'},
                         {'requests': 0})

    def test_tavily_and_serpapi_collect_and_resolve_round_trip(self):
        for name, pool, ok in (('tavily', 'credits', TAVILY_OK), ('serpapi', 'searches', SERPAPI_OK)):
            self.store.confirm_budget(name, pool, '3', 'synthetic free')
            report = self.run_jobs(search_jobs(name, [QUERY]), [Response(200, ok)])
            self.assertEqual(report['results'][0]['status'], 'completed', name)
            jobs = resolution_jobs([TX_FIRM], provider=name)
            self.assertEqual(jobs, [{'provider': name, 'operation': 'search',
                                     'payload': {'query': '"Harborlight Homes" Bexar County Texas real estate'}}])
            site = {'title': 'Harborlight Homes | Bexar County, Texas real estate', 'snippet': 'Harborlight Homes'}
            if name == 'tavily':
                answer = {'query': jobs[0]['payload']['query'], 'results': [
                    {'title': 'Harborlight Homes - Zillow', 'url': 'https://www.zillow.com/x', 'content': 'x'},
                    {'title': site['title'], 'url': 'https://www.harborlighthomes.example/', 'content': site['snippet']}]}
            else:
                answer = {'search_metadata': {'status': 'Success'}, 'organic_results': [
                    {'position': 1, 'title': 'Harborlight Homes - Zillow', 'link': 'https://www.zillow.com/x', 'snippet': 'x'},
                    {'position': 2, 'title': site['title'], 'link': 'https://www.harborlighthomes.example/', 'snippet': site['snippet']}]}
            self.assertEqual(self.run_jobs(jobs, [Response(200, answer)])['results'][0]['status'], 'completed')
            resolved = resolve_from_store(self.store, [TX_FIRM], provider=name)[0]
            self.assertEqual((resolved['company_website'], resolved['resolution']['status'], resolved['resolution']['provider']),
                             ('harborlighthomes.example', 'resolved', name))
            again = Runner(self.store, self.config, env=ENV).plan(jobs)
            self.assertEqual(again[0]['status'], 'cached')
        self.assertEqual(resolve_from_store(self.store, [TX_FIRM], provider='serper')[0]['company_website'], '')
        prospects = collect(self.store)['prospects']
        jane = [p for p in prospects if p.get('linkedin_url') == LINKEDIN]
        self.assertEqual(len(jane), 1)
        self.assertEqual({s['provider'] for s in jane[0]['sources']}, {'tavily', 'serpapi'})
        with self.assertRaises(PolicyError):
            resolution_jobs([TX_FIRM], provider='pdl')

    def test_firecrawl_scrape_round_trip(self):
        self.store.confirm_budget('firecrawl', 'scrape_credits', '2', 'synthetic free')
        jobs = firecrawl_scrape_jobs(['https://example.org/team'])
        report = self.run_jobs(jobs, [Response(200, FIRECRAWL_SCRAPE)])
        self.assertEqual(report['results'][0]['status'], 'completed')
        self.assertEqual(scraped_html(report['results'][0]['outcome']), TEAM_HTML)
        pages = scraped_pages(self.store)
        self.assertEqual([(p['url'], p['requested_url'], p['html']) for p in pages],
                         [('https://example.org/team', 'https://example.org/team', TEAM_HTML)])
        body = json.loads(self.transport.requests[0].body)
        self.assertEqual(body['url'], 'https://example.org/team')
        self.assertEqual(collect(self.store), {'prospects': [], 'companies': []})

    def test_pdl_enrichment_collects_identity_without_contact_data(self):
        self.store.confirm_budget('pdl', 'credits', '2', 'synthetic free')
        jobs = pdl_enrich_jobs([{'full_name': 'Jane Example', 'company_website': 'example.org', 'lead_id': 'L9'},
                                {'linkedin_url': 'https://www.linkedin.com/in/nobody-example/'}])
        report = self.run_jobs(jobs, [Response(200, PDL_MATCH), Response(404, PDL_MISS)])
        self.assertEqual([r['outcome']['assessment'] for r in report['results']], ['enriched', 'not_found'])
        self.assertFalse(self.store.provider_state('pdl').get('paused'))
        rows = collect(self.store)['prospects']
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row['full_name'], row['linkedin_url'], row['company_website'], row['lead_id'], row['source']),
                         ('Jane Example', LINKEDIN, 'example.org', 'L9', 'pdl'))
        self.assertNotIn('email', row)

    def test_emailable_try_again_is_error_not_verdict(self):
        self.store.confirm_budget('emailable', 'credits', '1', 'synthetic free')
        report = self.run_jobs([{'provider': 'emailable', 'operation': 'verify', 'payload': {'email': EMAIL}}],
                               [Response(249, {'message': 'retry'})])
        self.assertEqual(report['results'][0]['outcome']['error'], 'provider_retry_later')
        self.assertNotEqual(self.store.assess(EMAIL).get('assessment'), 'provider_invalid')


class JobBuilderTests(unittest.TestCase):
    def test_search_jobs(self):
        self.assertEqual(search_jobs('serpapi', [QUERY, QUERY], pages=2),
                         [{'provider': 'serpapi', 'operation': 'search', 'payload': {'query': QUERY}},
                          {'provider': 'serpapi', 'operation': 'search', 'payload': {'query': QUERY, 'page': 2}}])
        self.assertEqual(search_jobs('brave', [QUERY], pages=2)[1]['payload'], {'query': QUERY, 'offset': 1})
        self.assertEqual(search_jobs('tavily', [QUERY], include_domains=['www.example.org'])[0]['payload'],
                         {'query': QUERY, 'include_domains': ['example.org']})
        for provider in ('serper', 'brave', 'tavily', 'serpapi', 'exa', 'firecrawl'):
            for job in search_jobs(provider, [QUERY]):
                normalize_job(job)
                get_provider(provider).request('search', strict_payload('search', job['payload']), {
                    **ENV, 'SERPER_API_KEY': SECRET, 'BRAVE_API_KEY': SECRET, 'EXA_API_KEY': SECRET})
        for bad in (lambda: search_jobs('tavily', [QUERY], pages=2), lambda: search_jobs('serper', [QUERY], include_domains=['example.org']),
                    lambda: search_jobs('hunter', [QUERY]), lambda: search_jobs('exa', [QUERY], pages=0)):
            with self.assertRaises(PolicyError):
                bad()


if __name__ == '__main__':
    unittest.main()
