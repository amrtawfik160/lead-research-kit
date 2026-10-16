"""Lead-source operations: offline, synthetic fixtures only (reserved example domains)."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, unquote_plus, urlsplit

from lead_research.configuration import DEFAULT, default_config, load_config, save_config
from lead_research.discovery import (DEFAULT_DM_TITLES, HUNTER_REAL_ESTATE_INDUSTRIES, US_STATES, build_xray_queries,
                                     canonical_linkedin, collect, hunter_discover_jobs, hunter_domain_jobs, osm_jobs,
                                     parse_linkedin_result, xray_jobs)
from lead_research.models import Outcome, PolicyError, ProviderError, Request, Response
from lead_research.policy import input_key, normalize_job, validate_evidence_url
from lead_research.providers import build_overpass_query, get_provider
from lead_research.runner import Runner
from lead_research.store import Store
from lead_research.transport import MAX_RESPONSE_CEILING, UrllibTransport

ENV = {'HUNTER_API_KEY': 'fixture-secret', 'SERPER_API_KEY': 'fixture-secret', 'BRAVE_API_KEY': 'fixture-secret'}

DISCOVER_OK = {'data': [
    {'domain': 'harbor.example.org', 'organization': 'Harbor Realty Group', 'emails_count': {'personal': 4, 'generic': 1, 'total': 5}},
    {'domain': 'not a domain', 'organization': 'Broken'},
    {'domain': 'Summit.Example.NET', 'organization': 'Summit <b>Homes</b>', 'emails_count': {'personal': 2, 'total': 2}},
], 'meta': {'results': 2, 'limit': 100, 'offset': 0, 'filters': {
    'industry': {'include': ['Real Estate']}, 'headcount': ['1-10'], 'limit': 100,
    'headquarters_location': {'include': [{'country': 'US', 'state': 'TX'}]}, 'evil': 'https://example.org/?token=x'}}}

DOMAIN_OK = {'data': {'domain': 'example.org', 'accept_all': False, 'pattern': '{first}.{last}', 'organization': 'Example Realty',
                      'emails': [
    {'value': 'jane.example@example.org', 'type': 'personal', 'confidence': 94, 'first_name': 'Jane', 'last_name': 'Example',
     'position': 'Founder & CEO', 'seniority': 'executive', 'department': 'executive', 'decision_maker': True,
     'linkedin': 'jane-example', 'verification': {'date': '2026-01-01', 'status': 'valid'},
     'sources': [{'uri': 'https://example.org/team', 'domain': 'example.org'}]},
    {'value': 'sam@agents.example.org', 'type': 'personal', 'confidence': 70, 'first_name': 'Sam', 'last_name': 'Sample',
     'position': 'Managing Broker', 'decision_maker': 'yes', 'linkedin': 'https://uk.linkedin.com/in/sam-sample?trk=x',
     'verification': {'status': 'weird'}, 'sources': [{'uri': 'http://example.org/old'}]},
    {'value': 'info@example.org', 'type': 'generic', 'confidence': 99},
    {'value': 'pat@other.example', 'type': 'personal', 'confidence': 90, 'first_name': 'Pat'},
    {'value': 'not-an-email', 'type': 'personal'},
]}, 'meta': {'results': 5}}

SERPER_OK = {'searchParameters': {'q': 'x'}, 'organic': [
    {'title': 'Jane Example - Founder & CEO - Harbor Realty Group | LinkedIn',
     'link': 'https://www.linkedin.com/in/jane-example?originalSubdomain=us',
     'snippet': 'Location: Austin, Texas · Founder & CEO · Experience: Harbor Realty Group · Education: Example University',
     'position': 1},
    {'title': 'Harbor Realty Group | Austin Homes', 'link': 'https://harbor.example.org/about?utm_source=x#top',
     'snippet': 'See https://example.org/?token=abc for more', 'position': 2},
]}

OSM_OK = {'version': 0.6, 'elements': [
    {'type': 'node', 'id': 101, 'tags': {'name': 'Lakeside Realty', 'office': 'estate_agent', 'website': 'https://www.lakeside.example.net/contact',
                                        'phone': '+1 512-555-0100', 'addr:city': 'Austin', 'addr:state': 'TX'}},
    {'type': 'way', 'id': 202, 'tags': {'name': 'Social Only Homes', 'contact:website': 'https://facebook.com/socialonly'}},
    {'type': 'node', 'id': 303, 'tags': {'office': 'estate_agent'}},
    {'type': 'node', 'id': 404, 'tags': {'name': 'Bad Site', 'url': 'javascript:alert(1)', 'phone': 'call me'}},
]}


class FakeTransport:
    def __init__(self, responses):
        self.responses, self.requests = list(responses), []

    def send(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


def parse(provider, operation, data, status=200, json_valid=True):
    return get_provider(provider).parse(operation, Response(status, data, {}, json_valid))


class HunterDiscoveryTests(unittest.TestCase):
    def test_discover_request_and_parse(self):
        req = get_provider('hunter').request('discover', {'filters': {'headcount': ['1-10'], 'industry': {'include': ['Real Estate']}}}, ENV)
        self.assertEqual((req.method, req.url), ('POST', 'https://api.hunter.io/v2/discover'))
        self.assertEqual(json.loads(req.body), {'headcount': ['1-10'], 'industry': {'include': ['Real Estate']}})
        self.assertNotIn('fixture-secret', req.url)
        out = parse('hunter', 'discover', DISCOVER_OK)
        self.assertEqual(out.assessment, 'results')
        self.assertEqual([c['domain'] for c in out.fields['companies']], ['harbor.example.org', 'summit.example.net'])
        self.assertEqual(out.fields['companies'][0]['emails_count_personal'], 4)
        self.assertEqual(out.fields['companies'][1]['organization'], 'Summit Homes')
        self.assertEqual(out.fields['applied_filters']['headquarters_location']['include'][0]['state'], 'TX')
        self.assertNotIn('limit', out.fields['applied_filters'])
        self.assertNotIn('token', json.dumps(out.fields))
        self.assertEqual(parse('hunter', 'discover', {'data': [], 'meta': {}}).assessment, 'not_found')

    def test_discover_failures(self):
        self.assertEqual(parse('hunter', 'discover', {'data': {}}).error, 'invalid_response')
        self.assertEqual(parse('hunter', 'discover', {'data': [{'domain': 'nope'}]}).error, 'invalid_response')
        self.assertEqual(parse('hunter', 'discover', {'errors': [{'id': 'invalid_headcount'}]}).error, 'provider_application_error')
        self.assertEqual(parse('hunter', 'discover', None, json_valid=False).error, 'invalid_response')
        self.assertEqual(parse('hunter', 'discover', {}, status=401).error, 'auth')
        self.assertEqual(parse('hunter', 'discover', {}, status=429).error, 'rate_limit')

    def test_domain_search_request(self):
        req = get_provider('hunter').request('domain_search', {'domain': 'www.example.org', 'seniority': ['executive']}, ENV)
        q = parse_qs(urlsplit(req.url).query)
        self.assertEqual(urlsplit(req.url).path, '/v2/domain-search')
        self.assertEqual(q['domain'], ['example.org'])
        self.assertEqual((q['type'], q['limit'], q['decision_maker']), (['personal'], ['10'], ['true']))
        self.assertEqual(q['required_field'], ['full_name,position'])
        self.assertEqual(q['seniority'], ['executive'])
        self.assertEqual(req.headers['X-API-KEY'], 'fixture-secret')
        req = get_provider('hunter').request('domain_search', {'domain': 'example.org', 'decision_maker': False, 'limit': 5, 'required_field': []}, ENV)
        q = parse_qs(urlsplit(req.url).query)
        self.assertNotIn('decision_maker', q)
        self.assertNotIn('required_field', q)
        self.assertEqual(q['limit'], ['5'])

    def test_domain_search_parse(self):
        out = parse('hunter', 'domain_search', DOMAIN_OK)
        self.assertEqual(out.assessment, 'results')
        self.assertEqual((out.fields['pattern'], out.fields['accept_all'], out.fields['organization']), ('{first}.{last}', False, 'Example Realty'))
        emails = [p['email'] for p in out.fields['prospects']]
        self.assertEqual(emails, ['jane.example@example.org', 'sam@agents.example.org'])
        jane, sam = out.fields['prospects']
        self.assertEqual(jane['linkedin_url'], 'https://www.linkedin.com/in/jane-example/')
        self.assertEqual((jane['title'], jane['email_confidence'], jane['email_verification']), ('Founder & CEO', 94, 'valid'))
        self.assertEqual((jane['source_url'], jane['company_website'], jane['email_source']), ('https://example.org/team', 'example.org', 'hunter_domain_search'))
        self.assertTrue(jane['decision_maker'])
        self.assertEqual(sam['linkedin_url'], 'https://www.linkedin.com/in/sam-sample/')
        self.assertIsNone(sam['source_url'])
        self.assertIsNone(sam['email_verification'])
        self.assertFalse(sam['decision_maker'])
        unsafe = json.loads(json.dumps(DOMAIN_OK))
        unsafe['data']['pattern'] = '{first}<script>'
        self.assertIsNone(parse('hunter', 'domain_search', unsafe).fields['pattern'])
        empty = {'data': {'domain': None, 'pattern': None, 'organization': None, 'emails': []}, 'meta': {'results': 0}}
        self.assertEqual(parse('hunter', 'domain_search', empty).assessment, 'not_found')
        self.assertEqual(parse('hunter', 'domain_search', {'data': {'domain': None, 'emails': [{'value': 'a@example.org'}]}}).error, 'invalid_response')
        self.assertEqual(parse('hunter', 'domain_search', {'data': {'domain': 'example.org'}}).error, 'invalid_response')

    def test_domain_search_scoped_to_queried_domain(self):
        hunter = get_provider('hunter')
        out = hunter.scope_outcome('domain_search', parse('hunter', 'domain_search', DOMAIN_OK), {'domain': 'agents.example.org'})
        self.assertEqual([p['email'] for p in out.fields['prospects']], ['sam@agents.example.org'])
        out = hunter.scope_outcome('domain_search', parse('hunter', 'domain_search', DOMAIN_OK), {'domain': 'other.example'})
        self.assertEqual((out.assessment, out.fields['prospects']), ('not_found', []))

    def test_existing_find_verify_unchanged(self):
        hunter = get_provider('hunter')
        self.assertEqual((hunter.min_spacing['find'], hunter.min_spacing['verify']), (10.0, 10.0))
        self.assertGreaterEqual(hunter.min_spacing['domain_search'], 0.2)
        self.assertIn('/v2/email-verifier?', hunter.request('verify', {'email': 'a@example.org'}, ENV).url)


class SearchProviderTests(unittest.TestCase):
    def test_serper_request(self):
        req = get_provider('serper').request('search', {'query': 'site:linkedin.com/in "CEO"', 'page': 2}, ENV)
        self.assertEqual((req.method, req.url), ('POST', 'https://google.serper.dev/search'))
        self.assertEqual(json.loads(req.body), {'q': 'site:linkedin.com/in "CEO"', 'num': 10, 'gl': 'us', 'hl': 'en', 'page': 2})
        self.assertEqual(req.headers['X-API-KEY'], 'fixture-secret')
        for payload in ({'query': 'x', 'num': 20}, {'query': 'x', 'offset': 1}, {'query': 'x', 'page': 11}):
            with self.assertRaises(PolicyError):
                get_provider('serper').request('search', payload, ENV)
        with self.assertRaisesRegex(ProviderError, 'missing_auth'):
            get_provider('serper').request('search', {'query': 'x'}, {})

    def test_serper_parse(self):
        out = parse('serper', 'search', SERPER_OK)
        self.assertEqual(out.assessment, 'results')
        self.assertEqual(len(out.fields['results']), 2)
        self.assertEqual(out.fields['results'][1]['link'], 'https://harbor.example.org/about')
        self.assertEqual(out.fields['results'][1]['snippet'], 'See for more')
        [prospect] = out.fields['prospects']
        self.assertEqual((prospect['full_name'], prospect['title'], prospect['company'], prospect['location']),
                         ('Jane Example', 'Founder & CEO', 'Harbor Realty Group', 'Austin, Texas'))
        self.assertEqual(prospect['linkedin_url'], 'https://www.linkedin.com/in/jane-example/')
        self.assertEqual(parse('serper', 'search', {'searchParameters': {}, 'organic': []}).assessment, 'not_found')
        self.assertEqual(parse('serper', 'search', {'searchParameters': {}}).assessment, 'not_found')
        self.assertEqual(parse('serper', 'search', {'message': 'Not enough credits', 'statusCode': 400}).error, 'provider_application_error')
        self.assertEqual(parse('serper', 'search', {'organic': 'nope'}).error, 'invalid_response')
        self.assertEqual(parse('serper', 'search', {}).error, 'invalid_response')
        self.assertEqual(parse('serper', 'search', None, json_valid=False).error, 'invalid_response')
        self.assertEqual(parse('serper', 'search', {'message': 'Unauthorized'}, status=403).error, 'auth')
        self.assertEqual(parse('serper', 'search', {}, status=429).error, 'rate_limit')

    def test_brave_request_and_parse(self):
        req = get_provider('brave').request('search', {'query': '"Founder" real estate', 'offset': 3}, ENV)
        parts = urlsplit(req.url)
        self.assertEqual((req.method, parts.netloc, parts.path), ('GET', 'api.search.brave.com', '/res/v1/web/search'))
        self.assertEqual(parse_qs(parts.query), {'q': ['"Founder" real estate'], 'count': ['20'], 'country': ['us'], 'offset': ['3']})
        self.assertEqual(req.headers, {'X-Subscription-Token': 'fixture-secret', 'Accept': 'application/json'})
        for payload in ({'query': 'x', 'count': 21}, {'query': 'x', 'offset': 10}, {'query': 'x', 'page': 2}):
            with self.assertRaises(PolicyError):
                get_provider('brave').request('search', payload, ENV)
        data = {'type': 'search', 'web': {'results': [
            {'title': 'Sam Sample – Managing Broker – Lakeside Realty | LinkedIn', 'url': 'https://www.linkedin.com/in/sam-sample/en',
             'description': 'Austin, Texas, United States · Managing Broker at <strong>Lakeside Realty</strong> · 500+ connections'}]}}
        out = parse('brave', 'search', data)
        [prospect] = out.fields['prospects']
        self.assertEqual((prospect['title'], prospect['company'], prospect['snippet_country']), ('Managing Broker', 'Lakeside Realty', 'US'))
        self.assertEqual(parse('brave', 'search', {'type': 'search'}).assessment, 'not_found')
        self.assertEqual(parse('brave', 'search', {'type': 'ErrorResponse', 'error': {'code': 'X'}}).error, 'provider_application_error')
        self.assertEqual(parse('brave', 'search', {'web': []}).error, 'invalid_response')
        self.assertEqual(parse('brave', 'search', {}, status=401).error, 'auth')
        self.assertEqual(parse('brave', 'search', {}, status=429).error, 'rate_limit')


class OverpassTests(unittest.TestCase):
    def test_request_is_form_encoded_ql_with_larger_cap(self):
        req = get_provider('overpass').request('places', {'area': 'Austin', 'state': 'TX'}, {})
        self.assertEqual((req.method, req.url), ('POST', 'https://overpass-api.de/api/interpreter'))
        self.assertEqual(req.headers['Content-Type'], 'application/x-www-form-urlencoded')
        self.assertEqual(req.max_response_bytes, MAX_RESPONSE_CEILING)
        body = req.body.decode('ascii')
        self.assertTrue(body.startswith('data='))
        ql = unquote_plus(body[5:])
        self.assertIn('area["ISO3166-2"="US-TX"]->.s;', ql)
        self.assertIn('rel["boundary"="administrative"]["name"="Austin"](area.s)->.r;', ql)
        self.assertIn('nwr["office"="estate_agent"](area.a);', ql)
        self.assertTrue(ql.endswith('out tags center 500;'))
        self.assertIn('area["ISO3166-2"="US-TX"]->.a;', build_overpass_query('Texas'))

    def test_ql_injection_and_escaping(self):
        for area in ('Austin"];out;', 'Austin\n[out:xml]', 'A' * 81, 'Austin (TX)', 'x', 'Austin; node(1)', '12345'):
            with self.assertRaises(PolicyError):
                build_overpass_query(area)
            with self.assertRaises(PolicyError):
                normalize_job({'provider': 'overpass', 'operation': 'places', 'payload': {'area': area}})
        ql = build_overpass_query("Coeur d'Alene", 'Idaho')
        self.assertIn('["name"="Coeur d\'Alene"]', ql)
        self.assertEqual(ql.count('"'), 16)
        from lead_research.providers import _ql_string
        self.assertEqual(_ql_string('a"b\\c'), '"a\\"b\\\\c"')
        with self.assertRaises(PolicyError):
            _ql_string('a\nb')
        for tags in (['amenity=bar'], ['office=estate_agent"];out;'], []):
            with self.assertRaises(PolicyError):
                normalize_job({'provider': 'overpass', 'operation': 'places', 'payload': {'area': 'Austin', 'tags': tags}})
        with self.assertRaises(PolicyError):
            normalize_job({'provider': 'overpass', 'operation': 'places', 'payload': {'area': 'Austin', 'query': '[out:json];node;out;'}})

    def test_parse(self):
        out = parse('overpass', 'places', OSM_OK)
        self.assertEqual(out.assessment, 'results')
        self.assertIn('OpenStreetMap contributors', out.fields['attribution'])
        rows = {c['osm_id']: c for c in out.fields['companies']}
        self.assertEqual(set(rows), {'node/101', 'way/202', 'node/404'})
        self.assertEqual((rows['node/101']['domain'], rows['node/101']['phone'], rows['node/101']['state']),
                         ('lakeside.example.net', '+1 512-555-0100', 'TX'))
        self.assertEqual((rows['way/202']['domain'], rows['node/404']['domain'], rows['node/404']['phone']), ('', '', None))
        self.assertEqual(parse('overpass', 'places', {'elements': []}).assessment, 'not_found')
        self.assertEqual(parse('overpass', 'places', {'elements': [], 'remark': 'runtime error: Query timed out'}).error, 'provider_application_error')
        self.assertEqual(parse('overpass', 'places', {'version': 0.6}).error, 'invalid_response')
        self.assertEqual(parse('overpass', 'places', {}, status=429).error, 'rate_limit')
        self.assertEqual(parse('overpass', 'places', {}, status=504).error, 'provider_error')


class PayloadValidationTests(unittest.TestCase):
    def job(self, operation, payload, provider='hunter'):
        return normalize_job({'provider': provider, 'operation': operation, 'payload': payload})

    def test_discover_filters_strict(self):
        good = self.job('discover', {'filters': {'headcount': ['11-50', '1-10', '1-10'], 'keywords': {'include': ['broker'], 'match': 'any'},
                                                 'headquarters_location': {'include': [{'country': 'US', 'state': 'TX', 'city': 'Austin'}]}}})
        self.assertEqual(good['payload']['filters']['headcount'], ['1-10', '11-50'])
        bad = [{'headcount': ['1-5']}, {'headcount': []}, {'offset': 100, 'query': 'real estate'}, {'limit': 101, 'query': 'abc'},
               {'limit': 50}, {'query': 'ab'}, {'query': 'x' * 501}, {'industry': {'include': ['Real Estate'], 'other': []}},
               {'company_type': {'include': ['corporation']}}, {'keywords': {'include': ['x'], 'match': 'some'}},
               {'headquarters_location': {'include': [{'state': 'TX'}]}},
               {'headquarters_location': {'include': [{'country': 'CA', 'state': 'TX'}]}},
               {'headquarters_location': {'include': [{'continent': 'Europe', 'country': 'FR'}]}},
               {'headquarters_location': {'include': [{'continent': 'Atlantis'}]}},
               {'similar_to': {'domain': 'example.org'}}, {'query': 'real estate\x00'}, {'limit': True, 'query': 'abc'}]
        for filters in bad:
            with self.subTest(filters=filters), self.assertRaises(PolicyError):
                self.job('discover', {'filters': filters})
        with self.assertRaises(PolicyError):
            self.job('discover', {'filters': {'query': 'real estate'}, 'api_key': 'fixture-secret'})

    def test_domain_search_strict(self):
        job = self.job('domain_search', {'domain': 'WWW.Example.org', 'department': 'sales,executive'})
        self.assertEqual(job['payload']['domain'], 'example.org')
        self.assertEqual(job['payload']['department'], ['executive', 'sales'])
        for payload in ({'domain': 'example.org', 'limit': 11}, {'domain': 'example.org', 'seniority': ['ceo']},
                        {'domain': 'example.org', 'decision_maker': 'true'}, {'domain': 'example.org', 'required_field': ['email']},
                        {'domain': '127.0.0.1'}, {'limit': 5}):
            with self.subTest(payload=payload), self.assertRaises(PolicyError):
                self.job('domain_search', payload)

    def test_search_strict_and_identity(self):
        for payload in ({'query': 'x' * 401}, {'query': 'abc\r\nX-Header: 1'}, {'query': ''}, {'query': ' '.join(['w'] * 51)},
                        {'query': 'ok', 'page': 0}, {'query': 'ok', 'num': 100}, {'query': 42}):
            with self.subTest(payload=payload), self.assertRaises(PolicyError):
                self.job('search', payload, 'serper')
        with self.assertRaises(PolicyError):
            self.job('search', {'query': 'ok', 'count': 10}, 'serper')
        with self.assertRaises(PolicyError):
            self.job('search', {'query': 'ok', 'page': 2}, 'brave')
        a = self.job('search', {'query': ' a  OR b ', 'page': 1}, 'serper')
        b = self.job('search', {'query': 'a OR b'}, 'serper')
        c = self.job('search', {'query': 'a OR b', 'page': 2}, 'serper')
        self.assertEqual(input_key('search', a['payload']), input_key('search', b['payload']))
        self.assertNotEqual(input_key('search', b['payload']), input_key('search', c['payload']))
        self.assertNotEqual(input_key('search', b['payload']), input_key('verify', {'email': 'a@example.org'}))
        d1 = self.job('discover', {'filters': {'headcount': ['11-50', '1-10'], 'query': 'abc'}})
        d2 = self.job('discover', {'filters': {'query': 'abc', 'headcount': ['1-10', '11-50']}})
        self.assertEqual(input_key('discover', d1['payload']), input_key('discover', d2['payload']))
        e1 = self.job('domain_search', {'domain': 'example.org'})
        e2 = self.job('domain_search', {'domain': 'example.org', 'limit': 10, 'decision_maker': True})
        e3 = self.job('domain_search', {'domain': 'example.org', 'limit': 5})
        self.assertEqual(input_key('domain_search', e1['payload']), input_key('domain_search', e2['payload']))
        self.assertNotEqual(input_key('domain_search', e1['payload']), input_key('domain_search', e3['payload']))

    def test_legacy_operations_unchanged(self):
        job = normalize_job({'provider': 'zerobounce', 'operation': 'verify', 'email': 'A@example.org', 'query': 'ignored', 'tags': 'x'})
        self.assertEqual(job['payload'], {'email': 'a@example.org'})


class LinkedInParsingTests(unittest.TestCase):
    def test_canonical_linkedin(self):
        expected = 'https://www.linkedin.com/in/jane-example/'
        for url in ('https://www.linkedin.com/in/jane-example', 'http://linkedin.com/in/Jane-Example/',
                    'https://uk.linkedin.com/in/jane-example?trk=public', 'linkedin.com/in/jane-example/en',
                    'https://m.linkedin.com/in/jane-example/details/experience/#x', 'https://www.linkedin.com/in/jane%2Dexample'):
            self.assertEqual(canonical_linkedin(url), expected, url)
        self.assertEqual(canonical_linkedin('https://www.linkedin.com/in/jos%C3%A9-ejemplo-12ab'),
                         'https://www.linkedin.com/in/jos%C3%A9-ejemplo-12ab/')
        for url in ('https://www.linkedin.com/company/harbor', 'https://linkedin.com.evil.example/in/jane',
                    'https://www.linkedin.com/in/', 'https://www.linkedin.com/in/a%22b', 'ftp://linkedin.com/in/jane-example',
                    'https://user:pw@linkedin.com/in/jane-example', None, 'https://www.linkedin.com/pub/jane/1/2/3'):
            self.assertIsNone(canonical_linkedin(url), url)
        validate_evidence_url(canonical_linkedin('https://ca.linkedin.com/in/jos%C3%A9-ejemplo?x=1'))

    def test_serp_variants(self):
        url = 'https://www.linkedin.com/in/jane-example'
        cases = [
            ('Jane Example - Founder & CEO - Harbor Realty Group | LinkedIn', '', ('Jane Example', 'Founder & CEO', 'Harbor Realty Group')),
            ('Jane Example – CEO – Harbor Realty | LinkedIn', '', ('Jane Example', 'CEO', 'Harbor Realty')),
            ('Jane Example — CEO — Harbor Realty ...', '', ('Jane Example', 'CEO', 'Harbor Realty')),
            ('Jane Example - Harbor Realty Group | LinkedIn', '', ('Jane Example', None, 'Harbor Realty Group')),
            ('Jane Example - Broker/Owner | LinkedIn', '', ('Jane Example', 'Broker/Owner', None)),
            ('Jane Example | LinkedIn', '', ('Jane Example', None, None)),
            ('Jane Example, CCIM, CRS - Principal - Harbor Realty | LinkedIn', '', ('Jane Example', 'Principal', 'Harbor Realty')),
            ('🏡 Jane Example 🏡 - Owner - Harbor Homes | LinkedIn', '', ('Jane Example', 'Owner', 'Harbor Homes')),
            ('Jane Example | LinkedIn', 'Austin, Texas, United States · Founder & CEO at Harbor Realty Group · 500+ connections',
             ('Jane Example', 'Founder & CEO', 'Harbor Realty Group')),
            ('Jane Example | LinkedIn', 'Location: Austin, Texas · Founder & CEO · Experience: Harbor Realty Group · Education: Example U',
             ('Jane Example', 'Founder & CEO', 'Harbor Realty Group')),
        ]
        for title, snippet, expected in cases:
            with self.subTest(title=title, snippet=snippet):
                row = parse_linkedin_result(title, snippet, url)
                self.assertEqual((row['full_name'], row['title'], row['company']), expected)
                self.assertEqual((row['source'], row['linkedin_url'], row['source_url']), ('xray', url + '/', url + '/'))
        row = parse_linkedin_result('Jane Example | LinkedIn', 'Austin, Texas, United States · CEO at Harbor · 500+ connections', url)
        self.assertEqual((row['location'], row['snippet_country']), ('Austin, Texas, United States', 'US'))
        row = parse_linkedin_result('Jane Example | LinkedIn', 'Location: Toronto, Ontario, Canada · Broker', url)
        self.assertEqual(row['snippet_country'], 'Canada')
        self.assertIsNone(parse_linkedin_result('Jane Example | LinkedIn', '', 'https://example.org/jane'))
        self.assertIsNone(parse_linkedin_result('🏡 | LinkedIn', '', url))


class BuilderTests(unittest.TestCase):
    def test_xray_queries(self):
        queries = build_xray_queries(DEFAULT_DM_TITLES, ['Austin, Texas', 'Dallas, Texas', 'Austin, Texas'])
        self.assertEqual(queries, build_xray_queries(DEFAULT_DM_TITLES, ['Austin, Texas', 'Dallas, Texas']))
        self.assertEqual(len(queries), len(set(queries)))
        self.assertTrue(all(len(q) <= 300 for q in queries))
        self.assertTrue(all(q.startswith('site:linkedin.com/in (') or q.startswith('site:linkedin.com/in "') for q in queries))
        self.assertEqual(len(queries), 2)  # all ten default titles fit one 300-char query per location
        queries = build_xray_queries(DEFAULT_DM_TITLES, ['Austin, Texas', 'Dallas, Texas'], max_length=150)
        self.assertTrue(all(len(q) <= 150 for q in queries))
        austin = [q for q in queries if '"Austin, Texas"' in q]
        self.assertGreaterEqual(len(austin), 2)
        for title in DEFAULT_DM_TITLES:
            self.assertTrue(any('"' + title + '"' in q for q in austin), title)
        self.assertTrue(all(q.endswith('-jobs -hiring') and '"real estate"' in q for q in queries))
        self.assertIn(' OR ', austin[0])
        short = build_xray_queries(['CEO', 'Founder'], ['Austin, Texas'])
        self.assertEqual(short, ['site:linkedin.com/in ("CEO" OR "Founder") "real estate" "Austin, Texas" -jobs -hiring'])
        self.assertEqual(build_xray_queries(['CEO'], [], industry_terms=('real estate', 'realtor'), exclude_terms=()),
                         ['site:linkedin.com/in "CEO" ("real estate" OR "realtor")'])
        with self.assertRaises(PolicyError):
            build_xray_queries(['x' * 80], ['y' * 80], extra_terms=('z' * 80, 'w' * 80))
        for q in queries:
            normalize_job({'provider': 'serper', 'operation': 'search', 'payload': {'query': q}})

    def test_job_builders(self):
        jobs = hunter_discover_jobs(['TX', 'Florida'], headcounts=('1-10', '11-50'))
        self.assertEqual(len(jobs), 4)
        keys = {input_key('discover', normalize_job(j)['payload']) for j in jobs}
        self.assertEqual(len(keys), 4)
        self.assertEqual(jobs[2]['payload']['filters']['headquarters_location']['include'], [{'country': 'US', 'state': 'FL'}])
        self.assertEqual(jobs[0]['payload']['filters']['industry']['include'], list(HUNTER_REAL_ESTATE_INDUSTRIES))
        with self.assertRaises(PolicyError):
            hunter_discover_jobs(['Atlantis'])
        domain_jobs = hunter_domain_jobs(['example.org', 'www.example.org', 'example.net'])
        self.assertEqual([j['payload']['domain'] for j in domain_jobs], ['example.org', 'example.net'])
        for job in domain_jobs + osm_jobs(['Texas', ('Austin', 'TX'), {'area': 'Travis County', 'state': 'Texas'}]) \
                + xray_jobs('brave', ['"CEO" real estate'], pages=2) + xray_jobs('serper', ['"CEO"'], pages=3):
            normalize_job(job)
        self.assertEqual([j['payload'].get('page') for j in xray_jobs('serper', ['q'], pages=3)], [None, 2, 3])
        self.assertEqual([j['payload'].get('offset') for j in xray_jobs('brave', ['q'], pages=2)], [None, 1])
        self.assertEqual(len(US_STATES), 51)


class V3JobBuilderTests(unittest.TestCase):
    def test_apollo_jobs(self):
        from lead_research.discovery import apollo_jobs
        jobs = apollo_jobs(['CEO', 'Owner'], ['Texas', 'Florida', 'Texas'], pages=2)
        self.assertEqual(len(jobs), 4)
        first = normalize_job(jobs[0])['payload']['criteria']
        self.assertEqual(first, {'titles': ['CEO', 'Owner'], 'seniorities': ['owner', 'founder', 'c_suite', 'partner'],
                                 'employee_ranges': ['1,10', '11,50', '51,200'], 'organization_locations': ['Texas'],
                                 'page': 1, 'per_page': 100})
        self.assertEqual(jobs[1]['payload']['criteria']['page'], 2)
        people = apollo_jobs([], ['Austin, Texas'], location_field='person_locations', employee_ranges=())
        self.assertEqual(people[0]['payload']['criteria']['person_locations'], ['Austin, Texas'])
        with self.assertRaises(PolicyError):
            apollo_jobs(['CEO'], ['Texas'], pages=501)
        with self.assertRaises(PolicyError):
            apollo_jobs(['CEO'], ['Texas'], location_field='evil')

    def test_enrich_and_linkedin_jobs(self):
        from lead_research.discovery import apollo_enrich_jobs, prospeo_enrich_jobs, tomba_linkedin_jobs
        prospects = [{'apollo_id': '67bdafd0c3a4c50001bbd7c2', 'lead_id': 7}, {'apollo_id': '67bdafd0c3a4c50001bbd7c2'},
                     {'full_name': 'Jane Example', 'company_website': 'example.org'}, {'email': 'x@example.org', 'apollo_id': 'a' * 10},
                     {'linkedin_url': 'https://www.linkedin.com/in/jane-example/', 'prospeo_id': 'aaaacd817619fba3d254cd64'},
                     {'full_name': 'No Domain'}]
        jobs = apollo_enrich_jobs(prospects)
        self.assertEqual([j['payload'] for j in jobs], [
            {'apollo_id': '67bdafd0c3a4c50001bbd7c2', 'lead_id': '7'}, {'name': 'Jane Example', 'domain': 'example.org'},
            {'linkedin_url': 'https://www.linkedin.com/in/jane-example/'}])
        for job in jobs:
            normalize_job(job)
        self.assertEqual(prospeo_enrich_jobs(prospects)[-1]['payload'], {'person_id': 'aaaacd817619fba3d254cd64'})
        urls = tomba_linkedin_jobs(['https://uk.linkedin.com/in/jane-example?trk=1', 'https://www.linkedin.com/in/jane-example/',
                                    'https://example.org/in/x', {'linkedin_url': 'https://www.linkedin.com/in/sam-sample', 'lead_id': 3}])
        self.assertEqual([j['payload'] for j in urls], [{'linkedin_url': 'https://www.linkedin.com/in/jane-example/'},
                                                        {'linkedin_url': 'https://www.linkedin.com/in/sam-sample/', 'lead_id': '3'}])

    def test_prospeo_jobs_chunk_domains(self):
        from lead_research.discovery import prospeo_jobs
        domains = ['www.site%d.example.org' % i for i in range(1001)] + ['site0.example.org']
        jobs = prospeo_jobs(domains, titles=['CEO'])
        self.assertEqual([len(j['payload']['criteria']['websites']) for j in jobs], [500, 500, 1])
        criteria = normalize_job(jobs[0])['payload']['criteria']
        self.assertEqual((criteria['title_match_mode'], criteria['seniorities'], criteria['page']),
                         ('CONTAINS', ['Founder/Owner', 'C-Suite', 'Partner'], 1))
        with self.assertRaises(PolicyError):
            prospeo_jobs(domains, chunk=501)

    def test_places_and_exa_jobs(self):
        from lead_research.discovery import exa_jobs, exa_people_queries, places_jobs, places_queries
        self.assertEqual(places_queries([('Austin', 'TX')]), ['real estate brokerage in Austin, TX',
                                                              'commercial real estate firm in Austin, TX',
                                                              'property management company in Austin, TX'])
        jobs = places_jobs([{'city': 'Miami', 'state': 'FL'}, 'luxury realtor in Austin, TX', 'luxury realtor in Austin, TX'],
                           included_type='real_estate_agency')
        self.assertEqual(len(jobs), 4)
        self.assertEqual(normalize_job(jobs[-1])['payload'], {'text_query': 'luxury realtor in Austin, TX', 'page_size': 20,
                                                              'included_type': 'real_estate_agency'})
        with self.assertRaises(PolicyError):
            places_queries(['Austin'])
        queries = exa_people_queries(['CEO'], ['Austin, Texas'])
        self.assertEqual(queries, ['CEO at a real estate company in Austin, Texas'])
        self.assertEqual(normalize_job(exa_jobs(queries)[0])['payload'], {'query': queries[0]})

    def test_v3_identity_and_unknown_keys(self):
        a = normalize_job({'provider': 'google_places', 'operation': 'text_search', 'payload': {'text_query': 'Realty in Austin, TX'}})
        b = normalize_job({'provider': 'google_places', 'operation': 'text_search',
                           'payload': {'text_query': ' realty  in austin, tx ', 'page_size': 20}})
        self.assertEqual(input_key('text_search', a['payload']), input_key('text_search', b['payload']))
        c = normalize_job({'provider': 'google_places', 'operation': 'text_search', 'payload': {**a['payload'], 'page_cursor': 'abc'}})
        self.assertNotEqual(input_key('text_search', a['payload']), input_key('text_search', c['payload']))
        x = normalize_job({'provider': 'apollo', 'operation': 'people_enrich', 'payload': {'name': 'Jane  Example', 'domain': 'www.example.org'}})
        y = normalize_job({'provider': 'apollo', 'operation': 'people_enrich', 'payload': {'name': 'jane example', 'domain': 'example.org'}})
        self.assertEqual(input_key('people_enrich', x['payload']), input_key('people_enrich', y['payload']))
        for job in ({'provider': 'serper', 'operation': 'people_search', 'payload': {'criteria': {'titles': ['CEO']}}},
                    {'provider': 'tomba', 'operation': 'linkedin_email', 'payload': {'linkedin_url': 'x', 'api_key': 'k'}},
                    {'provider': 'prospeo', 'operation': 'enrich', 'payload': {'person_id': 'aaaacd817619fba3d254cd64', 'secret': 1}}):
            with self.assertRaises(PolicyError):
                normalize_job(job)


class ConfigMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'providers.json'

    def tearDown(self):
        self.tmp.cleanup()

    def legacy(self):
        config = default_config()
        for name in ('serper', 'brave', 'overpass'):
            del config['providers'][name]
        for op in ('discover', 'domain_search'):
            del config['providers']['hunter']['charges'][op]
        config['providers']['hunter']['enabled'] = True
        return config

    def write(self, config):
        self.path.write_text(json.dumps(config))
        return self.path

    def test_v1_file_migrates(self):
        loaded = load_config(self.write(self.legacy()))
        self.assertEqual(set(loaded['providers']), set(DEFAULT['providers']))
        self.assertTrue(loaded['providers']['hunter']['enabled'])
        self.assertEqual(loaded['providers']['hunter']['charges'], DEFAULT['providers']['hunter']['charges'])
        self.assertFalse(any(loaded['providers'][p]['enabled'] for p in ('serper', 'brave', 'overpass')))
        save_config(self.path, default_config())
        self.assertEqual(load_config(self.path), default_config())

    def test_tampering_still_rejected(self):
        mutations = [
            lambda c: c['providers']['hunter']['charges']['find'].update(credits='0.5'),
            lambda c: c['providers']['hunter']['charges'].update(discover={'discover_requests': '0.5'}),
            lambda c: c['providers'].update(evil={'enabled': True, 'charges': {}}),
            lambda c: c['providers'].pop('zerobounce'),
            lambda c: c['providers']['clearout']['charges'].pop('verify'),
            lambda c: c['providers']['hunter'].update(origin='https://example.org'),
            lambda c: c.update(extra=True),
            lambda c: c['providers']['hunter']['charges'].update(batch={'credits': '1'}),
            lambda c: c['providers'].update(serper={'enabled': 'yes', 'charges': {'search': {'queries': '1'}}}),
        ]
        for mutate in mutations:
            config = self.legacy()
            mutate(config)
            with self.assertRaises(PolicyError):
                load_config(self.write(config))


class TransportLimitTests(unittest.TestCase):
    class Opener:
        def __init__(self, body):
            self.body, self.calls = body, 0

        def open(self, request, timeout):
            self.calls += 1
            response = io.BytesIO(self.body)
            response.code, response.headers = 200, {}
            return response

    def test_per_request_cap(self):
        body = b'{"elements": []}' + b' ' * 30
        small = UrllibTransport(max_response_bytes=20, opener=self.Opener(body))
        with self.assertRaisesRegex(ProviderError, 'response_too_large'):
            small.send(Request('POST', 'https://overpass-api.de/api/interpreter', {}, b'data=x'))
        out = small.send(Request('POST', 'https://overpass-api.de/api/interpreter', {}, b'data=x', 30, 1000))
        self.assertEqual(out.data, {'elements': []})
        for limit in (0, MAX_RESPONSE_CEILING + 1, True):
            with self.assertRaisesRegex(PolicyError, 'invalid_response_limit'):
                small.send(Request('GET', 'https://google.serper.dev/search', max_response_bytes=limit))
        for url in ('https://google.serper.dev/search', 'https://api.search.brave.com/res/v1/web/search?q=x'):
            UrllibTransport(opener=self.Opener(b'{}')).send(Request('GET', url))


class RunnerDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name + '/ledger.db')
        self.config = default_config()
        for name in ('hunter', 'serper', 'brave', 'overpass'):
            self.config['providers'][name]['enabled'] = True

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def runner(self, responses=()):
        self.transport = FakeTransport(responses)
        return Runner(self.store, self.config, env=ENV, transport=self.transport, sleep=lambda _: None)

    def test_dry_run_plan(self):
        self.store.confirm_budget('hunter', 'discover_requests', '1', 'synthetic free')
        jobs = hunter_discover_jobs(['TX'], headcounts=('1-10', '11-50')) + osm_jobs([('Austin', 'TX')]) \
            + [{'provider': 'serper', 'operation': 'search', 'payload': {'query': 'x', 'count': 5}}]
        report = self.runner().run(jobs)
        statuses = [(r.get('status'), r.get('reason')) for r in report['results']]
        self.assertEqual(statuses[0], ('ready', None))
        self.assertEqual(report['results'][0]['charges'], {'discover_requests': '1'})
        self.assertEqual(statuses[1], ('ready', None))  # dry-run plans each job against the confirmed pool
        self.assertEqual(statuses[2], ('skipped', 'insufficient_confirmed_free_budget'))
        self.assertEqual(report['results'][2]['charges'], {'requests': '1'})
        self.assertEqual(statuses[3], ('skipped', 'invalid_job'))
        self.assertEqual((self.transport.requests, self.store.attempts()), ([], []))
        self.config['providers']['overpass']['enabled'] = False
        self.assertEqual(self.runner().run(osm_jobs(['Austin']))['results'][0]['reason'], 'provider_disabled')

    def test_serper_execute_cache_and_collect(self):
        self.store.confirm_budget('serper', 'queries', '3', 'synthetic free')
        job = {'provider': 'serper', 'operation': 'search', 'payload': {'query': 'site:linkedin.com/in "CEO" "real estate"'}}
        report = self.runner([Response(200, SERPER_OK)]).run([job], execute=True)
        self.assertEqual(report['results'][0]['status'], 'completed')
        self.assertEqual(self.store.budgets()[0]['remaining'], '2')
        attempt = self.store.attempts()[0]
        self.assertEqual(attempt['outcome']['fields']['prospects'][0]['linkedin_url'], 'https://www.linkedin.com/in/jane-example/')
        self.assertEqual(attempt['outcome']['fields']['results'][1]['link'], 'https://harbor.example.org/about')
        self.assertNotIn('[redacted]', json.dumps(attempt['outcome']))
        again = self.runner([]).run([{**job, 'payload': {'query': ' site:linkedin.com/in  "CEO" "real estate" ', 'page': 1}}], execute=True)
        self.assertEqual(again['results'][0]['status'], 'cached')
        self.assertEqual((self.transport.requests, self.store.budgets()[0]['remaining']), ([], '2'))
        found = collect(self.store)
        self.assertEqual(len(found['prospects']), 1)
        self.assertEqual(found['prospects'][0]['provider'], 'serper')
        self.assertEqual(found['prospects'][0]['attempt_id'], attempt['id'])
        self.assertIsNotNone(found['prospects'][0]['observed_at'])

    def test_hunter_domain_search_execute_and_dedupe(self):
        self.store.confirm_budget('hunter', 'credits', '2', 'synthetic free')
        self.store.confirm_budget('hunter', 'find_ops', '2', 'synthetic free')
        self.store.confirm_budget('serper', 'queries', '1', 'synthetic free')
        runner = self.runner([Response(200, DOMAIN_OK), Response(200, SERPER_OK)])
        report = runner.run(hunter_domain_jobs(['example.org'])
                            + [{'provider': 'serper', 'operation': 'search', 'payload': {'query': 'jane'}}], execute=True)
        self.assertEqual([r['status'] for r in report['results']], ['completed', 'completed'])
        self.assertIn('/v2/domain-search?', self.transport.requests[0].url)
        budgets = {(b['provider'], b['pool']): b['remaining'] for b in self.store.budgets()}
        self.assertEqual((budgets[('hunter', 'credits')], budgets[('hunter', 'find_ops')]), ('1', '1'))
        stored = self.store.attempts()[0]['outcome']
        self.assertEqual(stored['fields']['prospects'][0]['source_url'], 'https://example.org/team')
        self.assertEqual(stored['fields']['prospects'][0]['email'], 'jane.example@example.org')
        found = collect(self.store)
        jane = [p for p in found['prospects'] if p['linkedin_url'] == 'https://www.linkedin.com/in/jane-example/']
        self.assertEqual(len(jane), 1)
        self.assertEqual(jane[0]['email'], 'jane.example@example.org')
        self.assertEqual(jane[0]['location'], 'Austin, Texas')
        self.assertEqual({s['provider'] for s in jane[0]['sources']}, {'hunter', 'serper'})
        self.assertEqual(len(found['prospects']), 2)
        company = [c for c in found['companies'] if c['domain'] == 'example.org'][0]
        self.assertEqual(company['email_pattern'], '{first}.{last}')

    def test_provider_error_pauses_and_stops(self):
        self.store.confirm_budget('brave', 'queries', '5', 'synthetic free')
        jobs = xray_jobs('brave', ['"CEO" one', '"CEO" two'])
        report = self.runner([Response(429, {'type': 'ErrorResponse'})]).run(jobs, execute=True)
        self.assertTrue(report['stopped'])
        self.assertEqual(len(self.transport.requests), 1)
        self.assertTrue(self.store.provider_state('brave')['paused'])
        self.assertEqual(self.store.attempts()[0]['outcome']['error'], 'rate_limit')
        report = self.runner([]).run(jobs[1:], execute=True)
        self.assertEqual(report['results'][0]['reason'], 'provider_paused')
        self.assertEqual(collect(self.store), {'prospects': [], 'companies': []})

    def test_overpass_execute_without_auth_and_store_round_trip(self):
        self.store.confirm_budget('overpass', 'requests', '1', 'synthetic free')
        runner = Runner(self.store, self.config, env={}, transport=FakeTransport([Response(200, OSM_OK)]), sleep=lambda _: None)
        report = runner.run(osm_jobs([('Austin', 'TX')]), execute=True)
        self.assertEqual(report['results'][0]['status'], 'completed')
        companies = collect(self.store)['companies']
        self.assertEqual({c['osm_id'] for c in companies}, {'node/101', 'way/202', 'node/404'})
        attempt = self.store.attempts()[0]
        self.assertEqual(attempt['outcome']['fields']['attribution'], '\u00a9 OpenStreetMap contributors, ODbL')

    def test_store_finish_preserves_prospect_urls(self):
        self.store.confirm_budget('serper', 'queries', '1', 'synthetic free')
        attempt = self.store.reserve('serper', 'search', 'k', {'query': 'x'}, {'queries': '1'}, 'serper')
        self.store.mark_sent(attempt['id'])
        prospect = parse_linkedin_result('Jane Example - CEO - Harbor | LinkedIn', '', 'https://de.linkedin.com/in/jane-example?trk=a#b')
        saved = self.store.finish(attempt['id'], Outcome('results', fields={'prospects': [prospect], 'results': [
            {'title': 't', 'link': 'https://example.org/?token=x', 'snippet': None, 'position': 1}]}))
        self.assertEqual(saved['outcome']['fields']['prospects'][0], prospect)
        self.assertEqual(saved['outcome']['fields']['results'][0]['link'], '[redacted]')
        self.assertEqual(self.store.get_attempt(attempt['id'])['outcome']['fields']['prospects'][0]['linkedin_url'],
                         'https://www.linkedin.com/in/jane-example/')


if __name__ == '__main__':
    unittest.main()
