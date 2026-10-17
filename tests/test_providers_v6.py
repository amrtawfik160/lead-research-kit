"""v6 adapters: offline fixtures only (synthetic identities, reserved domains, fake keys)."""
import unittest
from datetime import date
from urllib.parse import parse_qs, urlsplit
import json

from lead_research.configuration import DEFAULT, MIGRATION_ADDED_PROVIDERS
from lead_research.models import PolicyError, ProviderError, Response
from lead_research.providers import get_provider
from lead_research.records import SOURCES, fetch_source, parse_ct_rows
from lead_research.transport import OFFICIAL_ORIGINS

SECRET = 'fixture-secret'
KEYS = {'quickemailverification': 'QUICKEMAILVERIFICATION_API_KEY', 'emailvalidation': 'EMAILVALIDATION_API_KEY',
        'emaillistverify': 'EMAILLISTVERIFY_API_KEY', 'enrow': 'ENROW_API_KEY', 'lusha': 'LUSHA_API_KEY',
        'zenserp': 'ZENSERP_API_KEY', 'linkup': 'LINKUP_API_KEY', 'youcom': 'YDC_API_KEY'}
ENV = {v: SECRET for v in KEYS.values()}
EMAIL = 'jane.example@example.org'
FIND = {'name': 'Jane Example', 'domain': 'example.org'}
LINKEDIN_TITLE = 'Jane Example - Broker Owner - Harbor Realty | LinkedIn'
LINKEDIN_URL = 'https://www.linkedin.com/in/jane-example'


def parse(provider, data, operation='verify', status=200, headers=None):
    return get_provider(provider).parse(operation, Response(status, data, headers or {}))


class RegistryTests(unittest.TestCase):
    def test_registered_disabled_and_official(self):
        for name, key in KEYS.items():
            p = get_provider(name)
            self.assertIn(p.origin, OFFICIAL_ORIGINS)
            self.assertEqual(p.auth_env, (key,))
            self.assertFalse(DEFAULT['providers'][name]['enabled'])
            self.assertEqual(set(DEFAULT['providers'][name]['charges']), set(p.operations))
            self.assertIn(name, MIGRATION_ADDED_PROVIDERS)
            op = p.operations[0]
            payload = {'email': EMAIL} if op == 'verify' else FIND if op == 'find' else {'query': 'Harbor Realty Austin'}
            with self.assertRaisesRegex(ProviderError, '^missing_auth$'):
                p.request(op, payload, {})
            req = p.request(op, payload, ENV)
            self.assertTrue(req.url.startswith(p.origin + '/'))
            for status, code in ((401, 'auth'), (402, 'quota'), (429, 'rate_limit'), (503, 'provider_error')):
                self.assertEqual(parse(name, {'x': SECRET}, op, status).error, code)


class VerifierTests(unittest.TestCase):
    def test_qev(self):
        req = get_provider('quickemailverification').request('verify', {'email': EMAIL}, ENV)
        q = parse_qs(urlsplit(req.url).query)
        self.assertEqual((q['email'], q['apikey']), ([EMAIL], [SECRET]))
        base = {'result': 'valid', 'reason': 'accepted_email', 'disposable': 'false', 'accept_all': 'false', 'role': 'false',
                'free': 'false', 'email': EMAIL, 'safe_to_send': 'true', 'success': 'true', 'message': ''}
        out = parse('quickemailverification', base, headers={'X-QEV-Remaining-Credits': '99'})
        self.assertEqual((out.assessment, out.email, out.fields['remaining_credits']), ('provider_valid', EMAIL, 99))
        self.assertEqual(parse('quickemailverification', {**base, 'accept_all': 'true'}).assessment, 'catch_all')
        self.assertEqual(parse('quickemailverification', {**base, 'safe_to_send': 'false'}).assessment, 'unknown')
        self.assertEqual(parse('quickemailverification', {**base, 'result': 'invalid', 'reason': 'rejected_email'}).assessment, 'provider_invalid')
        self.assertEqual(parse('quickemailverification', {'success': 'false', 'message': 'Low credit balance'}).error, 'quota')
        self.assertEqual(parse('quickemailverification', {**base, 'result': 'weird'}).error, 'invalid_response')

    def test_emailvalidation(self):
        req = get_provider('emailvalidation').request('verify', {'email': EMAIL}, ENV)
        self.assertEqual(req.headers['apikey'], SECRET)
        self.assertNotIn(SECRET, req.url)
        base = {'email': EMAIL, 'state': 'deliverable', 'reason': 'valid_mailbox', 'status': 'valid', 'role': False,
                'disposable': False, 'free': False, 'catch_all': None, 'score': 0.9}
        self.assertEqual(parse('emailvalidation', base).assessment, 'provider_valid')
        out = parse('emailvalidation', {**base, 'status': 'catch_all', 'reason': 'low_deliverability'})
        self.assertEqual((out.assessment, 'catch_all' in out.risk_flags), ('catch_all', True))
        self.assertEqual(parse('emailvalidation', {**base, 'status': 'invalid', 'state': 'undeliverable'}).assessment, 'provider_invalid')
        self.assertEqual(parse('emailvalidation', {**base, 'status': 'do_not_mail'}).assessment, 'unknown')
        self.assertEqual(parse('emailvalidation', {**base, 'status': 'unknown', 'state': 'unknown'}).assessment, 'unknown')

    def test_emaillistverify(self):
        req = get_provider('emaillistverify').request('verify', {'email': EMAIL}, ENV)
        self.assertTrue(req.url.startswith('https://api.emaillistverify.com/api/verifyEmailDetailed?'))
        base = {'email': EMAIL, 'result': 'ok', 'isRole': False, 'isFree': False, 'isNoReply': False}
        self.assertEqual(parse('emaillistverify', base).assessment, 'provider_valid')
        self.assertEqual(parse('emaillistverify', {**base, 'result': 'ok_for_all'}).assessment, 'catch_all')
        self.assertEqual(parse('emaillistverify', {**base, 'result': 'email_disabled'}).assessment, 'provider_invalid')
        self.assertEqual(parse('emaillistverify', {**base, 'result': 'antispam_system'}).assessment, 'unknown')
        self.assertEqual(parse('emaillistverify', {**base, 'result': 'error_credit'}).error, 'quota')
        self.assertIn('role', parse('emaillistverify', {**base, 'isRole': True}).risk_flags)


class FinderTests(unittest.TestCase):
    def test_enrow_submit_poll(self):
        p = get_provider('enrow')
        req = p.request('find', FIND, ENV)
        self.assertEqual(json.loads(req.body), {'fullname': 'Jane Example', 'company_domain': 'example.org',
                                                'settings': {'country_code': 'US'}})
        out = parse('enrow', {'message': 'Search started', 'id': 'a1b2-c3d4', 'credits_used': 1}, 'find')
        self.assertEqual(out.assessment, 'pending')
        self.assertEqual(out.job_url, 'https://api.enrow.io/email/find/single?id=a1b2-c3d4')
        poll = p.poll_request(out.job_url, ENV)
        self.assertEqual((poll.method, poll.url), ('GET', out.job_url))
        with self.assertRaises(PolicyError):
            p.poll_request('https://evil.example/email/find/single?id=x', ENV)
        self.assertEqual(parse('enrow', {'qualification': 'ongoing'}, 'find', 202).assessment, 'pending')
        found = parse('enrow', {'email': EMAIL, 'qualification': 'valid', 'credits_used': 1,
                                'info': {'firstname': 'Jane', 'lastname': 'Example', 'company_domain': 'example.org'}}, 'find')
        self.assertEqual((found.assessment, found.email, found.fields['finder_verification_status']), ('candidate', EMAIL, 'valid'))
        self.assertEqual(parse('enrow', {'qualification': 'invalid', 'credits_used': 0}, 'find').assessment, 'not_found')
        self.assertEqual(parse('enrow', {'reason': 'Insufficient credits', 'success': False}, 'find', 402).error, 'quota')

    def test_lusha(self):
        req = get_provider('lusha').request('find', FIND, ENV)
        q = parse_qs(urlsplit(req.url).query)
        self.assertEqual((q['firstName'], q['lastName'], q['companyDomain']), (['Jane'], ['Example'], ['example.org']))
        self.assertEqual(req.headers['api_key'], SECRET)
        data = {'contact': {'error': None, 'isCreditCharged': True, 'data': {
            'firstName': 'Jane', 'lastName': 'Example', 'fullName': 'Jane Example',
            'emailAddresses': [{'address': 'jane@private.example', 'type': 'private'},
                               {'address': EMAIL, 'type': 'work', 'emailConfidence': 'A+'}],
            'jobTitle': {'title': 'Broker Owner'}, 'company': {'name': 'Harbor Realty', 'fqdn': 'example.org'},
            'socialLinks': {'linkedin': LINKEDIN_URL}}}}
        out = parse('lusha', data, 'find')
        self.assertEqual((out.assessment, out.email, out.fields['prospect']['email_confidence']), ('candidate', EMAIL, 'A+'))
        self.assertEqual(out.fields['finder_verification_status'], 'unknown')
        self.assertEqual(parse('lusha', {'contact': {'error': {'code': 404, 'name': 'EMPTY'}, 'data': None}}, 'find').assessment, 'not_found')
        self.assertEqual(parse('lusha', {'contact': {'error': {'code': 402}, 'data': None}}, 'find').error, 'quota')


class SearchTests(unittest.TestCase):
    def check(self, name, data):
        out = parse(name, data, 'search')
        self.assertEqual(out.assessment, 'results')
        self.assertEqual(out.fields['results'][0]['link'], LINKEDIN_URL)
        self.assertEqual(len(out.fields['prospects']), 1)
        return out

    def test_zenserp(self):
        req = get_provider('zenserp').request('search', {'query': 'Harbor Realty Austin'}, ENV)
        self.assertEqual(req.headers['apikey'], SECRET)
        out = self.check('zenserp', {'organic': [
            {'position': 1, 'title': LINKEDIN_TITLE, 'url': LINKEDIN_URL, 'description': 'Austin, Texas. Broker Owner.'},
            {'position': 2, 'questions': [{'question': 'What?'}]},
            {'position': 3, 'title': 'Harbor Realty', 'url': 'https://example.org/', 'description': 'Brokerage'}]})
        self.assertEqual(len(out.fields['results']), 2)
        self.assertEqual(parse('zenserp', {'organic': []}, 'search').assessment, 'not_found')
        self.assertEqual(parse('zenserp', {}, 'search').error, 'invalid_response')

    def test_linkup(self):
        req = get_provider('linkup').request('search', {'query': 'Harbor Realty Austin'}, ENV)
        self.assertEqual(json.loads(req.body)['outputType'], 'searchResults')
        self.check('linkup', {'results': [{'type': 'text', 'name': LINKEDIN_TITLE, 'url': LINKEDIN_URL, 'content': 'Broker Owner'}]})

    def test_youcom(self):
        req = get_provider('youcom').request('search', {'query': 'Harbor Realty Austin'}, ENV)
        self.assertEqual(req.headers['X-API-Key'], SECRET)
        self.check('youcom', {'results': {'web': [{'url': LINKEDIN_URL, 'title': LINKEDIN_TITLE, 'description': 'd',
                                                   'snippets': ['Broker Owner at Harbor Realty']}], 'news': []},
                              'metadata': {'latency': 0.3}})
        self.assertEqual(parse('youcom', {'detail': 'x'}, 'search').error, 'invalid_response')


CT_ROWS = [
    {'name': 'HARBORLIGHT HOMES LLC', 'type': 'LIMITED LIABILITY COMPANY', 'businessname': 'HARBORLIGHT HOMES LLC',
     'credentialtype': 'REB', 'fullcredentialcode': 'REB.0900001', 'status': 'ACTIVE', 'active': '1',
     'issuedate': '2026-10-02T00:00:00.000', 'expirationdate': '2028-11-30T00:00:00.000', 'city': 'WEST HARTFORD', 'state': 'CT'},
    {'name': 'JASPER D QUILLON', 'type': 'INDIVIDUAL', 'dba': 'JASPER QUILLON', 'credentialtype': 'REB', 'status': 'ACTIVE',
     'active': '1', 'city': 'ORANGE', 'state': 'CT'},
    {'name': 'OLD FIRM INC', 'type': 'CORPORATION', 'credentialtype': 'REB', 'status': 'EXPIRED', 'active': '0', 'state': 'CT'},
    {'name': 'DESIGNEE CO LLC', 'type': 'LIMITED LIABILITY COMPANY', 'dba': 'SEE REB#1', 'credentialtype': 'REB',
     'status': 'QUALIFIED', 'active': '1', 'state': 'CT'},
    {'name': 'LAPSED REALTY LLC', 'type': 'LIMITED LIABILITY COMPANY', 'credentialtype': 'REB', 'status': 'ACTIVE IN RENEWAL',
     'active': '1', 'expirationdate': '2020-01-01T00:00:00.000', 'state': 'CT'},
]


class FakeGetter:
    def __init__(self, rows):
        self.rows, self.urls = rows, []

    def get_json(self, url):
        self.urls.append(url)
        return self.rows if len(self.urls) == 1 else []


class ConnecticutTests(unittest.TestCase):
    def test_parse_firms_only(self):
        companies, prospects = parse_ct_rows(CT_ROWS, date(2026, 10, 17))
        self.assertEqual(prospects, [])
        self.assertEqual([c['organization'] for c in companies], ['Harborlight Homes LLC'])
        self.assertEqual((companies[0]['city'], companies[0]['state'], companies[0]['source']), ('West Hartford', 'CT', 'ct-dls'))
        self.assertIn('Public Domain', SOURCES['ct-dls']['terms_note'])
