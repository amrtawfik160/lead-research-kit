import csv
import json
import os
import stat
import tempfile
import unittest

from lead_research.quality import (
    HEADERS, Target, assess, classify_title, clean_csv, clean_records, dedupe, escape_cell, normalize_record,
    website_matches_company, write_outputs,
)
from lead_research.quality import clean_company, clean_name, clean_title, email_matches_name, normalize_linkedin


def addr(local, domain):
    """Build addresses at non-reserved domains without email literals in source."""
    return local + '@' + domain


def lead(**overrides):
    """A complete synthetic decision-maker lead on reserved domains."""
    row = {
        'Full Name': 'Jane Sample',
        'Title': 'Founder & CEO',
        'Company': 'Harbor Realty Group',
        'Company Website': 'harborrealty.example',
        'LinkedIn URL': 'https://www.linkedin.com/in/jane-sample/',
        'Email': 'jane@harborrealty.example',
    }
    row.update(overrides)
    return row


class HeaderAndAliasTests(unittest.TestCase):
    def test_header_order_is_exact(self):
        self.assertEqual(HEADERS, ['Full Name', 'Title', 'Company', 'Company Website', 'LinkedIn URL', 'Email'])

    def test_alias_keys_case_insensitive(self):
        record = normalize_record({
            'NAME': 'Ana Placeholder', 'position': 'Owner', 'Organization': 'Pine Ridge Properties',
            'domain': 'https://www.PineRidge.example/about?x=1', 'profile_url': 'linkedin.com/in/ana-placeholder',
            'Work_Email': ' Ana@PineRidge.Example ', 'email_source': 'site', 'Phone': '+1 555 0100',
            'unrelated': 'dropped',
        })
        self.assertEqual(record['full_name'], 'Ana Placeholder')
        self.assertEqual((record['first_name'], record['last_name']), ('Ana', 'Placeholder'))
        self.assertEqual(record['title'], 'Owner')
        self.assertEqual(record['company'], 'Pine Ridge Properties')
        self.assertEqual(record['company_website'], 'pineridge.example')
        self.assertEqual(record['linkedin_url'], 'https://www.linkedin.com/in/ana-placeholder/')
        self.assertEqual(record['email'], 'ana@pineridge.example')
        self.assertEqual(record['email_source'], 'site')
        self.assertEqual(record['phone'], '+1 555 0100')
        self.assertNotIn('unrelated', record)
        self.assertIsInstance(record['fixes'], list)

    def test_first_and_last_name_columns(self):
        record = normalize_record({'first_name': 'omar', 'last_name': 'EXAMPLE', 'job_title': 'President'})
        self.assertEqual(record['full_name'], 'Omar Example')

    def test_invalid_email_and_website_blanked(self):
        record = normalize_record(lead(Email='not-an-email', **{'Company Website': 'n/a'}))
        self.assertEqual(record['email'], '')
        self.assertEqual(record['company_website'], '')
        self.assertIn('email_invalid', record['flags'])
        self.assertIn('website_invalid', record['flags'])


class LinkedInTests(unittest.TestCase):
    def test_canonicalization(self):
        self.assertEqual(normalize_linkedin('https://uk.linkedin.com/in/Eva-Garz%C3%B3n-1a2b/?trk=abc#top'),
                         'https://www.linkedin.com/in/eva-garzón-1a2b/')
        self.assertEqual(normalize_linkedin('http://linkedin.com/in/jane-sample/details/experience/'),
                         'https://www.linkedin.com/in/jane-sample/')
        self.assertEqual(normalize_linkedin('ca.linkedin.com/in/JaneS'), 'https://www.linkedin.com/in/janes/')

    def test_non_profiles_rejected(self):
        for url in ('https://www.linkedin.com/company/harbor-realty/', 'https://www.linkedin.com/pub/x/1/2',
                    'https://linkedin.com.evil.example/in/jane', 'https://example.com/in/jane', 'not a url'):
            self.assertEqual(normalize_linkedin(url), '', url)


class NameTests(unittest.TestCase):
    def test_honorifics_credentials_and_casing(self):
        cases = {
            'Ms. Jane Q. Sample, MBA': 'Jane Q. Sample',
            'Dr. Alex Example PhD': 'Alex Example',
            'Lee Sample CCIM': 'Lee Sample',
            'Dana Example, P.A.': 'Dana Example',
            'j.e. sampleton': 'J.E. Sampleton',
            'Gordon H.w. Example': 'Gordon H.W. Example',
            "MARY O'BRIEN-MCDONALD": "Mary O'Brien-McDonald",
            'juan de la cruz': 'Juan de la Cruz',
            'Sam Placeholder AKA Samuel Other': 'Sam Placeholder',
            'Robert Example Jr': 'Robert Example Jr',
            'Paul Example III': 'Paul Example III',
            "Livio Sample .'.": 'Livio Sample',
        }
        for raw, expected in cases.items():
            self.assertEqual(clean_name(raw)[0], expected, raw)

    def test_nicknames_kept_as_aliases(self):
        full, first, last, aliases, fixes, flags = clean_name('Michael (Mike) Example')
        self.assertEqual(full, 'Michael Example')
        self.assertEqual(aliases, ['mike'])
        self.assertEqual(clean_name('Howard W. "Hoby" Example IV')[0], 'Howard W. Example IV')

    def test_flags(self):
        self.assertIn('name_truncated', clean_name('Taylor N.')[5])
        self.assertIn('name_truncated', clean_name('Helena K')[5])
        self.assertNotIn('name_truncated', clean_name('Simon Y Example')[5])
        self.assertIn('multiple_people', clean_name('Pat And Chris Example')[5])
        self.assertIn('multiple_people', clean_name('Pat & Chris Example')[5])
        self.assertNotIn('multiple_people', clean_name('Andy Example')[5])
        self.assertIn('name_not_person', clean_name('Harbor Real Estate')[5])


class CompanyTests(unittest.TestCase):
    def test_cleanup_cases(self):
        cases = {
            'CEO at Harbor Realty Group': 'Harbor Realty Group',
            'Founder & CEO at Pine Ridge Properties, Inc.': 'Pine Ridge Properties, Inc.',
            'Chief Executive Officer at Cedar Homes': 'Cedar Homes',
            'CEO of Birch Commercial': 'Birch Commercial',
            'Harbor Realty at Harbor Realty': 'Harbor Realty',
            'Harbor Realty, LLC. at Harbor Realty': 'Harbor Realty',
            'Maple Team at RE/MAX Summit at Maple Team...': 'Maple Team',
            'Sample Team at Keller Williams Realty': 'Sample Team',
            'Oak Realty / Compass': 'Oak Realty',
            '"Quoted Realty"': 'Quoted Realty',
            'Ocean Realty, Seattle WA': 'Ocean Realty',
            'Legal Name Holdings, Inc dba Bay Homes': 'Bay Homes',
            'Turner Real Estate Network': 'Turner Real Estate Network',
            'Carpenter/Robbins Commercial Real Estate, Inc.': 'Carpenter/Robbins Commercial Real Estate, Inc.',
            'Sample Brokerage (formerly Old Name)': 'Sample Brokerage',
        }
        for raw, expected in cases.items():
            self.assertEqual(clean_company(raw)[0], expected, raw)

    def test_flags(self):
        company, fixes, flags = clean_company('harbor-realty.example')
        self.assertEqual(company, 'Harbor Realty')
        self.assertIn('company_is_domain', flags)
        self.assertIn('company_from_domain', fixes)
        self.assertIn('company_truncated', clean_company('Maple Holdings and Partners...')[2])
        for generic in ('Real Estate company', 'Real Estate Brokerage', 'Self-employed', 'Real Estate Investor'):
            self.assertIn('company_generic', clean_company(generic)[2], generic)
        self.assertNotIn('company_generic', clean_company('Harbor Real Estate')[2])

    def test_company_from_title_when_missing(self):
        record = normalize_record(lead(Title='CEO of The Cove Real Estate Group', Company='Real Estate company'))
        self.assertEqual(record['title'], 'CEO')
        self.assertEqual(record['company'], 'The Cove Real Estate Group')


class TitleTests(unittest.TestCase):
    def test_split_and_trim(self):
        cases = {
            'President & CEO | Real Estate Agent | #0000000 | brokered by Sample Realty Ltd. Co...': 'President & CEO',
            'CEO / Partner @ Atlas Realty': 'CEO / Partner',
            'Founder @ Sample Real Estate': 'Founder',
            'CEO | Founder | Abundant Example Real Estate': 'CEO / Founder',
            'Chief Executive Officer Equity Sample Real Estate': 'Chief Executive Officer',
            "CEO, Sample's International Realty": 'CEO',
            'Chief Development Officer (CDO)': 'Chief Development Officer',
            'PHD Chief Executive Officer': 'Chief Executive Officer',
            'Founder & CEO': 'Founder & CEO',
            'Vice President of Sales': 'Vice President of Sales',
            'Director of Real Estate': 'Director of Real Estate',
        }
        for raw, expected in cases.items():
            self.assertEqual(clean_title(raw)[0], expected, raw)
        title, hint, _ = clean_title('CEO of Strong Example Real Estate')
        self.assertEqual((title, hint), ('CEO', 'Strong Example Real Estate'))
        long_title = 'Founder ' + ' '.join('word%d' % i for i in range(40))
        self.assertLessEqual(len(clean_title(long_title)[0]), 80)

    def test_decision_maker_classification(self):
        cases = [
            ('Chief Executive Officer', 'c_level', True), ('CEO', 'c_level', True), ('Group CEO', 'c_level', True),
            ('Founder', 'founder', True), ('Co-Founder & CEO', 'founder', True), ('Owner', 'owner', True),
            ('Co-Owner / Lic. Broker', 'owner', True), ('President', 'president', True),
            ('Chairman', 'c_level', True), ('Managing Partner', 'partner', True), ('Partner', 'partner', True),
            ('Principal', 'principal', True), ('Managing Director', 'managing_director', True),
            ('Managing Member', 'partner', True), ('Broker/Owner', 'owner', True), ('Broker & CEO', 'c_level', True),
            ('Designated Broker', 'managing_broker', True), ('Managing Broker', 'managing_broker', True),
            ('Principal Broker', 'managing_broker', True), ('Qualifying Broker', 'managing_broker', True),
            ('COO', 'c_level', True), ('Chief Financial Officer', 'c_level', True),
            # false-positive guards
            ('Executive Assistant', 'other', False), ('Executive Assistant to the CEO', 'other', False),
            ('Partnerships Manager', 'manager', False), ('Product Owner', 'other', False),
            ('Principal Engineer', 'other', False), ('Former CEO', 'other', False),
            ('Vice President', 'vp', False), ('VP of Sales', 'vp', False), ('Senior Vice President', 'vp', False),
            ('Director of Real Estate', 'director', False),
            # brokers and agents
            ('Real Estate Broker', 'agent', False), ('Licensed Broker', 'agent', False),
            ('Broker Associate', 'agent', False), ('Real Estate Agent', 'agent', False), ('Realtor', 'agent', False),
            ('Licensed REALTOR®', 'agent', False), ('Team Leader', 'agent', False),
            ('Real Estate Investor', 'investor', False), ('Member', 'member', False),
            ('Official Member of Example Council', 'member', False), ('Strategic Advisor', 'advisor', False),
            ('Technology Instructor', 'other', False),
        ]
        for title, seniority, dm in cases:
            result = classify_title(title)
            self.assertEqual((result['seniority'], result['decision_maker']), (seniority, dm), title)

    def test_points_ordering(self):
        ceo = classify_title('CEO')['points']
        self.assertEqual(ceo, 30)
        self.assertEqual(classify_title('Managing Broker')['points'], 26)
        self.assertLess(classify_title('COO')['points'], ceo)
        self.assertEqual(classify_title('Real Estate Agent')['points'], 0)


class WebsiteTests(unittest.TestCase):
    def test_acronym_and_token_matches(self):
        self.assertTrue(website_matches_company('logiccre.example', 'LOGIC Commercial Real Estate'))
        self.assertTrue(website_matches_company('hsacommercial.example', 'HSA Commercial Real Estate'))
        self.assertTrue(website_matches_company('crcre.example', 'Carpenter/Robbins Commercial Real Estate, Inc.'))
        self.assertTrue(website_matches_company('sbrep.example', 'SB Real Estate Partners'))
        self.assertTrue(website_matches_company('harborrealty.example', 'Harbor Realty Group'))
        self.assertFalse(website_matches_company('zebra.example', 'Harbor Realty Group'))

    def test_bad_websites_blanked(self):
        cases = {
            'gmail.com': 'website_personal_domain', 'verizon.net': 'website_personal_domain',
            'wharton.example.edu': 'website_edu_gov', 'facebook.com': 'website_social_or_builder',
            'mysite.wixsite.com': 'website_social_or_builder', 'kw.com': 'website_franchise_parent',
        }
        for host, code in cases.items():
            result = assess(lead(**{'Company Website': host, 'Email': ''}))
            self.assertEqual(result['company_website'], '', host)
            self.assertIn(code, result['issues'], host)

    def test_franchise_parent_brand_rules(self):
        mismatch = assess(lead(Company='Source Realty Group',
                               **{'Company Website': 'kw.com', 'Email': addr('jane', 'kw.com')}))
        self.assertIn('website_franchise_parent', mismatch['issues'])
        self.assertIn('email_franchise_domain', mismatch['issues'])
        self.assertEqual(mismatch['email'], addr('jane', 'kw.com'))
        for company, host in (('Keller Williams Coastal', 'kw.com'), ('Compass', 'compass.com'),
                              ('Douglas Elliman Real Estate', 'elliman.com')):
            result = assess(lead(Company=company, **{'Company Website': host, 'Email': ''}))
            self.assertEqual(result['company_website'], host, company)
            self.assertNotIn('website_franchise_parent', result['issues'])

    def test_website_from_matching_email(self):
        result = assess(lead(**{'Company Website': ''}))
        self.assertEqual(result['company_website'], 'harborrealty.example')
        self.assertIn('website_from_email', result['fixes'])

    def test_mismatch_is_soft(self):
        result = assess(lead(**{'Company Website': 'zebra.example', 'Email': 'jane@zebra.example'}))
        self.assertIn('website_company_mismatch', result['issues'])
        self.assertNotEqual(result['tier'], 'reject')


class EmailTests(unittest.TestCase):
    def test_name_based_patterns(self):
        for local in ('jane', 'sample', 'jane.sample', 'janesample', 'jsample', 'j.sample', 'janes', 'jane_sample',
                      'js', 'sjane', 'jane.sample2'):
            self.assertTrue(email_matches_name(local + '@harborrealty.example', 'Jane Sample'), local)
        self.assertTrue(email_matches_name('mike@x.example', 'Michael Example'))
        self.assertTrue(email_matches_name('eva@x.example', 'Éva Garzón'))
        self.assertTrue(email_matches_name('garzon@x.example', 'Eva Garzón'))
        self.assertFalse(email_matches_name('zz9@x.example', 'Jane Sample'))

    def test_personal_email_blanked_in_clean_but_kept_in_review(self):
        row = lead(Email=addr('jane.sample', 'gmail.com'))
        result = assess(row)
        self.assertIn('personal_email', result['issues'])
        self.assertEqual(result['email'], addr('jane.sample', 'gmail.com'))
        out = clean_records([row])
        self.assertEqual(len(out['clean']), 1)
        with tempfile.TemporaryDirectory() as tmp:
            write_outputs(out, tmp)
            with open(os.path.join(tmp, 'leads-clean.csv'), encoding='utf-8') as handle:
                self.assertEqual(list(csv.DictReader(handle))[0]['Email'], '')
            review_out = clean_records([row], Target(min_tier='A'))
            self.assertEqual(len(review_out['review']), 1)
            write_outputs(review_out, tmp)
            with open(os.path.join(tmp, 'leads-review.csv'), encoding='utf-8') as handle:
                review = list(csv.DictReader(handle))[0]
            self.assertEqual(review['Email'], addr('jane.sample', 'gmail.com'))
            self.assertIn('personal_email', review['Issues'])
        allowed = clean_records([row], Target(allow_personal_email=True))
        self.assertEqual(allowed['summary']['with_work_email'], 1)

    def test_role_mailbox(self):
        for local in ('info', 'hello', 'broker', 'office', 'sales', 'team', 'admin', 'contact', 'info2'):
            result = assess(lead(Email=local + '@harborrealty.example'))
            self.assertIn('role_email', result['issues'], local)
        out = clean_records([lead(Email='info@harborrealty.example')])
        self.assertEqual(out['summary']['with_work_email'], 0)

    def test_domain_mismatch_and_verification(self):
        result = assess(lead(Email='jane@otherfirm.example'))
        self.assertIn('email_domain_mismatch', result['issues'])
        alias = assess(lead(Email='jane@harborrealty-group.example'))
        self.assertNotIn('email_domain_mismatch', alias['issues'])
        invalid = assess(dict(lead(), email_verification='invalid'))
        self.assertEqual(invalid['email'], '')
        self.assertIn('email_invalid', invalid['issues'])
        risky = assess(dict(lead(), email_verification='accept_all'))
        self.assertIn('email_unverified_risky', risky['issues'])
        verified = assess(dict(lead(), email_verification='valid', email_source='provider'))
        self.assertGreater(verified['score'], risky['score'])
        self.assertEqual(verified['email_source'], 'provider')

    def test_not_name_based_is_soft(self):
        result = assess(lead(Email='zz9@harborrealty.example'))
        self.assertIn('email_not_name_based', result['issues'])
        self.assertNotEqual(result['tier'], 'reject')


class AssessTests(unittest.TestCase):
    def test_perfect_lead_is_tier_a(self):
        result = assess(dict(lead(), email_verification='valid'))
        self.assertEqual(result['tier'], 'A')
        self.assertEqual(result['score'], 100)
        self.assertEqual(result['issues'], [])
        self.assertTrue(result['decision_maker'])

    def test_hard_rejects(self):
        cases = [
            (lead(**{'Full Name': ''}), 'missing_full_name'),
            (lead(Title=''), 'missing_title'),
            (lead(Title='Real Estate Agent'), 'not_decision_maker'),
            (lead(**{'Full Name': 'Pat And Chris Example'}), 'multiple_people'),
            (lead(Company='Coastal Realtors Association'), 'non_target_org'),
            (lead(Company='Example Real Estate Council'), 'non_target_org'),
            (lead(Company='Sample Realty Media'), 'non_target_org'),
            (lead(**{'Company Website': 'harbor.example.ca', 'Email': addr('jane', 'harbor.example.ca')}), 'non_us_signal'),
            (dict(lead(), country='Canada'), 'non_us_signal'),
            (lead(**{'Company Website': '', 'LinkedIn URL': '', 'Email': addr('jane', 'gmail.com')}), 'no_contact_path'),
        ]
        for row, reason in cases:
            result = assess(row)
            self.assertEqual(result['tier'], 'reject', reason)
            self.assertIn(reason, result['reject_reasons'])

    def test_false_positive_guards(self):
        self.assertNotEqual(assess(lead(Company='Turner Real Estate Network'))['tier'], 'reject')
        self.assertNotEqual(assess(dict(lead(), country='USA'))['tier'], 'reject')
        for host in ('harbor.co', 'harbor.us', 'harbor.io', 'harbor.realestate'):
            self.assertNotIn('non_us_signal', assess(lead(**{'Company Website': host, 'Email': ''}))['issues'], host)

    def test_agents_allowed_when_not_required(self):
        result = assess(lead(Title='Real Estate Agent'), Target(require_decision_maker=False))
        self.assertNotEqual(result['tier'], 'reject')
        self.assertFalse(result['decision_maker'])

    def test_truncated_name_penalized(self):
        self.assertLess(assess(lead(**{'Full Name': 'Jane S.'}))['score'], assess(lead())['score'])

    def test_tiers(self):
        no_email = assess(lead(Email=''))
        self.assertEqual((no_email['score'], no_email['tier']), (65, 'B'))
        bare = assess(lead(Email='', **{'Company Website': ''}))
        self.assertEqual(bare['tier'], 'C')


class DedupeTests(unittest.TestCase):
    def test_merge_by_linkedin_email_and_name_website(self):
        rows = [
            lead(Email=addr('jane.sample', 'gmail.com'), Title='CEO'),
            lead(**{'Full Name': 'jane sample', 'Company Website': '', 'Email': 'jane@harborrealty.example',
                    'LinkedIn URL': 'https://uk.linkedin.com/in/Jane-Sample?trk=1'}),
            lead(**{'Full Name': 'Omar Example', 'LinkedIn URL': '', 'Email': 'omar@cove.example',
                    'Company': 'Cove Homes', 'Company Website': 'cove.example'}),
            lead(**{'Full Name': 'Omar Example', 'LinkedIn URL': 'https://www.linkedin.com/in/omar-example/',
                    'Email': 'OMAR@cove.example', 'Company': 'Cove Homes', 'Company Website': ''}),
            lead(**{'Full Name': 'Lee Sample', 'LinkedIn URL': '', 'Email': '', 'Company': 'Bay Homes',
                    'Company Website': 'bay.example'}),
            lead(**{'Full Name': 'Lée Sample', 'LinkedIn URL': '', 'Email': 'lee@bay.example', 'Company': 'Bay Homes',
                    'Company Website': 'www.bay.example'}),
        ]
        merged = dedupe([normalize_record(r) for r in rows])
        self.assertEqual(len(merged), 3)
        jane, omar, lee = merged
        self.assertEqual(jane['email'], 'jane@harborrealty.example')
        self.assertEqual(jane['company_website'], 'harborrealty.example')
        self.assertEqual(jane['merged_count'], 2)
        self.assertEqual(omar['linkedin_url'], 'https://www.linkedin.com/in/omar-example/')
        self.assertEqual(omar['company_website'], 'cove.example')
        self.assertEqual(lee['email'], 'lee@bay.example')
        summary = clean_records(rows)['summary']
        self.assertEqual(summary['duplicates_merged'], 3)
        self.assertEqual(summary['input_rows'], 6)

    def test_deterministic(self):
        rows = [normalize_record(lead()), normalize_record(lead(**{'Full Name': 'Ana Placeholder',
                                                                   'LinkedIn URL': '', 'Email': ''}))]
        self.assertEqual(dedupe(rows), dedupe(rows))


class OutputTests(unittest.TestCase):
    def test_formula_escaping(self):
        for value in ('=SUM(A1)', '+1 555 0100', '-2', '@cmd', '  =x', '\tfoo', '\rbar', '＝x', '＋x', '－x', '＠x'):
            self.assertTrue(escape_cell(value).startswith("'"), repr(value))
        for value in ('Mary-Jane Example', 'https://www.linkedin.com/in/a-b/', 'Founder & CEO', '', 'jane@x.example'):
            self.assertEqual(escape_cell(value), value)

    def test_summary_counts(self):
        rows = [
            dict(lead(), email_verification='valid'),
            lead(**{'Full Name': 'Omar Example', 'Email': '', 'LinkedIn URL': 'https://www.linkedin.com/in/omar-e/'}),
            lead(**{'Full Name': 'Lee Sample', 'Email': '', 'Company Website': '',
                    'LinkedIn URL': 'https://www.linkedin.com/in/lee-s/'}),
            lead(**{'Full Name': 'Ana Placeholder', 'Title': 'Realtor', 'Email': 'ana@harborrealty.example',
                    'LinkedIn URL': 'https://www.linkedin.com/in/ana-p/'}),
            lead(**{'Full Name': 'Kim Example', 'Company': 'Example Realtors Association', 'Email': '',
                    'Company Website': 'realtorsassoc.example', 'LinkedIn URL': 'https://www.linkedin.com/in/kim-e/'}),
        ]
        out = clean_records(rows)
        summary = out['summary']
        self.assertEqual(summary['input_rows'], 5)
        self.assertEqual((summary['clean'], summary['review'], summary['rejected']), (2, 1, 2))
        self.assertEqual(summary['by_tier'], {'A': 1, 'B': 1, 'C': 1, 'reject': 2})
        self.assertEqual(summary['with_work_email'], 1)
        self.assertEqual(summary['with_linkedin'], 2)
        self.assertEqual(summary['with_website'], 2)
        self.assertEqual(summary['reject_reasons'], {'not_decision_maker': 1, 'non_target_org': 1})
        self.assertIn('missing_email', summary['top_issues'])
        self.assertEqual([r['tier'] for r in out['clean']], ['A', 'B'])

    def test_clean_csv_end_to_end(self):
        rows = [
            lead(**{'Full Name': 'Éva Garzón', 'Email': 'eva@harborrealty.example',
                    'LinkedIn URL': 'https://www.linkedin.com/in/eva-garz%C3%B3n/'}),
            lead(**{'Full Name': 'Omar Example', 'Company': '+Cove Homes', 'Company Website': 'cove.example',
                    'Email': 'omar@cove.example', 'LinkedIn URL': 'https://www.linkedin.com/in/omar-example/'}),
            lead(**{'Full Name': 'Ana Placeholder', 'Title': 'Real Estate Agent', 'Email': 'ana@harborrealty.example',
                    'LinkedIn URL': 'https://www.linkedin.com/in/ana-placeholder/'}),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, 'in.csv')
            with open(source, 'w', encoding='utf-8-sig', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=HEADERS)
                writer.writeheader()
                writer.writerows(rows)
            out_dir = os.path.join(tmp, 'out')
            result = clean_csv(source, out_dir)
            self.assertEqual(stat.S_IMODE(os.stat(out_dir).st_mode), 0o700)
            for path in result['paths'].values():
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600, path)
            with open(result['paths']['clean'], encoding='utf-8', newline='') as handle:
                text = handle.read()
            self.assertTrue(text.startswith('Full Name,Title,Company,Company Website,LinkedIn URL,Email\r\n'))
            clean_rows = list(csv.reader(text.splitlines()))[1:]
            self.assertEqual(len(clean_rows), 2)
            self.assertIn(['Éva Garzón', 'Founder & CEO', 'Harbor Realty Group', 'harborrealty.example',
                           'https://www.linkedin.com/in/eva-garzón/', 'eva@harborrealty.example'], clean_rows)
            self.assertIn("'+Cove Homes", [row[2] for row in clean_rows])
            with open(result['paths']['rejected'], encoding='utf-8') as handle:
                rejected = list(csv.DictReader(handle))
            self.assertEqual(rejected[0]['Reject Reasons'], 'not_decision_maker')
            with open(result['paths']['review'], encoding='utf-8') as handle:
                self.assertEqual(next(csv.reader(handle))[-4:], ['Tier', 'Score', 'Issues', 'Email Source'])
            with open(result['paths']['summary'], encoding='utf-8') as handle:
                self.assertEqual(json.load(handle)['clean'], 2)

            json_source = os.path.join(tmp, 'in.json')
            with open(json_source, 'w', encoding='utf-8') as handle:
                json.dump({'leads': rows}, handle)
            self.assertEqual(clean_csv(json_source, os.path.join(tmp, 'json'))['summary']['clean'], 2)
            jsonl_source = os.path.join(tmp, 'in.jsonl')
            with open(jsonl_source, 'w', encoding='utf-8') as handle:
                handle.write('\n'.join(json.dumps(r) for r in rows) + '\n')
            self.assertEqual(clean_csv(jsonl_source, os.path.join(tmp, 'jsonl'))['summary']['rejected'], 1)



class TitleRegressionTests(unittest.TestCase):
    def test_agent_partner_is_not_decision_maker(self):
        from lead_research.quality import classify_title
        self.assertFalse(classify_title('Agent Partner')['decision_maker'])
        self.assertTrue(classify_title('Managing Partner')['decision_maker'])

    def test_phone_numbers_and_caps_removed_from_titles(self):
        from lead_research.quality import clean_title
        self.assertEqual(clean_title('Co-Founder 555.010.1234')[0], 'Co-Founder')
        self.assertEqual(clean_title('FOUNDING PARTNER')[0], 'Founding Partner')
        self.assertEqual(clean_title('CEO & FOUNDER')[0], 'CEO & Founder')

if __name__ == '__main__':
    unittest.main()
