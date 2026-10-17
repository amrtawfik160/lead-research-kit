import gzip
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from xml.sax.saxutils import escape

from lead_research import records
from lead_research.records import (SOURCES, RecordsError, RecordsGetter, company_name, fetch_source, load_local_file,
                                   parse_co_rows, parse_form_d, parse_fl_rows, parse_ny_rows, parse_sec_index,
                                   parse_tx_rows, person_name, sec_filing_urls, socrata_fetch)
from lead_research.quality import classify_title

TODAY = date(2026, 10, 15)
SEC_ENV = {'SEC_USER_AGENT': 'Synthetic Research ops@example.com'}


class FakeResponse:
    def __init__(self, body=b'', status=200, headers=None, url=None):
        self.body, self.status, self.headers, self.url = body, status, headers or {}, url
        self.closed = False

    def read(self, n=-1):
        if n is None or n < 0:
            n = len(self.body)
        chunk, self.body = self.body[:n], self.body[n:]
        return chunk

    def geturl(self):
        return self.url

    def close(self):
        self.closed = True


class FakeOpener:
    def __init__(self, handler):
        self.handler = handler
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        result = self.handler(request.full_url)
        if isinstance(result, Exception):
            raise result
        if result.url is None:
            result.url = request.full_url
        return result


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.slept = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def json_response(data, **kw):
    return FakeResponse(json.dumps(data).encode(), **kw)


def getter_for(handler, env=None, **kw):
    clock = FakeClock()
    opener = FakeOpener(handler)
    getter = RecordsGetter(env or {}, opener=opener, clock=clock.clock, sleep=clock.sleep, **kw)
    return getter, opener, clock


TX_ROWS = [
    {'license_type': 'Broker Company', 'license_number': '9000001-BB', 'full_name': 'HARBORLIGHT HOMES LLC',
     'status': 'Active', 'original_license_date': '10/14/2026', 'license_expiration_date': '10/31/2028',
     'county': 'Bexar', 'related_license_type': 'Broker Individual', 'related_license_number': '700001-B',
     'related_license_full_name': 'JASPER D QUILLON', 'updated': '2026-10-15T07:30:08.000'},
    # duplicate of the first row (same person + firm) -> deduped
    {'license_type': 'Broker Company', 'license_number': '9000001-BB', 'full_name': 'HARBORLIGHT HOMES LLC',
     'status': 'Active', 'original_license_date': '10/14/2026', 'license_expiration_date': '10/31/2028',
     'county': 'Bexar', 'related_license_type': 'Broker Individual', 'related_license_number': '700001-B',
     'related_license_full_name': 'JASPER D QUILLON', 'updated': '2026-10-15T07:30:08.000'},
    {'license_type': 'Broker Company', 'license_number': '9000002-BB', 'full_name': 'MAPLEFERN REALTY GROUP',
     'status': 'Expired less than 6 months', 'original_license_date': '01/02/2020',
     'license_expiration_date': '01/31/2026', 'county': 'Travis', 'related_license_type': 'Broker Individual',
     'related_license_full_name': 'NOVA R TESTWELL', 'updated': '2026-10-14T07:30:08.000'},
    {'license_type': 'Broker Company', 'license_number': '9000003-BB', 'full_name': 'BLUEJAY PROPERTIES INC',
     'status': 'Active', 'original_license_date': '01/02/2020', 'license_expiration_date': '01/31/2026',
     'county': 'Travis', 'related_license_type': 'Broker Individual',
     'related_license_full_name': 'ORLA P SAMPLETON', 'updated': '2026-10-14T07:30:08.000'},
    {'license_type': 'Broker Company', 'license_number': '9000004-BB', 'full_name': 'NO BROKER YET LLC',
     'status': 'Active', 'original_license_date': '10/10/2026', 'license_expiration_date': '10/31/2028',
     'county': 'Harris', 'related_license_type': '', 'related_license_full_name': ''},
    {'license_type': 'Broker Individual', 'license_number': '700002-B', 'full_name': 'PERRIN Q FIXTURE',
     'status': 'Active', 'designated_supervisor_flag': '1', 'county': 'Collin', 'related_license_type': '',
     'license_expiration_date': '10/31/2027'},
    {'license_type': 'Broker Company', 'license_number': '9000005-BB',
     'full_name': 'CRESTWAVE PROPERTY SOLUTIONS DBA RENTWAVE', 'status': 'Probation - Active',
     'original_license_date': '03/04/2024', 'license_expiration_date': '03/31/2027', 'county': 'Dallas',
     'related_license_type': 'Broker Individual', 'related_license_full_name': "O'NEIL-SMITH, MARY ANN"},
]

NY_ROWS = [
    {'business_name': 'CORRAWAY REALTY GROUP LLC', 'business_city': 'NEW YORK', 'business_state': 'NY',
     'business_zip': '10174', 'county': 'NEW YORK', 'license_holder_name': 'QUILLON JASPER D',
     'license_expiration_date': '2027-10-28T00:00:00.000', 'license_number': '10490000001',
     'license_type': 'LIMITED LIABILITY BROKER'},
    {'business_name': 'FIXTURE NOVA', 'business_city': 'ALBANY', 'business_state': 'NY', 'business_zip': '12207',
     'county': 'NONE', 'license_holder_name': 'FIXTURE NOVA', 'license_expiration_date': '2028-04-15T00:00:00.000',
     'license_number': '10350000002', 'license_type': 'INDIVIDUAL BROKER'},
    {'business_name': 'SAMPLETON HOMES', 'business_city': 'BUFFALO', 'business_state': 'NY', 'business_zip': '14201',
     'county': 'ERIE', 'license_holder_name': 'SAMPLETON ORLA', 'license_expiration_date': '2027-01-15T00:00:00.000',
     'license_number': '10350000003', 'license_type': 'TRADENAME BROKER'},
    {'business_name': 'CORRAWAY REALTY GROUP LLC', 'business_city': 'NEW YORK', 'business_state': 'NY',
     'county': 'NONE', 'license_number': '109900001', 'license_type': 'REAL ESTATE PRINCIPAL OFFICE'},
    {'business_name': 'OLDCO REALTY INC', 'business_city': 'ITHACA', 'business_state': 'NY', 'county': 'TOMPKINS',
     'license_holder_name': 'TESTWELL PERRIN', 'license_expiration_date': '2026-01-01T00:00:00.000',
     'license_number': '10310000004', 'license_type': 'CORPORATE BROKER'},
    {'business_name': 'BIGFIRM INC', 'business_city': 'NEW YORK', 'business_state': 'NY', 'county': 'NONE',
     'license_holder_name': 'AGENT AMY', 'license_expiration_date': '2027-01-01T00:00:00.000',
     'license_number': '10400000005', 'license_type': 'REAL ESTATE SALESPERSON'},
]

CO_ROWS = [
    {'firstname': 'Jasper', 'middlename': '', 'lastname': 'Quillon', 'suffix': '', 'entityname': '',
     'city': 'Sterling', 'state': 'CO', 'zipcode': '80751', 'licenseprefix': 'ER', 'licensenumber': '100200011',
     'licensetype': 'Employing Level Real Estate Broker - Responsible Broker', 'licensefirstissuedate': '10/07/2026',
     'licenseexpirationdate': '12/31/2026', 'licensestatus': 'Active'},
    {'firstname': '', 'lastname': '', 'entityname': 'Prairiefox Realty, LLC', 'city': 'Sterling', 'state': 'CO',
     'zipcode': '80751', 'licenseprefix': 'EC', 'licensenumber': '100200012',
     'licensetype': 'Real Estate Company - Employing', 'licensefirstissuedate': '10/07/2026',
     'licenseexpirationdate': '', 'licensestatus': 'Active'},
    {'firstname': 'Nova', 'middlename': 'R', 'lastname': 'Fixture', 'entityname': '', 'city': 'Denver',
     'state': 'CO', 'zipcode': '80202', 'licenseprefix': 'IR', 'licensenumber': '100100001',
     'licensetype': 'Independent Level Real Estate Broker - Responsible Broker',
     'licensefirstissuedate': '05/01/2019', 'licenseexpirationdate': '12/31/2026', 'licensestatus': 'Active'},
    {'firstname': 'Orla', 'lastname': 'Sampleton', 'entityname': '', 'city': 'Boulder', 'state': 'CO',
     'zipcode': '80301', 'licenseprefix': 'ER', 'licensenumber': '100100002',
     'licensetype': 'Employing Level Real Estate Broker - Responsible Broker',
     'licensefirstissuedate': '05/01/2019', 'licenseexpirationdate': '12/31/2026', 'licensestatus': 'Inactive'},
    {'firstname': 'Perrin', 'lastname': 'Testwell', 'entityname': '', 'city': 'Aurora', 'state': 'CO',
     'zipcode': '80010', 'licenseprefix': 'FA', 'licensenumber': '100100003',
     'licensetype': 'Associate Level Real Estate Broker', 'licensefirstissuedate': '05/01/2019',
     'licenseexpirationdate': '12/31/2026', 'licensestatus': 'Active'},
]

SEC_IDX = """Description:           Daily Index of EDGAR Dissemination Feed by Form Type
Last Data Received:    Oct 14, 2026
Comments:              webmaster contact
Anonymous FTP:         ftp://ftp.sec.gov/edgar/



Form Type   Company Name                                                  CIK
      Date Filed  File Name
---------------------------------------------------------------------------------------------------------------------------------------------
1-A POS          Synthetic Portfolio 9 LP                                      1000001     20261014    edgar/data/1000001/0001000001-26-000001.txt
D                Harborlight Residential Fund I, LLC                           1000002     20261014    edgar/data/1000002/0001000002-26-000001.txt
D                Widget Software Inc.                                          1000003     20261014    edgar/data/1000003/0001000003-26-000001.txt
D/A              Harborlight Residential Fund I, LLC                           1000002     20261014    edgar/data/1000002/0001000002-26-000002.txt
D                Maple  Twin Spaces LP                                         1000004     20261014    edgar/data/1000004/0001000004-26-000001.txt
"""


def form_d(entity='Harborlight Residential Fund I, LLC', group='Residential', state='TX', submission='D',
           amendment='false', persons=None):
    persons = persons if persons is not None else [
        ('Jasper', 'D', 'Quillon', ['Executive Officer', 'Director'], 'President & CEO'),
        ('Nova', '', 'Fixture', ['Director'], ''),
        ('Orla', '', 'Sampleton', ['Executive Officer'], ''),
        ('-', '', 'Harborlight GP LLC', ['Executive Officer'], 'Manager'),
        ('Perrin', '', 'Testwell', ['Promoter'], 'Placement contact'),
        ('Ivy', '', 'Placeholder', ['Promoter'], 'Managing Member of the Sponsor'),
    ]
    people = ''
    for first, middle, last, rels, clar in persons:
        people += ('<relatedPersonInfo><relatedPersonName><firstName>%s</firstName>%s<lastName>%s</lastName>'
                   '</relatedPersonName><relatedPersonAddress><city>Austin</city><stateOrCountry>TX'
                   '</stateOrCountry></relatedPersonAddress><relatedPersonRelationshipList>%s'
                   '</relatedPersonRelationshipList><relationshipClarification>%s</relationshipClarification>'
                   '</relatedPersonInfo>') % (first, '<middleName>%s</middleName>' % middle if middle else '', escape(last),
                                              ''.join('<relationship>%s</relationship>' % r for r in rels), escape(clar))
    return ('<?xml version="1.0"?><edgarSubmission><schemaVersion>X0708</schemaVersion><submissionType>%s'
            '</submissionType><primaryIssuer><cik>0001000002</cik><entityName>%s</entityName><issuerAddress>'
            '<street1>1 Synthetic Way</street1><city>AUSTIN</city><stateOrCountry>%s</stateOrCountry>'
            '<zipCode>78701</zipCode></issuerAddress><issuerPhoneNumber>555-010-0000</issuerPhoneNumber>'
            '<entityType>Limited Liability Company</entityType></primaryIssuer><relatedPersonsList>%s'
            '</relatedPersonsList><offeringData><industryGroup><industryGroupType>%s</industryGroupType>'
            '</industryGroup><typeOfFiling><newOrAmendment><isAmendment>%s</isAmendment></newOrAmendment>'
            '</typeOfFiling></offeringData></edgarSubmission>') % (submission, escape(entity), state, people, group, amendment)


class NameTests(unittest.TestCase):
    def test_person_names(self):
        self.assertEqual(person_name('QUILLON, JASPER D'), 'Jasper D Quillon')
        self.assertEqual(person_name('JASPER D QUILLON'), 'Jasper D Quillon')
        self.assertEqual(person_name("O'NEIL-SMITH, MARY ANN"), "Mary Ann O'Neil-Smith")
        self.assertEqual(person_name('MCDONALD'), 'McDonald')
        self.assertEqual(person_name('FIXTURE, NOVA JR'), 'Nova Fixture Jr')
        self.assertEqual(person_name('FIXTURE JR, NOVA'), 'Nova Fixture Jr')
        self.assertEqual(person_name('PERRIN V TESTWELL'), 'Perrin V Testwell')
        self.assertEqual(person_name('QUILLON JASPER D', order='last_first'), 'Jasper D Quillon')
        self.assertEqual(person_name('Nova DeWitt'), 'Nova DeWitt')
        self.assertEqual(person_name('  '), '')

    def test_company_names(self):
        self.assertEqual(company_name('HARBORLIGHT HOMES LLC'), 'Harborlight Homes LLC')
        self.assertEqual(company_name('NNN PROPERTIES OF TEXAS, INC.'), 'NNN Properties of Texas, INC.')
        self.assertEqual(company_name("SOTHEBY'S INTERNATIONAL REALTY"), "Sotheby's International Realty")
        self.assertEqual(company_name('1ST CHOICE HOMES'), '1st Choice Homes')
        self.assertEqual(company_name('Prairiefox Realty, llc'), 'Prairiefox Realty, LLC')
        self.assertEqual(company_name('CRESTWAVE DBA RENTWAVE'), 'Crestwave DBA Rentwave')


class ParserTests(unittest.TestCase):
    def test_tx_rules(self):
        companies, prospects = parse_tx_rows(TX_ROWS, TODAY)
        self.assertEqual([p['full_name'] for p in prospects], ['Jasper D Quillon', "Mary Ann O'Neil-Smith"])
        first = prospects[0]
        self.assertEqual(first['title'], 'Designated Broker')
        self.assertEqual(first['company'], 'Harborlight Homes LLC')
        self.assertEqual(first['location'], 'Bexar County, TX')
        self.assertEqual(first['company_website'], '')
        self.assertEqual(first['record_date'], '2026-10-14')
        self.assertEqual(first['source_url'], 'https://data.texas.gov/d/s7ft-44qi')
        self.assertNotIn('?', first['source_url'])
        self.assertEqual(companies[0]['decision_maker'], 'Jasper D Quillon')
        self.assertEqual(companies[0]['county'], 'Bexar')
        self.assertTrue(classify_title(first['title'])['decision_maker'])

    def test_ny_rules(self):
        companies, prospects = parse_ny_rows(NY_ROWS, TODAY)
        by_name = {p['full_name']: p for p in prospects}
        self.assertEqual(set(by_name), {'Jasper D Quillon', 'Nova Fixture', 'Orla Sampleton'})
        self.assertEqual(by_name['Jasper D Quillon']['title'], 'Principal Broker')
        self.assertEqual(by_name['Jasper D Quillon']['company'], 'Corraway Realty Group LLC')
        self.assertEqual(by_name['Jasper D Quillon']['location'], 'New York, NY')
        self.assertEqual(by_name['Nova Fixture']['title'], 'Broker/Owner')
        self.assertEqual(by_name['Nova Fixture']['company'], 'Nova Fixture')
        self.assertEqual(by_name['Nova Fixture']['county'], '')
        self.assertEqual(by_name['Orla Sampleton']['company'], 'Sampleton Homes')
        for p in prospects:
            self.assertTrue(classify_title(p['title'])['decision_maker'])
        self.assertEqual(len(companies), 3)

    def test_co_rules_and_adjacent_company_join(self):
        companies, prospects = parse_co_rows(CO_ROWS, TODAY)
        by_name = {p['full_name']: p for p in prospects}
        self.assertEqual(set(by_name), {'Jasper Quillon', 'Nova R Fixture'})
        self.assertEqual(by_name['Jasper Quillon']['company'], 'Prairiefox Realty, LLC')
        self.assertEqual(by_name['Jasper Quillon']['title'], 'Responsible Broker')
        self.assertEqual(by_name['Jasper Quillon']['license_number'], 'ER.100200011')
        self.assertEqual(by_name['Nova R Fixture']['company'], '')
        self.assertEqual([c['organization'] for c in companies], ['Prairiefox Realty, LLC'])

    def test_co_ambiguous_join_left_blank(self):
        twin = dict(CO_ROWS[1], entityname='Second Prairie LLC', licensenumber='100200010')
        _, prospects = parse_co_rows(CO_ROWS[:2] + [twin], TODAY)
        self.assertEqual(prospects[0]['company'], '')

    def test_sec_index(self):
        rows = parse_sec_index(SEC_IDX)
        self.assertEqual([r['cik'] for r in rows], ['1000002', '1000003', '1000004'])
        self.assertEqual(rows[0], {'form_type': 'D', 'company': 'Harborlight Residential Fund I, LLC',
                                   'cik': '1000002', 'date_filed': '2026-10-14',
                                   'file_name': 'edgar/data/1000002/0001000002-26-000001.txt'})
        self.assertEqual(rows[2]['company'], 'Maple Twin Spaces LP')
        self.assertEqual(len(parse_sec_index(SEC_IDX, ('D', 'D/A'))), 4)
        doc, index = sec_filing_urls('edgar/data/1000002/0001000002-26-000001.txt')
        self.assertEqual(doc, 'https://www.sec.gov/Archives/edgar/data/1000002/000100000226000001/primary_doc.xml')
        self.assertEqual(index, 'https://www.sec.gov/Archives/edgar/data/1000002/000100000226000001/'
                                '0001000002-26-000001-index.htm')
        with self.assertRaises(RecordsError):
            sec_filing_urls('../etc/passwd')

    def test_form_d_real_estate_issuer(self):
        filing = parse_sec_index(SEC_IDX)[0]
        companies, prospects = parse_form_d(form_d(), filing)
        titles = {p['full_name']: p['title'] for p in prospects}
        self.assertEqual(titles, {'Jasper D Quillon': 'President & CEO', 'Orla Sampleton': 'Executive Officer',
                                  'Ivy Placeholder': 'Managing Member'})
        self.assertEqual(prospects[0]['company'], 'Harborlight Residential Fund I, LLC')
        self.assertEqual(prospects[0]['location'], 'Austin, TX')
        self.assertEqual(prospects[0]['record_date'], '2026-10-14')
        self.assertTrue(prospects[0]['source_url'].endswith('0001000002-26-000001-index.htm'))
        self.assertEqual(companies[0]['phone'], '555-010-0000')
        self.assertEqual(companies[0]['industry_group'], 'Residential')
        for p in prospects:
            self.assertTrue(classify_title(p['title'])['decision_maker'], p['title'])

    def test_form_d_filters(self):
        self.assertEqual(parse_form_d(form_d(group='Computers')), ([], []))
        self.assertEqual(parse_form_d(form_d(state='A6')), ([], []))  # non-US issuer
        self.assertEqual(parse_form_d(form_d(submission='D/A', amendment='true')), ([], []))
        self.assertTrue(parse_form_d(form_d(submission='D/A', amendment='true'), allow_amendments=True)[1])
        self.assertEqual(parse_form_d(form_d(amendment='true')), ([], []))
        self.assertEqual(parse_form_d('<!DOCTYPE x [<!ENTITY a "b">]><edgarSubmission/>'), ([], []))
        self.assertEqual(parse_form_d('not xml <'), ([], []))
        for group in ('Commercial', 'Construction', 'REITS and Finance', 'Other Real Estate'):
            self.assertTrue(parse_form_d(form_d(group=group))[1], group)

    def test_fl_rows(self):
        rows = [
            {'licensee_name': 'QUILLON, JASPER D', 'rank': 'Broker', 'city': 'MIAMI', 'state': 'FL',
             'county': 'Dade', 'license_number': 'BK0000001', 'primary_status': 'Current',
             'secondary_status': 'Active', 'original_license_date': '01/02/2010',
             'license_expiration_date': '03/31/2028', 'employer_name': 'GULFWIND REALTY INC'},
            {'licensee_name': 'FIXTURE, NOVA', 'rank': 'BROKER', 'dba_name': 'FIXTURE COASTAL HOMES',
             'city': 'TAMPA', 'state': 'FL', 'license_number': 'BK0000002', 'primary_status': 'C',
             'secondary_status': 'A', 'license_expiration_date': '03/31/2028', 'self_proprietor_name': 'FIXTURE, NOVA'},
            {'licensee_name': 'SAMPLETON, ORLA', 'rank': 'Broker Sales', 'employer_name': 'GULFWIND REALTY INC',
             'primary_status': 'Current', 'secondary_status': 'Active', 'license_expiration_date': '03/31/2028'},
            {'licensee_name': 'TESTWELL, PERRIN', 'rank': 'Broker', 'employer_name': 'GULFWIND REALTY INC',
             'primary_status': 'Current', 'secondary_status': 'Inactive', 'license_expiration_date': '03/31/2028'},
            {'licensee_name': 'GULFWIND REALTY INC', 'rank': 'RE Corp.', 'city': 'MIAMI', 'state': 'FL',
             'license_number': 'CQ0000001', 'primary_status': 'Current', 'secondary_status': 'Active',
             'license_expiration_date': '03/31/2028'},
        ]
        companies, prospects = parse_fl_rows(rows, TODAY)
        self.assertEqual([(p['full_name'], p['title'], p['company']) for p in prospects],
                         [('Jasper D Quillon', 'Qualifying Broker', 'Gulfwind Realty INC'),
                          ('Nova Fixture', 'Broker/Owner', 'Fixture Coastal Homes')])
        self.assertEqual({c['organization']: c['decision_maker'] for c in companies},
                         {'Gulfwind Realty INC': 'Jasper D Quillon', 'Fixture Coastal Homes': 'Nova Fixture'})
        for p in prospects:
            self.assertTrue(classify_title(p['title'])['decision_maker'])


class LocalFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fl_file_with_tolerant_header(self):
        path = self.root / 'fl.csv'
        path.write_text('License Code,Licensee Name,DBA Name,Rank,Address 1,Address 2,Address 3,City,State,Zip,'
                        'County Name,License Number,Primary Status,Secondary Status,Original License Date,'
                        'Status Effective Date,License Expiration Date,Alternate License Number,'
                        'Self Proprietor’s Name,Employer’s Name,Employer’s License Number\n'
                        '2501,"QUILLON, JASPER D",,Broker,1 Synthetic Way,,,MIAMI,FL,33101,Dade,BK0000001,'
                        'Current,Active,01/02/2010,01/02/2010,03/31/2028,,,GULFWIND REALTY INC,CQ0000001\n'
                        '2501,"SAMPLETON, ORLA",,Sales Associate,2 Synthetic Way,,,MIAMI,FL,33101,Dade,SL0000002,'
                        'Current,Active,01/02/2010,01/02/2010,03/31/2028,,,GULFWIND REALTY INC,CQ0000001\n',
                        encoding='utf-8')
        result = load_local_file('fl-dbpr-file', path, today=TODAY)
        self.assertEqual(result['source'], 'fl-dbpr-file')
        self.assertEqual([p['full_name'] for p in result['prospects']], ['Jasper D Quillon'])
        self.assertEqual(result['prospects'][0]['company'], 'Gulfwind Realty INC')
        self.assertIn('Cloudflare', result['terms_note'])

    def test_fl_file_without_header(self):
        path = self.root / 'fl2.csv'
        path.write_text('2501,"FIXTURE, NOVA",FIXTURE COASTAL HOMES,BK,1 Synthetic Way,,,TAMPA,FL,33601,Hillsborough,'
                        'BK0000002,C,A,01/02/2010,01/02/2010,03/31/2028,,"FIXTURE, NOVA",,\n', encoding='utf-8')
        result = load_local_file('fl-dbpr-file', path, today=TODAY)
        self.assertEqual([(p['full_name'], p['title']) for p in result['prospects']], [('Nova Fixture', 'Broker/Owner')])

    def test_generic_csv(self):
        path = self.root / 'g.csv'
        path.write_text('Name,Title,Company,City,State\nQUILLON JASPER,Owner,HARBORLIGHT HOMES LLC,Austin,TX\n'
                        'QUILLON JASPER,Owner,HARBORLIGHT HOMES LLC,Austin,TX\n', encoding='utf-8')
        result = load_local_file('generic-csv', path, today=TODAY)
        self.assertEqual(len(result['prospects']), 1)
        self.assertEqual(result['prospects'][0]['company'], 'Harborlight Homes LLC')
        with self.assertRaises(RecordsError):
            load_local_file('ca-dre', path)


class GetterTests(unittest.TestCase):
    def test_origin_allowlist(self):
        getter, opener, _ = getter_for(lambda url: json_response([]))
        for url in ('https://evil.example/resource/abcd-1234.json', 'http://data.texas.gov/x',
                    'https://data.texas.gov:8443/x', 'https://user' + '@' + 'data.texas.gov/x', 'ftp://www.sec.gov/x'):
            with self.assertRaises(RecordsError) as ctx:
                getter.get(url)
            self.assertIn(str(ctx.exception), ('origin_not_allowed', 'invalid_url'))
        self.assertEqual(opener.requests, [])

    def test_redirect_off_host_blocked(self):
        handler = records._SameOriginRedirect()
        request = urllib.request.Request('https://data.ny.gov/resource/abcd-1234.json')
        with self.assertRaises(RecordsError) as ctx:
            handler.redirect_request(request, None, 302, 'Found', {}, 'https://evil.example/x')
        self.assertEqual(str(ctx.exception), 'redirect_blocked')
        same = handler.redirect_request(request, None, 302, 'Found', {}, '/resource/abcd-5678.json')
        self.assertEqual(same.full_url, 'https://data.ny.gov/resource/abcd-5678.json')
        getter, _, _ = getter_for(lambda url: json_response([], url='https://evil.example/landing'))
        with self.assertRaises(RecordsError) as ctx:
            getter.get_json('https://data.ny.gov/resource/abcd-1234.json')
        self.assertEqual(str(ctx.exception), 'redirect_blocked')

    def test_size_cap_and_gzip(self):
        big, _, _ = getter_for(lambda url: FakeResponse(b'x' * 50), max_bytes=10)
        with self.assertRaises(RecordsError) as ctx:
            big.get('https://data.ct.gov/resource/abcd-1234.json')
        self.assertEqual(str(ctx.exception), 'response_too_large')
        declared, _, _ = getter_for(lambda url: FakeResponse(b'[]', headers={'Content-Length': '999'}), max_bytes=10)
        with self.assertRaises(RecordsError):
            declared.get('https://data.ct.gov/resource/abcd-1234.json')
        zipped = gzip.compress(json.dumps([{'a': 1}]).encode())
        ok, _, _ = getter_for(lambda url: FakeResponse(zipped, headers={'Content-Encoding': 'gzip'}))
        self.assertEqual(ok.get_json('https://data.ct.gov/resource/abcd-1234.json'), [{'a': 1}])
        bomb = gzip.compress(b'0' * 5000)
        capped, _, _ = getter_for(lambda url: FakeResponse(bomb, headers={'Content-Encoding': 'gzip'}), max_bytes=1000)
        with self.assertRaises(RecordsError):
            capped.get('https://data.ct.gov/resource/abcd-1234.json')

    def test_headers_tokens_and_errors_are_safe(self):
        token = 'SYNTHETICTOKEN123'

        def fail(url):
            return urllib.error.HTTPError(url + '&token=' + token, 500, 'boom', {}, None)
        getter, opener, _ = getter_for(fail, env={'SOCRATA_APP_TOKEN': token})
        with self.assertRaises(RecordsError) as ctx:
            getter.get('https://data.texas.gov/resource/abcd-1234.json?$limit=1')
        self.assertEqual(str(ctx.exception), 'http_500')
        self.assertIsNone(ctx.exception.__cause__)
        self.assertTrue(ctx.exception.__suppress_context__)
        self.assertEqual(opener.requests[0].get_header('X-app-token'), token)
        self.assertEqual(opener.requests[0].get_header('Accept-encoding'), 'gzip')
        network, _, _ = getter_for(lambda url: urllib.error.URLError('dns ' + url))
        with self.assertRaises(RecordsError) as ctx:
            network.get('https://data.texas.gov/resource/abcd-1234.json')
        self.assertEqual(str(ctx.exception), 'network_error')
        missing, _, _ = getter_for(lambda url: urllib.error.HTTPError(url, 404, 'nf', {}, None), env=SEC_ENV)
        self.assertIsNone(missing.get_text('https://www.sec.gov/Archives/edgar/daily-index/2026/QTR4/form.20261017.idx'))

    def test_sec_user_agent_required_and_token_not_sent_to_sec(self):
        getter, opener, _ = getter_for(lambda url: FakeResponse(b'ok'), env={'SOCRATA_APP_TOKEN': 'SYNTHETICTOKEN123'})
        with self.assertRaises(RecordsError) as ctx:
            getter.get('https://www.sec.gov/Archives/edgar/daily-index/')
        self.assertEqual(str(ctx.exception), 'sec_user_agent_required')
        self.assertEqual(opener.requests, [])
        with self.assertRaises(RecordsError) as ctx:
            fetch_source('sec-form-d', env={}, today=TODAY)
        self.assertEqual(str(ctx.exception), 'sec_user_agent_required')
        sec, opener, _ = getter_for(lambda url: FakeResponse(b'ok'), env=dict(SEC_ENV, SOCRATA_APP_TOKEN='SYNTHETICTOKEN123'))
        sec.get('https://www.sec.gov/Archives/edgar/daily-index/')
        self.assertEqual(opener.requests[0].get_header('User-agent'), SEC_ENV['SEC_USER_AGENT'])
        self.assertIsNone(opener.requests[0].get_header('X-app-token'))

    def test_polite_spacing(self):
        getter, _, clock = getter_for(lambda url: json_response([]), env=SEC_ENV)
        getter.get('https://data.texas.gov/resource/abcd-1234.json')
        getter.get('https://data.texas.gov/resource/abcd-1234.json')
        getter.get('https://www.sec.gov/a')
        getter.get('https://www.sec.gov/a')
        self.assertEqual(clock.slept, [0.5, 0.25])


class FetchTests(unittest.TestCase):
    def test_socrata_paging(self):
        pages = {0: [{'n': 1}, {'n': 2}], 2: [{'n': 3}, {'n': 4}], 4: [{'n': 5}]}
        seen = []

        def handler(url):
            q = parse_qs(urlsplit(url).query)
            seen.append(q)
            return json_response(pages[int(q['$offset'][0])])
        getter, _, _ = getter_for(handler)
        rows = list(socrata_fetch('https://data.ny.gov/resource/yg7h-zjbf.json', "a='b'", 'a,b', ':id', 2,
                                  getter=getter))
        self.assertEqual([r['n'] for r in rows], [1, 2, 3, 4, 5])
        self.assertEqual(seen[0]['$where'], ["a='b'"])
        self.assertEqual(seen[0]['$order'], [':id'])
        limited = list(socrata_fetch('https://data.ny.gov/resource/yg7h-zjbf.json', page_size=2, max_rows=3,
                                     getter=getter_for(handler)[0]))
        self.assertEqual(len(limited), 3)
        for bad in ('https://www.sec.gov/resource/abcd-1234.json', 'https://data.ny.gov/api/views/x.json',
                    'https://data.ny.gov/resource/yg7h-zjbf.json?$limit=5'):
            with self.assertRaises(RecordsError):
                list(socrata_fetch(bad, getter=getter))

    def test_fetch_tx_delta(self):
        seen = []

        def handler(url):
            seen.append(parse_qs(urlsplit(url).query))
            return json_response(TX_ROWS)
        getter, _, _ = getter_for(handler)
        result = fetch_source('tx-trec', since='2026-10-14', getter=getter, today=TODAY, max_rows=50)
        self.assertIn("updated >= '2026-10-14T00:00:00'", seen[0]['$where'][0])
        self.assertIn("license_type='Broker Company'", seen[0]['$where'][0])
        self.assertEqual(result['cursor'], '2026-10-15')
        self.assertEqual(result['source'], 'tx-trec')
        self.assertEqual(len(result['prospects']), 2)
        self.assertEqual(set(result), {'source', 'fetched_at', 'terms_note', 'companies', 'prospects', 'cursor',
                                       'requests'})

    def test_fetch_ny_and_co_where(self):
        seen = []

        def handler(url):
            q = parse_qs(urlsplit(url).query)
            seen.append(q)
            return json_response(NY_ROWS if 'yg7h' in url else CO_ROWS)
        getter, _, _ = getter_for(handler)
        ny = fetch_source('ny-dos', since='2026-10-01', getter=getter, today=TODAY)
        self.assertIn("license_expiration_date >= '2028-10-01T00:00:00'", seen[0]['$where'][0])
        self.assertIn("'INDIVIDUAL BROKER'", seen[0]['$where'][0])
        self.assertEqual(len(ny['prospects']), 3)
        co = fetch_source('co-dora', since='2026-10-13', getter=getter, today=TODAY)
        self.assertIn("licensefirstissuedate in ('10/13/2026','10/14/2026','10/15/2026')", seen[1]['$where'][0])
        self.assertEqual(len(co['prospects']), 2)
        with self.assertRaises(RecordsError):
            fetch_source('co-dora', since='2026-01-01', getter=getter, today=TODAY)
        with self.assertRaises(RecordsError):
            fetch_source('tx-trec', since='2027-01-01', getter=getter, today=TODAY)
        with self.assertRaises(RecordsError):
            fetch_source('fl-dbpr-file', getter=getter)
        with self.assertRaises(RecordsError):
            fetch_source('ca-dre', getter=getter)

    def test_fetch_sec_form_d(self):
        calls = []
        docs = {'1000002/000100000226000001': form_d(),
                '1000003/000100000326000001': form_d(entity='Widget Software Inc.', group='Computers'),
                '1000004/000100000426000001': None}

        def handler(url):
            calls.append(url)
            if 'daily-index' in url:
                if url.endswith('form.20261014.idx'):
                    return FakeResponse(SEC_IDX.encode())
                return urllib.error.HTTPError(url, 404, 'nf', {}, None)
            for key, body in docs.items():
                if key in url:
                    return FakeResponse(body.encode()) if body else urllib.error.HTTPError(url, 404, 'nf', {}, None)
            raise AssertionError(url)
        getter, _, _ = getter_for(handler, env=SEC_ENV)
        result = fetch_source('sec-form-d', since='2026-10-01', getter=getter, today=TODAY)
        index_calls = [c for c in calls if 'daily-index' in c]
        # clamped to 5 days (10/11..10/15); 10/11 is a Sunday
        self.assertEqual([c.rsplit('.', 2)[-2] for c in index_calls], ['20261012', '20261013', '20261014', '20261015'])
        self.assertTrue(all('/QTR4/' in c for c in index_calls))
        self.assertFalse(any('000100000226000002' in c for c in calls))  # D/A skipped
        self.assertEqual({p['full_name'] for p in result['prospects']},
                         {'Jasper D Quillon', 'Orla Sampleton', 'Ivy Placeholder'})
        self.assertEqual(result['companies'][0]['phone'], '555-010-0000')
        self.assertEqual(result['cursor'], '2026-10-15')

    def test_registry(self):
        self.assertEqual(set(SOURCES), {'tx-trec', 'ny-dos', 'co-dora', 'ct-dls', 'sec-form-d', 'fl-dbpr-file'})
        for meta in SOURCES.values():
            self.assertTrue(meta['description'] and meta['terms_note'] and meta['decision_maker_rule'])
        self.assertNotIn('ca-dre', SOURCES)


if __name__ == '__main__':
    unittest.main()
