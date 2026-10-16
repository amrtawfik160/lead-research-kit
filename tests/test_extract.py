"""Offline extraction tests over synthetic HTML on reserved domains."""
import json
import threading
import unittest

from lead_research.extract import (apply_pattern, classify_email, clean_person_name, crawl_domains,
                                   decode_cfemail, deobfuscate_emails, discover_pages,
                                   extract_company, extract_people, find_emails, infer_pattern,
                                   looks_like_title)
from lead_research.webfetch import FetchResult

SITE = 'https://harbor-realty.example/'
PAGE = 'https://harbor-realty.example/team'


def cf_encode(email, key=0x5A):
    return f'{key:02x}' + ''.join(f'{ord(c) ^ key:02x}' for c in email)


def by_name(people):
    return {p['full_name']: p for p in people}


class NameAndTitleTests(unittest.TestCase):
    def test_person_name_heuristic(self):
        for value, expected in [('Jane Doe', 'Jane Doe'), ('Maria de la Cruz', 'Maria de la Cruz'),
                                ("Tom O'Brien", "Tom O'Brien"), ('Mary-Kate Olsen-Smith', 'Mary-Kate Olsen-Smith'),
                                ('J. Robert Smith', 'J. Robert Smith'), ('JANE DOE', 'Jane Doe'),
                                ('Dr. Ann Lee', 'Ann Lee'), ('Jane Doe, REALTOR®', 'Jane Doe'),
                                ('John Smith Jr.', 'John Smith'), ('Ludwig van Beethoven', 'Ludwig van Beethoven'),
                                ('José Núñez', 'José Núñez'), ('Kelly McDonald', 'Kelly McDonald')]:
            with self.subTest(value=value):
                self.assertEqual(clean_person_name(value), expected)
        for value in ('Our Team', 'Contact Us', 'Real Estate', 'Read More', 'View Profile',
                      'Commercial Properties', 'Boston Office', 'Managing Broker', 'Meet The Team',
                      'Jane', 'Call 617 555 0142', 'jane doe', 'The Harbor Group', 'San Diego',
                      'Chief Executive Officer', 'Smith, John', 'IDX Broker Reciprocity'):
            with self.subTest(value=value):
                self.assertIsNone(clean_person_name(value))

    def test_title_heuristic(self):
        for value in ('CEO', 'Broker/Owner', 'Managing Broker', 'Vice President of Sales',
                      'Designated Broker', 'REALTOR®', 'Co-Founder & Principal'):
            self.assertTrue(looks_like_title(value), value)
        for value in ('Our Agents', 'Find an Agent', 'Jane is a top agent in Boston', 'Jane Doe',
                      'Contact Us'):
            self.assertFalse(looks_like_title(value), value)


class EmailTests(unittest.TestCase):
    def test_deobfuscation_forms(self):
        text = ('Reach jane [at] harbor-realty [dot] example or bob(at)harbor-realty.example, '
                'ann {at} harbor-realty {dot} example, kim AT harbor-realty DOT example')
        self.assertEqual(find_emails(text), ['jane@harbor-realty.example', 'bob@harbor-realty.example',
                                             'ann@harbor-realty.example', 'kim@harbor-realty.example'])
        self.assertEqual(deobfuscate_emails('meet us at noon'), 'meet us at noon')

    def test_junk_emails_filtered(self):
        self.assertEqual(find_emails('logo@' '2x.png you@example.com abc@' 'sentry.io real@harbor.example'),
                         ['real@harbor.example'])

    def test_cloudflare_decode(self):
        self.assertEqual(decode_cfemail(cf_encode('ann.lee@harbor-realty.example')),
                         'ann.lee@harbor-realty.example')
        self.assertIsNone(decode_cfemail('zz'))
        self.assertIsNone(decode_cfemail(cf_encode('not an email')))

    def test_classify_email(self):
        domain = 'www.harbor-realty.example'
        self.assertEqual(classify_email('jane.doe@harbor-realty.example', domain), 'person')
        self.assertEqual(classify_email('Info@Harbor-Realty.example', domain), 'role')
        self.assertEqual(classify_email('sales2@harbor-realty.example', 'harbor-realty.example'), 'role')
        self.assertEqual(classify_email('leasing.boston@mail.harbor-realty.example', domain), 'role')
        self.assertEqual(classify_email('noreply@harbor-realty.example', domain), 'role')
        self.assertEqual(classify_email('jane@other.example', domain), 'other_domain')
        self.assertEqual(classify_email('jane.doe@' 'gmail.com', domain), 'personal_provider')


class PeopleExtractionTests(unittest.TestCase):
    def test_jsonld_graph_people(self):
        data = {'@context': 'https://schema.org', '@graph': [
            {'@type': 'RealEstateAgent', 'name': 'Harbor Realty',
             'employee': [{'@type': 'Person', 'name': 'Maria de la Cruz', 'jobTitle': 'Managing Broker',
                           'email': 'mailto:Maria@Harbor-Realty.example',
                           'sameAs': ['https://twitter.com/x', 'https://www.linkedin.com/in/mariadlc/?trk=1']},
                          {'givenName': 'Sam', 'familyName': 'Park', 'jobTitle': ['COO']}],
             'founder': {'@type': 'Person', 'name': "Tom O'Brien"}},
            {'@type': 'RealEstateAgent', 'name': 'Lena Ortiz', 'jobTitle': 'Realtor'},
            {'@type': 'BlogPosting', 'author': {'@type': 'Person', 'name': 'Ghost Writer'}}]}
        html = f'<script type="application/ld+json">{json.dumps(data)}</script><p>Maria de la Cruz</p>'
        people = by_name(extract_people(html, PAGE))
        self.assertEqual(set(people), {'Maria de la Cruz', 'Sam Park', "Tom O'Brien", 'Lena Ortiz'})
        maria = people['Maria de la Cruz']
        self.assertEqual((maria['title'], maria['email'], maria['linkedin_url'], maria['method']),
                         ('Managing Broker', 'maria@harbor-realty.example',
                          'https://www.linkedin.com/in/mariadlc', 'jsonld'))
        self.assertIn('Maria de la Cruz', maria['excerpt'])
        self.assertEqual(people["Tom O'Brien"]['title'], 'Founder')
        self.assertEqual(people['Sam Park']['title'], 'COO')

    def test_invalid_jsonld_is_ignored(self):
        html = '<script type="application/ld+json">{not json</script><h3>Jane Doe</h3><p>CEO</p>'
        self.assertEqual([p['full_name'] for p in extract_people(html, PAGE)], ['Jane Doe'])

    def test_microdata_person(self):
        html = ('<div itemscope itemtype="https://schema.org/Person"><span itemprop="name">Ann Lee</span>'
                '<span itemprop="jobTitle">Chief Operating Officer</span>'
                '<a itemprop="email" href="mailto:ann.lee@harbor-realty.example">Email</a>'
                '<div itemprop="worksFor" itemscope itemtype="https://schema.org/Organization">'
                '<span itemprop="name">Harbor Realty</span></div></div>')
        ann = by_name(extract_people(html, PAGE))['Ann Lee']
        self.assertEqual((ann['title'], ann['email'], ann['method']),
                         ('Chief Operating Officer', 'ann.lee@harbor-realty.example', 'microdata'))

    def test_mailto_card_with_heading(self):
        html = '''<h2>Our Team</h2>
        <div class="card"><h3>Jane Doe</h3><p>Broker/Owner</p><p>Jane has sold homes for 20 years.</p>
          <a href="tel:+16175550142">617.555.0142</a>
          <a href="mailto:jane.doe@harbor-realty.example?subject=Hi">Email Jane</a></div>
        <div class="card"><h3>Bob Stone</h3><p>Realtor</p>
          <a href="mailto:bob@harbor-realty.example">Email</a></div>
        <footer><p>Questions? <a href="mailto:info@harbor-realty.example">info@harbor-realty.example</a></p></footer>'''
        people = by_name(extract_people(html, PAGE))
        jane = people['Jane Doe']
        self.assertEqual((jane['title'], jane['email'], jane['method'], jane['evidence_url']),
                         ('Broker/Owner', 'jane.doe@harbor-realty.example', 'mailto', PAGE))
        self.assertIn('Jane Doe', jane['excerpt'])
        self.assertIn('jane.doe@harbor-realty.example', jane['excerpt'])
        self.assertLessEqual(len(jane['excerpt']), 240)
        self.assertEqual(people['Bob Stone']['email'], 'bob@harbor-realty.example')
        self.assertNotIn('info@harbor-realty.example', [p['email'] for p in people.values()])

    def test_inline_name_title_and_anchor_name(self):
        html = ('<ul><li>Karen White - Designated Broker</li><li>Leo Gray, CFO</li></ul>'
                '<p>Write to <a href="mailto:pat.kim@harbor-realty.example">Pat Kim</a> today.</p>')
        people = by_name(extract_people(html, PAGE))
        self.assertEqual(people['Karen White']['title'], 'Designated Broker')
        self.assertEqual(people['Leo Gray']['title'], 'CFO')
        self.assertEqual(people['Pat Kim']['email'], 'pat.kim@harbor-realty.example')

    def test_linkedin_links(self):
        html = ('<div><h4>Chris Hall</h4><p>Principal</p><a href="https://www.linkedin.com/in/chrishall">'
                '<img alt="LinkedIn"></a></div>'
                '<p><a href="https://linkedin.com/in/dana-fox-123/">Dana Fox</a></p>'
                '<a href="https://www.linkedin.com/company/harbor-realty">Company</a>')
        people = by_name(extract_people(html, PAGE))
        self.assertEqual(people['Chris Hall']['linkedin_url'], 'https://www.linkedin.com/in/chrishall')
        self.assertEqual(people['Chris Hall']['method'], 'linkedin_link')
        self.assertEqual(people['Dana Fox']['linkedin_url'], 'https://www.linkedin.com/in/dana-fox-123')

    def test_obfuscated_and_cloudflare_emails_in_cards(self):
        hexed = cf_encode('ann.lee@harbor-realty.example')
        html = (f'<div><h3>John Smith</h3><p>Agent</p><p>john.smith [at] harbor-realty [dot] example</p></div>'
                f'<div><h3>Ann Lee</h3><p>President</p><a href="/cdn-cgi/l/email-protection#{hexed}">'
                f'<span class="__cf_email__" data-cfemail="{hexed}">[email&#160;protected]</span></a></div>')
        people = by_name(extract_people(html, PAGE))
        self.assertEqual(people['John Smith']['email'], 'john.smith@harbor-realty.example')
        self.assertEqual(people['Ann Lee']['email'], 'ann.lee@harbor-realty.example')
        self.assertEqual(people['Ann Lee']['title'], 'President')

    def test_email_is_not_misattributed_to_neighbor(self):
        html = ('<div><h3>Mary Jones</h3><p>Realtor</p></div>'
                '<div><h3>Paul Reed</h3><p>Broker</p><p>mjones@harbor-realty.example</p></div>')
        people = by_name(extract_people(html, PAGE))
        self.assertIsNone(people['Paul Reed']['email'])

    def test_non_names_produce_no_people(self):
        html = ('<nav><a>Our Team</a><a>Contact Us</a></nav><h2>Commercial Properties</h2><p>Agent</p>'
                '<h3>Read More</h3><p>Real Estate Agent</p><h3>View Profile</h3><p>Broker</p>')
        self.assertEqual(extract_people(html, PAGE), [])

    def test_same_person_merged_across_methods(self):
        data = {'@type': 'Person', 'name': 'Jane Doe', 'jobTitle': 'CEO'}
        html = (f'<script type="application/ld+json">{json.dumps(data)}</script>'
                '<h3>Jane Doe</h3><p>CEO</p><a href="mailto:jane@harbor-realty.example">x</a>'
                '<a href="https://www.linkedin.com/in/janedoe">in</a>')
        people = extract_people(html, PAGE)
        self.assertEqual(len(people), 1)
        self.assertEqual((people[0]['title'], people[0]['email'], people[0]['linkedin_url'], people[0]['method']),
                         ('CEO', 'jane@harbor-realty.example', 'https://www.linkedin.com/in/janedoe', 'jsonld'))
        self.assertEqual(set(people[0]), {'full_name', 'title', 'email', 'linkedin_url', 'evidence_url',
                                          'excerpt', 'method'})


class DiscoveryAndCompanyTests(unittest.TestCase):
    def test_discover_pages_ranking_and_exclusion(self):
        html = '''<a href="/contact">Contact</a><a href="/about-us/">About Us</a>
        <a href="/our-team?utm_source=nav#top">Our Team</a><a href="/our-team/">Team</a>
        <a href="https://www.harbor-realty.example/leadership">Leadership</a>
        <a href="/listing/123-main-st">Agent pick</a><a href="/homes-for-sale/boston">Agents</a>
        <a href="/idx/search">Search</a><a href="/blog/meet-our-team-post">Meet our team</a>
        <a href="/agents/page/2">Agents</a><a href="/2023/05/team-news">Team</a>
        <a href="/team.pdf">Team PDF</a><a href="mailto:info@harbor-realty.example">Email</a>
        <a href="tel:6175550142">Call</a><a href="https://other.example/team">Team</a>
        <a href="/agents/jane-doe">Jane</a><a href="/agents/bob">Bob</a><a href="/agents/kim">Kim</a>
        <a href="/">Home</a><a href="/buy">Buy</a>'''
        pages = discover_pages(SITE, html, limit=8)
        self.assertEqual(pages[:2], ['https://harbor-realty.example/our-team',
                                     'https://www.harbor-realty.example/leadership'])
        self.assertIn('https://harbor-realty.example/about-us/', pages)
        self.assertIn('https://harbor-realty.example/contact', pages)
        self.assertLess(pages.index('https://harbor-realty.example/about-us/'),
                        pages.index('https://harbor-realty.example/contact'))
        joined = ' '.join(pages)
        for bad in ('listing', 'homes-for-sale', 'idx', 'blog', 'page/2', '2023', '.pdf', 'other.example',
                    'mailto', 'tel:', '/buy'):
            self.assertNotIn(bad, joined)
        self.assertEqual(sum('/agents/' in p for p in pages), 2)  # capped per parent path
        self.assertEqual(len(pages), len(set(pages)))
        self.assertEqual(len(discover_pages(SITE, html, limit=2)), 2)

    def test_extract_company(self):
        data = {'@context': 'https://schema.org', '@type': 'RealEstateAgent', 'name': 'Harbor Realty Group',
                'telephone': '+1-617-555-0100', 'email': 'hello@harbor-realty.example',
                'sameAs': ['https://www.linkedin.com/company/harbor-realty/'],
                'address': {'@type': 'PostalAddress', 'addressLocality': 'Boston', 'addressRegion': 'MA',
                            'addressCountry': {'@type': 'Country', 'name': 'US'}}}
        html = (f'<html><head><title>Home | Harbor Realty</title><meta name="description" content="Boston '
                f'brokerage since 1999."><script type="application/ld+json">{json.dumps(data)}</script></head>'
                '<body><p>Call (617) 555-0142 or 617.555.0143</p><a href="tel:16175550144">Call</a>'
                '<p>info [at] harbor-realty [dot] example</p><a href="mailto:jane.doe@harbor-realty.example">J</a>'
                '<p>Order #2023-555-1234-99</p></body></html>')
        company = extract_company(html, 'https://www.harbor-realty.example/')
        self.assertEqual(company['name'], 'Harbor Realty Group')
        self.assertEqual(company['domain'], 'harbor-realty.example')
        self.assertEqual(company['generic_emails'], ['hello@harbor-realty.example', 'info@harbor-realty.example'])
        self.assertIn('jane.doe@harbor-realty.example', company['all_emails'])
        self.assertEqual(company['phones'], ['(617) 555-0100', '(617) 555-0142', '(617) 555-0143',
                                             '(617) 555-0144'])
        self.assertEqual(company['linkedin_company_url'], 'https://www.linkedin.com/company/harbor-realty')
        self.assertEqual((company['address_region'], company['country'], company['address_locality']),
                         ('MA', 'US', 'Boston'))
        self.assertEqual(company['description'], 'Boston brokerage since 1999.')

    def test_company_name_fallbacks(self):
        og = '<meta property="og:site_name" content="Bay Homes">'
        self.assertEqual(extract_company(og, 'https://bay-homes.example/')['name'], 'Bay Homes')
        title = '<title>Boston Homes for Sale - Harbor Realty</title>'
        self.assertEqual(extract_company(title, SITE)['name'], 'Harbor Realty')
        self.assertEqual(extract_company('<title>Home | Welcome</title>', SITE)['name'], None)


class PatternTests(unittest.TestCase):
    DOMAIN = 'harbor-realty.example'

    def test_infer_and_apply(self):
        people = [{'full_name': 'Jane Doe', 'email': 'jdoe@harbor-realty.example'},
                  {'full_name': 'Bob Stone', 'email': 'bstone@harbor-realty.example'},
                  {'full_name': 'Ann Lee', 'email': 'ann@harbor-realty.example'},
                  {'full_name': 'Office', 'email': 'info@harbor-realty.example'},
                  {'full_name': 'Kim Wu', 'email': 'kim@' 'gmail.com'}]
        self.assertEqual(infer_pattern(people, self.DOMAIN),
                         {'pattern': '{f}{last}', 'support': 2, 'total': 3, 'confidence': 0.667})
        self.assertEqual(apply_pattern('{f}{last}', 'José Núñez-García', self.DOMAIN),
                         'jnunezgarcia@harbor-realty.example')
        self.assertEqual(apply_pattern('{first}.{last}', 'Dr. Mary Ann Smith Jr.', 'www.' + self.DOMAIN),
                         'mary.smith@harbor-realty.example')
        self.assertIsNone(apply_pattern('{first}.{last}', 'Cher', self.DOMAIN))
        self.assertIsNone(apply_pattern('{bogus}', 'Jane Doe', self.DOMAIN))

    def test_all_patterns_round_trip(self):
        for pattern in ('{first}', '{last}', '{first}.{last}', '{first}{last}', '{f}{last}', '{f}.{last}',
                        '{first}{l}', '{first}_{last}', '{last}{f}', '{last}.{first}', '{f}{l}'):
            email = apply_pattern(pattern, 'Jane Doe', self.DOMAIN)
            result = infer_pattern([{'full_name': 'Jane Doe', 'email': email}], self.DOMAIN)
            self.assertEqual(result['pattern'], pattern, email)

    def test_tie_and_empty_return_none(self):
        tie = [{'full_name': 'Jane Doe', 'email': 'jane@harbor-realty.example'},
               {'full_name': 'Bob Stone', 'email': 'stone@harbor-realty.example'}]
        self.assertIsNone(infer_pattern(tie, self.DOMAIN))
        self.assertIsNone(infer_pattern([], self.DOMAIN))
        self.assertIsNone(infer_pattern([{'full_name': 'Jane Doe', 'email': 'zz9@harbor-realty.example'}],
                                        self.DOMAIN))


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []
        self.lock = threading.Lock()

    def get(self, url):
        with self.lock:
            self.calls.append(url)
        page = self.pages.get(url)
        if page is None:
            return FetchResult(url, url, 404, 'text/html', '', False, 0.0, 'http_error')
        if isinstance(page, str) and page in ('network_error', 'blocked_by_robots', 'timeout'):
            return FetchResult(url, url, None, '', '', False, 0.0, page)
        if isinstance(page, Exception):
            raise page
        final, text = page if isinstance(page, tuple) else (url, page)
        return FetchResult(url, final, 200, 'text/html', text, False, 0.0, None)


class CrawlTests(unittest.TestCase):
    def test_crawl_domains_batch(self):
        harbor_home = ('<title>Harbor Realty</title><a href="/our-team">Our Team</a>'
                       '<a href="/about">About</a><a href="/contact">Contact</a>'
                       '<p>info@harbor-realty.example</p>')
        team = ('<h3>Jane Doe</h3><p>Broker/Owner</p><a href="mailto:jdoe@harbor-realty.example">E</a>'
                '<h3>Bob Stone</h3><p>Realtor</p><a href="mailto:bstone@harbor-realty.example">E</a>'
                '<h3>Ann Lee</h3><p>Agent</p><a href="/agents/ann-lee">Profile</a>')
        about = '<h2>Jane Doe</h2><p>Founder</p><a href="https://www.linkedin.com/in/janedoe">in</a>'
        pages = {
            'https://harbor-realty.example/': ('https://www.harbor-realty.example/', harbor_home),
            'https://www.harbor-realty.example/our-team': team,
            'https://www.harbor-realty.example/about': about,
            'https://www.harbor-realty.example/agents/ann-lee': '<h1>Ann Lee</h1><p>Agent</p><p>alee@harbor-realty.example</p>',
            'https://down.example/': 'network_error', 'https://www.down.example/': 'network_error',
            'http://down.example/': 'network_error',
            'https://closed.example/': 'blocked_by_robots',
            'https://quiet.example/': '<title>Quiet Co</title><p>Welcome</p>',
            'https://broken.example/': RuntimeError('boom'),
        }
        fetcher = FakeFetcher(pages)
        progress = []
        results = crawl_domains(['https://Harbor-Realty.example/home', 'down.example', 'closed.example',
                                 'quiet.example', 'broken.example', 'down.example', ''],
                                fetcher, max_pages=4, workers=4,
                                progress=lambda d, t, dom: progress.append((d, t, dom)))
        self.assertEqual([r['domain'] for r in results],
                         ['harbor-realty.example', 'down.example', 'closed.example', 'quiet.example',
                          'broken.example'])
        harbor, down, closed, quiet, broken = results
        self.assertEqual(harbor['status'], 'ok')
        self.assertEqual(harbor['homepage_url'], 'https://www.harbor-realty.example/')
        self.assertLessEqual(harbor['pages_fetched'], 4)
        people = by_name(harbor['people'])
        self.assertEqual(people['Jane Doe']['email'], 'jdoe@harbor-realty.example')
        self.assertEqual(people['Jane Doe']['linkedin_url'], 'https://www.linkedin.com/in/janedoe')
        self.assertEqual(people['Jane Doe']['evidence_url'], 'https://www.harbor-realty.example/our-team')
        self.assertEqual(harbor['pattern']['pattern'], '{f}{last}')
        self.assertEqual(harbor['company']['name'], 'Harbor Realty')
        self.assertEqual(harbor['company']['generic_emails'], ['info@harbor-realty.example'])
        self.assertEqual((down['status'], down['errors']), ('unreachable', ['network_error']))
        self.assertEqual(fetcher.calls.count('http://down.example/'), 1)
        self.assertEqual(closed['status'], 'blocked')
        self.assertEqual((quiet['status'], quiet['pages_fetched']), ('no_people', 1))
        self.assertEqual((broken['status'], broken['errors']), ('unreachable', ['internal_error']))
        self.assertEqual(sorted(p[0] for p in progress), [1, 2, 3, 4, 5])
        self.assertTrue(all(p[1] == 5 for p in progress))
        self.assertEqual(crawl_domains([], fetcher), [])


if __name__ == '__main__':
    unittest.main()
