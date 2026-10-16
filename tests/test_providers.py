"""Offline contracts use synthetic identities and credentials only."""
import json
import unittest
from urllib.parse import parse_qs, urlsplit

from lead_research.models import PolicyError, ProviderError, Response
from lead_research.providers import PROVIDERS, get_provider

ENV = {'HUNTER_API_KEY': 'fixture-secret', 'VERIFALIA_TOKEN': 'fixture-secret',
       'CLEAROUT_API_KEY': 'fixture-secret', 'ZEROBOUNCE_API_KEY': 'fixture-secret',
       'SERPER_API_KEY': 'fixture-secret', 'BRAVE_API_KEY': 'fixture-secret'}
# Lead-source operations take their own payload shapes (see tests/test_discovery.py).
OPERATION_PAYLOADS = {'discover': {'filters': {'query': 'real estate companies in Texas'}},
                      'domain_search': {'domain': 'example.org'}, 'search': {'query': 'site:linkedin.com/in "CEO"'},
                      'places': {'area': 'Austin', 'state': 'TX'}}
PAYLOAD = {'email': 'alex@example.org', 'domain': 'example.org', 'full_name': 'Alex Example',
           'first_name': 'Alex', 'last_name': 'Example'}


class ProviderTests(unittest.TestCase):
    def parse(self, provider, data, operation='verify', status=200, headers=None):
        return get_provider(provider).parse(operation, Response(status, data, headers or {}))

    def test_contract_all_six(self):
        self.assertEqual(set(PROVIDERS), {'hunter', 'verifalia', 'clearout', 'zerobounce', 'hunter-insight', 'disify',
                                          'serper', 'brave', 'overpass'})
        for p in PROVIDERS.values():
            for operation in p.operations:
                request = p.request(operation, OPERATION_PAYLOADS.get(operation, PAYLOAD), ENV)
                self.assertTrue(request.url.startswith('https://'))
                self.assertGreater(p.min_spacing[operation], 0)
            with self.assertRaisesRegex(PolicyError, '^unsupported_operation$'):
                p.request('batch', PAYLOAD, ENV)
            with self.assertRaises(PolicyError):
                p.parse('batch', Response(200, {}))
        with self.assertRaisesRegex(PolicyError, '^unsupported_provider$'):
            get_provider('fixture-secret')
        self.assertEqual(PROVIDERS['hunter'].independence_group, PROVIDERS['hunter-insight'].independence_group)

    def test_missing_and_injected_auth(self):
        for name in ('hunter', 'verifalia', 'clearout', 'zerobounce'):
            p = get_provider(name)
            with self.assertRaisesRegex(ProviderError, '^missing_auth$'):
                p.request(p.operations[0], PAYLOAD, {})
        with self.assertRaisesRegex(PolicyError, '^invalid_auth$'):
            get_provider('hunter').request('verify', PAYLOAD, {'HUNTER_API_KEY': 'abc\r\nX: bad'})
        req = get_provider('verifalia').request('verify', PAYLOAD, {'VERIFALIA_USERNAME': 'fake', 'VERIFALIA_PASSWORD': 'fake'})
        self.assertTrue(req.headers['Authorization'].startswith('Basic '))

    def test_http_failures_all_adapters(self):
        for p in PROVIDERS.values():
            for status, code in ((401, 'auth'), (403, 'auth'), (402, 'quota'), (429, 'rate_limit'), (524, 'provider_error'), (404, 'http_error')):
                out = p.parse(p.operations[0], Response(status, {'secret': 'fixture-secret'}))
                self.assertEqual((out.assessment, out.error), ('error', code))
                self.assertNotIn('fixture-secret', repr(out))
            for data in ({}, [], None, {'error': 'fixture-secret'}, {'errors': ['fixture-secret']}):
                out = p.parse(p.operations[0], Response(200, data))
                self.assertEqual(out.assessment, 'error')
                self.assertNotIn('fixture-secret', repr(out))
            self.assertEqual(p.parse(p.operations[0], Response(200, {}, json_valid=False)).error, 'invalid_response')

    def test_hunter_requests_and_statuses(self):
        p = get_provider('hunter')
        req = p.request('find', PAYLOAD, ENV)
        self.assertIn('/v2/email-finder?', req.url)
        self.assertNotIn('fixture-secret', req.url)
        self.assertEqual(req.headers['X-API-KEY'], 'fixture-secret')
        for status, assessment in (('valid', 'provider_valid'), ('invalid', 'provider_invalid'), ('accept_all', 'catch_all'), ('unknown', 'unknown')):
            out = self.parse('hunter', {'data': {'status': status, 'email': PAYLOAD['email'], 'disposable': True, 'smtp_check': False}})
            self.assertEqual(out.assessment, assessment)
            self.assertIn('disposable', out.risk_flags)
            self.assertIs(out.fields['smtp_check'], False)
        out = self.parse('hunter', {'data': {'email': PAYLOAD['email'], 'verification': {'status': 'valid'}}}, 'find')
        self.assertEqual(out.assessment, 'candidate')
        self.assertFalse(out.fields['verification_is_fresh'])
        self.assertEqual(self.parse('hunter', {'data': {'email': None}}, 'find').assessment, 'not_found')
        self.assertEqual(self.parse('hunter', {'data': {'status': 'fixture-secret'}}).error, 'invalid_response')

    def test_hunter_balances_require_manual_free_attestation(self):
        p = get_provider('hunter')
        data = {'plan_name': 'Free', 'requests': {'credits': {'remaining': '1.25'},
                'searches': {'remaining': '2'}, 'verifications': {'remaining': 3}}}
        result = p.parse_balance(Response(200, {'data': data}))
        self.assertEqual(result['free_pools'], {})
        self.assertEqual(result['metadata'], {'plan': 'Free', 'reported_credits': '1.25',
            'find_ops': 2, 'verify_ops': 3, 'manual_free_attestation_required': True})
        # Even an explicitly Free plan with paid add-ons must not auto-fund.
        data['credit_packs'] = [{'remaining': '900', 'type': 'paid'}]
        self.assertEqual(p.parse_balance(Response(200, {'data': data}))['free_pools'], {})
        data['plan_name'] = 'Paid'
        result = p.parse_balance(Response(200, {'data': data}))
        self.assertEqual(result['free_pools'], {})
        self.assertEqual(result['metadata']['reported_credits'], '1.25')
        self.assertEqual(result['metadata']['plan'], 'non_free')
        # Ignore obsolete top-level credits and advertised quotas; neither proves remaining funds.
        data = {'plan_name': 'Free', 'credits': '99',
                'requests': {'searches': {'available': 100, 'used': 1}}}
        result = p.parse_balance(Response(200, {'data': data}))
        self.assertEqual(result['free_pools'], {})
        self.assertNotIn('reported_credits', result['metadata'])
        self.assertNotIn('find_ops', result['metadata'])
        data = {'plan_name': 'Free', 'requests': {'credits': {'remaining': 'NaN'},
                'searches': {'remaining': '-1'}, 'verifications': {'remaining': '3.5'}}}
        result = p.parse_balance(Response(200, {'data': data}))
        self.assertEqual(result['metadata'], {'plan': 'Free', 'manual_free_attestation_required': True})

    def test_verifalia_post_and_poll(self):
        p = get_provider('verifalia')
        req = p.request('verify', PAYLOAD, ENV)
        self.assertEqual(req.method, 'POST')
        self.assertEqual(json.loads(req.body), {'entries': [{'inputData': PAYLOAD['email']}], 'quality': 'Standard'})
        url = 'https://api-1.verifalia.com/v2.7/email-validations/fixture-job'
        out = self.parse('verifalia', None, status=202, headers={'Location': url})
        self.assertEqual((out.assessment, out.job_url), ('pending', url))
        self.assertEqual(p.poll_request(url, ENV).method, 'GET')
        for bad in ('https://evil.example.org/job', '//api-1.verifalia.com/job',
                    'http://api-1.verifalia.com/v2.7/email-validations/job', url + '?api_key=fixture-secret',
                    url + '#fragment', url.replace('https://', 'https://fake:fixture-secret@'),
                    url.replace('.com/', '.com:443/'), 'https://127.0.0.1/job', None):
            out = self.parse('verifalia', None, status=202, headers={'Location': bad})
            self.assertEqual(out.error, 'unsafe_job_url')
            self.assertNotIn('fixture-secret', repr(out))
            with self.assertRaises((PolicyError, TypeError)):
                p.poll_request(bad, ENV)

    def test_verifalia_classifications(self):
        for classification, status, assessment in (('Deliverable', 'Success', 'provider_valid'),
                ('Undeliverable', 'MailboxDoesNotExist', 'provider_invalid'), ('Risky', 'ServerIsCatchAll', 'unknown'),
                ('Unknown', 'Timeout', 'unknown'), ('Deliverable', 'NovelStatus', 'unknown')):
            out = self.parse('verifalia', {'entries': {'data': [{'classification': classification, 'status': status, 'inputData': PAYLOAD['email']}]}})
            self.assertEqual(out.assessment, assessment)
        self.assertEqual(get_provider('verifalia').parse_balance(Response(200, {'freeCredits': '5.25', 'creditPacks': 999}))['free_pools'], {'free_credits': '5.25'})

    def test_clearout_status_risks(self):
        for status, expected in (('valid', 'provider_valid'), ('invalid', 'provider_invalid'), ('catch_all', 'catch_all'), ('unknown', 'unknown')):
            out = self.parse('clearout', {'status': 'success', 'data': {'email_address': PAYLOAD['email'], 'status': status, 'safe_to_send': 'risky', 'reason': 'disposable', 'role': True}})
            self.assertEqual(out.assessment, expected)
            self.assertIn('safe_to_send_risky', out.risk_flags)
            self.assertIn('disposable', out.risk_flags)
        self.assertEqual(self.parse('clearout', {'status': 'error', 'message': 'fixture-secret'}).error, 'provider_application_error')
        self.assertEqual(get_provider('clearout').parse_balance(Response(200, {'status': 'success', 'data': {'available_credits': '99'}}))['free_pools'], {})

    def test_zerobounce_safe_query_and_statuses(self):
        req = get_provider('zerobounce').request('verify', PAYLOAD, ENV)
        query = parse_qs(urlsplit(req.url).query, keep_blank_values=True)
        self.assertEqual(query['ip_address'], [''])
        self.assertEqual(query['activity_data'], ['false'])
        self.assertEqual(query['verify_plus'], ['false'])
        self.assertEqual(query['timeout'], ['10'])
        self.assertEqual(req.timeout, 10)
        for status, assessment in (('valid', 'provider_valid'), ('invalid', 'provider_invalid'), ('catch-all', 'catch_all'), ('unknown', 'unknown'), ('spamtrap', 'unknown'), ('abuse', 'unknown'), ('do_not_mail', 'unknown')):
            out = self.parse('zerobounce', {'address': PAYLOAD['email'], 'status': status, 'sub_status': 'role_based', 'catchall_domain': True})
            self.assertEqual(out.assessment, assessment)
            self.assertIn('role_based', out.risk_flags)
            self.assertIn('catchall_domain', out.risk_flags)
        for confidence in ('high', 'medium', 'low', 'undetermined'):
            out = self.parse('zerobounce', {'email': PAYLOAD['email'], 'email_confidence': confidence}, 'find')
            self.assertEqual(out.assessment, 'candidate')
        self.assertEqual(self.parse('zerobounce', {'email_confidence': 'undetermined'}, 'find').assessment, 'not_found')

    def test_strict_zerobounce_balance(self):
        p = get_provider('zerobounce')
        self.assertEqual(p.parse_balance(Response(200, {'Credits': '12'}))['metadata']['total_credits'], 12)
        self.assertEqual(p.parse_balance(Response(200, {'Credits': '0'}))['free_pools'], {})
        for bad in (1.2, True, '-2', '0012', '1.0', '1e2', '+2', ' 2', '2 ', '٢', None):
            with self.assertRaisesRegex(ProviderError, '^invalid_response$'):
                p.parse_balance(Response(200, {'Credits': bad}))
        with self.assertRaisesRegex(ProviderError, '^auth$'):
            p.parse_balance(Response(200, {'Credits': '-1'}))

    def test_signals_are_not_mailbox_proof(self):
        for name, body in (('hunter-insight', {'data': {'regexp': True, 'mx_records': True, 'disposable': True}}),
                           ('disify', {'format': True, 'dns': True, 'disposable': True})):
            out = self.parse(name, body, 'signals')
            self.assertEqual(out.assessment, 'signals')
            self.assertIn('disposable', out.risk_flags)
            req = get_provider(name).request('signals', PAYLOAD, {})
            self.assertFalse(req.headers)
            with self.assertRaisesRegex(PolicyError, 'unsupported_operation'):
                get_provider(name).balance_request({})
        self.assertIn('alex%40example.org', get_provider('disify').request('signals', PAYLOAD, {}).url)

    def test_redaction_arbitrary_provider_text(self):
        data = {'status': 'valid', 'address': PAYLOAD['email'], 'sub_status': 'fixture-secret', 'message': 'fixture-secret', 'url': 'https://example.org/?token=fixture-secret'}
        out = self.parse('zerobounce', data)
        self.assertNotIn('fixture-secret', repr(out))
        for p in PROVIDERS.values():
            if p.auth_env:
                with self.assertRaisesRegex(ProviderError, 'provider_application_error'):
                    p.parse_balance(Response(200, {'error': 'fixture-secret'}))


if __name__ == '__main__':
    unittest.main()

class SharedPayloadTests(unittest.TestCase):
    def test_name_contract(self):
        payload = {'name': 'Alex van Example', 'domain': 'example.org'}
        hunter = get_provider('hunter').request('find', payload, ENV)
        self.assertEqual(parse_qs(urlsplit(hunter.url).query)['full_name'], ['Alex van Example'])
        zero = get_provider('zerobounce').request('find', payload, ENV)
        self.assertEqual(parse_qs(urlsplit(zero.url).query)['last_name'], ['van Example'])

    def test_non_scalar_statuses_fail_closed(self):
        for body in ({'status': {}}, {'status': []}):
            self.assertEqual(get_provider('zerobounce').parse('verify', Response(200, body)).assessment, 'error')
        self.assertEqual(get_provider('hunter').parse('verify', Response(200, {'data': {'status': {}}})).assessment, 'error')
        self.assertEqual(get_provider('verifalia').parse('verify', Response(200, {'entries': {'data': [{'classification': {}}]}})).assessment, 'error')


class RealResponseShapeTests(unittest.TestCase):
    def test_missing_recipient_never_produces_verification(self):
        fixtures = {
            'hunter': {'data': {'status': 'valid'}},
            'verifalia': {'entries': {'data': [{'classification': 'Deliverable', 'status': 'Success'}]}},
            'clearout': {'status': 'success', 'data': {'status': 'valid'}},
            'zerobounce': {'status': 'valid'},
        }
        for provider, data in fixtures.items():
            with self.subTest(provider=provider):
                out = get_provider(provider).parse('verify', Response(200, data))
                self.assertEqual((out.assessment, out.error), ('error', 'invalid_response'))
        for provider, data in (
            ('hunter', {'data': {'status': 'valid', 'email': 'malformed'}}),
            ('clearout', {'status': 'success', 'data': {'status': 'valid', 'email_address': 'malformed'}}),
            ('zerobounce', {'status': 'valid', 'address': 'malformed'}),
        ):
            self.assertEqual(get_provider(provider).parse('verify', Response(200, data)).error, 'invalid_response')

    def test_actual_finder_field_and_omitted_email(self):
        p = get_provider('zerobounce')
        for confidence in ('high', 'medium', 'low', 'undetermined'):
            out = p.parse('find', Response(200, {'email': PAYLOAD['email'], 'email_confidence': confidence}))
            self.assertEqual(out.assessment, 'candidate')
            self.assertEqual(out.fields['confidence'], confidence)
        out = p.parse('find', Response(200, {'email_confidence': 'undetermined'}))
        self.assertEqual(out.assessment, 'not_found')
        self.assertIsNone(out.email)
        self.assertEqual(p.parse('find', Response(200, {'email_confidence': 'high'})).error, 'invalid_response')
        self.assertEqual(p.parse('find', Response(200, {'email': PAYLOAD['email'], 'confidence': 'high'})).assessment, 'candidate')

    def test_actual_clearout_and_verifalia_recipient_names(self):
        for field in ('email_address', 'email'):
            out = get_provider('clearout').parse('verify', Response(200, {
                'status': 'success', 'data': {'status': 'valid', field: PAYLOAD['email']}}))
            self.assertEqual((out.assessment, out.email), ('provider_valid', PAYLOAD['email']))
        for field in ('inputData', 'emailAddress'):
            out = get_provider('verifalia').parse('verify', Response(200, {'entries': {'data': [
                {'classification': 'Deliverable', 'status': 'Success', field: PAYLOAD['email']}]}}))
            self.assertEqual((out.assessment, out.email), ('provider_valid', PAYLOAD['email']))

    def test_zerobounce_integer_balances_and_auth(self):
        p = get_provider('zerobounce')
        for total in (0, 12, '0', '12'):
            out = p.parse_balance(Response(200, {'Credits': total}))
            self.assertEqual(out['metadata']['total_credits'], int(total))
            self.assertEqual(out['free_pools'], {})
        for invalid in (-1, '-1'):
            with self.assertRaisesRegex(ProviderError, '^auth$'):
                p.parse_balance(Response(200, {'Credits': invalid}))

    def test_non_delivery_negative_status_is_unknown_not_invalid(self):
        for status in ('spamtrap', 'abuse', 'do_not_mail'):
            out = get_provider('zerobounce').parse('verify', Response(200, {'status': status, 'address': PAYLOAD['email']}))
            self.assertEqual(out.assessment, 'unknown')
            self.assertIn(status, out.risk_flags)

    def test_benign_substatus_preserved_without_false_risk(self):
        p = get_provider('zerobounce')
        for substatus in ('', 'alias_address', 'alternate', 'gold', 'leading_period_removed'):
            out = p.parse('verify', Response(200, {'status': 'valid', 'address': PAYLOAD['email'], 'sub_status': substatus}))
            self.assertEqual(out.fields['sub_status'], substatus)
            self.assertEqual(out.risk_flags, ())
            flagged = p.parse('verify', Response(200, {'status': 'valid', 'address': PAYLOAD['email'],
                'sub_status': substatus, 'catchall_domain': True}))
            self.assertEqual(flagged.risk_flags, ('catchall_domain',))
        for substatus in ('role_based', 'disposable', 'toxic', 'possible_trap', 'accept_all'):
            out = p.parse('verify', Response(200, {'status': 'valid', 'address': PAYLOAD['email'], 'sub_status': substatus}))
            self.assertIn(substatus, out.risk_flags)

    def test_free_webmail_are_information_not_risk(self):
        fixtures = {
            'hunter': {'data': {'status': 'valid', 'email': PAYLOAD['email'], 'free': True, 'webmail': True}},
            'clearout': {'status': 'success', 'data': {'status': 'valid', 'email_address': PAYLOAD['email'], 'free': True, 'webmail': True}},
            'verifalia': {'entries': {'data': [{'classification': 'Deliverable', 'status': 'Success',
                'emailAddress': PAYLOAD['email'], 'free': True, 'webmail': True}]}},
            'zerobounce': {'status': 'valid', 'address': PAYLOAD['email'], 'free': True, 'webmail': True},
        }
        for provider, body in fixtures.items():
            out = get_provider(provider).parse('verify', Response(200, body))
            self.assertEqual(out.assessment, 'provider_valid')
            self.assertTrue(out.fields['free'])
            self.assertTrue(out.fields['webmail'])
            self.assertEqual(out.risk_flags, ())

    def test_returned_recipient_is_not_url_or_injected_text(self):
        for email in ('alex@example.org?key=fixture-secret', 'alex@example.org/#fixture-secret',
                      'alex@https://example.org', '.alex@example.org', 'alex..x@example.org',
                      'alex@example.org\r\nfixture-secret', 'alex@-example.org'):
            out = get_provider('zerobounce').parse('verify', Response(200, {'status': 'valid', 'address': email}))
            self.assertEqual(out.error, 'invalid_response')
            self.assertNotIn('fixture-secret', repr(out))
