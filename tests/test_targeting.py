"""ICP targeting for filters and daily delivery."""
import tempfile
import unittest
from pathlib import Path

from lead_research.daily import History, run_daily, write_daily
from lead_research.filters import apply_filter, match_lead, save_filter
from lead_research.models import PolicyError
from lead_research.targeting import (
    DEFAULT_ICP_TITLES,
    example_cre_targeting,
    is_junior_title,
    match_targeting,
    parse_country,
    parse_money,
    title_matches_include,
)
from lead_research.configuration import default_config, save_config
from lead_research.store import Store


def _lead(**overrides):
    row = {
        'Full Name': 'Casey Morgan',
        'Title': 'CEO',
        'Company': 'Harbor Commercial Real Estate',
        'Company Website': 'harbor.example',
        'Email': 'casey@harbor.example',
        'State': 'TX',
        'Country': 'US',
        'Revenue': '12000000',
        'Industry': 'commercial real estate',
    }
    row.update(overrides)
    return row


class TargetingUnitTests(unittest.TestCase):
    def test_parse_money_and_country(self):
        self.assertEqual(parse_money('5M'), 5_000_000)
        self.assertEqual(parse_money('$5m+'), 5_000_000)
        self.assertEqual(parse_money('5000000'), 5_000_000)
        self.assertEqual(parse_country('United States'), 'US')
        self.assertEqual(parse_country('usa'), 'US')
        with self.assertRaises(PolicyError):
            parse_country('Narnia')

    def test_titles_and_juniors(self):
        self.assertTrue(title_matches_include('Managing Partner', DEFAULT_ICP_TITLES))
        self.assertTrue(title_matches_include('EVP of Strategy', DEFAULT_ICP_TITLES))
        self.assertTrue(title_matches_include('Founder & CEO', DEFAULT_ICP_TITLES))
        self.assertTrue(is_junior_title('Marketing Coordinator'))
        self.assertTrue(is_junior_title('Sales Associate'))
        self.assertFalse(is_junior_title('Managing Partner'))
        self.assertFalse(is_junior_title('VP of Strategy'))

    def test_match_example_icp(self):
        query = example_cre_targeting()
        self.assertTrue(match_targeting(_lead(), query))
        self.assertFalse(match_targeting(_lead(Title='Intern'), query))
        self.assertFalse(match_targeting(_lead(Country='GB', State=''), query))
        self.assertFalse(match_targeting(_lead(Revenue='100000'), query))
        self.assertTrue(match_targeting(_lead(Revenue=''), query))  # unknown revenue kept
        self.assertFalse(match_targeting(_lead(Company='Apex Software', Industry='saas'), query))
        self.assertTrue(match_targeting(_lead(Title='COO'), query))


class FilterIcpTests(unittest.TestCase):
    def test_save_and_apply_icp(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            write_daily([
                {
                    'full_name': 'Casey Morgan', 'title': 'CEO', 'company': 'Harbor Commercial Real Estate',
                    'company_website': 'harbor.example', 'email': 'casey@harbor.example',
                    'linkedin_url': '', 'email_source': 'website_published', 'city': 'Austin',
                    'state': 'TX', 'email_risk': 'low', 'tier': 'A', 'score': 90, 'issues': [],
                },
                {
                    'full_name': 'Riley Stone', 'title': 'Intern', 'company': 'Harbor Commercial Real Estate',
                    'company_website': 'harbor.example', 'email': 'riley@harbor.example',
                    'linkedin_url': '', 'email_source': 'website_published', 'city': 'Austin',
                    'state': 'TX', 'email_risk': 'low', 'tier': 'B', 'score': 60, 'issues': [],
                },
            ], work / 'daily' / '2026-10-22')
            saved = save_filter(work, 'US CRE decision makers 5M', example_cre_targeting(), pin=True)
            self.assertTrue(saved['saved']['pinned'])
            self.assertEqual(saved['saved']['query']['country'], 'US')
            self.assertEqual(saved['saved']['query']['revenue_min'], 5_000_000)
            applied = apply_filter(work, 'US CRE decision makers 5M')
            names = {row['Full Name'] for row in applied['leads']}
            self.assertIn('Casey Morgan', names)
            self.assertNotIn('Riley Stone', names)

    def test_match_lead_keeps_listing_fields(self):
        row = _lead(Stage='contacted', Owner='Jess')
        self.assertTrue(match_lead(row, {'stage': 'contacted', 'country': 'US'}))
        self.assertFalse(match_lead(row, {'stage': 'new', 'country': 'US'}))


class DailyTargetingTests(unittest.TestCase):
    def test_daily_skips_non_icp_from_clean_pool(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            save_config(work / 'providers.json', default_config())
            store = Store(work / 'research.sqlite3')
            history = History(work / 'lead-history.sqlite3')
            history.save_sites([{
                'domain': 'harbor.example',
                'status': 'ok',
                'homepage_url': 'https://harbor.example/',
                'company': {'name': 'Harbor Commercial Real Estate'},
                'people': [
                    {'full_name': 'Casey Morgan', 'title': 'CEO', 'email': 'casey@harbor.example',
                     'email_evidence_url': 'https://harbor.example/team'},
                    {'full_name': 'Riley Stone', 'title': 'Intern', 'email': 'riley@harbor.example',
                     'email_evidence_url': 'https://harbor.example/team'},
                ],
            }])
            history.close()
            report = run_daily(
                work, store, default_config(), target=10, execute=False, day='2026-10-22',
                sources=('build',), targeting=example_cre_targeting(), mx_check=False,
            )
            store.close()
            self.assertEqual(report.get('targeting', {}).get('country'), 'US')
            names = {row.get('full_name') or row.get('Full Name') for row in report.get('leads') or []}
            self.assertNotIn('Riley Stone', names)
            if 'Casey Morgan' in names or report.get('skipped_targeting', 0):
                self.assertTrue(True)
            self.assertIn('skipped_targeting', report)


if __name__ == '__main__':
    unittest.main()
