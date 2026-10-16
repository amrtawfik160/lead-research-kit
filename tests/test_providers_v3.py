"""v3 adapters: offline fixtures only (synthetic identities, reserved example domains, fake keys)."""
import json
import tempfile
import unittest
from urllib.parse import parse_qs, parse_qsl, urlsplit

from lead_research.configuration import DEFAULT, default_config, load_config
from lead_research.discovery import collect
from lead_research.models import PolicyError, Response
from lead_research.policy import input_key, normalize_job
from lead_research.providers import PROVIDERS, get_provider
from lead_research.runner import Runner
from lead_research.store import Store
from lead_research.transport import OFFICIAL_ORIGINS
from lead_research.workflow import route_run

SECRET = 'fixture-secret'
ENV = {k: SECRET for k in ('APOLLO_API_KEY', 'PROSPEO_API_KEY', 'GOOGLE_MAPS_API_KEY', 'EXA_API_KEY', 'TOMBA_API_KEY',
                           'TOMBA_SECRET', 'ICYPEAS_API_KEY', 'REOON_API_KEY', 'MILLIONVERIFIER_API_KEY')}
NEW = ('apollo', 'prospeo', 'google_places', 'exa', 'tomba', 'icypeas', 'reoon', 'millionverifier')
EMAIL = 'jane.example@example.org'
FIND = {'name': 'Jane Example', 'domain': 'example.org', 'domain_evidence_url': 'https://example.org/team'}
APOLLO_ID = '67bdafd0c3a4c50001bbd7c2'
PROSPEO_ID = 'aaaacd817619fba3d254cd64'
LINKEDIN = 'https://www.linkedin.com/in/jane-example/'

APOLLO_SEARCH = {'total_entries': 2, 'people': [
    {'id': APOLLO_ID, 'first_name': 'Jane', 'last_name_obfuscated': 'Ex***e', 'title': 'Founder & CEO',
     'has_email': True, 'organization': {'name': 'Harbor Realty Group', 'has_industry': True}},
    {'id': 'not an id', 'first_name': None},
    {'id': '6775057df8360a0001a6852c', 'first_name': 'Sam', 'last_name': 'Sample', 'title': 'Managing Broker',
     'linkedin_url': 'http://www.linkedin.com/in/sam-sample', 'seniority': 'owner',
     'organization': {'name': 'Summit Homes', 'primary_domain': 'summit.example.net'}}]}
APOLLO_MATCH = {'person': {'id': APOLLO_ID, 'first_name': 'Jane', 'last_name': 'Example', 'name': 'Jane Example',
                           'linkedin_url': 'http://www.linkedin.com/in/jane-example', 'title': 'Founder & CEO',
                           'email_status': 'verified', 'email': EMAIL, 'match_confidence': 'high',
                           'city': 'Austin', 'state': 'Texas',
                           'organization': {'name': 'Harbor Realty Group', 'website_url': 'https://www.example.org',
                                            'primary_domain': 'example.org', 'phone': '+1 512-555-0100'}},
                'request_id': 'r1'}
PLACES_OK = {'places': [
    {'id': 'ChIJabcdefghij123', 'displayName': {'text': 'Harbor Realty Group', 'languageCode': 'en'},
     'websiteUri': 'https://www.harbor.example.org/?utm_source=gmb', 'nationalPhoneNumber': '(512) 555-0100',
     'formattedAddress': '1 Main St, Austin, TX 78701', 'types': ['real_estate_agency', 'point_of_interest'],
     'addressComponents': [{'longText': 'Austin', 'shortText': 'Austin', 'types': ['locality', 'political']},
                           {'longText': 'Texas', 'shortText': 'TX', 'types': ['administrative_area_level_1']},
                           {'longText': '78701', 'shortText': '78701', 'types': ['postal_code']},
                           {'longText': 'United States', 'shortText': 'US', 'types': ['country']}]},
    {'id': 'ChIJsocial000000001', 'displayName': {'text': 'Social Only Homes'},
     'websiteUri': 'https://www.facebook.com/socialonly'},
    {'id': 'ChIJbuilder00000001', 'displayName': {'text': 'Builder Site Realty'},
     'websiteUri': 'https://buildersite.wixsite.com/home'},
    {'id': 'bad', 'displayName': {'text': 'No Valid Id'}}],
    'nextPageToken': 'AeCrKXsZWzNVbPzO-MRWPu52jWO_Xx8a'}
PROSPEO_SEARCH = {'error': False, 'free': False, 'results': [
    {'person': {'person_id': PROSPEO_ID, 'first_name': 'Jane', 'last_name': 'Example', 'full_name': 'Jane Example',
                'linkedin_url': 'https://www.linkedin.com/in/jane-example', 'current_job_title': 'Broker/Owner',
                'email': {'status': 'VERIFIED', 'revealed': False, 'email': 'jane.*****@example.org'},
                'location': {'country_code': 'US', 'state': 'Texas', 'city': 'Austin'}},
     'company': {'name': 'Harbor Realty Group', 'website': 'https://example.org', 'domain': 'example.org'}}],
    'pagination': {'current_page': 1, 'per_page': 25, 'total_page': 1, 'total_count': 1}}
PROSPEO_ENRICH = {'error': False, 'free_enrichment': False,
                  'person': {**PROSPEO_SEARCH['results'][0]['person'],
                             'email': {'status': 'VERIFIED', 'revealed': True, 'email': EMAIL, 'verification_method': 'SMTP'}},
                  'company': PROSPEO_SEARCH['results'][0]['company']}
TOMBA_FOUND = {'data': {'email': EMAIL, 'first_name': 'Jane', 'last_name': 'Example', 'full_name': 'Jane Example',
                        'website_url': 'example.org', 'company': 'Harbor Realty Group', 'position': 'CEO',
                        'linkedin': 'https://www.linkedin.com/in/jane-example', 'score': 92, 'accept_all': True,
                        'type': 'personal', 'phone_number': False, 'verification': {'date': None, 'status': 'valid'},
                        'sources': []}}
ICY_SUBMIT = {'success': True, 'item': {'status': 'NONE', '_id': 'p_gfS5EB4liCGm90KDFC'}}
ICY_JOB = 'https://app.icypeas.com/api/bulk-single-searchs/read?id=p_gfS5EB4liCGm90KDFC'


def icy_read(status, emails=()):
    return {'success': True, 'total': 1, 'sorts': [[], []], 'items': [{
        'name': '__icypeas__individual', 'status': status, 'order': 0, '_id': '',
        'results': {'firstname': 'Jane', 'lastname': 'Example', 'fullname': 'Jane Example', 'emails': list(emails)}}]}


REOON_SAFE = {'email': EMAIL, 'status': 'safe', 'overall_score': 98, 'is_safe_to_send': True, 'is_deliverable': True,
              'is_catch_all': False, 'is_disposable': False, 'is_role_account': False, 'is_spamtrap': False,
              'has_inbox_full': False, 'is_disabled': False, 'is_free_email': False, 'mx_accepts_mail': True,
              'can_connect_smtp': True, 'verification_mode': 'power', 'mx_records': ['mx.example.org']}
MV_OK = {'email': EMAIL, 'quality': 'good', 'result': 'ok', 'resultcode': 1, 'subresult': 'ok', 'free': False,
         'role': False, 'didyoumean': '', 'credits': 3454, 'executiontime': 2, 'error': '', 'livemode': True}
EXA_OK = {'requestId': 'r', 'results': [
    {'id': 'x1', 'title': 'Jane Example - Founder & CEO - Harbor Realty Group | LinkedIn',
     'url': 'https://www.linkedin.com/in/jane-example', 'highlights': ['Location: Austin, Texas']},
    {'id': 'x2', 'title': 'Harbor Realty Group', 'url': 'https://harbor.example.org/'}]}


def parse(provider, operation, data, status=200, json_valid=True):
    return get_provider(provider).parse(operation, Response(status, data, {}, json_valid))


class FakeTransport:
    def __init__(self, responses):
        self.responses, self.requests = list(responses), []

    def send(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


class ContractTests(unittest.TestCase):
    def test_registry_origins_defaults(self):
        for name in NEW:
            provider = get_provider(name)
            self.assertIn(provider.origin, OFFICIAL_ORIGINS)
            self.assertEqual(provider.independence_group, name)
            entry = DEFAULT['providers'][name]
            self.assertFalse(entry['enabled'])
            self.assertEqual(set(entry['charges']), set(provider.operations))
            for charges in entry['charges'].values():
                self.assertEqual(list(charges.values()), ['1'])
        self.assertEqual(DEFAULT['providers']['apollo']['charges']['people_search'], {'search_requests': '1'})
        self.assertEqual(DEFAULT['providers']['google_places']['charges']['text_search'], {'enterprise_requests': '1'})

    def test_missing_auth_and_secrets_stay_in_request(self):
        payloads = {'apollo': {'criteria': {'titles': ['CEO']}}, 'prospeo': {'criteria': {'websites': ['example.org']}},
                    'google_places': {'text_query': 'real estate brokerage in Austin, TX'}, 'exa': {'query': 'ceo'},
                    'tomba': FIND, 'icypeas': FIND, 'reoon': {'email': EMAIL}, 'millionverifier': {'email': EMAIL}}
        for name, payload in payloads.items():
            provider = get_provider(name)
            with self.assertRaisesRegex(Exception, 'missing_auth'):
                provider.request(provider.operations[0], payload, {})
            request = provider.request(provider.operations[0], payload, ENV)
            self.assertNotIn(SECRET, repr(request))
            in_url = SECRET in request.url
            self.assertEqual(in_url, name in ('reoon', 'millionverifier'), name)  # query-key auth per their docs
            if not in_url:
                self.assertIn(SECRET, request.headers.values())
        with self.assertRaisesRegex(Exception, 'missing_auth'):
            get_provider('tomba').request('find', FIND, {'TOMBA_API_KEY': SECRET})


class ApolloTests(unittest.TestCase):
    def test_search_request(self):
        payload = normalize_job({'provider': 'apollo', 'operation': 'people_search', 'payload': {'criteria': {
            'titles': ['Owner', 'CEO'], 'seniorities': ['founder', 'owner'], 'organization_locations': ['Texas'],
            'employee_ranges': ['1,10', '11,50'], 'domains': ['www.Example.org'], 'include_similar_titles': False}}})['payload']
        req = get_provider('apollo').request('people_search', payload, ENV)
        parts = urlsplit(req.url)
        self.assertEqual((req.method, parts.path), ('POST', '/api/v1/mixed_people/api_search'))
        query = parse_qs(parts.query)
        self.assertEqual(query['person_titles[]'], ['CEO', 'Owner'])
        self.assertEqual(query['person_seniorities[]'], ['owner', 'founder'])
        self.assertEqual(query['q_organization_domains_list[]'], ['example.org'])
        self.assertEqual(query['organization_num_employees_ranges[]'], ['1,10', '11,50'])
        self.assertEqual((query['page'], query['per_page'], query['include_similar_titles']), (['1'], ['100'], ['false']))
        self.assertEqual(req.headers['x-api-key'], SECRET)
        self.assertIsNone(req.body)

    def test_search_parse(self):
        out = parse('apollo', 'people_search', APOLLO_SEARCH)
        self.assertEqual(out.assessment, 'results')
        jane, sam = out.fields['prospects']
        self.assertEqual((jane['apollo_id'], jane['name_complete'], jane['full_name']), (APOLLO_ID, False, None))
        self.assertEqual((jane['last_name_obfuscated'], jane['display_name'], jane['has_email']), ('Ex***e', 'Jane Ex***e', True))
        self.assertEqual((sam['full_name'], sam['name_complete'], sam['company_website']), ('Sam Sample', True, 'summit.example.net'))
        self.assertEqual(sam['linkedin_url'], 'https://www.linkedin.com/in/sam-sample/')
        self.assertEqual(out.fields['total_results'], 2)
        self.assertEqual(parse('apollo', 'people_search', {'total_entries': 0, 'people': []}).assessment, 'not_found')
        for data, code in (({'error': 'Invalid parameters'}, 'provider_application_error'),
                           ({'error_details': {'code': 'X'}, 'people': []}, 'provider_application_error'),
                           ({'people': {'x': 1}}, 'invalid_response'), ({'people': [{'id': 'bad'}]}, 'invalid_response')):
            self.assertEqual(parse('apollo', 'people_search', data).error, code)
        self.assertEqual(parse('apollo', 'people_search', None, 401).error, 'auth')
        self.assertEqual(parse('apollo', 'people_search', None, json_valid=False).error, 'invalid_response')

    def test_search_payload_validation(self):
        bad = [{'titles': ['CEO'], 'seniorities': ['boss']}, {'titles': ['CEO'], 'page': 501}, {'titles': ['CEO'], 'per_page': 0},
               {'employee_ranges': ['1,10']}, {'titles': ['CEO'], 'evil': 1}, {'titles': ['CEO'], 'employee_ranges': ['10,1']},
               {'titles': ['CEO'], 'include_similar_titles': 'yes'}, {'titles': ['<script>']}]
        for criteria in bad:
            with self.assertRaises(PolicyError, msg=criteria):
                normalize_job({'provider': 'apollo', 'operation': 'people_search', 'payload': {'criteria': criteria}})
        with self.assertRaises(PolicyError):
            normalize_job({'provider': 'apollo', 'operation': 'people_search', 'payload': {'criteria': {'titles': ['CEO']}, 'x': 1}})
        with self.assertRaises(PolicyError):
            get_provider('apollo').request('people_search', {'criteria': {'titles': ['CEO']}, 'query': 'x'}, ENV)
        a = normalize_job({'provider': 'apollo', 'operation': 'people_search', 'payload': {'criteria': {'titles': ['ceo', 'Owner']}}})
        b = normalize_job({'provider': 'apollo', 'operation': 'people_search', 'payload': {'criteria': {'titles': ['Owner', 'ceo'], 'page': 1}}})
        self.assertEqual(input_key('people_search', a['payload']), input_key('people_search', b['payload']))

    def test_enrich_request_and_parse(self):
        req = get_provider('apollo').request('people_enrich', {'apollo_id': APOLLO_ID}, ENV)
        parts = urlsplit(req.url)
        self.assertEqual(parts.path, '/api/v1/people/match')
        self.assertEqual(dict(parse_qsl(parts.query)), {'id': APOLLO_ID, 'reveal_personal_emails': 'false', 'reveal_phone_number': 'false'})
        req = get_provider('apollo').request('people_enrich', {'name': 'Jane Example', 'domain': 'www.example.org'}, ENV)
        self.assertEqual(parse_qs(urlsplit(req.url).query)['domain'], ['example.org'])
        for payload in ({'apollo_id': APOLLO_ID, 'linkedin_url': LINKEDIN}, {'name': 'Jane Example'}, {}, {'apollo_id': 'short'},
                        {'apollo_id': APOLLO_ID, 'email': EMAIL}, {'name': 'Jane', 'domain': 'example.org'}):
            with self.assertRaises(PolicyError, msg=payload):
                normalize_job({'provider': 'apollo', 'operation': 'people_enrich', 'payload': payload})
        out = parse('apollo', 'people_enrich', APOLLO_MATCH)
        self.assertEqual((out.assessment, out.email), ('candidate', EMAIL))
        row = out.fields['prospect']
        self.assertEqual((row['full_name'], row['name_complete'], row['email_verification']), ('Jane Example', True, 'valid'))
        self.assertEqual((row['company_website'], row['linkedin_url']), ('example.org', LINKEDIN))
        self.assertNotIn('request_id', json.dumps(out.fields))
        self.assertEqual(parse('apollo', 'people_enrich', {'person': None}).assessment, 'not_found')
        masked = {'person': {**APOLLO_MATCH['person'], 'email': 'jane.***@example.org', 'email_status': None}}
        self.assertEqual(parse('apollo', 'people_enrich', masked).assessment, 'not_found')
        nomatch = {'person': {**APOLLO_MATCH['person'], 'match_confidence': 'none'}}
        self.assertEqual(parse('apollo', 'people_enrich', nomatch).assessment, 'not_found')
        self.assertEqual(parse('apollo', 'people_enrich', {}).error, 'invalid_response')
        self.assertEqual(parse('apollo', 'people_enrich', {'error': 'x'}).error, 'provider_application_error')


class GooglePlacesTests(unittest.TestCase):
    def test_request(self):
        payload = normalize_job({'provider': 'google_places', 'operation': 'text_search', 'payload': {
            'text_query': 'real estate brokerage in Austin, TX', 'included_type': 'real_estate_agency', 'page_cursor': 'AeCr-x_1'}})['payload']
        req = get_provider('google_places').request('text_search', payload, ENV)
        self.assertEqual((req.method, req.url), ('POST', 'https://places.googleapis.com/v1/places:searchText'))
        self.assertEqual(json.loads(req.body), {'textQuery': 'real estate brokerage in Austin, TX', 'pageSize': 20,
                                                'regionCode': 'US', 'includedType': 'real_estate_agency', 'pageToken': 'AeCr-x_1'})
        self.assertEqual(req.headers['X-Goog-Api-Key'], SECRET)
        self.assertEqual(req.headers['X-Goog-FieldMask'], 'places.id,places.displayName,places.websiteUri,places.nationalPhoneNumber,'
                         'places.formattedAddress,places.addressComponents,places.types,nextPageToken')
        for bad in ({'text_query': 'x' * 201}, {'text_query': 'ok query', 'page_size': 21}, {'text_query': 'ok query', 'page_cursor': 'a b'},
                    {'text_query': 'ok query', 'included_type': 'Real Estate'}, {'text_query': 'ok query', 'pageToken': 'x'}, {}):
            with self.assertRaises(PolicyError, msg=bad):
                normalize_job({'provider': 'google_places', 'operation': 'text_search', 'payload': bad})

    def test_parse(self):
        out = parse('google_places', 'text_search', PLACES_OK)
        self.assertEqual(out.assessment, 'results')
        harbor, social, builder = out.fields['companies']
        self.assertEqual((harbor['domain'], harbor['phone'], harbor['city'], harbor['state']), ('harbor.example.org', '(512) 555-0100', 'Austin', 'TX'))
        self.assertEqual((harbor['postal_code'], harbor['country'], harbor['place_id'], harbor['source']),
                         ('78701', 'US', 'ChIJabcdefghij123', 'google_places'))
        self.assertEqual((social['domain'], builder['domain']), ('', ''))
        self.assertEqual(out.fields['next_page_cursor'], 'AeCrKXsZWzNVbPzO-MRWPu52jWO_Xx8a')
        self.assertEqual(parse('google_places', 'text_search', {}).assessment, 'not_found')
        self.assertEqual(parse('google_places', 'text_search', {'error': {'code': 400, 'status': 'INVALID_ARGUMENT'}}).error,
                         'provider_application_error')
        self.assertEqual(parse('google_places', 'text_search', {'places': {}}).error, 'invalid_response')
        self.assertEqual(parse('google_places', 'text_search', {'places': [{'id': 'bad'}]}).error, 'invalid_response')
        self.assertEqual(parse('google_places', 'text_search', {'error': {}}, 403).error, 'auth')


class ProspeoTests(unittest.TestCase):
    def test_search_request_and_parse(self):
        payload = normalize_job({'provider': 'prospeo', 'operation': 'people_search', 'payload': {'criteria': {
            'websites': ['www.example.org', 'example.net'], 'titles': ['CEO'], 'seniorities': ['C-Suite', 'Founder/Owner'],
            'max_person_per_company': 3}}})['payload']
        req = get_provider('prospeo').request('people_search', payload, ENV)
        self.assertEqual((req.url, req.headers['X-KEY']), ('https://api.prospeo.io/search-person', SECRET))
        self.assertEqual(json.loads(req.body), {'page': 1, 'filters': {
            'company': {'websites': {'include': ['example.net', 'example.org']}},
            'person_job_title': {'include': ['CEO'], 'match_mode': 'CONTAINS'},
            'person_seniority': {'include': ['Founder/Owner', 'C-Suite']}, 'max_person_per_company': 3}})
        out = parse('prospeo', 'people_search', PROSPEO_SEARCH)
        row = out.fields['prospects'][0]
        self.assertEqual((row['prospeo_id'], row['full_name'], row['linkedin_url'], row['company_website']),
                         (PROSPEO_ID, 'Jane Example', LINKEDIN, 'example.org'))
        self.assertNotIn('email', row)
        self.assertEqual((out.fields['total_results'], out.fields['free']), (1, False))
        for bad in ({'websites': ['a.example.org'] * 0}, {'titles': ['CEO'], 'seniorities': ['founder']}, {'page': 2},
                    {'websites': ['example.org'], 'page': 1001}, {'websites': ['example%d.org' % i for i in range(501)]},
                    {'websites': ['example.org'], 'title_match_mode': 'EXACT'}):
            with self.assertRaises(PolicyError, msg=bad):
                normalize_job({'provider': 'prospeo', 'operation': 'people_search', 'payload': {'criteria': bad}})

    def test_errors_and_enrich(self):
        self.assertEqual(parse('prospeo', 'people_search', {'error': True, 'error_code': 'NO_RESULTS'}, 400).assessment, 'not_found')
        self.assertEqual(parse('prospeo', 'people_search', {'error': True, 'error_code': 'INVALID_API_KEY'}, 400).error, 'auth')
        self.assertEqual(parse('prospeo', 'people_search', {'error': True, 'error_code': 'INSUFFICIENT_CREDITS'}, 400).error, 'quota')
        self.assertEqual(parse('prospeo', 'people_search', {'error': True, 'error_code': 'INVALID_FILTERS'}).error, 'provider_application_error')
        self.assertEqual(parse('prospeo', 'people_search', {'error': False, 'results': 'x'}).error, 'invalid_response')
        self.assertEqual(parse('prospeo', 'people_search', {'x': 1}, 400).error, 'http_error')
        req = get_provider('prospeo').request('enrich', {'name': 'Jane Example', 'domain': 'example.org'}, ENV)
        self.assertEqual(json.loads(req.body), {'only_verified_email': False, 'enrich_mobile': False,
                                                'data': {'full_name': 'Jane Example', 'company_website': 'example.org'}})
        out = parse('prospeo', 'enrich', PROSPEO_ENRICH)
        self.assertEqual((out.assessment, out.email, out.fields['prospect']['email_verification']), ('candidate', EMAIL, 'valid'))
        masked = {**PROSPEO_ENRICH, 'person': {**PROSPEO_ENRICH['person'], 'email': {'status': 'VERIFIED', 'revealed': True,
                                                                                    'email': 'jane.*****@example.org'}}}
        self.assertEqual(parse('prospeo', 'enrich', masked).assessment, 'not_found')
        self.assertEqual(parse('prospeo', 'enrich', {'error': True, 'error_code': 'NO_MATCH'}, 400).assessment, 'not_found')


class TombaTests(unittest.TestCase):
    def test_requests(self):
        req = get_provider('tomba').request('find', FIND, ENV)
        parts = urlsplit(req.url)
        self.assertEqual((parts.path, dict(parse_qsl(parts.query))),
                         ('/v1/email-finder', {'domain': 'example.org', 'first_name': 'Jane', 'last_name': 'Example'}))
        self.assertEqual((req.headers['X-Tomba-Key'], req.headers['X-Tomba-Secret']), (SECRET, SECRET))
        req = get_provider('tomba').request('linkedin_email', {'linkedin_url': 'https://uk.linkedin.com/in/jane-example?trk=x'}, ENV)
        self.assertEqual(dict(parse_qsl(urlsplit(req.url).query)), {'url': LINKEDIN})
        for bad in ({'linkedin_url': 'https://example.org/in/jane'}, {'linkedin_url': LINKEDIN, 'name': 'Jane Example'}, {}):
            with self.assertRaises(PolicyError, msg=bad):
                normalize_job({'provider': 'tomba', 'operation': 'linkedin_email', 'payload': bad})

    def test_parse(self):
        out = parse('tomba', 'find', TOMBA_FOUND)
        self.assertEqual((out.assessment, out.email, out.fields['score'], out.fields['finder_verification_status']),
                         ('candidate', EMAIL, 92, 'valid'))
        self.assertIn('catch_all', out.risk_flags)
        self.assertEqual(out.fields['prospect']['linkedin_url'], LINKEDIN)
        empty = {'data': {**TOMBA_FOUND['data'], 'email': None, 'score': 0}}
        self.assertEqual(parse('tomba', 'linkedin_email', empty).assessment, 'not_found')
        self.assertEqual(parse('tomba', 'find', {'errors': {'type': 'unknown_record', 'code': 400, 'message': SECRET}}, 400).assessment, 'not_found')
        self.assertEqual(parse('tomba', 'find', {'errors': {'type': 'claimed_email', 'code': 451}}, 451).assessment, 'not_found')
        self.assertEqual(parse('tomba', 'find', {'errors': {'type': 'authentication_failed', 'code': 400}}, 400).error, 'auth')
        self.assertEqual(parse('tomba', 'find', {'errors': {'type': 'params_invalid', 'code': 422}}, 422).error, 'provider_application_error')
        self.assertEqual(parse('tomba', 'find', {'data': {'first_name': 'x'}}).error, 'invalid_response')
        self.assertEqual(parse('tomba', 'find', {'data': {'email': 'not-an-email'}}).error, 'invalid_response')


class IcypeasTests(unittest.TestCase):
    def test_submit_poll_parse(self):
        p = get_provider('icypeas')
        req = p.request('find', FIND, ENV)
        self.assertEqual((req.method, req.url, req.headers['Authorization']), ('POST', 'https://app.icypeas.com/api/email-search', SECRET))
        self.assertEqual(json.loads(req.body), {'firstname': 'Jane', 'lastname': 'Example', 'domainOrCompany': 'example.org'})
        out = parse('icypeas', 'find', ICY_SUBMIT)
        self.assertEqual((out.assessment, out.job_url), ('pending', ICY_JOB))
        poll = p.poll_request(ICY_JOB, ENV)
        self.assertEqual((poll.method, poll.url, json.loads(poll.body)),
                         ('POST', 'https://app.icypeas.com/api/bulk-single-searchs/read', {'id': 'p_gfS5EB4liCGm90KDFC'}))
        for url in ('https://app.icypeas.com/api/email-search?id=x', 'https://example.org/api/bulk-single-searchs/read?id=x',
                    ICY_JOB + '&key=x'):
            with self.assertRaises(PolicyError):
                p.poll_request(url, ENV)
        self.assertEqual(parse('icypeas', 'find', icy_read('IN_PROGRESS')).assessment, 'pending')
        found = parse('icypeas', 'find', icy_read('DEBITED', [{'email': 'j@example.org', 'certainty': 'probable'},
                                                              {'email': EMAIL, 'certainty': 'ULTRA_SURE'}]))
        self.assertEqual((found.assessment, found.email, found.fields['certainty']), ('candidate', EMAIL, 'ultra_sure'))
        self.assertEqual(parse('icypeas', 'find', icy_read('DEBITED_NOT_FOUND')).assessment, 'not_found')
        self.assertEqual(parse('icypeas', 'find', icy_read('FOUND', [{'email': EMAIL, 'certainty': 'undeliverable'}])).assessment, 'not_found')
        self.assertEqual(parse('icypeas', 'find', icy_read('INSUFFICIENT_FUNDS')).error, 'quota')
        self.assertEqual(parse('icypeas', 'find', {'success': False, 'validationErrors': [{'field': 'x'}]}).error, 'provider_application_error')
        self.assertEqual(parse('icypeas', 'find', {'success': True, 'item': {'_id': 'bad id!', 'status': 'NONE'}}).error, 'invalid_response')
        self.assertEqual(parse('icypeas', 'find', {'success': True, 'items': []}).error, 'invalid_response')


class VerifierTests(unittest.TestCase):
    def test_reoon(self):
        req = get_provider('reoon').request('verify', {'email': EMAIL}, ENV)
        parts = urlsplit(req.url)
        self.assertEqual((parts.path, dict(parse_qsl(parts.query))), ('/api/v1/verify', {'email': EMAIL, 'key': SECRET, 'mode': 'power'}))
        self.assertEqual(req.timeout, 90)
        cases = {'safe': 'provider_valid', 'invalid': 'provider_invalid', 'disabled': 'provider_invalid', 'catch_all': 'catch_all',
                 'unknown': 'unknown', 'inbox_full': 'unknown', 'spamtrap': 'unknown', 'disposable': 'unknown', 'role_account': 'unknown'}
        for status, assessment in cases.items():
            out = parse('reoon', 'verify', {**REOON_SAFE, 'status': status})
            self.assertEqual(out.assessment, assessment, status)
            if status not in ('safe', 'invalid', 'unknown'):
                self.assertIn(status, out.risk_flags)
        self.assertEqual(parse('reoon', 'verify', {**REOON_SAFE, 'verification_mode': 'quick'}).assessment, 'unknown')
        self.assertEqual(parse('reoon', 'verify', {**REOON_SAFE, 'is_safe_to_send': False}).assessment, 'unknown')
        self.assertIn('role_account', parse('reoon', 'verify', {**REOON_SAFE, 'is_role_account': True}).risk_flags)
        self.assertEqual(parse('reoon', 'verify', {'status': 'error', 'reason': SECRET}).error, 'provider_application_error')
        self.assertEqual(parse('reoon', 'verify', {**REOON_SAFE, 'status': 'weird'}).error, 'invalid_response')
        self.assertEqual(parse('reoon', 'verify', {**REOON_SAFE, 'email': None}).error, 'invalid_response')

    def test_millionverifier(self):
        req = get_provider('millionverifier').request('verify', {'email': EMAIL}, ENV)
        self.assertEqual(dict(parse_qsl(urlsplit(req.url).query)), {'api': SECRET, 'email': EMAIL, 'timeout': '10'})
        for result, code, assessment in (('ok', 1, 'provider_valid'), ('catch_all', 2, 'catch_all'), ('unknown', 3, 'unknown'),
                                         ('disposable', 5, 'unknown'), ('invalid', 6, 'provider_invalid')):
            out = parse('millionverifier', 'verify', {**MV_OK, 'result': result, 'resultcode': code})
            self.assertEqual(out.assessment, assessment)
        self.assertIn('disposable', parse('millionverifier', 'verify', {**MV_OK, 'result': 'disposable', 'resultcode': 5}).risk_flags)
        risky = parse('millionverifier', 'verify', {**MV_OK, 'quality': 'risky', 'role': True})
        self.assertEqual(set(risky.risk_flags), {'quality_risky', 'role'})
        self.assertNotIn('credits', risky.fields)
        self.assertEqual(parse('millionverifier', 'verify', {**MV_OK, 'result': 'error', 'resultcode': 4, 'error': 'apikey_not_found'}).error, 'auth')
        self.assertEqual(parse('millionverifier', 'verify', {**MV_OK, 'result': 'error', 'error': 'Insufficient credits'}).error, 'quota')
        self.assertEqual(parse('millionverifier', 'verify', {**MV_OK, 'livemode': False}).error, 'provider_application_error')
        self.assertEqual(parse('millionverifier', 'verify', {**MV_OK, 'resultcode': 6}).error, 'invalid_response')
        self.assertEqual(parse('millionverifier', 'verify', {**MV_OK, 'email': ''}).error, 'invalid_response')


class ExaTests(unittest.TestCase):
    def test_request_parse(self):
        req = get_provider('exa').request('search', {'query': 'Founder at a real estate company in Austin, TX'}, ENV)
        self.assertEqual((req.url, req.headers['x-api-key']), ('https://api.exa.ai/search', SECRET))
        self.assertEqual(json.loads(req.body), {'query': 'Founder at a real estate company in Austin, TX', 'type': 'auto',
                                                'category': 'people', 'numResults': 10, 'userLocation': 'US'})
        with self.assertRaises(PolicyError):
            normalize_job({'provider': 'exa', 'operation': 'search', 'payload': {'query': 'x', 'page': 2}})
        with self.assertRaises(PolicyError):
            get_provider('exa').request('search', {'query': 'x', 'num': 10}, ENV)
        out = parse('exa', 'search', EXA_OK)
        self.assertEqual(len(out.fields['results']), 2)
        prospect = out.fields['prospects'][0]
        self.assertEqual((prospect['full_name'], prospect['linkedin_url'], prospect['source'], prospect['location']),
                         ('Jane Example', LINKEDIN, 'exa', 'Austin, Texas'))
        self.assertEqual(parse('exa', 'search', {'results': []}).assessment, 'not_found')
        self.assertEqual(parse('exa', 'search', {'error': 'bad'}).error, 'provider_application_error')
        self.assertEqual(parse('exa', 'search', {'results': 'x'}).error, 'invalid_response')


class ConfigTests(unittest.TestCase):
    def test_older_saved_config_migrates_with_new_providers_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = tmp + '/providers.json'
            old = default_config()
            for name in NEW:
                del old['providers'][name]
            old['providers']['clearout']['enabled'] = True
            with open(path, 'w') as fh:
                json.dump(old, fh)
            loaded = load_config(path)
            self.assertEqual(list(loaded['providers']), list(DEFAULT['providers']))
            self.assertTrue(loaded['providers']['clearout']['enabled'])
            self.assertFalse(any(loaded['providers'][n]['enabled'] for n in NEW))
            old['providers']['reoon'] = {'enabled': True, 'charges': {'verify': {'credits': '0.5'}}}
            with open(path, 'w') as fh:
                json.dump(old, fh)
            with self.assertRaises(PolicyError):
                load_config(path)


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

    def runner(self, responses):
        self.transport = FakeTransport(responses)
        return Runner(self.store, self.config, env=ENV, transport=self.transport, sleep=lambda _: None)

    def test_verify_routing_includes_new_verifiers_in_config_order(self):
        quotes = route_run(self.store, self.config, [{'provider': 'auto', 'operation': 'verify', 'payload': {'email': EMAIL}}], env=ENV)
        options = [q['provider'] for q in quotes['results'][0]['options']]
        verifiers = [p for p in options if 'verify' in get_provider(p).operations]
        self.assertEqual(verifiers[-7:], ['reoon', 'abstract', 'emailable', 'kickbox', 'bouncer', 'myemailverifier',
                                          'millionverifier'])
        self.assertLess(options.index('zerobounce'), options.index('reoon'))
        self.store.confirm_budget('millionverifier', 'credits', '1', 'synthetic free')
        plan = route_run(self.store, self.config, [{'provider': 'auto', 'operation': 'verify', 'payload': {'email': EMAIL}}], env=ENV)
        self.assertEqual(plan['results'][0]['provider'], 'millionverifier')
        self.store.confirm_budget('reoon', 'credits', '1', 'synthetic free')
        transport = FakeTransport([Response(200, REOON_SAFE)])
        done = route_run(self.store, self.config, [{'provider': 'auto', 'operation': 'verify', 'payload': {'email': EMAIL}}],
                         execute=True, env=ENV, transport=transport, sleep=lambda _: None)
        self.assertEqual((done['results'][0]['provider'], done['results'][0]['status']), ('reoon', 'completed'))
        self.assertEqual(self.store.assess(EMAIL)['assessment'], 'provider_valid')
        self.assertNotIn(SECRET, json.dumps(self.store.attempts()))

    def test_verifier_recipient_mismatch(self):
        self.store.confirm_budget('millionverifier', 'credits', '1', 'synthetic free')
        report = self.runner([Response(200, {**MV_OK, 'email': 'other@example.org'})]).run(
            [{'provider': 'millionverifier', 'operation': 'verify', 'payload': {'email': EMAIL}}], execute=True)
        self.assertEqual(report['results'][0]['outcome']['error'], 'recipient_mismatch')
        self.assertTrue(self.store.provider_state('millionverifier')['paused'])

    def test_prospeo_no_results_400_completes_without_pause(self):
        self.store.confirm_budget('prospeo', 'credits', '2', 'synthetic free')
        job = {'provider': 'prospeo', 'operation': 'people_search', 'payload': {'criteria': {'websites': ['example.org']}}}
        report = self.runner([Response(400, {'error': True, 'error_code': 'NO_RESULTS'})]).run([job], execute=True)
        self.assertEqual((report['results'][0]['status'], report['results'][0]['outcome']['assessment']), ('completed', 'not_found'))
        self.assertFalse(self.store.provider_state('prospeo').get('paused'))
        report = self.runner([Response(400, {'error': True, 'error_code': 'INVALID_API_KEY'})]).run(
            [{**job, 'payload': {'criteria': {'websites': ['example.net']}}}], execute=True)
        self.assertEqual(report['results'][0]['outcome']['error'], 'auth')
        self.assertTrue(self.store.provider_state('prospeo')['paused'])

    def test_apollo_search_enrich_collect_round_trip(self):
        self.store.confirm_budget('apollo', 'search_requests', '1', 'synthetic free')
        self.store.confirm_budget('apollo', 'credits', '1', 'synthetic free')
        from lead_research.discovery import apollo_enrich_jobs, apollo_jobs
        runner = self.runner([Response(200, APOLLO_SEARCH), Response(200, APOLLO_MATCH)])
        report = runner.run(apollo_jobs(['CEO'], ['Texas']), execute=True)
        self.assertEqual(report['results'][0]['status'], 'completed')
        found = collect(self.store)
        jobs = apollo_enrich_jobs(found['prospects'])
        self.assertEqual([j['payload'] for j in jobs], [{'apollo_id': APOLLO_ID}, {'apollo_id': '6775057df8360a0001a6852c'}])
        report = runner.run(jobs[:1], execute=True)
        self.assertEqual(report['results'][0]['status'], 'completed')
        self.assertEqual(self.transport.requests[1].method, 'POST')
        found = collect(self.store)
        jane = [p for p in found['prospects'] if p.get('apollo_id') == APOLLO_ID]
        self.assertEqual(len(jane), 1)
        jane = jane[0]
        self.assertEqual((jane['full_name'], jane['name_complete'], jane['email'], jane['email_source']),
                         ('Jane Example', True, EMAIL, 'apollo_finder'))
        self.assertEqual((jane['email_verification'], jane['linkedin_url'], jane['last_name_obfuscated']), ('valid', LINKEDIN, None))
        self.assertEqual({s['operation'] for s in jane['sources']}, {'people_search', 'people_enrich'})
        self.assertEqual(len(found['prospects']), 2)
        budgets = {(b['provider'], b['pool']): b['remaining'] for b in self.store.budgets()}
        self.assertEqual((budgets[('apollo', 'search_requests')], budgets[('apollo', 'credits')]), ('0', '0'))

    def test_places_and_tomba_collect(self):
        self.store.confirm_budget('google_places', 'enterprise_requests', '1', 'synthetic free')
        self.store.confirm_budget('tomba', 'credits', '1', 'synthetic free')
        from lead_research.discovery import places_jobs, tomba_linkedin_jobs
        runner = self.runner([Response(200, PLACES_OK), Response(200, TOMBA_FOUND)])
        runner.run(places_jobs([('Austin', 'TX')])[:1], execute=True)
        runner.run(tomba_linkedin_jobs([LINKEDIN]), execute=True)
        stored = self.store.attempts()
        self.assertEqual(stored[0]['outcome']['fields']['next_page_cursor'], PLACES_OK['nextPageToken'])
        found = collect(self.store)
        self.assertEqual({c['place_id'] for c in found['companies']}, {'ChIJabcdefghij123', 'ChIJsocial000000001', 'ChIJbuilder00000001'})
        self.assertEqual(found['prospects'][0]['email'], EMAIL)
        self.assertEqual((found['prospects'][0]['email_source'], found['prospects'][0]['email_verification']), ('tomba_finder', 'valid'))
        self.assertIn('catch_all', found['prospects'][0]['email_risk_flags'])

    def test_icypeas_pending_resume_post_and_capture(self):
        self.store.confirm_budget('icypeas', 'credits', '1', 'synthetic free')
        runner = self.runner([Response(200, ICY_SUBMIT), Response(200, icy_read('IN_PROGRESS')),
                              Response(200, icy_read('DEBITED', [{'email': EMAIL, 'certainty': 'very_sure'}]))])
        report = runner.run([{'provider': 'icypeas', 'operation': 'find', 'payload': FIND}], execute=True)
        self.assertEqual(report['results'][0]['status'], 'pending')
        attempt = self.store.attempts()[0]
        self.assertEqual(attempt['outcome']['job_url'], ICY_JOB)
        first = runner.resume(attempt['id'], execute=True)
        self.assertEqual(first['results'][0]['status'], 'pending')
        self.assertEqual(self.store.get_attempt(attempt['id'])['outcome']['job_url'], ICY_JOB)
        second = runner.resume(attempt['id'], execute=True)
        self.assertEqual(second['results'][0]['status'], 'completed')
        self.assertEqual([r.method for r in self.transport.requests], ['POST', 'POST', 'POST'])
        self.assertEqual(self.store.budgets()[0]['remaining'], '0')
        self.assertEqual(self.store.candidates()[0]['email'], EMAIL)
        prospect = collect(self.store)['prospects'][0]
        self.assertEqual((prospect['email_source'], prospect['full_name'], prospect['company_website']),
                         ('icypeas_finder', 'Jane Example', 'example.org'))
        with self.assertRaisesRegex(PolicyError, 'only_pending'):
            runner.resume(attempt['id'], execute=True)


if __name__ == '__main__':
    unittest.main()
