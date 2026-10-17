"""Offline speed and filtering tests: parallel crawl, verification lanes, off-topic sites."""
import tempfile
import threading
import time
import unittest
from pathlib import Path

from lead_research import providers as providers_module
from lead_research.daily import _run_verify_lanes, plan_verify_lanes
from lead_research.extract import crawl_domains
from lead_research.models import Outcome, Request, Response
from lead_research.relevance import classify_site, is_licensed_source
from lead_research.store import Store
from lead_research.webfetch import SafeFetcher

from test_webfetch import PUBLIC_IP, FakeResponse

HOME = (b'<!doctype html><html><head><title>Harbor Realty</title></head><body>'
        b'<h1>Harbor Realty</h1><p>Homes for sale and listings.</p></body></html>')
SIGNS = (b'<!doctype html><html><head><title>Fast Signs</title></head><body>'
         b'<p>Custom signs and signage. Real estate signs, banners and vehicle wraps.</p>'
         b'<a href="/team">Team</a><a href="/about">About</a></body></html>')


class SlowOpener:
    """Every request takes `delay` seconds; tracks overlap globally and per host."""

    def __init__(self, delay, pages=None):
        self.delay, self.pages = delay, pages or {}
        self.lock = threading.Lock()
        self.active, self.peak, self.per_host, self.host_peak, self.calls = 0, 0, {}, 0, []

    def open(self, request, timeout):
        host = request.full_url.split('/')[2]
        with self.lock:
            self.calls.append(request.full_url)
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.per_host[host] = self.per_host.get(host, 0) + 1
            self.host_peak = max(self.host_peak, self.per_host[host])
        try:
            time.sleep(self.delay)
        finally:
            with self.lock:
                self.active -= 1
                self.per_host[host] -= 1
        if request.full_url.endswith('/robots.txt'):
            return FakeResponse(404, {'Content-Type': 'text/plain'}, b'')
        body = self.pages.get(host, HOME)
        if request.full_url.rstrip('/').count('/') > 2:
            return FakeResponse(404, {'Content-Type': 'text/html'}, b'')
        return FakeResponse(200, {'Content-Type': 'text/html; charset=utf-8'}, body)


def resolver(host, port, *args):
    return [(2, 1, 6, '', (PUBLIC_IP, port))]


class CrawlSpeedTests(unittest.TestCase):
    def test_many_sites_overlap_but_each_host_stays_serial(self):
        opener = SlowOpener(0.05)
        fetcher = SafeFetcher(per_host_delay=0.0, opener=opener, resolver=resolver)
        domains = [f'site{i}.example' for i in range(40)]
        start = time.monotonic()
        sites = crawl_domains(domains, fetcher, max_pages=1, workers=40)
        elapsed = time.monotonic() - start
        # 2 requests per site (robots + homepage) x 0.05s: ~4s serially, ~0.1s fully parallel.
        self.assertEqual(len(sites), 40)
        self.assertTrue(all(s['status'] in ('ok', 'no_people') for s in sites))
        self.assertLess(elapsed, 1.5)
        self.assertGreater(opener.peak, 10)
        self.assertEqual(opener.host_peak, 1)  # politeness: never two requests to one host at once

    def test_connect_timeout_is_passed_to_requests(self):
        seen = []

        class Capture(SlowOpener):
            def open(self, request, timeout):
                seen.append((getattr(request, 'connect_timeout', None), timeout))
                return super().open(request, timeout)
        fetcher = SafeFetcher(per_host_delay=0.0, opener=Capture(0), resolver=resolver, timeout=15, connect_timeout=6)
        fetcher.get('https://one.example/')
        self.assertTrue(seen and all(c == 6 and t == 15 for c, t in seen))
        with self.assertRaises(ValueError):
            SafeFetcher(connect_timeout=0)

    def test_struggling_site_stops_after_consecutive_failures(self):
        class Flaky:
            def __init__(self):
                self.calls = []

            def get(self, url):
                from lead_research.webfetch import FetchResult
                self.calls.append(url)
                if url.rstrip('/').count('/') <= 2:
                    links = ''.join(f'<a href="/team{i}">Team {i}</a>' for i in range(8))
                    html = f'<html><head><title>Harbor Realty</title></head><body>Realty listings {links}</body></html>'
                    return FetchResult(url, url, 200, 'text/html', html, False, 0.0, None)
                return FetchResult(url, url, None, '', '', False, 0.0, 'timeout')
        flaky = Flaky()
        site = crawl_domains(['harbor.example'], flaky, max_pages=6)[0]
        self.assertEqual(len(flaky.calls), 3)  # homepage + 2 failing subpages, then stop
        self.assertIn('timeout', site['errors'])


class OffTopicTests(unittest.TestCase):
    def test_classifier(self):
        self.assertEqual(classify_site({'name': 'Harbor Realty', 'domain': 'harborrealty.com'}, 'Homes for sale, MLS listings')[0], 'real_estate')
        self.assertEqual(classify_site({'name': 'Fast Signs'}, SIGNS.decode())[0], 'off_topic')
        self.assertEqual(classify_site({'name': 'Peak Roofing'}, 'Roofing and roofers. Residential roofing.')[0], 'off_topic')
        self.assertEqual(classify_site({'name': 'Shield'}, 'Home warranty plans. Home warranty coverage for buyers and sellers.')[0], 'off_topic')
        # Adjacent businesses are never filtered
        self.assertEqual(classify_site({'name': 'Acme Mortgage'}, 'Mortgage lender. Home loans.')[0], 'unknown')
        self.assertEqual(classify_site({'name': 'Smith Law'}, 'Law firm handling closings')[0], 'unknown')
        # A strong real-estate signal vetoes off_topic even with some trade words
        self.assertNotEqual(classify_site({'name': 'Peak Partners'}, 'Commercial real estate brokerage. Roofing, HVAC and plumbing upgrades for tenants.')[0], 'off_topic')
        self.assertTrue(is_licensed_source('tx-trec') and is_licensed_source('records'))
        self.assertFalse(is_licensed_source('hunter-discover'))

    def test_crawl_stops_on_off_topic_unless_trusted(self):
        opener = SlowOpener(0, pages={'signs.example': SIGNS})
        fetcher = SafeFetcher(per_host_delay=0.0, opener=opener, resolver=resolver)
        site = crawl_domains(['signs.example'], fetcher, max_pages=6)[0]
        self.assertEqual(site['status'], 'off_topic')
        self.assertEqual(site['people'], [])
        self.assertEqual(site['pages_fetched'], 1)
        self.assertFalse(any('/team' in u or '/about' in u for u in opener.calls))
        opener2 = SlowOpener(0, pages={'signs.example': SIGNS})
        fetcher2 = SafeFetcher(per_host_delay=0.0, opener=opener2, resolver=resolver)
        trusted = crawl_domains(['signs.example'], fetcher2, max_pages=6, trusted={'signs.example'})[0]
        self.assertNotEqual(trusted['status'], 'off_topic')
        self.assertTrue(any('/team' in u for u in opener2.calls))


class FakeVerifier(providers_module.Provider):
    operations = ('verify',)
    origin = 'https://api.fake.example'

    def __init__(self, pid):
        self.id, self.independence_group, self.auth_env = pid, pid, (pid.upper() + '_KEY',)

    def request(self, operation, payload, env):
        return Request('GET', f'https://api.{self.id}.example/v?e={payload["email"]}', {}, None, 5)

    def parse(self, operation, response):
        if response.data.get('fail'):
            return Outcome('error', error='provider_error')
        return Outcome('provider_valid', response.data['email'])


class SlowTransport:
    def __init__(self, delay, failing=()):
        self.delay, self.failing = delay, set(failing)
        self.lock = threading.Lock()
        self.active, self.peak, self.calls = 0, 0, []

    def send(self, request):
        provider = request.url.split('.')[1]
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.calls.append(provider)
        time.sleep(self.delay)
        with self.lock:
            self.active -= 1
        email = request.url.split('e=')[1]
        return Response(200, {'email': email, 'fail': provider in self.failing})


class VerifyLaneTests(unittest.TestCase):
    IDS = ('lanea', 'laneb', 'lanec')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'research.sqlite3'
        self.store = Store(self.path)
        self.saved = dict(providers_module.PROVIDERS)
        for pid in self.IDS:
            providers_module.PROVIDERS[pid] = FakeVerifier(pid)
        self.config = {'providers': {pid: {'enabled': True, 'charges': {'verify': {'credits': '1'}}} for pid in self.IDS}}
        self.env = {pid.upper() + '_KEY': 'x' for pid in self.IDS}
        for pid, amount in zip(self.IDS, ('3', '4', '10')):
            self.store.confirm_budget(pid, 'credits', amount, 'synthetic', attested_free=True)

    def tearDown(self):
        providers_module.PROVIDERS.clear()
        providers_module.PROVIDERS.update(self.saved)
        self.store.close()
        self.tmp.cleanup()

    def emails(self, n):
        return [f'p{i}@harbor{i}.example' for i in range(n)]

    def test_plan_respects_order_budget_and_missing_providers(self):
        lanes, rest = plan_verify_lanes(self.store, self.config, self.emails(20), self.env,
                                        order=('not-a-provider', 'laneb', 'lanea', 'lanec'))
        self.assertEqual(list(lanes), ['laneb', 'lanea', 'lanec'])
        self.assertEqual([len(v) for v in lanes.values()], [4, 3, 10])
        self.assertEqual(len(rest), 3)
        self.store.pause('laneb', 'synthetic')
        lanes, _ = plan_verify_lanes(self.store, self.config, self.emails(5), self.env, order=self.IDS)
        self.assertNotIn('laneb', lanes)

    def test_lanes_overlap_and_budgets_hold(self):
        lanes, _ = plan_verify_lanes(self.store, self.config, self.emails(9), self.env, order=self.IDS)
        transport = SlowTransport(0.1)
        start = time.monotonic()
        step = _run_verify_lanes(self.store, self.config, lanes, True, self.env, transport)
        elapsed = time.monotonic() - start
        self.assertEqual(step['sent'], 9)
        self.assertGreater(transport.peak, 1)
        self.assertLess(elapsed, 0.85)  # 9 x 0.1s serially; lanes of 3/4/2 finish in ~0.4s
        budgets = {b['provider']: b['remaining_units'] for b in self.store.budgets()}
        self.assertEqual(budgets['lanea'], 0)
        self.assertEqual(budgets['laneb'], 0)
        self.assertEqual(budgets['lanec'], 8_000_000)
        self.assertTrue(self.store.verify_audit())
        self.assertFalse(any(a['state'] in ('reserved', 'sent', 'pending') for a in self.store.attempts()))

    def test_failing_provider_stops_only_its_lane(self):
        lanes, _ = plan_verify_lanes(self.store, self.config, self.emails(9), self.env, order=self.IDS)
        step = _run_verify_lanes(self.store, self.config, lanes, True, self.env, SlowTransport(0.02, failing={'laneb'}))
        self.assertTrue(step['lanes']['laneb']['stopped'])
        self.assertEqual(step['lanes']['laneb']['sent'], 1)
        self.assertEqual(step['lanes']['lanea']['sent'], 3)
        self.assertEqual(step['lanes']['lanec']['sent'], 2)
        self.assertTrue(self.store.provider_state('laneb')['paused'])
        self.assertFalse(self.store.provider_state('lanea').get('paused'))
        self.assertTrue(self.store.verify_audit())

    def test_serial_connections_keep_workspace_wide_rule(self):
        from lead_research.models import BusyError
        self.store.reserve('lanea', 'verify', 'k1', {'email': 'a@x.example'}, {'credits': '1'}, 'lanea')
        with self.assertRaises(BusyError):
            self.store.reserve('laneb', 'verify', 'k2', {'email': 'b@x.example'}, {'credits': '1'}, 'laneb')
        lane = Store(self.path, parallel_providers=True)
        try:
            lane.reserve('laneb', 'verify', 'k2', {'email': 'b@x.example'}, {'credits': '1'}, 'laneb')
            with self.assertRaises(BusyError):
                lane.reserve('laneb', 'verify', 'k3', {'email': 'c@x.example'}, {'credits': '1'}, 'laneb')
        finally:
            lane.close()


if __name__ == '__main__':
    unittest.main()
