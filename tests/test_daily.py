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

    def test_same_day_execute_merges_instead_of_overwrite(self):
        self.seed([site('harbor.example', [
            person('Jordan', 'Blake', 'harbor.example'),
            person('Casey', 'Morgan', 'harbor.example', 'President'),
            person('Riley', 'Quinn', 'harbor.example', 'Managing Broker'),
        ])])
        first = run_daily(self.work, self.store, self.config, target=1, sources=('build',), execute=True, day='2026-01-01')
        second = run_daily(self.work, self.store, self.config, target=3, sources=('build',), execute=True, day='2026-01-01')
        self.assertEqual(first['delivered'], 1)
        self.assertEqual(second['prior_delivered'], 1)
        self.assertEqual(second['this_run'], 2)
        self.assertEqual(second['delivered'], 3)
        self.assertEqual(second['shortfall'], 0)
        from lead_research.quality import read_rows
        rows = read_rows(second['files']['leads'])
        names = {r['Full Name'] for r in rows}
        self.assertEqual(len(names), 3)
        self.assertIn('Jordan Blake', names)
        self.assertTrue((self.work / 'daily' / '2026-01-01' / 'runs').is_dir())
        third = run_daily(self.work, self.store, self.config, target=3, sources=('build',), execute=True, day='2026-01-01')
        self.assertEqual((third['delivered'], third['this_run'], third['shortfall']), (3, 0, 0))

    def test_jina_read_follows_team_page(self):
        import json as _json
        from lead_research.models import Response
        self.config['providers']['jina']['enabled'] = True
        self.store.confirm_budget('jina', 'reads', '2', 'synthetic keyless', attested_free=True)
        home = ('<html><head><title>Ridge Realty</title></head><body>'
                '<a href="/team">Our Team</a><p>Welcome to Ridge Realty.</p></body></html>')
        team = ('<html><head><title>Team</title></head><body>'
                '<div><h3>Casey Morgan</h3><p>President</p>'
                '<a href="mailto:casey@ridge.example">casey@ridge.example</a></div></body></html>')
        seen = []

        class Jina:
            def send(self, request):
                body = _json.loads(request.body)
                seen.append(body['url'])
                html = team if '/team' in body['url'] else home
                return Response(200, {'code': 200, 'data': {'html': html, 'url': body['url'], 'title': 'Ridge'}})

        h = History(self.work / 'lead-history.sqlite3')
        h.add_domains(['ridge.example'], 'records')
        h.save_sites([{'domain': 'ridge.example', 'status': 'ok', 'company': {'name': 'Ridge Realty'},
                       'people': [], 'pattern': None, 'pages_fetched': 1, 'errors': []}])
        h.close()
        report = run_daily(self.work, self.store, self.config, target=5, sources=('jina-read', 'build'),
                           execute=True, day='2026-01-01', transport=Jina(), env={})
        self.assertEqual(report['steps']['jina-read']['reads']['sent'], 1)
        self.assertEqual(report['steps']['jina-read']['team_pages']['sent'], 1)
        self.assertEqual(report['steps']['jina-read']['people'], 1)
        self.assertEqual(report['delivered'], 1)
        self.assertEqual(seen, ['https://ridge.example/', 'https://ridge.example/team'])

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
        self.assertIn('"Jordan Blake" "Harbor Realty Group"', jobs[0]['payload']['queries'][0])
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
                import json as _json
                Serper.calls += 1
                body = _json.loads(request.body)
                organic = [{'title': 'Harbor Realty Group | Austin Texas Real Estate', 'link': 'https://harborrealty.example/',
                            'snippet': 'Travis County Texas brokerage', 'position': 1}]
                if isinstance(body, list):
                    return Response(200, [{'searchParameters': {'q': item['q']}, 'organic': organic} for item in body])
                return Response(200, {'organic': organic})
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

    def test_guess_runs_before_serper_and_only_unmatched_are_searched(self):
        import json as _json
        from lead_research.models import Response
        from lead_research.webfetch import FetchResult
        self.config['providers']['serper']['enabled'] = True
        self.store.confirm_budget('serper', 'queries', '5', 'synthetic', attested_free=True)
        searched = []

        class Serper:
            def send(self, request):
                body = _json.loads(request.body)
                items = body if isinstance(body, list) else [body]
                searched.extend(item['q'] for item in items)
                return Response(200, [{'searchParameters': {'q': item['q']}, 'organic': []} for item in items])

        home = ('<html><head><title>Harbor Realty Group | Austin</title></head><body><h1>Harbor Realty Group</h1>'
                + 'Homes for sale. ' * 30 + '<footer>&copy; 2026 Harbor Realty Group, Austin, TX 78701</footer></body></html>')

        class Guesser:
            urls = []
            def get(self, url):
                Guesser.urls.append(url)
                if url == 'https://harborrealtygroup.com/':
                    return FetchResult(url, url, 200, 'text/html', home)
                return FetchResult(url, url, None, '', '', error='network_error')
            def close(self):
                pass

        def dns(host, port, type=None):
            if host.removeprefix('www.') == 'harborrealtygroup.com':
                return [(2, 1, 6, '', ('203.0.113.7', port))]
            raise OSError('nxdomain')

        records = [{'source': 'tx-trec',
                    'companies': [{'organization': 'Harbor Realty Group', 'city': 'Austin', 'county': 'Travis', 'state': 'TX',
                                   'source': 'tx-trec', 'license_number': 'G1', 'decision_maker': 'Jordan Blake'},
                                  {'organization': 'Quillstone Ventures', 'county': 'Bexar', 'state': 'TX',
                                   'source': 'tx-trec', 'license_number': 'G2', 'decision_maker': 'Casey Morgan'}],
                    'prospects': []}]
        report = run_daily(self.work, self.store, self.config, target=5, execute=True, day='2026-01-01', record_sources=records,
                           sources=('records', 'resolve', 'build'), env={'SERPER_API_KEY': 'SYNTHETIC_TEST'},
                           transport=Serper(), fetcher=Guesser(), resolver=dns, record_source_names=())
        step = report['steps']['resolve']
        self.assertEqual((step['guess_tried'], step['guess_matched']), (2, 1))
        self.assertEqual(step['by_method'], {'guess': 1})
        self.assertTrue(searched and all('Quillstone' in q for q in searched))  # only the unmatched firm is searched
        self.assertNotIn('https://quillstoneventures.com/', Guesser.urls)  # no DNS record, no fetch
        h = History(self.work / 'lead-history.sqlite3')
        rows = {r['key']: (r['status'], r['domain'], json.loads(r['data']).get('guess_tried'))
                for r in h.db.execute('SELECT key, status, domain, data FROM firms')}
        h.close()
        self.assertEqual(rows['tx-trec:G1'][:2], ('resolved', 'harborrealtygroup.com'))
        self.assertEqual(rows['tx-trec:G2'][2], '2026-01-01')
        # A firm is guessed only once: even back in the pending queue, the next run skips the guess
        h = History(self.work / 'lead-history.sqlite3')
        with h.db:
            h.db.execute("UPDATE firms SET status='pending' WHERE key='tx-trec:G2'")
        h.close()
        fetched = len(Guesser.urls)
        again = run_daily(self.work, self.store, self.config, target=5, execute=True, day='2026-01-02', record_sources=(),
                          sources=('resolve', 'build'), env={'SERPER_API_KEY': 'SYNTHETIC_TEST'},
                          transport=Serper(), fetcher=Guesser(), resolver=dns, record_source_names=())
        self.assertEqual((again['steps']['resolve']['pending_checked'], again['steps']['resolve']['guess_tried']), (1, 0))
        self.assertEqual(len(Guesser.urls), fetched)

    def test_guess_is_planned_not_fetched_in_dry_run(self):
        records = [{'source': 'tx-trec', 'companies': [{'organization': 'Harbor Realty Group', 'state': 'TX', 'source': 'tx-trec',
                                                       'license_number': 'G3'}], 'prospects': []}]
        h = History(self.work / 'lead-history.sqlite3')
        h.add_firms(records[0]['companies'], [])
        h.close()

        class Boom:
            def get(self, url):
                raise AssertionError('no fetch in dry run')
        report = run_daily(self.work, self.store, self.config, target=5, sources=('resolve', 'build'), fetcher=Boom(),
                           resolver=lambda *a, **k: (_ for _ in ()).throw(AssertionError('no dns')))
        self.assertEqual(report['steps']['resolve']['guess_planned'], 1)

    def test_sec_source_needs_user_agent(self):
        report = run_daily(self.work, self.store, self.config, target=5, execute=True, day='2026-01-01', sources=('records', 'build'),
                           record_source_names=('sec-form-d',), env={})
        self.assertIn('SEC_USER_AGENT', report['steps']['records']['sources']['sec-form-d'])

    def test_suppression_feedback_and_source_quality(self):
        self.seed([site('harbor.example', [person('Jordan', 'Blake', 'harbor.example'), person('Casey', 'Morgan', 'harbor.example', 'President')]),
                   site('ridge.example', [person('Riley', 'Stone', 'ridge.example')])])
        h = History(self.work / 'lead-history.sqlite3')
        h.suppress([{'Company Website': 'ridge.example'}], 'customer')
        h.close()
        first = run_daily(self.work, self.store, self.config, target=1, sources=('build',), execute=True, day='2026-01-01')
        self.assertEqual(first['delivered'], 1)
        delivered_email = first['files']['leads']
        import csv as _csv
        row = next(_csv.DictReader(open(delivered_email)))
        h = History(self.work / 'lead-history.sqlite3')
        counts = h.record_outcomes([{'Email': row['Email'], 'Outcome': 'hard bounce'}, {'Email': 'x@harbor.example', 'Outcome': 'weird'}])
        self.assertEqual(counts, {'bounced': 1, 'ignored': 1})
        quality_by_source = h.source_quality()
        self.assertEqual(quality_by_source['website_published']['bounced'], 1)
        h.close()
        second = run_daily(self.work, self.store, self.config, target=5, sources=('build',), execute=True, day='2026-01-02')
        self.assertEqual(second['delivered'], 1, 'ridge.example is blocked as a customer domain; one harbor person left')
        self.assertTrue(Path(second['backup']['folder'], 'lead-history.sqlite3').exists())
        self.assertIn('Leads 2026-01-02', Path(second['files']['leads']).with_name('report.md').read_text())

    def test_recurring_refill_resets_once_per_period(self):
        h = History(self.work / 'lead-history.sqlite3')
        h.add_refill('reoon', 'credits', '20', 'daily', 'Reoon free plan: 20 credits per day')
        self.assertEqual(h.apply_refills(self.store, '2026-01-01')['applied'], ['reoon.credits=20'])
        self.assertEqual(h.apply_refills(self.store, '2026-01-01')['applied'], [])
        budgets = {(b['provider'], b['pool']): b['remaining'] for b in self.store.budgets()}
        self.assertEqual(budgets[('reoon', 'credits')], '20')
        h.close()


if __name__ == '__main__':
    unittest.main()
