import csv
import tempfile
import unittest
from pathlib import Path

from lead_research import emailcheck, quality
from lead_research.configuration import default_config
from lead_research.daily import History, _selection_key, annotate_email_risk, discover_locations, fill_locations, run_daily
from lead_research.emailcheck import TYPE_MX
from lead_research.store import Store


def rec(**values):
    base = {'full_name': 'Jane Doe', 'title': 'Owner', 'company': 'Harbor Realty', 'company_website': 'harbor.test',
            'email': 'jane@harbor.test', 'email_source': 'website_published'}
    base.update(values)
    return base


class EmailRiskTests(unittest.TestCase):
    def risk(self, mx=None, **values):
        return quality.email_risk(rec(**values), mx)

    def test_high_risk_cases(self):
        self.assertEqual(self.risk(email='info@harbor.test'), ('high', 'role_inbox'))
        self.assertEqual(self.risk(email='office@harbor.test', email_source=''), ('high', 'role_inbox'))
        self.assertEqual(self.risk(email='@'.join(('jane.doe', 'gmail.com'))), ('high', 'not_work_email'))
        self.assertEqual(self.risk({'has_mx': False, 'null_mx': True}), ('high', 'null_mx'))
        self.assertEqual(self.risk({'has_mx': False, 'error': 'nxdomain'}), ('high', 'no_mail_server'))
        self.assertEqual(self.risk(email_verification='provider_invalid'), ('high', 'verifier_invalid'))
        self.assertEqual(self.risk(email_source='pattern'), ('high', 'unverified_guess'))

    def test_unknown_dns_never_drops(self):
        self.assertEqual(self.risk({'has_mx': None, 'error': 'dns_error'})[0], 'low')

    def test_low_and_medium(self):
        self.assertEqual(self.risk(), ('low', 'published_name_based'))
        self.assertEqual(self.risk(email_verification='accept_all'), ('low', 'published_name_based'))  # company printed it
        self.assertEqual(self.risk(email='dre@harbor.test', full_name='Andre Smith'), ('medium', 'published_not_name_based'))
        self.assertEqual(self.risk(email_source='pattern_verified', email_verification='valid'), ('low', 'verified_valid'))
        self.assertEqual(self.risk(email_source='deepline_finder', email_verification='accept_all'), ('medium', 'catch_all'))
        self.assertEqual(self.risk(email_source='hunter_finder', email_catch_all=True), ('medium', 'catch_all'))
        self.assertEqual(self.risk(email_source='hunter_finder'), ('medium', 'finder_unverified'))
        self.assertEqual(self.risk(email_source='hunter_finder', email_verification='valid'), ('low', 'verified_valid'))
        self.assertEqual(self.risk(email_source='tomba', email_verification='unknown'), ('medium', 'verifier_unsure'))
        self.assertEqual(self.risk(email_source=''), ('medium', 'unverified_source'))
        self.assertEqual(self.risk(email=''), ('', 'no_email'))


class CompanyTypeTests(unittest.TestCase):
    def test_name_head_noun_decides(self):
        cases = {
            'Capital Realty Group': 'brokerage', 'Realty Capital Partners LLC': 'investor',
            'Harvard Investments': 'investor', 'ABC Property Management': 'property_manager',
            'Toll Brothers Development': 'developer', 'Elm Tree Communities': 'developer',
            'First Integrity Title Company': 'other', 'Coastal Mortgage & Realty Lending': 'other',
            'Keller Williams Austin': 'brokerage', 'Lone Star Commercial Real Estate': 'brokerage',
            'Smith & Co': 'other', 'Peak Apartments LLC': 'property_manager',
        }
        for company, expected in cases.items():
            self.assertEqual(quality.company_type({'company': company}), expected, company)

    def test_fallbacks(self):
        self.assertEqual(quality.company_type({'company': 'Smith & Co', 'source': 'tx-trec'}), 'brokerage')
        self.assertEqual(quality.company_type({'company': 'Smith & Co', 'source': 'website; sec-form-d'}), 'investor')
        self.assertEqual(quality.company_type({'company': 'Smith & Co', 'company_description': 'We manage apartments and rentals'}),
                         'property_manager')
        self.assertEqual(quality.company_type({'company': 'Smith & Co', 'title': 'Designated Broker'}), 'brokerage')
        self.assertEqual(quality.company_type({'company': 'Acme', 'company_website': 'acmerealty.test'}), 'brokerage')
        self.assertEqual(quality.company_type({'company': 'Acme', 'company_website': 'acmecapital.test'}), 'investor')
        self.assertIn(quality.company_type({}), quality.COMPANY_TYPES)
        self.assertEqual(quality.company_type({'company': 'Sunrise Homes', 'company_description': 'Proudly serving our communities'}), 'other')


class LocationSourceConfidenceTests(unittest.TestCase):
    def test_city_state(self):
        self.assertEqual(quality.lead_city_state({'location': 'Scottsdale, AZ'}), ('Scottsdale', 'AZ'))
        self.assertEqual(quality.lead_city_state({'location': 'AUSTIN, TX'}), ('Austin', 'TX'))
        self.assertEqual(quality.lead_city_state({'location': 'Austin TX'}), ('Austin', 'TX'))
        self.assertEqual(quality.lead_city_state({'location': 'Harris County, TX'}), ('', 'TX'))
        self.assertEqual(quality.lead_city_state({'location': 'New York, New York, USA'}), ('New York', 'NY'))
        self.assertEqual(quality.lead_city_state({'city': 'Scottsdale', 'state': 'Arizona'}), ('Scottsdale', 'AZ'))
        self.assertEqual(quality.lead_city_state({'state': 'Ontario', 'city': 'Toronto'}), ('Toronto', ''))
        self.assertEqual(quality.lead_city_state({}), ('', ''))
        self.assertEqual(quality.us_state_code('AZ 85255'), 'AZ')

    def test_source(self):
        self.assertEqual(quality.lead_source({'email_source': 'website_published', 'source': 'tx-trec'}), 'website')
        self.assertEqual(quality.lead_source({'email_source': 'deepline_finder'}), 'finder')
        self.assertEqual(quality.lead_source({'email_source': 'pattern_verified', 'source': 'tx-trec'}), 'public_record')
        self.assertEqual(quality.lead_source({'email_source': 'pattern_verified', 'source': 'website'}), 'verified_guess')
        self.assertEqual(quality.lead_source({'source': 'xray'}), 'linkedin')
        self.assertEqual(quality.lead_source({'source': 'apollo'}), 'finder')
        self.assertEqual(quality.lead_source({'source': 'exa'}), 'search')

    def test_confidence(self):
        self.assertEqual(quality.lead_confidence({'email_risk': 'low', 'tier': 'A', 'email_evidence_url': 'x'}), 'high')
        self.assertEqual(quality.lead_confidence({'email_risk': 'low', 'tier': 'B', 'linkedin_url': 'x'}), 'high')
        self.assertEqual(quality.lead_confidence({'email_risk': 'low', 'tier': 'B'}), 'medium')
        self.assertEqual(quality.lead_confidence({'email_risk': 'medium', 'tier': 'A', 'linkedin_url': 'x'}), 'medium')
        self.assertEqual(quality.lead_confidence({'email_risk': 'high', 'tier': 'A'}), 'low')
        self.assertEqual(quality.lead_confidence({'email_risk': '', 'tier': 'A'}), 'low')

    def test_daily_row_extends_clean_columns(self):
        r = quality.assess(rec(city='Austin', state='TX'))
        row = quality.daily_row({**r, 'email_risk': 'low'})
        self.assertEqual(len(row), len(quality.DAILY_HEADERS))
        self.assertEqual(row[:6], quality.output_row(r))
        self.assertEqual(row[6:], ['Austin', 'TX', 'brokerage', 'website', 'high' if r['tier'] == 'A' else 'medium'])
        self.assertEqual(len(quality.HEADERS), 6)  # the clean command's contract is unchanged

    def test_selection_prefers_low_risk(self):
        low_b = {'email': 'a@x.test', 'email_risk': 'low', 'tier': 'B', 'score': 60}
        med_a = {'email': 'b@x.test', 'email_risk': 'medium', 'tier': 'A', 'score': 95}
        low_a = {'email': 'c@x.test', 'email_risk': 'low', 'tier': 'A', 'score': 80}
        none = {'email': '', 'tier': 'A', 'score': 99}
        ordered = sorted([none, med_a, low_b, low_a], key=_selection_key)
        self.assertEqual(ordered, [low_a, low_b, med_a, none])

    def test_annotate_uses_catch_all_context_and_mx(self):
        records = [quality.assess(rec(email_source='hunter_finder', email='jane@harbor.test')),
                   quality.assess(rec(full_name='Bob Lane', email='bob@dead.test', company_website='dead.test'))]
        context = {'catch_all_emails': set(), 'catch_all_domains': {'harbor.test'}, 'checked_domains': set()}
        annotate_email_risk(records, {'dead.test': {'has_mx': False, 'null_mx': True, 'provider': ''},
                                      'harbor.test': {'has_mx': True, 'provider': 'google'}}, context)
        self.assertEqual((records[0]['email_risk'], records[0]['mx_provider']), ('medium', 'google'))
        self.assertEqual((records[1]['email_risk'], records[1]['email_risk_reason']), ('high', 'null_mx'))
        self.assertEqual(records[0]['company_type'], 'brokerage')


class FakeResolver:
    def __init__(self, null_mx=()):
        self.null_mx = set(null_mx)
        self.calls = 0

    def query(self, name, qtype):
        self.calls += 1
        if name in self.null_mx:
            return 0, [(TYPE_MX, (0, ''))]
        return 0, [(TYPE_MX, (10, 'aspmx.l.google.com'))]


def site(domain, people, name, city='', region='', description=''):
    return {'domain': domain, 'status': 'ok', 'people': people, 'pattern': None, 'pages_fetched': 2, 'errors': [],
            'company': {'name': name, 'address_locality': city or None, 'address_region': region or None,
                        'description': description or None}}


def person(full, title, email, domain):
    return {'full_name': full, 'title': title, 'email': email, 'linkedin_url': None,
            'evidence_url': f'https://{domain}/team', 'method': 'mailto'}


class DailySafetyTests(unittest.TestCase):
    def setUp(self):
        emailcheck.clear_cache()
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name) / 'ws'
        self.work.mkdir()
        self.store = Store(self.work / 'research.sqlite3')
        self.config = default_config()
        h = History(self.work / 'lead-history.sqlite3')
        h.save_sites([
            site('harborrealty.test', [person('Jane Doe', 'Owner', 'jane@harborrealty.test', 'harborrealty.test'),
                                      person('Andre Smith', 'Managing Broker', 'dre@harborrealty.test', 'harborrealty.test')],
                 'Harbor Realty', 'Austin', 'Texas'),
            site('deadmailcapital.test', [person('Bob Lane', 'Managing Partner', 'bob@deadmailcapital.test', 'deadmailcapital.test')],
                 'Deadmail Capital'),
        ])
        h.add_prospects([{'full_name': 'Rita Stone', 'title': 'Owner', 'company': 'Bay Realty', 'company_website': 'bayrealty.test',
                          'email': 'info@bayrealty.test', 'linkedin_url': 'https://www.linkedin.com/in/rita-stone', 'source': 'apollo'}])
        h.close()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()
        emailcheck.clear_cache()

    def test_unsafe_emails_dropped_and_columns_written(self):
        resolver = FakeResolver(null_mx={'deadmailcapital.test'})
        report = run_daily(self.work, self.store, self.config, target=10, sources=('build',), day='2026-01-05',
                           mx_resolver=resolver)
        self.assertGreater(resolver.calls, 0)
        safety = report['steps']['email-safety']
        self.assertTrue(safety['mx_check'])
        self.assertEqual(safety['dropped'].get('null_mx'), 1)
        self.assertEqual(safety['dropped'].get('role_inbox'), 1)
        with open(report['files']['leads'], newline='', encoding='utf-8') as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(list(rows[0].keys()), quality.DAILY_HEADERS)
        self.assertEqual([r['Full Name'] for r in rows], ['Jane Doe', 'Andre Smith'])  # low risk before medium
        jane = rows[0]
        self.assertEqual((jane['City'], jane['State'], jane['Company Type'], jane['Source']), ('Austin', 'TX', 'brokerage', 'website'))
        self.assertIn(jane['Confidence'], ('high', 'medium'))
        self.assertEqual(rows[1]['Confidence'], 'medium')
        with open(report['files']['details'], newline='', encoding='utf-8') as handle:
            details = list(csv.DictReader(handle))
        self.assertEqual(details[0]['Email Risk'], 'low')
        self.assertEqual(details[0]['MX Provider'], 'google')
        self.assertEqual(details[1]['Email Risk Reason'], 'published_not_name_based')
        self.assertEqual(report['delivered_by_risk'], {'low': 1, 'medium': 1})
        self.assertIn('Dropped unsafe emails', (Path(report['files']['leads']).parent / 'report.md').read_text())
        # cached for next time: no new DNS queries
        emailcheck.clear_cache()
        before = resolver.calls
        run_daily(self.work, self.store, self.config, target=10, sources=('build',), day='2026-01-05', mx_resolver=resolver)
        self.assertEqual(resolver.calls, before)

    def test_preview_without_resolver_skips_dns(self):
        report = run_daily(self.work, self.store, self.config, target=10, sources=('build',), day='2026-01-05')
        self.assertFalse(report['steps']['email-safety']['mx_check'])
        self.assertEqual(report['delivered'], 3)  # role inbox still dropped; null MX unknown without DNS
        self.assertEqual(report['steps']['email-safety']['dropped'], {'role_inbox': 1})

    def test_verify_step_plans_risk_first(self):
        report = run_daily(self.work, self.store, self.config, target=10, sources=('verify', 'build'), day='2026-01-05',
                           mx_resolver=FakeResolver(null_mx={'deadmailcapital.test'}))
        step = report['steps']['verify']
        self.assertEqual(step['priority'], {'published_weak_unknown_domain': 1, 'published_unchecked': 1})
        self.assertEqual(step['not_worth_checking'], {'no_mail_server': 1})
        self.assertEqual(step['sent'], 0)  # preview: nothing sent


class DiscoverLocationTests(unittest.TestCase):
    def test_locations_from_single_hq_filter(self):
        class FakeStore:
            def attempts(self):
                def att(include, domains, state='completed', provider='hunter'):
                    return {'provider': provider, 'operation': 'discover', 'state': state,
                            'payload': {'filters': {'headquarters_location': {'include': include}}},
                            'outcome': {'fields': {'companies': [{'domain': d} for d in domains]}}}
                return [att([{'country': 'US', 'state': 'TX'}], ['statewide.test', 'austinco.test']),
                        att([{'city': 'Austin', 'country': 'US', 'state': 'TX'}], ['austinco.test']),
                        att([{'country': 'US', 'state': 'AZ'}, {'country': 'US', 'state': 'NV'}], ['multi.test']),
                        att([{'country': 'US', 'state': 'FL'}], ['failed.test'], state='uncertain')]
        places = discover_locations(FakeStore())
        self.assertEqual(places, {'statewide.test': ('', 'TX'), 'austinco.test': ('Austin', 'TX')})
        records = [{'company_website': 'austinco.test'}, {'company_website': 'statewide.test', 'location': 'Dallas, TX'},
                   {'company_website': 'austinco.test', 'state': 'TX'}, {'company_website': 'other.test'}]
        self.assertEqual(fill_locations(records, places), 2)
        self.assertEqual([quality.lead_city_state(r) for r in records],
                         [('Austin', 'TX'), ('Dallas', 'TX'), ('Austin', 'TX'), ('', '')])


if __name__ == '__main__':
    unittest.main()
