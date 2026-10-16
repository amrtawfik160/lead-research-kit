"""Offline tests for SafeFetcher: fake resolver, opener, clock and sleep. No sockets."""
import gzip
import io
import os
import socket
import stat
import tempfile
import threading
import time
import unittest
import urllib.error
import zlib

from lead_research.webfetch import (CRAWL_DELAY_CAP, FetchResult, SafeFetcher, decode_body,
                                    is_public_address, normalize_url)

PUBLIC_IP = '93.184.216.34'
HTML = b'<!doctype html><html><body><h1>Hello</h1></body></html>'


class FakeResponse(io.BytesIO):
    def __init__(self, status=200, headers=None, body=b''):
        super().__init__(body)
        self.status = status
        self.code = status
        self.headers = dict(headers or {})


class FakeOpener:
    """Routes URLs to canned (status, headers, body) tuples, callables or exceptions."""

    def __init__(self, routes=None, default_robots=True):
        self.routes = dict(routes or {})
        self.calls = []
        self.lock = threading.Lock()
        self.default_robots = default_robots

    def open(self, request, timeout):
        url = request.full_url
        with self.lock:
            self.calls.append((url, getattr(request, 'pinned_address', None), dict(request.header_items())))
        route = self.routes.get(url)
        if route is None and url.endswith('/robots.txt') and self.default_robots:
            route = (404, {'Content-Type': 'text/plain'}, b'')
        if route is None:
            route = (404, {'Content-Type': 'text/html'}, b'')
        if callable(route):
            route = route(request)
        if isinstance(route, BaseException):
            raise route
        status, headers, body = route
        return FakeResponse(status, headers, body)

    def urls(self):
        return [c[0] for c in self.calls]


def html_route(body=HTML, status=200, **headers):
    merged = {'Content-Type': 'text/html; charset=utf-8'}
    merged.update({k.replace('_', '-'): v for k, v in headers.items()})
    return status, merged, body


class FakeResolver:
    def __init__(self, mapping=None):
        self.mapping = mapping or {}
        self.calls = []

    def __call__(self, host, port, *args):
        self.calls.append(host)
        addresses = self.mapping.get(host, [PUBLIC_IP])
        if isinstance(addresses, BaseException):
            raise addresses
        return [(socket.AF_INET6 if ':' in a else socket.AF_INET, socket.SOCK_STREAM, 6, '', (a, port))
                for a in addresses]


class FakeClock:
    def __init__(self, start=1_000_000.0):
        self.now = start
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(round(seconds, 3))
        self.now += seconds


def make_fetcher(routes=None, resolver=None, clock=None, **kwargs):
    clock = clock or FakeClock()
    opener = kwargs.pop('opener', None) or FakeOpener(routes)
    kwargs.setdefault('per_host_delay', 0)
    fetcher = SafeFetcher(resolver=resolver or FakeResolver(), opener=opener, clock=clock,
                          sleep=clock.sleep, **kwargs)
    return fetcher, opener, clock


class UrlPolicyTests(unittest.TestCase):
    def test_normalize_url_canonicalizes(self):
        self.assertEqual(normalize_url('HTTPS://Harbor-Realty.Example:443/Team Page#x'),
                         'https://harbor-realty.example/Team%20Page')
        self.assertEqual(normalize_url('http://example.com'), 'http://example.com/')

    def test_unsafe_urls_rejected_without_network(self):
        fetcher, opener, _ = make_fetcher()
        for url in ('ftp://example.com/', 'file:///etc/passwd', 'http://user:pw@example.com/',
                    'http://example.com:8080/', 'http://exa\nmple.com/', 'javascript:alert(1)',
                    'http://exa mple.com/', 'http:\\\\example.com\\', '', None):
            with self.subTest(url=url):
                self.assertEqual(fetcher.get(url).error, 'unsafe_url')
        self.assertEqual(opener.calls, [])

    def test_private_and_special_addresses(self):
        for address in ('10.0.0.1', '127.0.0.1', '169.254.169.254', '::1', '::ffff:127.0.0.1',
                        '192.168.1.1', '172.16.0.5', '0.0.0.0', '224.0.0.1', '100.64.0.1',
                        'fe80::1%en0', 'fc00::1', '2002:7f00:1::', '64:ff9b::a00:1', 'not-an-ip'):
            with self.subTest(address=address):
                self.assertFalse(is_public_address(address))
        self.assertTrue(is_public_address(PUBLIC_IP))
        self.assertTrue(is_public_address('2606:2800:220:1:248:1893:25c8:1946'))

    def test_hostname_resolving_to_any_private_address_is_blocked(self):
        resolver = FakeResolver({'intranet.example': ['10.0.0.1'],
                                 'mixed.example': [PUBLIC_IP, '127.0.0.1'],
                                 'mapped.example': ['::ffff:127.0.0.1'],
                                 'nxdomain.example': socket.gaierror('nope')})
        fetcher, opener, _ = make_fetcher(resolver=resolver)
        self.assertEqual(fetcher.get('http://intranet.example/').error, 'private_address')
        self.assertEqual(fetcher.get('https://mixed.example/').error, 'private_address')
        self.assertEqual(fetcher.get('https://mapped.example/').error, 'private_address')
        self.assertEqual(fetcher.get('https://nxdomain.example/').error, 'network_error')
        self.assertEqual(fetcher.get('http://127.0.0.1/').error, 'private_address')
        self.assertEqual(opener.calls, [])

    def test_connection_is_pinned_to_validated_ipv4(self):
        resolver = FakeResolver({'harbor-realty.example': ['2606:2800:220:1:248:1893:25c8:1946', PUBLIC_IP]})
        fetcher, opener, _ = make_fetcher({'https://harbor-realty.example/': html_route()},
                                          resolver=resolver, respect_robots=False)
        result = fetcher.get('https://harbor-realty.example/')
        self.assertTrue(result.ok)
        self.assertEqual(opener.calls[0][1], PUBLIC_IP)
        headers = {k.lower(): v for k, v in opener.calls[0][2].items()}
        self.assertIn('LeadResearchKit/0.2', headers['user-agent'])
        self.assertNotIn('cookie', headers)


class RobotsAndPolitenessTests(unittest.TestCase):
    def test_robots_disallow_and_single_fetch(self):
        routes = {
            'https://harbor-realty.example/robots.txt': (200, {'Content-Type': 'text/plain'},
                                                         b'User-agent: *\nDisallow: /private\n'),
            'https://harbor-realty.example/team': html_route(),
        }
        fetcher, opener, _ = make_fetcher(routes)
        blocked = fetcher.get('https://harbor-realty.example/private/staff')
        self.assertEqual(blocked.error, 'blocked_by_robots')
        self.assertTrue(fetcher.get('https://harbor-realty.example/team').ok)
        self.assertEqual(opener.urls().count('https://harbor-realty.example/robots.txt'), 1)
        self.assertNotIn('https://harbor-realty.example/private/staff', opener.urls())

    def test_robots_specific_user_agent_group(self):
        robots = b'User-agent: LeadResearchKit\nDisallow: /\n\nUser-agent: *\nAllow: /\n'
        fetcher, _, _ = make_fetcher({
            'https://a.example/robots.txt': (200, {'Content-Type': 'text/plain'}, robots),
            'https://a.example/': html_route()})
        self.assertEqual(fetcher.get('https://a.example/').error, 'blocked_by_robots')

    def test_robots_failure_modes(self):
        routes = {
            'https://four.example/robots.txt': (403, {}, b''),
            'https://four.example/': html_route(),
            'https://five.example/robots.txt': (503, {}, b''),
            'https://five.example/': html_route(),
            'https://down.example/robots.txt': urllib.error.URLError(ConnectionRefusedError()),
            'https://down.example/': html_route(),
        }
        fetcher, _, _ = make_fetcher(routes)
        self.assertTrue(fetcher.get('https://four.example/').ok)          # 4xx: allow all
        self.assertEqual(fetcher.get('https://five.example/').error, 'blocked_by_robots')  # 5xx
        self.assertEqual(fetcher.get('https://down.example/').error, 'blocked_by_robots')  # network

    def test_respect_robots_false_skips_robots(self):
        fetcher, opener, _ = make_fetcher({'https://a.example/': html_route()}, respect_robots=False)
        self.assertTrue(fetcher.get('https://a.example/').ok)
        self.assertEqual(opener.urls(), ['https://a.example/'])

    def test_crawl_delay_raises_spacing_and_is_capped(self):
        routes = {
            'https://slow.example/robots.txt': (200, {}, b'User-agent: *\nCrawl-delay: 5\n'),
            'https://slow.example/a': html_route(), 'https://slow.example/b': html_route(),
            'https://lazy.example/robots.txt': (200, {}, b'User-agent: *\nCrawl-delay: 60\n'),
            'https://lazy.example/a': html_route(),
        }
        fetcher, _, clock = make_fetcher(routes, per_host_delay=1)
        fetcher.get('https://slow.example/a')
        fetcher.get('https://slow.example/b')
        self.assertEqual(clock.sleeps, [5.0, 5.0])
        self.assertEqual(fetcher.host_delay('slow.example'), 5.0)
        fetcher.get('https://lazy.example/a')
        self.assertEqual(fetcher.host_delay('lazy.example'), CRAWL_DELAY_CAP)

    def test_per_host_spacing_uses_clock_and_sleep(self):
        routes = {u: html_route() for u in ('https://a.example/1', 'https://a.example/2',
                                            'https://b.example/1', 'https://a.example/3')}
        fetcher, _, clock = make_fetcher(routes, per_host_delay=2, respect_robots=False)
        fetcher.get('https://a.example/1')
        fetcher.get('https://b.example/1')      # other host: no wait
        clock.now += 0.5
        fetcher.get('https://a.example/2')      # 2 - 0.5 elapsed
        fetcher.get('https://a.example/3')
        self.assertEqual(clock.sleeps, [1.5, 2.0])

    def test_concurrent_gets_are_serialized_per_host(self):
        active, peak, lock = {}, {}, threading.Lock()

        def slow(request):
            host = request.host
            with lock:
                active[host] = active.get(host, 0) + 1
                peak[host] = max(peak.get(host, 0), active[host])
            time.sleep(0.005)
            with lock:
                active[host] -= 1
            return html_route()

        opener = FakeOpener()
        opener.routes = {f'https://h{h}.example/p{i}': slow for h in range(4) for i in range(4)}
        fetcher = SafeFetcher(resolver=FakeResolver(), opener=opener, per_host_delay=0,
                              respect_robots=False)
        urls = list(opener.routes)
        results = [None] * len(urls)

        def worker(i):
            results[i] = fetcher.get(urls[i])

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(len(urls))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertTrue(all(r.ok for r in results))
        self.assertEqual(set(peak.values()), {1})


class RedirectAndBodyTests(unittest.TestCase):
    def test_redirect_apex_to_www_and_http_to_https(self):
        routes = {
            'http://harbor-realty.example/': (301, {'Location': 'https://www.harbor-realty.example/'}, b''),
            'https://www.harbor-realty.example/': html_route(),
        }
        fetcher, _, _ = make_fetcher(routes, respect_robots=False)
        result = fetcher.get('http://harbor-realty.example/')
        self.assertTrue(result.ok)
        self.assertEqual(result.url, 'http://harbor-realty.example/')
        self.assertEqual(result.final_url, 'https://www.harbor-realty.example/')

    def test_redirect_to_private_address_is_revalidated_and_blocked(self):
        resolver = FakeResolver({'metadata.example': ['169.254.169.254']})
        routes = {'https://a.example/': (302, {'Location': 'http://metadata.example/latest'}, b''),
                  'https://b.example/': (302, {'Location': 'http://10.0.0.1/admin'}, b''),
                  'https://c.example/': (302, {'Location': 'ftp://c.example/file'}, b''),
                  'https://d.example/': (302, {'Location': 'http://d.example:8080/'}, b'')}
        fetcher, opener, _ = make_fetcher(routes, resolver=resolver, respect_robots=False)
        self.assertEqual(fetcher.get('https://a.example/').error, 'private_address')
        self.assertEqual(fetcher.get('https://b.example/').error, 'private_address')
        self.assertEqual(fetcher.get('https://c.example/').error, 'unsafe_url')
        self.assertEqual(fetcher.get('https://d.example/').error, 'unsafe_url')
        self.assertFalse(any('metadata' in u or '10.0.0.1' in u for u in opener.urls()))

    def test_redirect_limit(self):
        loop = {f'https://a.example/{i}': (302, {'Location': f'/{i + 1}'}, b'') for i in range(10)}
        fetcher, opener, _ = make_fetcher(loop, max_redirects=2, respect_robots=False)
        self.assertEqual(fetcher.get('https://a.example/0').error, 'redirect_limit')
        self.assertEqual(len(opener.calls), 3)

    def test_size_limits(self):
        routes = {'https://a.example/big': html_route(b'<html>' + b'x' * 5000),
                  'https://a.example/declared': html_route(HTML, Content_Length='999999'),
                  'https://a.example/bomb': html_route(gzip.compress(b'<html>' + b'a' * 100_000),
                                                       Content_Encoding='gzip')}
        fetcher, _, _ = make_fetcher(routes, max_bytes=1000, respect_robots=False)
        self.assertEqual(fetcher.get('https://a.example/big').error, 'too_large')
        self.assertEqual(fetcher.get('https://a.example/declared').error, 'too_large')
        self.assertEqual(fetcher.get('https://a.example/bomb').error, 'too_large')

    def test_non_html_is_skipped(self):
        routes = {'https://a.example/brochure': (200, {'Content-Type': 'application/pdf'}, b'%PDF-1.4'),
                  'https://a.example/untyped': (200, {}, b'\x89PNG....'),
                  'https://a.example/sniffed': (200, {}, HTML)}
        fetcher, _, _ = make_fetcher(routes, respect_robots=False)
        result = fetcher.get('https://a.example/brochure')
        self.assertEqual((result.error, result.text, result.content_type), ('not_html', '', 'application/pdf'))
        self.assertEqual(fetcher.get('https://a.example/untyped').error, 'not_html')
        self.assertTrue(fetcher.get('https://a.example/sniffed').ok)

    def test_gzip_and_deflate_decoding(self):
        routes = {'https://a.example/gz': html_route(gzip.compress(HTML), Content_Encoding='gzip'),
                  'https://a.example/zlib': html_route(zlib.compress(HTML), Content_Encoding='deflate'),
                  'https://a.example/raw': html_route(zlib.compress(HTML)[2:-4], Content_Encoding='deflate'),
                  'https://a.example/br': html_route(HTML, Content_Encoding='br')}
        fetcher, _, _ = make_fetcher(routes, respect_robots=False)
        for path in ('gz', 'zlib', 'raw'):
            with self.subTest(path=path):
                self.assertIn('<h1>Hello</h1>', fetcher.get(f'https://a.example/{path}').text)
        self.assertEqual(fetcher.get('https://a.example/br').error, 'network_error')

    def test_charset_decoding(self):
        self.assertEqual(decode_body('Café'.encode('latin-1'), 'text/html; charset=ISO-8859-1'), 'Café')
        meta = '<html><head><meta charset="windows-1252"></head>Peña</html>'.encode('cp1252')
        self.assertIn('Peña', decode_body(meta, 'text/html'))
        self.assertIn('\ufffd', decode_body(b'<html>\xff\xfe bad', 'text/html; charset=bogus-charset'))

    def test_errors_never_raise(self):
        routes = {'https://a.example/500': urllib.error.HTTPError('https://a.example/500', 500, 'x', {}, io.BytesIO(b'')),
                  'https://a.example/404': html_route(b'', status=404),
                  'https://a.example/slow': TimeoutError(),
                  'https://a.example/urlslow': urllib.error.URLError(TimeoutError()),
                  'https://a.example/refused': ConnectionResetError(),
                  'https://a.example/noloc': (302, {}, b'')}
        fetcher, _, _ = make_fetcher(routes, respect_robots=False)
        r500 = fetcher.get('https://a.example/500')
        self.assertEqual((r500.error, r500.status), ('http_error', 500))
        self.assertEqual(fetcher.get('https://a.example/404').status, 404)
        self.assertEqual(fetcher.get('https://a.example/slow').error, 'timeout')
        self.assertEqual(fetcher.get('https://a.example/urlslow').error, 'timeout')
        self.assertEqual(fetcher.get('https://a.example/refused').error, 'network_error')
        self.assertEqual(fetcher.get('https://a.example/noloc').error, 'http_error')


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'cache', 'pages.sqlite')

    def tearDown(self):
        self.tmp.cleanup()

    def test_hit_miss_expiry_negative_and_permissions(self):
        routes = {'https://a.example/team': html_route(b'<html><p>Jane Doe</p></html>'),
                  'https://a.example/gone': html_route(b'', status=404),
                  'https://a.example/flaky': html_route(b'', status=503)}
        clock = FakeClock()
        fetcher, opener, _ = make_fetcher(routes, clock=clock, cache_path=self.path,
                                          cache_max_age=100, respect_robots=False)
        first = fetcher.get('https://a.example/team')
        self.assertFalse(first.from_cache)
        second = fetcher.get('https://A.example/team#bio')
        self.assertTrue(second.from_cache)
        self.assertEqual(second.text, first.text)
        self.assertEqual(second.url, 'https://A.example/team#bio')
        self.assertEqual(len(opener.calls), 1)
        fetcher.get('https://a.example/gone')
        self.assertTrue(fetcher.get('https://a.example/gone').from_cache)  # negative cached
        fetcher.get('https://a.example/flaky')
        self.assertFalse(fetcher.get('https://a.example/flaky').from_cache)  # transient: not cached
        clock.now += 101
        self.assertFalse(fetcher.get('https://a.example/team').from_cache)  # expired
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.path)).st_mode), 0o700)
        fetcher.close()

    def test_cache_survives_new_fetcher_and_caches_robots_block(self):
        routes = {'https://a.example/robots.txt': (200, {}, b'User-agent: *\nDisallow: /x\n'),
                  'https://a.example/': html_route()}
        fetcher, opener, clock = make_fetcher(routes, cache_path=self.path)
        self.assertEqual(fetcher.get('https://a.example/x').error, 'blocked_by_robots')
        self.assertTrue(fetcher.get('https://a.example/').ok)
        fetcher.close()
        again, opener2, _ = make_fetcher(routes, clock=clock, cache_path=self.path)
        self.assertTrue(again.get('https://a.example/x').from_cache)
        self.assertTrue(again.get('https://a.example/').from_cache)
        self.assertEqual(opener2.calls, [])
        again.close()


class ResultShapeTests(unittest.TestCase):
    def test_fetch_result_fields(self):
        fetcher, _, clock = make_fetcher({'https://a.example/': html_route()}, respect_robots=False)
        result = fetcher.get('https://a.example/')
        self.assertIsInstance(result, FetchResult)
        self.assertEqual((result.status, result.content_type, result.error, result.fetched_at),
                         (200, 'text/html', None, clock.now))


if __name__ == '__main__':
    unittest.main()
