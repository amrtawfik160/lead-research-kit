"""Deepline finder adapter: offline fixtures only (synthetic identities, reserved domains, fake keys)."""
import json
import tempfile
import unittest

from lead_research.configuration import DEFAULT, MIGRATION_ADDED_PROVIDERS, default_config, load_config
from lead_research.discovery import collect, deepline_find_jobs
from lead_research.models import PolicyError, ProviderError, Response
from lead_research.policy import normalize_job
from lead_research.providers import get_provider
from lead_research.runner import Runner
from lead_research.store import Store
from lead_research.transport import OFFICIAL_ORIGINS

SECRET = 'fixture-secret'
ENV = {'DEEPLINE_API_KEY': SECRET}
WATERFALL_ENV = {**ENV, 'DEEPLINE_FIND_ROUTE': 'waterfall'}
EMAIL = 'jane.example@example.org'
FIND = {'name': 'Jane Example', 'domain': 'example.org', 'domain_evidence_url': 'https://example.org/'}
RUN_ID = 'play_run_8f3a2c1d'
RUN_URL = 'https://code.deepline.com/api/v2/runs/' + RUN_ID
EXECUTE_URL = 'https://code.deepline.com/api/v2/integrations/leadmagic_email_finder/execute'

# Shaped like the documented tool card (toolResponse.raw = {data: {email, status, ...}}).
FINDER_OK = {'toolId': 'leadmagic_email_finder', 'toolResponse': {'raw': {'data': {
    'email': EMAIL, 'status': 'valid', 'first_name': 'Jane', 'last_name': 'Example', 'domain': 'example.org',
    'is_domain_catch_all': False, 'mx_provider': 'example', 'message': 'Email found'}}},
    'billing': {'credits': 0.34}}
FINDER_MISS = {'toolResponse': {'raw': {'data': {'email': None, 'status': 'not_found', 'message': 'not found'}}}}
# Shaped like the documented plays/run response and the OpenAPI PlayRunPackage.
SUBMIT = {'workflowId': RUN_ID, 'apiVersion': 2, 'status': 'running',
          'dashboardUrl': 'https://code.deepline.com/dashboard/plays/x'}


def package(status, outputs=None, result=None, ident=RUN_ID):
    data = {'schemaVersion': 1, 'kind': 'play_run',
            'run': {'id': ident, 'playName': 'prebuilt/name-and-domain-to-email-waterfall', 'status': status},
            'steps': [], 'outputs': outputs or {}, 'datasets': [], 'next': {}}
    if result is not None:
        data['result'] = result
    return data


def parse(data, status=200, json_valid=True):
    return get_provider('deepline').parse('find', Response(status, data, {}, json_valid))


class FakeTransport:
    def __init__(self, responses):
        self.responses, self.requests = list(responses), []

    def send(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


class RegistryTests(unittest.TestCase):
    def test_defaults_origin_charges_spacing(self):
        p = get_provider('deepline')
        self.assertEqual((p.origin, p.auth_env, p.operations, p.independence_group),
                         ('https://code.deepline.com', ('DEEPLINE_API_KEY',), ('find',), 'deepline'))
        self.assertIn(p.origin, OFFICIAL_ORIGINS)
        self.assertEqual(DEFAULT['providers']['deepline'], {'enabled': False, 'charges': {'find': {'credits': '1'}}})
        order = list(DEFAULT['providers'])
        self.assertLess(order.index('deepline'), order.index('jina'))  # v7 adapters are appended after
        self.assertIn('deepline', MIGRATION_ADDED_PROVIDERS)
        self.assertEqual((p.min_spacing['find'], p.min_spacing['poll']), (1.0, 3.0))
        self.assertTrue(p.supports_pending)
        self.assertEqual(p.poll_methods, ('GET',))

    def test_older_config_migrates_disabled_and_rejects_charge_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = tmp + '/providers.json'
            old = default_config()
            del old['providers']['deepline']
            with open(path, 'w') as fh:
                json.dump(old, fh)
            self.assertFalse(load_config(path)['providers']['deepline']['enabled'])
            old['providers']['deepline'] = {'enabled': True, 'charges': {'find': {'credits': '0.14'}}}
            with open(path, 'w') as fh:
                json.dump(old, fh)
            with self.assertRaises(PolicyError):
                load_config(path)


class RequestTests(unittest.TestCase):
    def test_finder_route_is_default_sync_execute(self):
        p = get_provider('deepline')
        with self.assertRaisesRegex(ProviderError, '^missing_auth$'):
            p.request('find', FIND, {})
        req = p.request('find', FIND, ENV)
        self.assertEqual((req.method, req.url), ('POST', EXECUTE_URL))
        self.assertEqual(req.headers['Authorization'], 'Bearer ' + SECRET)
        self.assertEqual(json.loads(req.body), {'payload': {'first_name': 'Jane', 'last_name': 'Example', 'domain': 'example.org'}})
        self.assertNotIn(SECRET, repr(req))
        self.assertNotIn(SECRET, req.url)
        www = p.request('find', {**FIND, 'domain': 'www.example.org', 'name': 'Jane  Q Example'}, ENV)
        self.assertEqual(json.loads(www.body)['payload'], {'first_name': 'Jane', 'last_name': 'Q Example', 'domain': 'example.org'})
        with self.assertRaisesRegex(PolicyError, 'invalid_payload'):
            p.request('find', {**FIND, 'name': 'Jane'}, ENV)
        with self.assertRaisesRegex(PolicyError, 'invalid_auth'):
            p.request('find', FIND, {'DEEPLINE_API_KEY': 'a\r\nX: y'})
        with self.assertRaisesRegex(PolicyError, 'unsupported_operation'):
            p.request('verify', {'email': EMAIL}, ENV)

    def test_waterfall_route_starts_prebuilt_play(self):
        p = get_provider('deepline')
        req = p.request('find', FIND, WATERFALL_ENV)
        self.assertEqual((req.method, req.url), ('POST', 'https://code.deepline.com/api/v2/plays/run'))
        self.assertEqual(json.loads(req.body), {'name': 'prebuilt/name-and-domain-to-email-waterfall',
                                                'input': {'first_name': 'Jane', 'last_name': 'Example', 'domain': 'example.org'}})
        with self.assertRaisesRegex(PolicyError, 'invalid_deepline_route'):
            p.request('find', FIND, {**ENV, 'DEEPLINE_FIND_ROUTE': 'cheapest'})

    def test_poll_url_must_be_exact_run_url(self):
        p = get_provider('deepline')
        req = p.poll_request(RUN_URL, ENV)
        self.assertEqual((req.method, req.url, req.body), ('GET', RUN_URL, None))
        self.assertEqual(req.headers['Authorization'], 'Bearer ' + SECRET)
        for bad in (RUN_URL + '?full=true', RUN_URL + '#x', RUN_URL + '/', RUN_URL + '/tail',
                    'http://code.deepline.com/api/v2/runs/' + RUN_ID, 'https://user:pw@code.deepline.com/api/v2/runs/' + RUN_ID,
                    'https://example.org/api/v2/runs/' + RUN_ID, 'https://code.deepline.com/api/v2/runs/a%2Fb',
                    'https://code.deepline.com/api/v2/runs/', 'https://code.deepline.com/api/v2/runs/../plays/run',
                    'https://code.deepline.com/api/v2/runs/' + 'a' * 129, None):
            with self.assertRaisesRegex(PolicyError, 'unsafe_job_url'):
                p.poll_request(bad, ENV)

    def test_balance_read(self):
        p = get_provider('deepline')
        req = p.balance_request(ENV)
        self.assertEqual((req.method, req.url), ('GET', 'https://code.deepline.com/api/v2/billing/balance'))
        out = p.parse_balance(Response(200, {'balance': 25, 'balance_display': '25 Deepline Credits', 'rough_usd_balance': 2.5}))
        self.assertEqual(out, {'free_pools': {}, 'metadata': {'deepline_credits': '25', 'manual_free_attestation_required': True,
                                                              'rough_usd_balance': '2.5'}})
        with self.assertRaisesRegex(ProviderError, 'invalid_response'):
            p.parse_balance(Response(200, {'balance_display': '25'}))
        with self.assertRaisesRegex(ProviderError, 'auth'):
            p.parse_balance(Response(401, {'error': 'unauthorized'}))


class FinderParseTests(unittest.TestCase):
    def test_valid_candidate(self):
        out = parse(FINDER_OK)
        self.assertEqual((out.assessment, out.email, out.risk_flags), ('candidate', EMAIL, ()))
        self.assertEqual(out.fields['finder_verification_status'], 'valid')
        self.assertEqual((out.fields['provider'], out.fields['route'], out.fields['status']),
                         ('leadmagic', 'leadmagic_email_finder', 'valid'))
        self.assertEqual(out.fields['prospect']['full_name'], 'Jane Example')
        self.assertNotIn('message', json.dumps(out.fields))
        self.assertNotIn('Email found', json.dumps(out.fields))

    def test_envelope_locations(self):
        data = FINDER_OK['toolResponse']['raw']['data']
        for envelope in ({'toolResponse': {'rawV2': {'data': data}}}, {'toolResponse': {'rawV2': {'data': {'data': data}}, 'view': 'data'}},
                         {'result': {'data': data}}, {'data': data}, data):
            self.assertEqual(parse(envelope).email, EMAIL, envelope)

    def test_catch_all_and_misses(self):
        catch = parse({'result': {'data': {'email': EMAIL, 'status': 'valid_catch_all', 'is_domain_catch_all': True}}})
        self.assertEqual((catch.assessment, catch.risk_flags, catch.fields['finder_verification_status']),
                         ('candidate', ('catch_all',), 'accept_all'))
        self.assertEqual(parse(FINDER_MISS).assessment, 'not_found')
        self.assertEqual(parse({'result': {'data': {'email': 'jane.*****@example.org', 'status': 'valid'}}}).error, 'invalid_response')
        self.assertEqual(parse({'result': {'data': {'email': EMAIL, 'status': 'maybe'}}}).error, 'invalid_response')
        self.assertEqual(parse({'toolResponse': {}}).error, 'invalid_response')
        self.assertEqual(parse(None, json_valid=False).error, 'invalid_response')

    def test_errors(self):
        self.assertEqual(parse({'error': {'code': 'INSUFFICIENT_CREDITS', 'message': SECRET}}, status=402).error, 'quota')
        self.assertEqual(parse({'code': 'UPSTREAM_VALIDATION_ERROR', 'error_category': 'validation', 'error': 'x'}, status=400).error, 'provider_application_error')
        self.assertEqual(parse({'error': 'UPSTREAM_VALIDATION_ERROR'}, status=400).error, 'provider_application_error')
        self.assertEqual(parse({'error': {'code': 'UPSTREAM_VALIDATION_ERROR'}}, status=400).error, 'provider_application_error')
        self.assertEqual(parse({'error': {'code': 'INSUFFICIENT_CREDITS'}}, status=400).error, 'quota')
        self.assertEqual(parse({'error': 'Workspace balance is empty'}, status=409).error, 'quota')
        self.assertEqual(parse({'error': {'code': 'INVALID_INPUT', 'message': 'x'}}, status=422).error, 'http_error')
        self.assertEqual(parse({'error': {'code': 'NOT_FOUND'}}, status=404).error, 'http_error')
        self.assertEqual(parse({'error': {'code': 'UNAUTHORIZED'}}, status=401).error, 'auth')
        self.assertEqual(parse({'error': {'code': 'RATE_LIMITED'}}, status=429).error, 'rate_limit')
        self.assertEqual(parse({'error': 'boom'}, status=500).error, 'provider_error')
        self.assertEqual(parse({'error': {'code': 'PROVIDER_FAILED'}}).error, 'provider_application_error')
        self.assertEqual(parse({'error': {'code': 'insufficient_balance'}}).error, 'quota')


class WaterfallParseTests(unittest.TestCase):
    def test_submit_and_poll_states(self):
        out = parse(SUBMIT)
        self.assertEqual((out.assessment, out.job_url), ('pending', RUN_URL))
        self.assertEqual(parse(package('queued')).job_url, RUN_URL)
        self.assertEqual(parse({**SUBMIT, 'status': 'waiting'}).assessment, 'pending')
        self.assertEqual(parse({**SUBMIT, 'workflowId': 'bad id!'}).error, 'invalid_response')
        self.assertEqual(parse(package('failed')).error, 'provider_application_error')
        self.assertEqual(parse(package('cancelled')).error, 'provider_application_error')
        self.assertEqual(parse(package('exploded')).error, 'invalid_response')

    def test_completed_outputs(self):
        out = parse(package('completed', result={'email': EMAIL, 'email_validated': True, 'provider': 'hunter'}))
        self.assertEqual((out.assessment, out.email), ('candidate', EMAIL))
        self.assertEqual((out.fields['provider'], out.fields['email_validated'], out.fields['finder_verification_status']),
                         ('hunter', True, 'valid'))
        nested = parse(package('completed', outputs={'result': {'work_email': EMAIL, 'email_status': 'catch_all'}}))
        self.assertEqual((nested.email, nested.risk_flags, nested.fields['finder_verification_status']),
                         (EMAIL, ('catch_all',), 'accept_all'))
        self.assertEqual(parse({'status': 'completed', 'workflowId': RUN_ID, 'result': EMAIL}).email, EMAIL)
        unvalidated = parse(package('completed', result={'email': EMAIL, 'email_validated': False}))
        self.assertNotIn('finder_verification_status', unvalidated.fields)
        self.assertEqual(parse(package('completed', result={'email': None, 'email_validated': False})).assessment, 'not_found')
        self.assertEqual(parse(package('completed')).assessment, 'not_found')
        self.assertEqual(parse(package('completed', result={'email': 'not an email'})).assessment, 'not_found')


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name + '/ledger.db')
        self.config = default_config()
        self.config['providers']['deepline']['enabled'] = True
        self.store.confirm_budget('deepline', 'credits', '2', 'synthetic promo')

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def runner(self, responses, env=ENV):
        self.transport = FakeTransport(responses)
        self.sleeps = []
        return Runner(self.store, self.config, env=env, transport=self.transport, sleep=self.sleeps.append)

    def test_sync_finder_debits_one_and_captures(self):
        report = self.runner([Response(200, FINDER_OK)]).run(deepline_find_jobs([{'full_name': 'Jane Example', 'domain': 'example.org'}]),
                                                            execute=True)
        self.assertEqual(report['results'][0]['status'], 'completed')
        self.assertEqual([r.url for r in self.transport.requests], [EXECUTE_URL])
        self.assertEqual(self.store.budgets()[0]['remaining'], '1')
        prospect = collect(self.store)['prospects'][0]
        self.assertEqual((prospect['email'], prospect['email_source'], prospect['email_verification'], prospect['company_website']),
                         (EMAIL, 'deepline_finder', 'valid', 'example.org'))

    def test_quota_pauses_provider(self):
        report = self.runner([Response(402, {'error': {'code': 'INSUFFICIENT_CREDITS'}})]).run(
            [{'provider': 'deepline', 'operation': 'find', 'payload': FIND}], execute=True)
        self.assertEqual(report['results'][0]['outcome']['error'], 'quota')
        self.assertTrue(report['stopped'])
        self.assertTrue(self.store.provider_state('deepline').get('paused'))

    def test_waterfall_pending_resume_get_and_capture(self):
        runner = self.runner([Response(200, SUBMIT), Response(200, package('running')),
                              Response(200, package('completed', result={'email': EMAIL, 'email_validated': True}))], WATERFALL_ENV)
        report = runner.run([{'provider': 'deepline', 'operation': 'find', 'payload': FIND}], execute=True)
        self.assertEqual(report['results'][0]['status'], 'pending')
        attempt = self.store.attempts()[0]
        self.assertEqual(attempt['outcome']['job_url'], RUN_URL)
        self.assertEqual(runner.resume(attempt['id'], execute=True)['results'][0]['status'], 'pending')
        self.assertEqual(self.store.get_attempt(attempt['id'])['outcome']['job_url'], RUN_URL)
        self.assertEqual(runner.resume(attempt['id'], execute=True)['results'][0]['status'], 'completed')
        self.assertEqual([(r.method, r.url) for r in self.transport.requests],
                         [('POST', 'https://code.deepline.com/api/v2/plays/run'), ('GET', RUN_URL), ('GET', RUN_URL)])
        self.assertEqual(self.store.budgets()[0]['remaining'], '1')  # polls never debit
        self.assertEqual(self.store.candidates()[0]['email'], EMAIL)
        self.assertEqual(collect(self.store)['prospects'][0]['email_source'], 'deepline_finder')
        with self.assertRaisesRegex(PolicyError, 'only_pending'):
            runner.resume(attempt['id'], execute=True)

    def test_unsafe_job_url_from_server_is_not_stored(self):
        runner = self.runner([Response(200, {**SUBMIT, 'workflowId': '../x'})], WATERFALL_ENV)
        report = runner.run([{'provider': 'deepline', 'operation': 'find', 'payload': FIND}], execute=True)
        self.assertEqual(report['results'][0]['outcome']['error'], 'invalid_response')


class DiscoveryTests(unittest.TestCase):
    def test_find_jobs(self):
        records = [{'full_name': 'Jane Example', 'company_website': 'https://www.Example.org/about', 'lead_id': 7},
                   {'name': 'Jane  Example', 'domain': 'example.org'},  # duplicate after normalization
                   {'first_name': 'Alex', 'last_name': 'Sample', 'website': 'sample.example.net'},
                   {'full_name': 'Has Email', 'domain': 'example.com', 'email': EMAIL},
                   {'full_name': 'Mononym', 'domain': 'example.com'},
                   {'full_name': 'Bad Domain', 'domain': 'localhost'},
                   {'full_name': 'No Domain'}, 'not a dict']
        jobs = deepline_find_jobs(records)
        self.assertEqual(jobs, [
            {'provider': 'deepline', 'operation': 'find',
             'payload': {'name': 'Jane Example', 'domain': 'example.org', 'domain_evidence_url': 'https://example.org/', 'lead_id': '7'}},
            {'provider': 'deepline', 'operation': 'find',
             'payload': {'name': 'Alex Sample', 'domain': 'sample.example.net', 'domain_evidence_url': 'https://sample.example.net/'}}])
        for job in jobs:
            normalized = normalize_job(job)
            self.assertEqual((normalized['provider'], normalized['operation']), ('deepline', 'find'))
            get_provider('deepline').request('find', normalized['payload'], ENV)
        self.assertEqual(len(deepline_find_jobs(records, skip_with_email=False)), 3)




class DeeplineLiveShapeTests(unittest.TestCase):
    def test_no_result_is_not_found(self):
        from lead_research.providers import get_provider
        from lead_research.models import Response
        body = {'job_id': 'x', 'status': 'no_result', 'result': {'data': {'email': None, 'status': None, 'message': 'none',
                                                                         'first_name': 'Casey', 'last_name': 'Morgan', 'domain': 'harbor.example'}}}
        out = get_provider('deepline').parse('find', Response(200, body))
        self.assertEqual(out.assessment, 'not_found')
        self.assertIsNone(out.error)

if __name__ == '__main__':
    unittest.main()
