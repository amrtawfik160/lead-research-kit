"""v7 adapters: offline fixtures only (synthetic identities, reserved domains, fake keys)."""
import json
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dashboard'))

from lead_research.configuration import DEFAULT, MIGRATION_ADDED_PROVIDERS, default_config, load_config
from lead_research.discovery import jina_read_jobs
from lead_research.models import PolicyError, ProviderError, Response
from lead_research.providers import get_provider
from lead_research.transport import OFFICIAL_ORIGINS

SECRET = 'fixture-secret'
KEYS = {
    'apify': 'APIFY_API_TOKEN',
    'skrapp': 'SKRAPP_API_KEY',
    'producthunt': 'PRODUCTHUNT_TOKEN',
    'github': 'GITHUB_TOKEN',
}
OPTIONAL = {'jina': 'JINA_API_KEY'}
ENV = {v: SECRET for v in list(KEYS.values()) + list(OPTIONAL.values())}
KEYLESS = ('jina', 'hackernews')


def parse(provider, data, operation, status=200, headers=None):
    return get_provider(provider).parse(operation, Response(status, data, headers or {}))


class RegistryTests(unittest.TestCase):
    def test_registered_disabled_and_official(self):
        for name in ('jina', 'apify', 'skrapp', 'hackernews', 'producthunt', 'github'):
            p = get_provider(name)
            self.assertIn(p.origin, OFFICIAL_ORIGINS)
            self.assertFalse(DEFAULT['providers'][name]['enabled'])
            self.assertEqual(set(DEFAULT['providers'][name]['charges']), set(p.operations))
            self.assertIn(name, MIGRATION_ADDED_PROVIDERS)
            self.assertTrue(all(p.min_spacing[op] > 0 for op in p.operations))

    def test_older_config_migrates_v7_disabled(self):
        import tempfile
        from pathlib import Path
        cfg = default_config()
        for name in ('jina', 'apify', 'skrapp', 'hackernews', 'producthunt', 'github'):
            del cfg['providers'][name]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'providers.json'
            path.write_text(json.dumps(cfg))
            loaded = load_config(path)
            self.assertFalse(loaded['providers']['jina']['enabled'])
            self.assertEqual(loaded['providers']['skrapp']['charges']['find'], {'credits': '1'})

    def test_auth_rules(self):
        self.assertEqual(get_provider('jina').auth_env, ())
        self.assertEqual(get_provider('jina').optional_auth_env, ('JINA_API_KEY',))
        self.assertEqual(get_provider('hackernews').auth_env, ())
        req = get_provider('jina').request('read', {'url': 'https://example.org/team'}, {})
        self.assertNotIn('Authorization', req.headers)
        keyed = get_provider('jina').request('read', {'url': 'https://example.org/team'}, ENV)
        self.assertTrue(keyed.headers['Authorization'].startswith('Bearer '))
        for name, key in KEYS.items():
            p = get_provider(name)
            with self.assertRaisesRegex(ProviderError, '^missing_auth$'):
                p.request(p.operations[0], {'url': 'https://example.org', 'area': 'Austin', 'state': 'TX',
                                           'query': 'Harbor Realty', 'name': 'Jane Example',
                                           'domain': 'example.org'} if p.operations[0] != 'find' else
                          {'name': 'Jane Example', 'domain': 'example.org'}, {})
            self.assertEqual(p.auth_env, (key,))


class JinaTests(unittest.TestCase):
    def test_request_and_parse(self):
        req = get_provider('jina').request('read', {'url': 'https://example.org/team'}, {})
        self.assertEqual(req.method, 'POST')
        self.assertEqual(req.url, 'https://r.jina.ai/')
        body = json.loads(req.body)
        self.assertEqual(body['url'], 'https://example.org/team')
        html = '<html><body><a href="mailto:jane@example.org">Jane Example</a></body></html>'
        out = parse('jina', {'code': 200, 'data': {'html': html, 'url': 'https://example.org/team', 'title': 'Team'}}, 'read')
        self.assertEqual(out.assessment, 'results')
        self.assertIn('html_zb64', out.fields)
        self.assertEqual(parse('jina', {'code': 200, 'data': ''}, 'read').assessment, 'not_found')
        self.assertEqual(parse('jina', {'x': SECRET}, 'read', status=429).error, 'rate_limit')
        jobs = jina_read_jobs(['https://example.org/team', 'https://example.org/team'])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['provider'], 'jina')


class ApifyTests(unittest.TestCase):
    def test_places_and_contacts(self):
        places = get_provider('apify').request('places', {'area': 'Austin', 'state': 'TX'}, ENV)
        self.assertIn('/v2/actors/compass~crawler-google-places/run-sync-get-dataset-items', places.url)
        self.assertTrue(places.headers['Authorization'].startswith('Bearer '))
        self.assertNotIn(SECRET, places.url)
        body = json.loads(places.body)
        self.assertEqual(body['maxCrawledPlacesPerSearch'], 5)
        out = parse('apify', [{'title': 'Harbor Realty', 'website': 'https://harbor.example', 'city': 'Austin', 'state': 'TX'}], 'places', status=201)
        self.assertEqual(out.assessment, 'results')
        self.assertEqual(out.fields['companies'][0]['organization'], 'Harbor Realty')
        contacts = get_provider('apify').request('contacts', {'url': 'https://example.org'}, ENV)
        self.assertIn('vdrmota~contact-info-scraper', contacts.url)
        found = parse('apify', [{'emails': ['jane@example.org'], 'name': 'Jane Example'}], 'contacts', status=201)
        self.assertEqual(found.assessment, 'results')
        self.assertEqual(found.fields['prospects'][0]['email'], 'jane@example.org')
        self.assertEqual(parse('apify', [], 'places', status=201).assessment, 'not_found')
        self.assertEqual(parse('apify', {'error': SECRET}, 'places', status=408).error, 'provider_error')
        self.assertNotIn(SECRET, repr(parse('apify', {'error': SECRET}, 'places', status=401)))


class SkrappTests(unittest.TestCase):
    def test_find_and_company_search(self):
        req = get_provider('skrapp').request('find', {'name': 'Jane Example', 'domain': 'example.org'}, ENV)
        self.assertEqual(req.headers['X-Access-Key'], SECRET)
        q = parse_qs(urlsplit(req.url).query)
        self.assertEqual(q['firstName'], ['Jane'])
        self.assertEqual(q['domain'], ['example.org'])
        ok = parse('skrapp', {'email': 'jane@example.org', 'quality': {'status': 'valid'}, 'firstName': 'Jane', 'lastName': 'Example'}, 'find')
        self.assertEqual(ok.assessment, 'candidate')
        self.assertEqual(ok.fields['finder_verification_status'], 'valid')
        catch = parse('skrapp', {'email': 'jane@example.org', 'quality': {'status': 'catch-all'}}, 'find')
        self.assertIn('catch_all', catch.risk_flags)
        self.assertEqual(parse('skrapp', {'email': 'jane@example.org', 'quality': {'status': 'invalid'}}, 'find').assessment, 'not_found')
        self.assertEqual(parse('skrapp', {'status': '404', 'message': 'Not found'}, 'find', status=404).assessment, 'not_found')
        search = get_provider('skrapp').request('company_search', {'query': 'Harbor Realty'}, ENV)
        self.assertTrue(search.url.endswith('/api/v3/companies/search?size=10&lead_counts=false'))
        rows = parse('skrapp', {'results': [{'name': 'Harbor Realty', 'domain': 'harbor.example', 'city': 'Austin'}]}, 'company_search')
        self.assertEqual(rows.fields['companies'][0]['domain'], 'harbor.example')


class SignalTests(unittest.TestCase):
    def test_hackernews(self):
        req = get_provider('hackernews').request('watch', {'query': 'Harbor Realty'}, {})
        self.assertTrue(req.url.startswith('https://hn.algolia.com/api/v1/search?'))
        q = parse_qs(urlsplit(req.url).query)
        self.assertEqual(q['tags'], ['story'])
        out = parse('hackernews', {'hits': [{'title': 'Harbor Realty (YC)', 'url': 'https://news.ycombinator.com/x',
                                            'objectID': '1', 'points': 12}]}, 'watch')
        self.assertEqual(out.assessment, 'results')
        self.assertEqual(out.fields['results'][0]['title'], 'Harbor Realty (YC)')
        self.assertEqual(parse('hackernews', {'hits': []}, 'watch').assessment, 'not_found')
        self.assertEqual(parse('hackernews', {'hits': {}}, 'watch').error, 'invalid_response')

    def test_producthunt(self):
        req = get_provider('producthunt').request('watch', {'query': 'artificial-intelligence'}, ENV)
        self.assertEqual(req.url, 'https://api.producthunt.com/v2/api/graphql')
        body = json.loads(req.body)
        self.assertEqual(body['variables']['topic'], 'artificial-intelligence')
        plain = get_provider('producthunt').request('watch', {'query': 'Harbor Realty'}, ENV)
        self.assertIsNone(json.loads(plain.body)['variables']['topic'])
        out = parse('producthunt', {'data': {'posts': {'edges': [{'node': {'name': 'Harbor', 'tagline': 'AI CRM',
                                                                         'url': 'https://www.producthunt.com/posts/harbor'}}]}}}, 'watch')
        self.assertEqual(out.fields['results'][0]['title'], 'Harbor')
        self.assertEqual(parse('producthunt', {'errors': [{'message': SECRET}]}, 'watch').error, 'provider_application_error')
        self.assertNotIn(SECRET, repr(parse('producthunt', {'errors': [{'message': SECRET}]}, 'watch')))

    def test_github(self):
        req = get_provider('github').request('watch', {'query': 'harbor-realty'}, ENV)
        self.assertTrue(req.url.startswith('https://api.github.com/search/repositories?'))
        self.assertEqual(req.headers['User-Agent'], 'lead-research-kit github-signals')
        out = parse('github', {'items': [{'full_name': 'harbor/realty', 'html_url': 'https://github.com/harbor/realty',
                                         'description': 'demo'}]}, 'watch')
        self.assertEqual(out.fields['results'][0]['title'], 'harbor/realty')
        self.assertEqual(parse('github', {'message': SECRET}, 'watch', status=401).error, 'auth')


class DashboardPresenceTests(unittest.TestCase):
    def test_jina_row_is_keyless_present(self):
        import os
        import tempfile
        from pathlib import Path
        from lead_research.store import Store
        from api.sample import seed_sample
        from api.snapshot import Dashboard
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / 'sample'
        seed_sample(root)
        os.environ.pop('JINA_API_KEY', None)
        row = next(r for r in Dashboard(root, sample=False).providers()['providers'] if r['id'] == 'jina')
        self.assertEqual(row['auth_env'], ['JINA_API_KEY'])
        self.assertTrue(row['key_present'])


if __name__ == '__main__':
    unittest.main()
