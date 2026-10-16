import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lead_research.cli import main
from lead_research.configuration import default_config
from lead_research.daily import History, attach_linkedin, linkedin_match_jobs, run_daily
from lead_research.store import Store


def person(first, last, domain, title='Owner', email=True):
    full = f'{first} {last}'
    return {'full_name': full, 'title': title, 'email': f'{first.lower()}@{domain}' if email else None,
            'linkedin_url': None, 'evidence_url': f'https://{domain}/team', 'method': 'mailto'}


def site(domain, people, name=None):
    return {'domain': domain, 'status': 'ok', 'company': {'name': name or domain.split('.')[0].title() + ' Realty'},
            'people': people, 'pattern': None, 'pages_fetched': 2, 'errors': []}


class FakeFetcher:
    """Serves synthetic team pages; counts requests."""
    def __init__(self, sites):
        self.sites = sites
        self.calls = 0

    def get(self, url):
        from lead_research.webfetch import FetchResult
        self.calls += 1
        host = url.split('/')[2].removeprefix('www.')
        people = self.sites.get(host)
        if people is None:
            return FetchResult(url, url, None, '', '', error='network_error')
        cards = ''.join(f'<div><h3>{p[0]}</h3><p>{p[1]}</p><a href="mailto:{p[2]}">{p[2]}</a></div>' for p in people)
        html = f'<html><head><title>{host} Realty</title></head><body><a href="/team">Our Team</a>{cards}</body></html>'
        return FetchResult(url, url, 200, 'text/html', html)

    def close(self):
        pass


class DailyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name) / 'ws'
        self.work.mkdir()
        self.store = Store(self.work / 'research.sqlite3')
        self.config = default_config()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def seed(self, sites):
        h = History(self.work / 'lead-history.sqlite3')
        h.save_sites(sites)
        h.close()

    def test_preview_does_not_record_delivery(self):
        self.seed([site('harbor.example', [person('Jordan', 'Blake', 'harbor.example')])])
        report = run_daily(self.work, self.store, self.config, target=5, sources=('build',))
        self.assertEqual(report['delivered'], 1)
        self.assertTrue(report['files']['leads'].endswith('-preview/leads.csv'))
        again = run_daily(self.work, self.store, self.config, target=5, sources=('build',))
        self.assertEqual(again['delivered'], 1)

    def test_execute_records_and_never_repeats(self):
        self.seed([site('harbor.example', [person('Jordan', 'Blake', 'harbor.example'), person('Casey', 'Morgan', 'harbor.example', 'President')])])
        first = run_daily(self.work, self.store, self.config, target=1, sources=('build',), execute=True, day='2026-01-01')
        second = run_daily(self.work, self.store, self.config, target=5, sources=('build',), execute=True, day='2026-01-02')
        third = run_daily(self.work, self.store, self.config, target=5, sources=('build',), execute=True, day='2026-01-03')
        self.assertEqual((first['delivered'], second['delivered'], third['delivered']), (1, 1, 0))
        self.assertEqual(third['shortfall'], 5)
        self.assertTrue(third['advice'])

    def test_requires_work_email_by_default(self):
        self.seed([site('harbor.example', [person('Jordan', 'Blake', 'harbor.example', email=False)])])
        self.assertEqual(run_daily(self.work, self.store, self.config, target=5, sources=('build',))['delivered'], 0)
        self.assertEqual(run_daily(self.work, self.store, self.config, target=5, sources=('build',), require_email=False)['delivered'], 0,
                         'no email and no LinkedIn is still rejected by quality rules')

    def test_crawl_step_uses_backlog_and_fake_fetcher(self):
        h = History(self.work / 'lead-history.sqlite3')
        h.add_domains(['harbor.example', 'ridge.example', 'gmail.com'], 'records')
        h.close()
        fetcher = FakeFetcher({'harbor.example': [('Jordan Blake', 'Founder & CEO', 'jordan@harbor.example')],
                               'ridge.example': [('Casey Morgan', 'Managing Broker', 'casey@ridge.example')]})
        report = run_daily(self.work, self.store, self.config, target=10, sources=('crawl', 'build'), execute=True,
                           fetcher=fetcher, day='2026-01-01')
        self.assertEqual(report['steps']['crawl']['domains_planned'], 2)
        self.assertEqual(report['delivered'], 2)
        h = History(self.work / 'lead-history.sqlite3')
        self.assertEqual(h.backlog(), 0)
        h.close()

    def test_provider_sources_skip_without_budget_and_send_nothing(self):
        with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('network')):
            report = run_daily(self.work, self.store, self.config, target=5, sources=('hunter-discover', 'osm', 'build'), execute=True,
                               day='2026-01-01')
        self.assertEqual(report['steps']['hunter-discover']['sent'], 0)
        self.assertIn('no_eligible_funded_provider', report['steps']['hunter-discover']['skipped_reasons'])

    def test_market_rotation(self):
        h = History(self.work / 'lead-history.sqlite3')
        first = h.next_markets('hunter_state', ['TX', 'FL', 'NY'], 2)
        for m in first:
            h.mark_market('hunter_state', m, '2026-01-01')
        self.assertEqual(h.next_markets('hunter_state', ['TX', 'FL', 'NY'], 1), ['NY'])
        h.close()

    def test_record_sources_feed_backlog_and_prospects(self):
        records = [{'source': 'tx-trec', 'companies': [{'organization': 'Harbor Realty', 'domain': 'harbor.example'}],
                    'prospects': [{'full_name': 'Jordan Blake', 'title': 'Designated Broker', 'company': 'Harbor Realty',
                                   'company_website': 'harbor.example', 'email': 'jordan@harbor.example', 'source': 'tx-trec'}]}]
        report = run_daily(self.work, self.store, self.config, target=5, sources=('records', 'build'), record_sources=records)
        self.assertEqual((report['steps']['records']['domains_added'], report['steps']['records']['prospects_added']), (1, 1))
        self.assertEqual(report['delivered'], 1)

    def test_linkedin_helpers(self):
        rows = [{'full_name': 'Jordan Blake', 'company': 'Harbor Realty Group', 'linkedin_url': ''}]
        jobs = linkedin_match_jobs(rows)
        self.assertIn('"Jordan Blake" "Harbor Realty Group"', jobs[0]['payload']['query'])
        filled = attach_linkedin(rows, [{'full_name': 'Jordan Blake', 'company': 'Harbor Realty Group LLC',
                                         'linkedin_url': 'https://www.linkedin.com/in/jordan-blake-example/'},
                                        {'full_name': 'Jordan Blake', 'company': 'Other Co',
                                         'linkedin_url': 'https://www.linkedin.com/in/jordan-blake-other/'}])
        self.assertEqual(filled, 1)
        self.assertTrue(rows[0]['linkedin_url'].endswith('/jordan-blake-example/'))

    def test_history_import_cli(self):
        csv_path = Path(self.tmp.name) / 'old.csv'
        csv_path.write_text('Full Name,Title,Company,Company Website,LinkedIn URL,Email\n'
                            'Jordan Blake,Owner,Harbor Realty,harbor.example,,jordan@harbor.example\n')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(['--workspace', str(self.work), 'init'])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(['--workspace', str(self.work), 'history', 'import', str(csv_path)])
        self.assertEqual(json.loads(out.getvalue())['rows'], 1)
        self.seed([site('harbor.example', [person('Jordan', 'Blake', 'harbor.example')])])
        self.assertEqual(run_daily(self.work, self.store, self.config, target=5, sources=('build',))['delivered'], 0)

    def test_public_record_firm_resolved_then_delivered(self):
        from lead_research.models import Response
        self.config['providers']['serper']['enabled'] = True
        self.store.confirm_budget('serper', 'queries', '5', 'synthetic', attested_free=True)

        class Serper:
            calls = 0
            def send(self, request):
                Serper.calls += 1
                return Response(200, {'organic': [
                    {'title': 'Harbor Realty Group | Austin Texas Real Estate', 'link': 'https://harborrealty.example/', 'snippet': 'Travis County Texas brokerage', 'position': 1}]})
        records = [{'source': 'tx-trec',
                    'companies': [{'organization': 'Harbor Realty Group', 'county': 'Travis', 'state': 'TX', 'source': 'tx-trec', 'decision_maker': 'Jordan Blake'}],
                    'prospects': [{'full_name': 'Jordan Blake', 'title': 'Designated Broker', 'company': 'Harbor Realty Group', 'state': 'TX', 'source': 'tx-trec'}]}]
        fetcher = FakeFetcher({'harborrealty.example': [('Jordan Blake', 'Designated Broker', 'jordan@harborrealty.example')]})
        report = run_daily(self.work, self.store, self.config, target=5, execute=True, day='2026-01-01', record_sources=records,
                           sources=('records', 'resolve', 'crawl', 'build'), env={'SERPER_API_KEY': 'SYNTHETIC_TEST'},
                           transport=Serper(), fetcher=fetcher, record_source_names=())
        self.assertEqual(report['steps']['records']['firms_added'], 1)
        self.assertEqual(report['steps']['resolve']['resolved'], 1)
        self.assertEqual(report['delivered'], 1)
        self.assertEqual(Serper.calls, 1)
        again = run_daily(self.work, self.store, self.config, target=5, execute=True, day='2026-01-02', record_sources=records,
                          sources=('records', 'resolve', 'build'), env={'SERPER_API_KEY': 'SYNTHETIC_TEST'}, transport=Serper(), record_source_names=())
        self.assertEqual((again['delivered'], Serper.calls), (0, 1))

    def test_sec_source_needs_user_agent(self):
        report = run_daily(self.work, self.store, self.config, target=5, execute=True, day='2026-01-01', sources=('records', 'build'),
                           record_source_names=('sec-form-d',), env={})
        self.assertIn('SEC_USER_AGENT', report['steps']['records']['sources']['sec-form-d'])


if __name__ == '__main__':
    unittest.main()
