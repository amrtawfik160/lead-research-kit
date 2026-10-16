"""Lead quality engine: turn messy lead rows into a clean, reviewable CSV.

Pipeline::

    raw rows -> normalize_record -> dedupe -> assess -> clean / review / rejected

The clean CSV always uses exactly ``HEADERS``. Values that fail validation
(personal or role mailboxes, franchise-parent or ISP websites, generic company
names) are blanked in the clean file instead of being shipped as bad data. The
review and rejected files keep the questionable values so a human can audit
them, together with stable snake_case issue codes.

Everything here is offline and stdlib-only. It judges shape and consistency of
the data; it never proves that a mailbox exists or that a person still holds a
role. Provider verification results are only trusted when they are supplied in
``email_verification``.

Output encoding is UTF-8 without a BOM. Cells that a spreadsheet could execute
as a formula are prefixed with a single quote.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import tempfile
import unicodedata
from collections import Counter
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

__all__ = [
    'HEADERS', 'REVIEW_HEADERS', 'REJECTED_HEADERS', 'Target', 'normalize_record', 'classify_title',
    'assess', 'dedupe', 'clean_records', 'write_outputs', 'clean_csv',
]

HEADERS = ['Full Name', 'Title', 'Company', 'Company Website', 'LinkedIn URL', 'Email']
REVIEW_HEADERS = HEADERS + ['Tier', 'Score', 'Issues', 'Email Source']
REJECTED_HEADERS = HEADERS + ['Reject Reasons', 'Issues']
TIER_ORDER = {'A': 0, 'B': 1, 'C': 2, 'reject': 3}


@dataclass(frozen=True)
class Target:
    """Who we want in the clean file.

    ``industry_keywords`` is only a soft signal (missing it costs a few points).
    ``exclude_org_keywords`` are matched on word boundaries against the company
    name and cause a hard ``non_target_org`` reject.
    """

    country: str = 'US'
    industry_keywords: tuple = (
        'real estate', 'realty', 'realtor', 'realtors', 'properties', 'property', 'brokerage', 'commercial',
        'cre', 'homes', 'land', 'development', 'investments', 'capital', 'partners', 'group',
        'realestate', 're', 'estates', 'retail', 'multifamily', 'apartments', 'housing',
    )
    require_decision_maker: bool = True
    allow_personal_email: bool = False
    min_tier: str = 'B'
    exclude_org_keywords: tuple = (
        'association', 'associations', 'council', 'institute', 'society', 'chamber of commerce', 'chamber',
        'university', 'college', 'school', 'academy', 'media', 'magazine', 'podcast', 'software',
        'technologies', 'marketing agency', 'staffing', 'recruiting', 'recruitment', 'foundation',
        'federation', 'nonprofit', 'non-profit', 'mastermind', 'networking', 'coaching', 'education',
        'roundtable', 'summit', 'conference', 'expo', 'standards organization',
    )


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

FIELD_ALIASES = {
    'full_name': ('full_name', 'fullname', 'name', 'person_name', 'contact_name'),
    'first_name': ('first_name', 'firstname', 'first', 'given_name'),
    'last_name': ('last_name', 'lastname', 'last', 'surname', 'family_name'),
    'title': ('title', 'position', 'job_title', 'jobtitle', 'headline', 'role'),
    'company': ('company', 'organization', 'organisation', 'company_name', 'employer', 'account_name'),
    'company_website': ('company_website', 'website', 'domain', 'company_domain', 'company_url', 'url'),
    'linkedin_url': ('linkedin_url', 'linkedin', 'profile_url', 'person_linkedin_url', 'linkedin_profile'),
    'email': ('email', 'work_email', 'email_address', 'business_email'),
    'email_verification': ('email_verification', 'email_status', 'verification', 'email_verification_status'),
}
PASSTHROUGH_KEYS = (
    'email_source', 'email_confidence', 'email_verification', 'email_evidence_url', 'source', 'source_url',
    'location', 'country', 'phone',
)

PERSONAL_EMAIL_DOMAINS = frozenset({
    'gmail.com', 'googlemail.com', 'yahoo.com', 'ymail.com', 'rocketmail.com', 'aol.com', 'aim.com',
    'hotmail.com', 'outlook.com', 'live.com', 'msn.com', 'passport.com', 'icloud.com', 'me.com', 'mac.com',
    'comcast.net', 'xfinity.com', 'verizon.net', 'att.net', 'sbcglobal.net', 'bellsouth.net', 'pacbell.net',
    'prodigy.net', 'swbell.net', 'ameritech.net', 'flash.net', 'nvbell.net', 'snet.net', 'cox.net',
    'charter.net', 'spectrum.net', 'earthlink.net', 'mindspring.com', 'windstream.net', 'frontier.com',
    'frontiernet.net', 'optonline.net', 'optimum.net', 'rr.com', 'roadrunner.com', 'twc.com',
    'centurylink.net', 'centurytel.net', 'embarqmail.com', 'q.com', 'qwest.net', 'suddenlink.net',
    'mediacombb.net', 'wowway.com', 'rcn.com', 'ptd.net', 'juno.com', 'netzero.net', 'netzero.com',
    'netscape.net', 'cs.com', 'protonmail.com', 'proton.me', 'pm.me', 'gmx.com', 'gmx.net', 'gmx.us',
    'zoho.com', 'zohomail.com', 'yandex.com', 'yandex.ru', 'mail.com', 'email.com', 'usa.com', 'mail.ru',
    'hey.com', 'fastmail.com', 'fastmail.fm', 'tutanota.com', 'tuta.io', 'inbox.com', 'lycos.com',
    'excite.com', 'hushmail.com', 'att.com', 'bigpond.com', 'web.de', 'free.fr', 'orange.fr', 'qq.com',
    '163.com', '126.com', 'naver.com', 'rediffmail.com',
})
# Any TLD: gmail.co.uk, yahoo.ca, hotmail.fr ...
PERSONAL_EMAIL_LABELS = frozenset({
    'gmail', 'googlemail', 'yahoo', 'ymail', 'hotmail', 'outlook', 'live', 'aol', 'icloud', 'gmx', 'yandex',
    'protonmail', 'zoho', 'msn',
})
SOCIAL_OR_BUILDER_DOMAINS = frozenset({
    'linkedin.com', 'lnkd.in', 'facebook.com', 'fb.com', 'instagram.com', 'twitter.com', 'x.com',
    'tiktok.com', 'youtube.com', 'youtu.be', 'pinterest.com', 'threads.net', 'zillow.com', 'realtor.com',
    'trulia.com', 'homes.com', 'loopnet.com', 'crexi.com', 'apartments.com', 'yelp.com', 'bbb.org',
    'linktr.ee', 'linktree.com', 'beacons.ai', 'bio.link', 'about.me', 'carrd.co', 'wixsite.com', 'wix.com',
    'godaddysites.com', 'godaddy.com', 'squarespace.com', 'weebly.com', 'wordpress.com', 'blogspot.com',
    'blogger.com', 'medium.com', 'substack.com', 'webflow.io', 'business.site', 'google.com', 'sites.google.com',
    'github.io', 'netlify.app', 'vercel.app', 'myshopify.com', 'square.site', 'crunchbase.com',
    'zoominfo.com', 'apollo.io', 'rocketreach.co', 'bit.ly', 'tinyurl.com', 'calendly.com',
    'land.com', 'landwatch.com', 'landsofamerica.com', 'agentfire.com', 'expportal.com', 'kvcore.com', 'placester.net',
})
PLACEHOLDER_DOMAINS = frozenset({
    'selfemployed.com', 'self-employed.com', 'none.com', 'na.com', 'noemail.com', 'test.com',
})
# Large franchise / brokerage parents. A small team using the parent's domain
# is not "their" company website unless the company name names the brand.
FRANCHISE_PARENTS = {
    'kw.com': ('keller williams', 'kw'), 'kellerwilliams.com': ('keller williams', 'kw'),
    'kwcommercial.com': ('keller williams', 'kw'), 'compass.com': ('compass',),
    'elliman.com': ('elliman',), 'raveis.com': ('raveis',), 'era.com': ('era',),
    'remax.com': ('re/max', 'remax'), 'remax.net': ('re/max', 'remax'),
    'coldwellbanker.com': ('coldwell banker',), 'cbrealty.com': ('coldwell banker',),
    'cbcworldwide.com': ('coldwell banker',), 'century21.com': ('century 21', 'century21', 'c21'),
    'sothebysrealty.com': ("sotheby's", 'sothebys', 'sotheby'), 'bhhs.com': ('berkshire hathaway', 'bhhs'),
    'exprealty.com': ('exp', 'exp realty'), 'exprealty.net': ('exp', 'exp realty'),
    'realbrokerage.com': ('real broker', 'real brokerage'), 'joinreal.com': ('real broker', 'real brokerage'),
    'onereal.com': ('real broker', 'real brokerage'), 'bhgre.com': ('better homes and gardens', 'bhgre', 'bhg'),
    'corcoran.com': ('corcoran',), 'cbre.com': ('cbre',), 'jll.com': ('jll',), 'cushwake.com': ('cushman',),
    'marcusmillichap.com': ('marcus & millichap', 'marcus millichap'), 'naiglobal.com': ('nai',),
    'svn.com': ('svn',), 'weichert.com': ('weichert',), 'longandfoster.com': ('long & foster', 'long and foster'),
    'howardhanna.com': ('howard hanna',), 'homesmart.com': ('homesmart',), 'fathomrealty.com': ('fathom',),
    'realtyonegroup.com': ('realty one',), 'redfin.com': ('redfin',), 'windermere.com': ('windermere',),
    'bairdwarner.com': ('baird & warner', 'baird warner'), 'unitedrealestate.com': ('united real estate',),
    'christiesrealestate.com': ("christie's", 'christies', 'christie s'),
    'johnlscott.com': ('john l scott', 'john l. scott'),
    'reecenichols.com': ('reecenichols',), 'firstteam.com': ('first team',), 'interorealestate.com': ('intero',),
}
GENERIC_CCTLDS = frozenset({'us', 'co', 'io', 'ai', 'me', 'tv', 'cc', 'ws', 'fm', 're', 'gg', 'ly', 'to', 'vc'})
SECOND_LEVEL_SUFFIXES = frozenset({
    'co.uk', 'org.uk', 'ac.uk', 'gov.uk', 'ltd.uk', 'me.uk', 'com.au', 'net.au', 'org.au', 'co.nz', 'com.mx',
    'com.br', 'co.jp', 'co.in', 'com.sg', 'co.za', 'com.cn', 'com.tr', 'com.ar', 'co.il', 'com.my', 'com.ph',
    'co.kr', 'com.hk', 'com.tw', 'com.sa', 'com.eg', 'com.co', 'com.pe', 'com.ve', 'com.bz', 'com.pk',
})
ROLE_MAILBOXES = frozenset({
    'info', 'hello', 'hi', 'broker', 'brokers', 'manager', 'office', 'sales', 'contact', 'contactus', 'admin',
    'team', 'support', 'help', 'marketing', 'inquiries', 'inquiry', 'enquiries', 'mail', 'email', 'reception',
    'frontdesk', 'leasing', 'rentals', 'listings', 'realestate', 'homes', 'noreply', 'donotreply', 'billing',
    'accounts', 'accounting', 'hr', 'jobs', 'careers', 'service', 'customerservice', 'webmaster', 'postmaster',
    'press', 'media', 'general', 'agent', 'agents', 'staff', 'operations', 'ops', 'invest', 'investors', 'properties',
})
US_COUNTRY_ALIASES = frozenset({'us', 'usa', 'u.s.', 'u.s.a.', 'united states', 'united states of america', 'america'})
NON_US_TEXT = re.compile(
    r'\b(ksa|saudi|saudi arabia|uae|dubai|abu dhabi|qatar|kuwait|bahrain|belize|italia|italy|canada|toronto|'
    r'vancouver|ontario|mexico|méxico|london|united kingdom|england|australia|india|nigeria|ghana|kenya|'
    r'philippines|brasil|brazil|argentina|españa|spain|france|germany|deutschland|israel|egypt)\b', re.I)

CREDENTIALS = frozenset({
    'mba', 'ccim', 'phd', 'md', 'jd', 'cpa', 'cfa', 'pa', 'mai', 'sra', 'srwa', 'sior', 'crs', 'gri', 'abr',
    'crb', 'cips', 'clhms', 'sres', 'cpm', 'ccrm', 'alc', 'esq', 'rn', 'pe', 'pmp', 'msre', 'mred', 'ms',
    'ma', 'mpa', 'mph', 'msc', 'cdpe', 'csp', 'epro', 'asp', 'psa', 'rene', 'ahwd', 'c2ex', 'mrp', 'sfr',
    'cne', 'cdei', 'cre', 'leed', 'ap', 'realtor', 'llc', 'dds', 'dvm', 'edd', 'cfp', 'chfc', 'clu', 'cgc',
    'shrm', 'rcs', 'rce', 'mcne', 'cam', 'ccam', 'arm', 'ccie',
})
# Credentials that are never plausible name parts; the rest are only stripped
# when written in capitals or with periods ("PA", "P.A.") to protect surnames.
STRONG_CREDENTIALS = frozenset({
    'mba', 'ccim', 'phd', 'cpa', 'cfa', 'mai', 'sra', 'srwa', 'sior', 'crs', 'gri', 'abr', 'crb', 'cips', 'clhms',
    'sres', 'cpm', 'ccrm', 'esq', 'pmp', 'msre', 'mred', 'cdpe', 'epro', 'ahwd', 'c2ex', 'sfr', 'cne', 'cdei',
    'leed', 'cfp', 'chfc', 'clu', 'shrm', 'mcne', 'ccam', 'ccie',
})
NAME_SUFFIXES = frozenset({'jr', 'jr.', 'sr', 'sr.', 'ii', 'iii', 'iv', 'v', 'vi'})
NAME_PARTICLES = frozenset({
    'de', 'la', 'del', 'della', 'van', 'von', 'der', 'den', 'da', 'di', 'du', 'le', 'dos', 'das', 'bin', 'ibn', 'y',
})
NAME_ORG_WORDS = re.compile(
    r'\b(real estate|realty|llc|inc|properties|group|team|homes|company|corp|brokerage)\b', re.I)
NICKNAME_GROUPS = (
    ('michael', 'mike', 'mikey', 'mick'), ('robert', 'bob', 'bobby', 'rob', 'robbie', 'bert'),
    ('william', 'bill', 'billy', 'will', 'willy', 'liam'), ('james', 'jim', 'jimmy', 'jamie'),
    ('richard', 'rick', 'ricky', 'rich', 'dick'), ('thomas', 'tom', 'tommy'), ('joseph', 'joe', 'joey'),
    ('charles', 'chuck', 'charlie', 'chas'), ('christopher', 'chris', 'kit'), ('matthew', 'matt'),
    ('daniel', 'dan', 'danny'), ('david', 'dave', 'davey'), ('anthony', 'tony'), ('steven', 'steve', 'stevie'),
    ('stephen', 'steve', 'stevie'), ('edward', 'ed', 'eddie', 'ted', 'ned'), ('theodore', 'ted', 'teddy', 'theo'),
    ('jonathan', 'jon', 'jonny'), ('john', 'jack', 'johnny', 'jon'), ('benjamin', 'ben', 'benny', 'benji'),
    ('samuel', 'sam', 'sammy'), ('alexander', 'alex', 'xander'), ('andrew', 'andy', 'drew'),
    ('nicholas', 'nick', 'nicky'), ('gregory', 'greg'), ('jeffrey', 'jeff'), ('geoffrey', 'jeff', 'geoff'),
    ('kenneth', 'ken', 'kenny'), ('ronald', 'ron', 'ronnie'), ('donald', 'don', 'donnie'), ('douglas', 'doug'),
    ('gerald', 'gerry', 'jerry'), ('jerome', 'jerry'), ('lawrence', 'larry'), ('patrick', 'pat', 'patty'),
    ('peter', 'pete'), ('raymond', 'ray'), ('timothy', 'tim', 'timmy'), ('zachary', 'zack', 'zach'),
    ('jennifer', 'jen', 'jenny'), ('elizabeth', 'liz', 'beth', 'betty', 'lizzy'),
    ('katherine', 'kate', 'kathy', 'katie'),
    ('catherine', 'cathy', 'kate', 'cat'), ('margaret', 'maggie', 'peggy', 'meg'), ('rebecca', 'becky', 'becca'),
    ('susan', 'sue', 'suzie', 'suzy'), ('deborah', 'deb', 'debbie'), ('patricia', 'patty', 'pat', 'trish'),
    ('melissa', 'mel', 'missy'), ('barbara', 'barb'), ('kimberly', 'kim'), ('victoria', 'vicki', 'tori'),
    ('alexandra', 'alex', 'lexi'), ('stephanie', 'steph'), ('christine', 'chris', 'tina'),
    ('christina', 'chris', 'tina'), ('nathan', 'nate'), ('nathaniel', 'nate', 'nat'), ('jacob', 'jake'),
    ('joshua', 'josh'), ('frederick', 'fred', 'freddie'), ('bradley', 'brad'), ('phillip', 'phil'),
    ('philip', 'phil'), ('mitchell', 'mitch'), ('gabriel', 'gabe'), ('jeremiah', 'jerry'), ('abraham', 'abe'),
    ('jacqueline', 'jackie', 'jacky', 'jacq'), ('gabrielle', 'gabby', 'gabi', 'elle'), ('tomas', 'tom', 'tommy'),
    ('dominic', 'dom', 'nick'), ('alfred', 'al'), ('albert', 'al'), ('allan', 'al'),
    ('leonard', 'leo', 'len'), ('vincent', 'vince'),
)
NICKNAMES = {}
for _group in NICKNAME_GROUPS:
    for _name in _group:
        NICKNAMES.setdefault(_name, set()).update(n for n in _group if n != _name)

# Stopwords ignored when matching a company name to its website.
COMPANY_STOPWORDS = frozenset({
    'real', 'estate', 'group', 'llc', 'inc', 'the', 'and', 'co', 'company', 'companies', 'partners', 'realty',
    'corp', 'corporation', 'ltd', 'of', 'llp', 'lp', 'pllc', 'pc', 'services', 'commercial', 'properties',
    'property', 'international', 'team', 'holdings', 'holding', 'brokerage', 'advisors', 'associates',
    'investments', 'investment', 'capital', 'management', 'development', 'homes', 'luxury', 'residential',
    'dba', 'at', 'a', 'an', 'for', 'by', 'with', 'in', 'on',
})
LEGAL_SUFFIX_RE = re.compile(r'(?:,?\s+(?:llc|l\.l\.c|inc|corp|co|ltd|lp|llp|pllc|pc)\.?)+$', re.I)
GENERIC_COMPANY_WORDS = frozenset({
    'real', 'estate', 'company', 'brokerage', 'broker', 'investor', 'investors', 'group', 'agent', 'agents',
    'realtor', 'realtors', 'commercial', 'residential', 'investments', 'investment', 'properties', 'property',
    'llc', 'inc', 'self', 'employed', 'selfemployed', 'freelance', 'freelancer', 'independent', 'retired',
    'confidential', 'stealth', 'n/a', 'na', 'none', 'various', 'private', 'business', 'owner', 'consultant',
    'consulting', 'services', 'firm', 'office', 'the', 'a', 'and', 'my', 'own', 'startup',
})
US_STATES = (
    'AL|AK|AZ|AR|CA|CO|CT|DE|DC|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|'
    'OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY'
)
# ", Seattle WA" / ", WA" / " Auburn, AL" at the end of a company name
LOCATION_SUFFIX_RE = re.compile(
    r',\s*(?:[A-Z][a-zA-Z.]+\s){0,2}(?:%s)$|\s[A-Z][a-z]+,\s*(?:%s)$' % (US_STATES, US_STATES))

# Soft-issue penalties (points subtracted from the score).
SOFT_PENALTIES = {
    'name_truncated': 15, 'non_us_signal': 10, 'email_domain_mismatch': 8, 'missing_company': 10,
    'website_company_mismatch': 5, 'email_not_name_based': 5, 'email_unverified_risky': 5,
    'company_is_domain': 5, 'company_truncated': 5, 'company_generic': 5, 'industry_unclear': 5,
    'personal_email': 5, 'role_email': 5, 'email_franchise_domain': 3, 'email_invalid': 5,
    'website_personal_domain': 5, 'website_edu_gov': 5, 'website_social_or_builder': 5,
    'website_franchise_parent': 5, 'website_placeholder': 5, 'website_invalid': 3, 'linkedin_invalid': 3,
}
HARD_REASONS = (
    'missing_full_name', 'name_not_person', 'missing_title', 'not_decision_maker', 'multiple_people',
    'non_target_org', 'non_us_signal', 'no_contact_path',
)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _text(value):
    """Coerce any cell value to a single-line, whitespace-collapsed string."""
    if value is None:
        return ''
    if not isinstance(value, str):
        value = str(value)
    value = unicodedata.normalize('NFC', value).replace('\u00a0', ' ')
    return re.sub(r'\s+', ' ', value).strip()


def fold(value):
    """Lowercase and strip accents: 'Garzón' -> 'garzon'."""
    value = unicodedata.normalize('NFKD', value or '')
    return ''.join(ch for ch in value if not unicodedata.combining(ch)).lower()


def _alnum(value):
    return re.sub(r'[^a-z0-9]', '', fold(value))


def _key(name):
    return re.sub(r'[^a-z0-9]+', '_', str(name).strip().lower()).strip('_')


def _add(items, code):
    if code not in items:
        items.append(code)


# ---------------------------------------------------------------------------
# Field normalizers
# ---------------------------------------------------------------------------

def normalize_host(value):
    """Return a bare lowercase registrable host ('harbor.example') or ''."""
    value = _text(value).lower().strip('/').strip()
    if not value or ' ' in value:
        return ''
    if '@' in value and '://' not in value:
        value = value.rsplit('@', 1)[1]
    if '://' not in value:
        value = 'http://' + value
    try:
        host = urlsplit(value).hostname or ''
    except ValueError:
        return ''
    host = host.strip('.')
    try:
        host = host.encode('idna').decode('ascii')
    except UnicodeError:
        return ''
    labels = host.split('.')
    if len(labels) < 2 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', lab) for lab in labels):
        return ''
    if not re.fullmatch(r'[a-z]{2,63}|xn--[a-z0-9-]{1,59}', labels[-1]):
        return ''
    return registrable_domain(host)


def registrable_domain(host):
    """'www.team.harbor.co.uk' -> 'harbor.co.uk' (small built-in suffix list)."""
    labels = host.lower().strip('.').split('.')
    if len(labels) >= 3 and '.'.join(labels[-2:]) in SECOND_LEVEL_SUFFIXES:
        return '.'.join(labels[-3:])
    if len(labels) >= 3 and labels[-1] in ('edu', 'gov', 'mil'):
        return '.'.join(labels[-2:])
    return '.'.join(labels[-2:]) if len(labels) >= 2 else host


def _domain_label(host):
    labels = host.split('.')
    if len(labels) >= 3 and '.'.join(labels[-2:]) in SECOND_LEVEL_SUFFIXES:
        return labels[-3]
    return labels[0] if labels else ''


def _tld(host):
    return host.rsplit('.', 1)[-1] if host else ''


def is_reserved_domain(host):
    """Reserved test domains (RFC 2606) are valid company websites here."""
    return host in ('example.com', 'example.org', 'example.net') or _tld(host) in ('example', 'test')


def normalize_linkedin(value):
    """Return ``https://www.linkedin.com/in/<slug>/`` or '' when not a person profile."""
    value = _text(value)
    if not value:
        return ''
    if '://' not in value:
        value = 'https://' + value.lstrip('/')
    try:
        parts = urlsplit(value)
    except ValueError:
        return ''
    host = (parts.hostname or '').lower()
    if not (host == 'linkedin.com' or host.endswith('.linkedin.com')):
        return ''
    segments = [s for s in parts.path.split('/') if s]
    if len(segments) < 2 or segments[0].lower() != 'in':
        return ''
    slug = unquote(segments[1]).strip().lower()
    if not re.fullmatch(r'[\w-]{2,100}', slug):
        return ''
    return 'https://www.linkedin.com/in/%s/' % slug


def linkedin_slug(url):
    match = re.match(r'https://www\.linkedin\.com/in/([^/]+)/$', url or '')
    return match.group(1) if match else ''


def normalize_email_address(value):
    """Lowercased syntactically valid address, else ''."""
    value = _text(value).lower().strip('<>').strip()
    if value.startswith('mailto:'):
        value = value[7:]
    if len(value) > 254 or not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,63}", value):
        return ''
    local, domain = value.rsplit('@', 1)
    if local.startswith('.') or local.endswith('.') or '..' in local or '..' in domain:
        return ''
    if any(not lab or lab.startswith('-') or lab.endswith('-') for lab in domain.split('.')):
        return ''
    return value


# ---- names ----------------------------------------------------------------

def _smart_case_token(token, position, count):
    lower = token.lower()
    if re.fullmatch(r'(?:[^\W\d_]\.)+[^\W\d_]?\.?', token):  # J.e. -> J.E.
        return token.upper()
    if lower.rstrip('.') in ('ii', 'iii', 'iv', 'vi'):
        return lower.upper()
    if lower in NAME_PARTICLES and 0 < position < count - 1:
        return lower

    def cap(piece):
        if not piece:
            return piece
        if piece.lower().startswith('mc') and len(piece) > 2:
            return 'Mc' + piece[2:3].upper() + piece[3:].lower()
        return piece[:1].upper() + piece[1:].lower()

    hyphen_parts = []
    for part in token.split('-'):
        pieces = re.split(r"(['’])", part)
        hyphen_parts.append(''.join(cap(p) if p not in ("'", '’') else p for p in pieces))
    return '-'.join(hyphen_parts)


def _needs_case_fix(token):
    letters = [c for c in token if c.isalpha()]
    if not letters:
        return False
    if re.fullmatch(r'(?:[^\W\d_]\.)+[^\W\d_]?\.?', token) and token != token.upper():
        return True
    if all(c.islower() for c in letters):
        return True
    return len(letters) >= 4 and all(c.isupper() for c in letters)


def _is_credential_token(token):
    key = _alnum(token)
    if token.lower() in NAME_SUFFIXES or key not in CREDENTIALS:
        return False
    return key in STRONG_CREDENTIALS or '.' in token or (token.isupper() and len(key) >= 2)


def clean_name(raw):
    """Clean a person name.

    Returns ``(full_name, first_name, last_name, aliases, fixes, flags)`` where
    ``aliases`` are nicknames / maiden names found in quotes or parentheses
    (used only for email matching).
    """
    fixes, flags, aliases = [], [], []
    name = _text(raw)
    if not name:
        return '', '', '', aliases, fixes, flags
    original = name
    aka = re.split(r'\s+(?:a\.?k\.?a\.?|aka)\s+|\s*\(\s*aka\b', name, maxsplit=1, flags=re.I)
    if len(aka) > 1:
        name = aka[0]
        _add(fixes, 'name_aka_removed')
    for pattern in (r'\(([^)]*)\)', r'"([^"]*)"', r'“([^”]*)”'):
        found = re.findall(pattern, name)
        if found:
            aliases.extend(_alnum(f) for f in found if _alnum(f))
            name = re.sub(pattern, ' ', name)
            _add(fixes, 'name_nickname_removed')
    name = re.sub(r'\s+', ' ', name).strip(' ,')
    # honorifics at the start ("Dr.", "Ms", "Gp Capt"); rank words need a period
    while True:
        match = re.match(r'(?i)(?:(?:gp|grp)\.?\s+capt|mrs|mr|ms|miss|mx|dr|prof|professor|rev|sir|dame)\.?\s+'
                         r'|(?:hon|capt|col|lt|maj|gen|sgt|cmdr)\.\s+', name)
        if not match or not name[match.end():].strip():
            break
        name = name[match.end():]
        _add(fixes, 'name_honorific_removed')
    # post-nominal credentials: after a comma, or trailing tokens
    if ',' in name:
        head, *tail = [p.strip() for p in name.split(',')]
        kept = [head]
        for part in tail:
            words = part.split()
            is_credentials = words and all(_alnum(w) in CREDENTIALS and w.lower() not in NAME_SUFFIXES for w in words)
            if is_credentials:
                _add(fixes, 'name_credentials_removed')
            elif part:
                kept.append(part)
        name = ' '.join(kept)
    if re.search(r'^[^\W\d_][\w\'’.-]*\s+(?:and|&)\s+[^\W\d_]', name, re.I) and len(name.split()) >= 4:
        _add(flags, 'multiple_people')
    tokens = [t for t in name.split() if any(c.isalpha() for c in t)]
    if len(tokens) != len(name.split()):
        _add(fixes, 'name_junk_removed')
    while len(tokens) > 2 and _is_credential_token(tokens[-1]):
        tokens.pop()
        _add(fixes, 'name_credentials_removed')
    if not tokens:
        return '', '', '', aliases, fixes, flags
    letters = [c for c in ''.join(tokens) if c.isalpha()]
    whole = all(c.islower() for c in letters) or all(c.isupper() for c in letters)
    cased = []
    for i, token in enumerate(tokens):
        if whole or _needs_case_fix(token):
            new = _smart_case_token(token, i, len(tokens))
        else:
            new = token
        cased.append(new)
    if cased != tokens:
        _add(fixes, 'name_case_fixed')
    tokens = cased
    full = ' '.join(tokens)
    if NAME_ORG_WORDS.search(full):
        _add(flags, 'name_not_person')
    core = [t for t in tokens if t.lower() not in NAME_SUFFIXES]
    if len(core) >= 2 and re.fullmatch(r'[^\W\d_]\.?', core[-1]):
        _add(flags, 'name_truncated')
    if len(core) < 2:
        _add(flags, 'name_incomplete')
    first = core[0] if core else ''
    last = core[-1] if len(core) > 1 else ''
    if full != original and not fixes:
        _add(fixes, 'name_whitespace_fixed')
    return full, first, last, aliases, fixes, flags


# ---- titles ---------------------------------------------------------------

DM_SENIORITIES = frozenset({
    'owner', 'founder', 'c_level', 'president', 'partner', 'principal', 'managing_broker', 'managing_director',
})
SENIORITY_RANK = {
    'owner': 100, 'founder': 99, 'c_level': 90, 'president': 95, 'partner': 85, 'principal': 84,
    'managing_broker': 83, 'managing_director': 82, 'vp': 60, 'director': 55, 'manager': 45, 'investor': 30,
    'agent': 25, 'advisor': 22, 'member': 20, 'other': 0,
}
TITLE_GUARD = re.compile(
    r'\b(assistant|assistance|intern|internship|former|retired|aspiring|student|trainee)\b|\bex-|'
    r'\bto the (ceo|founder|president|owner|chairman)\b', re.I)
TITLE_KEYWORD = re.compile(
    r'\b(ceo|coo|cfo|cto|cio|cmo|cro|cdo|cpo|cso|cco|evp|svp|avp|vp|chief|officer|executive|founder|co-?founder|'
    r'founding|owner|co-?owner|proprietor|president|pres|chair|chairman|chairwoman|chairperson|partner|principal|'
    r'managing|director|head|manager|lead|leader|broker|agent|realtor|realtors|salesperson|associate|specialist|'
    r'consultant|advisor|adviser|developer|investor|wholesaler|member|instructor|coordinator|analyst|engineer|'
    r'assistant|rep|representative|facilitator|mentor|coach|strategist|operator|producer|attorney|lawyer|'
    r'appraiser|lender|officer|secretary|treasurer|trustee|administrator|supervisor)\b|realtor®', re.I)
TOP_TITLE = re.compile(
    r'\b(ceo|co-?ceo|coo|chief executive officer|chief operating officer|founder|co-?founder|owner|co-?owner|'
    r'president|chairman|chair|principal|partner|managing broker|broker)\b', re.I)
FUNCTION_AREAS = re.compile(
    r'^(?:of |for )?(?:the )?(sales|operations|marketing|finance|business|development|acquisitions|leasing|'
    r'technology|strategy|investments?|asset|capital markets|real estate$|north america|europe|global|the board|'
    r'the americas)\b', re.I)
COMPANY_HINT_WORDS = re.compile(
    r'\b(real estate|realty|group|properties|llc|inc|corp|company|co\.|partners|capital|homes|team|brokerage|'
    r'international|holdings|ventures|commercial)\b|\.[a-z]{2,4}$', re.I)


def classify_title(title):
    """Classify a title. Returns dict(seniority, decision_maker, points, label).

    points: CEO / chairman / founder / owner / president = 30; partner /
    principal / managing broker / managing director = 26; other chief officers
    (COO, CFO, CTO, CIO ...) = 22; non decision makers get small values that only
    matter when ``require_decision_maker`` is off.
    """
    title = _text(title)
    if not title or TITLE_GUARD.search(title):
        return {'seniority': 'other', 'decision_maker': False, 'points': 0, 'label': 'other'}
    best = ('other', 'other')
    for segment in re.split(r'\s*(?:[|•·/,;+&–—]|\s-\s|\band\b|\bat\b|@|\.\s)\s*', title, flags=re.I):
        result = _classify_segment(segment)
        upgrade_to_ceo = result[0] == best[0] == 'c_level' and result[1] in ('ceo', 'chairman') \
            and best[1] not in ('ceo', 'chairman')
        if SENIORITY_RANK[result[0]] > SENIORITY_RANK[best[0]] or upgrade_to_ceo:
            best = result
    seniority, label = best
    if seniority in ('owner', 'founder', 'president') or label in ('ceo', 'chairman'):
        points = 30
    elif seniority in ('partner', 'principal', 'managing_broker', 'managing_director'):
        points = 26
    elif seniority == 'c_level':
        points = 22
    else:
        points = {'vp': 12, 'director': 10, 'manager': 6}.get(seniority, 0)
    return {'seniority': seniority, 'decision_maker': seniority in DM_SENIORITIES, 'points': points, 'label': label}


def _classify_segment(segment):
    """(seniority, label) for one title fragment, most senior pattern first."""
    s = ' ' + fold(segment).replace('co founder', 'cofounder').replace('co-founder', 'cofounder').strip() + ' '
    s = s.replace('co-owner', 'coowner').replace('co owner', 'coowner')
    if not s.strip():
        return 'other', 'other'
    if re.search(r'\b(coowner|owner|proprietor)\b', s) and not re.search(
            r'\b(product|process|project|account|owner\'?s? rep|owner relations)\b', s):
        return 'owner', 'owner'
    if re.search(r'\b(cofounder|founder)\b', s):
        return 'founder', 'founder'
    if re.search(r'\b(ceo|co-ceo|chief executive)\b', s):
        return 'c_level', 'ceo'
    if re.search(r'\bvice[\s-]*(president|chair|chairman)\b|\b(vp|evp|svp|avp)\b', s):
        return 'vp', 'vp'
    if re.search(r'\b(chairman|chairwoman|chairperson|chair)\b', s):
        return 'c_level', 'chairman'
    if re.search(r'\b(president|pres)\b', s):
        return 'president', 'president'
    if re.search(r'\bmanaging member\b', s):
        return 'partner', 'partner'
    if re.search(r'\b(agent|realtor|sales|associate|junior|affiliate)\s+partner\b|\bpartner\s+agent\b', s):
        return 'agent', 'agent'
    if re.search(r'\bpartner\b', s) and not re.search(
            r'partner (manager|success|relations|marketing|program|development|with|enablement)|'
            r'(channel|business|trading|referral|strategic|preferred|lending) partner', s):
        return 'partner', 'partner'
    if re.search(r'\b(designated|managing|principal|qualifying|supervising|responsible|employing|chief)\s+'
                 r'(real estate\s+)?broker\b|\bbroker (in charge|of record)\b|\bbroker[-\s]?owner\b', s):
        return 'managing_broker', 'managing_broker'
    if re.search(r'\bprincipal\b', s) and not re.search(
            r'principal (engineer|consultant|designer|scientist|architect|analyst|product|software|developer|'
            r'investigator|account|associate|recruiter)', s):
        return 'principal', 'principal'
    if re.search(r'\bmanaging director\b', s):
        return 'managing_director', 'managing_director'
    if re.search(r'\bchief of staff\b', s):
        return 'other', 'other'
    if re.search(r'\bchief\s+[a-z]+(\s+[a-z]+)?\s+officer\b|\b(coo|cfo|cto|cio|cmo|cro|cdo|cpo|cso|cco|cao|cbo)\b', s):
        acronym = re.search(r'\b(c[a-z]o)\b', s)
        return 'c_level', acronym.group(1) if acronym else 'c_level'
    if re.search(r'\b(director|head of|head)\b', s):
        return 'director', 'director'
    if re.search(r'\bteam lead(er)?\b', s):
        return 'agent', 'team_leader'
    if re.search(r'\b(manager|supervisor)\b', s):
        return 'manager', 'manager'
    if re.search(r'\binvestor\b', s):
        return 'investor', 'investor'
    if re.search(r'\b(agent|realtors?|salesperson|sales associate|broker|wholesaler|buyer rep|listing specialist|'
                 r'licensed|producer)\b|realtor®', s):
        return 'agent', 'agent'
    if re.search(r'\b(advisor|adviser|consultant|mentor|coach|strategist)\b', s):
        return 'advisor', 'advisor'
    if re.search(r'\bmember\b', s):
        return 'member', 'member'
    return 'other', 'other'


def clean_title(raw, company=''):
    """Clean a title. Returns ``(title, company_hint, fixes)``.

    Multi-part titles ("CEO | Founder | Abundance Real Estate", "CEO / Partner @
    Atlas") are split; company names after ' at ' / '@' / ' of ' are removed and
    returned as a hint; noise fragments (license numbers, slogans) are dropped;
    the most senior segment is kept, plus further decision-maker segments while
    the result stays short.
    """
    fixes = []
    title = _text(raw)
    if not title:
        return '', '', fixes
    original = title
    hint = ''
    without_phone = re.sub(r'\s*(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b', '', title).strip(' ,;:-')
    if without_phone != title:
        title = without_phone
        _add(fixes, 'title_phone_removed')
        if not title:
            return '', '', fixes
    without_parens = re.sub(r'\s*\([^)]*\)', '', title).strip()
    if without_parens and without_parens != title:
        title = without_parens
        _add(fixes, 'title_parenthetical_removed')
    lead = re.match(r'(?i)(?:ph\.?d\.?|mba|dr\.?)\s+(?=\S)', title)
    if lead:
        title = title[lead.end():]
        _add(fixes, 'title_credential_removed')
    pieces = [p.strip() for p in re.split(r'\s*[|•·]\s*', title) if p.strip()]
    rebuilt = len(pieces) > 1 or len(title) > 60
    kept_pieces = []
    for piece in pieces:
        at = re.split(r'\s+at\s+|\s*@\s*', piece, maxsplit=1, flags=re.I)
        if len(at) > 1 and TITLE_KEYWORD.search(at[0]):
            if not hint and at[1].strip():
                hint = at[1].strip()
            piece = at[0].strip()
            rebuilt = True
            _add(fixes, 'title_company_removed')
        kept_pieces.append(piece)
    segments = []
    for piece in kept_pieces:
        for seg in re.split(r'\s*(?:/|,|;|\s[-–—]\s|\s\.\s?|\.\s)\s*', piece):
            seg = seg.strip(' .-–—')
            if not seg:
                continue
            seg, seg_hint = _strip_trailing_company(seg, company)
            if seg_hint:
                hint = hint or seg_hint
                rebuilt = True
                _add(fixes, 'title_company_removed')
            if not seg:
                continue
            if not TITLE_KEYWORD.search(seg):
                rebuilt = True
                _add(fixes, 'title_noise_removed')
                if not hint and COMPANY_HINT_WORDS.search(seg) and seg[:1].isupper():
                    hint = seg
                continue
            segments.append(seg)
    if not segments:
        return original[:80].strip(), '', []
    if rebuilt:
        ranked = sorted(range(len(segments)), key=lambda i: (-SENIORITY_RANK[_classify_segment(segments[i])[0]], i))
        chosen = [ranked[0]]
        if classify_title(segments[ranked[0]])['decision_maker']:
            for i in ranked[1:]:
                if not classify_title(segments[i])['decision_maker']:
                    continue
                trial = sorted(chosen + [i])
                if len(' / '.join(segments[j] for j in trial)) <= 50:
                    chosen = trial
        title = ' / '.join(segments[j] for j in sorted(chosen))
        _add(fixes, 'title_split')
    if len(title) > 80:
        title = title[:80].rsplit(' ', 1)[0]
        _add(fixes, 'title_truncated')
    if len(title) > 4 and title.isupper():
        title = _title_case_upper(title)
        _add(fixes, 'title_case_fixed')
    return title, hint.strip(' ,.-'), fixes


TITLE_ACRONYMS = {'CEO', 'COO', 'CFO', 'CTO', 'CIO', 'CMO', 'CRO', 'CCO', 'CPO', 'CSO', 'VP', 'EVP', 'SVP', 'AVP',
                  'LLC', 'LP', 'LLP', 'INC', 'II', 'III', 'IV', 'CRE', 'REIT', 'USA', 'US', 'NYC'}


def _title_case_upper(value):
    """'FOUNDING PARTNER' -> 'Founding Partner', keeping CEO/LLC-style acronyms."""
    small = {'of', 'and', 'the', 'for', 'in', 'at', 'a', 'an', 'to'}
    words = []
    for i, word in enumerate(value.split(' ')):
        bare = re.sub(r'[^A-Z]', '', word)
        if bare in TITLE_ACRONYMS:
            words.append(word)
        elif i and word.lower() in small:
            words.append(word.lower())
        else:
            words.append('-'.join(part[:1].upper() + part[1:].lower() for part in word.split('-')))
    return ' '.join(words)


def _strip_trailing_company(segment, company):
    """'CEO of Strong Will Real Estate' -> ('CEO', 'Strong Will Real Estate')."""
    of_match = re.match(r'(?i)^(.*?\b(?:ceo|co-ceo|founder|co-founder|owner|president|chairman|'
                        r'chief executive officer|principal|managing partner|partner))\s+(?:of|for)\s+(.+)$', segment)
    if of_match and not FUNCTION_AREAS.match(of_match.group(2)):
        rest = re.split(r'\s+(?:&|and)\s+(?=[^&]*\b(?:broker|agent|realtor|founder|ceo|owner|president|partner)\b)',
                        of_match.group(2), maxsplit=1, flags=re.I)[0]
        if COMPANY_HINT_WORDS.search(rest) or _company_tokens(rest) & _company_tokens(company) or rest[:1].isupper():
            hint = re.sub(r'^the\s', 'The ', rest.strip())
            return of_match.group(1).strip(), hint
    words = segment.split()
    if len(words) < 2 or not TOP_TITLE.search(segment):
        return segment, ''
    # index of the last word that is part of the title itself
    last_kw = -1
    for i, word in enumerate(words):
        cleaned = word.strip('.,&+').lower()
        if TITLE_KEYWORD.fullmatch(cleaned) or cleaned in ('&', 'and', '+', 'co-ceo'):
            last_kw = i
    if last_kw < 0 or last_kw == len(words) - 1:
        return segment, ''
    head, rest = ' '.join(words[:last_kw + 1]), ' '.join(words[last_kw + 1:])
    if rest.lower() in ('of', 'at', 'for'):
        return head, ''  # dangling "Founder & CEO of"
    if FUNCTION_AREAS.match(rest):
        return segment, ''  # "CEO North America", "President of Operations"
    bare = re.sub(r'(?i)^(?:of|for|at)\s+', '', rest)
    looks_like_company = bool(_company_tokens(company) & _company_tokens(bare)) or bool(COMPANY_HINT_WORDS.search(bare))
    if looks_like_company or (bare != rest and bare[:1].isupper()):
        return head, bare
    return segment, ''


# ---- companies ------------------------------------------------------------

def _company_tokens(name):
    tokens = re.findall(r'[a-z0-9]+', fold(name).replace("'", '').replace('’', ''))
    return {t for t in tokens if t not in COMPANY_STOPWORDS and len(t) >= 2}


def _brand_parent_hosts(name):
    """Franchise parent hosts whose brand is named in ``name``."""
    text = ' ' + re.sub(r'[^a-z0-9/&\' ]+', ' ', fold(name)) + ' '
    hits = set()
    for host, brands in FRANCHISE_PARENTS.items():
        for brand in brands:
            if re.search(r'(?<![a-z0-9])%s(?![a-z0-9])' % re.escape(brand), text):
                hits.add(host)
    return hits


def _company_key(name):
    return ' '.join(sorted(_company_tokens(LEGAL_SUFFIX_RE.sub('', name))))


def clean_company(raw):
    """Clean a company name. Returns ``(company, fixes, flags)``."""
    fixes, flags = [], []
    company = _text(raw).strip('"\'“”‘’ ')
    if not company:
        return '', fixes, flags
    if company != _text(raw):
        _add(fixes, 'company_quotes_removed')
    if company.endswith(('...', '…')):
        company = company.rstrip('.… ').rstrip(',')
        _add(flags, 'company_truncated')
        tail = re.split(r'\s+(?:and|&|dba|d/b/a)\s+', company, flags=re.I)
        if len(tail) > 1 and LEGAL_SUFFIX_RE.search(tail[0]):
            company = tail[0]
            _add(fixes, 'company_fragment_removed')
    # "CEO at X", "Founder & CEO at X", "CEO of X"
    prefix = re.match(r'^(.{2,60}?)\s+(?:at|@)\s+(.+)$', company, re.I) or re.match(
        r'^((?:co-?)?(?:founder|ceo|president|owner|chief executive officer)[\w\s&,/-]{0,40}?)\s+of\s+(.+)$',
        company, re.I)
    if prefix and TITLE_KEYWORD.search(prefix.group(1)) and _classify_segment(prefix.group(1))[0] != 'other' \
            and not COMPANY_HINT_WORDS.search(prefix.group(1)):
        company = prefix.group(2).strip()
        _add(fixes, 'company_title_prefix_removed')
    company = re.sub(r'\s*\((?:formerly|fka|f/k/a|previously)[^)]*\)', '', company, flags=re.I).strip()
    # "A at B at A..." chains
    parts = [p.strip(' ,.') for p in re.split(r'\s+at\s+', company, flags=re.I) if p.strip(' ,.')]
    if len(parts) > 1:
        company = _pick_company_part(parts)
        _add(fixes, 'company_at_collapsed')
    parts = [p.strip() for p in re.split(r'\s+[/|]\s+|\s*\|\s*', company) if p.strip()]
    if len(parts) > 1:
        company = _pick_company_part(parts)
        _add(fixes, 'company_multi_part')
    dba = re.split(r'\s+(?:dba|d/b/a)\s+', company, maxsplit=1, flags=re.I)
    if len(dba) == 2 and dba[0].strip() and dba[1].strip():
        company = dba[1].strip() if 'company_truncated' not in flags else dba[0].strip()
        _add(fixes, 'company_dba_used')
    location = LOCATION_SUFFIX_RE.search(company)
    if location and len(company[:location.start()].split()) >= 2:
        company = company[:location.start()].strip(' ,')
        _add(fixes, 'company_location_removed')
    company = re.sub(r'(?i)\b(llc)\.$', r'\1', company)
    company = re.sub(r'\s+', ' ', company).strip(' ,;-')
    if re.fullmatch(r'(?i)(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,10}', company):
        _add(flags, 'company_is_domain')
        label = _domain_label(normalize_host(company) or company.lower())
        company = ' '.join(w.capitalize() for w in label.split('-') if w)
        _add(fixes, 'company_from_domain')
    words = re.findall(r"[a-z0-9/']+", fold(company))
    if words and all(w in GENERIC_COMPANY_WORDS for w in words):
        _add(flags, 'company_generic')
    return company, fixes, flags


def _pick_company_part(parts):
    """Choose the operating company among 'Team at Brand' / 'A / B' parts."""
    unique = []
    for part in parts:
        if not any(_company_key(part) == _company_key(u) for u in unique):
            unique.append(part)
    if len(unique) == 1:
        return min((p for p in parts if _company_key(p) == _company_key(unique[0])), key=len)
    for part in unique:
        if not _brand_parent_hosts(part):
            return part
    return unique[0]


# ---------------------------------------------------------------------------
# Public: normalize_record
# ---------------------------------------------------------------------------

def normalize_record(raw):
    """Map alias keys to canonical keys and clean every field.

    Returns a dict with full_name, first_name, last_name, title, company,
    company_website, linkedin_url, email, optional passthrough keys, plus
    ``fixes`` (what was changed), ``flags`` (data problems found while
    cleaning, turned into issues by ``assess``) and ``name_aliases``.
    """
    if not isinstance(raw, dict):
        raise TypeError('lead rows must be dicts')
    by_key = {}
    for key, value in raw.items():
        if key is None:
            continue
        by_key.setdefault(_key(key), value)

    def pick(field):
        for alias in FIELD_ALIASES[field]:
            value = _text(by_key.get(alias))
            if value:
                return value
        return ''

    fixes, flags = [], []
    name_raw = pick('full_name')
    if not name_raw:
        name_raw = ' '.join(p for p in (pick('first_name'), pick('last_name')) if p)
    full_name, first, last, aliases, name_fixes, name_flags = clean_name(name_raw)
    company, company_fixes, company_flags = clean_company(pick('company'))
    title, hint, title_fixes = clean_title(pick('title'), company)
    if hint and (not company or 'company_generic' in company_flags or 'company_is_domain' in company_flags):
        hint_company, hint_fixes, hint_flags = clean_company(hint)
        if hint_company and 'company_generic' not in hint_flags:
            company = hint_company
            company_flags = [f for f in company_flags if f not in ('company_generic', 'company_is_domain')]
            _add(company_fixes, 'company_from_title')
    for group in (name_fixes, title_fixes, company_fixes):
        for code in group:
            _add(fixes, code)
    for group in (name_flags, company_flags):
        for code in group:
            _add(flags, code)

    website_raw = pick('company_website')
    website = normalize_host(website_raw)
    if website_raw and not website:
        _add(flags, 'website_invalid')
    elif website and website != website_raw.lower():
        _add(fixes, 'website_normalized')
    linkedin_raw = pick('linkedin_url')
    linkedin = normalize_linkedin(linkedin_raw)
    if linkedin_raw and not linkedin:
        _add(flags, 'linkedin_invalid')
    elif linkedin and linkedin != linkedin_raw:
        _add(fixes, 'linkedin_canonicalized')
    email_raw = pick('email')
    email = normalize_email_address(email_raw)
    if email_raw and not email:
        _add(flags, 'email_invalid')
    elif email and email != email_raw:
        _add(fixes, 'email_normalized')

    record = {
        'full_name': full_name, 'first_name': first, 'last_name': last, 'title': title, 'company': company,
        'company_website': website, 'linkedin_url': linkedin, 'email': email,
    }
    for key in PASSTHROUGH_KEYS:
        value = pick(key) if key in FIELD_ALIASES else _text(by_key.get(key))
        if value:
            record[key] = value
    record['name_aliases'] = aliases
    record['fixes'] = fixes
    record['flags'] = flags
    return record


# ---------------------------------------------------------------------------
# Validation helpers used by assess / dedupe
# ---------------------------------------------------------------------------

def website_problem(host, company=''):
    """Issue code when ``host`` must not be used as the company website, else ''."""
    if not host:
        return ''
    if is_reserved_domain(host):
        return ''
    if host in PERSONAL_EMAIL_DOMAINS or _domain_label(host) in PERSONAL_EMAIL_LABELS:
        return 'website_personal_domain'
    if _tld(host) in ('edu', 'gov', 'mil') or re.search(r'\.(ac|edu|gov)\.[a-z]{2}$', host):
        return 'website_edu_gov'
    if host in SOCIAL_OR_BUILDER_DOMAINS:
        return 'website_social_or_builder'
    if host in PLACEHOLDER_DOMAINS:
        return 'website_placeholder'
    if host in FRANCHISE_PARENTS and host not in _brand_parent_hosts(company):
        return 'website_franchise_parent'
    return ''


def email_kind(email):
    """'work', 'personal', 'role' or '' for an already normalized address."""
    if not email:
        return ''
    local, domain = email.rsplit('@', 1)
    host = registrable_domain(domain)
    if is_reserved_domain(host):
        pass
    elif host in PERSONAL_EMAIL_DOMAINS or _domain_label(host) in PERSONAL_EMAIL_LABELS or \
            _tld(host) in ('edu', 'gov', 'mil'):
        return 'personal'
    bare = re.sub(r'\d+$', '', local.replace('-', '').replace('_', '').replace('.', ''))
    first_part = re.split(r'[._+-]', local)[0]
    if bare in ROLE_MAILBOXES or local in ROLE_MAILBOXES or first_part in ROLE_MAILBOXES:
        return 'role'
    return 'work'


def website_matches_company(host, company, full_name=''):
    """Token / acronym overlap between the company (or person) name and the domain label."""
    if not host or not company:
        return False
    label = _domain_label(host).replace('-', '')
    if not label:
        return False
    tokens = _company_tokens(company)
    for token in tokens:
        if len(token) >= 3 and token in label:
            return True
    joined = ''.join(sorted(tokens, key=lambda t: fold(company).find(t)))
    if len(label) >= 4 and label in _alnum(company):
        return True
    if len(label) >= 4 and joined and label in joined:
        return True
    words = re.findall(r'[a-z0-9]+', fold(company).replace("'", ''))
    words = [w for w in words if w not in ('llc', 'inc', 'the', 'of', 'and', 'corp', 'ltd', 'co')]
    acronym = ''.join(w[0] for w in words)
    core = ''.join(w[0] for w in words if w not in COMPANY_STOPWORDS)
    for candidate in (acronym, core):
        if len(candidate) >= 3 and (label.startswith(candidate) or (len(label) >= 3 and candidate.startswith(label))):
            return True
    if host in FRANCHISE_PARENTS and host in _brand_parent_hosts(company):
        return True
    if _label_spells_company(label, company):
        return True
    surname = _alnum(full_name.split()[-1]) if full_name and len(full_name.split()) > 1 else ''
    return len(surname) >= 4 and surname in label


LABEL_SUFFIXES = (
    '', 're', 'cre', 'group', 'realty', 'homes', 'properties', 'inc', 'llc', 'usa', 'us', 'team', 'hq', 'co',
)


def _label_spells_company(label, company):
    """Can the domain label be spelled from company words in order, each word
    contributing a prefix (initial, abbreviation or whole word) or nothing when
    it is a stopword? 'sbrep' <- SB Real Estate Partners, 'hlcre' <- HL
    Commercial Real Estate, 'drealcap' <- Datz Real Estate Capital."""
    words = [w for w in re.findall(r'[a-z]+|[0-9]+', fold(company).replace("'", '').replace('’', ''))
             if w not in ('llc', 'inc', 'corp', 'ltd', 'the', 'of', 'and')]
    if not words or len(words) > 8 or len(label) < 2:
        return False
    for suffix in LABEL_SUFFIXES:
        if suffix and not label.endswith(suffix):
            continue
        core = label[:len(label) - len(suffix)] if suffix else label
        if len(core) >= 2 and _spell(core, tuple(words), 0, 0, False, {}):
            return True
    return False


def _spell(label, words, i, j, used_core, memo):
    key = (i, j, used_core)
    if key in memo:
        return memo[key]
    if i == len(label):
        return used_core
    if j == len(words):
        return False
    word = words[j]
    ok = False
    if word in COMPANY_STOPWORDS and _spell(label, words, i, j + 1, used_core, memo):
        ok = True
    if not ok:
        for n in range(min(len(word), len(label) - i), 0, -1):
            if label[i:i + n] == word[:n] and _spell(label, words, i + n, j + 1,
                                                     used_core or word not in COMPANY_STOPWORDS, memo):
                ok = True
                break
    memo[key] = ok
    return ok


def _domains_related(a, b, company=''):
    if a == b:
        return True
    la, lb = _domain_label(a).replace('-', ''), _domain_label(b).replace('-', '')
    if la == lb:
        return True
    if min(len(la), len(lb)) >= 4 and (la in lb or lb in la):
        return True
    return bool(company) and website_matches_company(a, company) and website_matches_company(b, company)


def email_matches_name(email, full_name, aliases=()):
    """Is the local part plausibly built from the person's name?"""
    if not email or not full_name:
        return False
    local = re.sub(r'\d+', '', _alnum(email.split('@', 1)[0]))
    if not local:
        return False
    tokens = [_alnum(t) for t in full_name.split() if t.lower() not in NAME_SUFFIXES]
    tokens = [t for t in tokens if t]
    if not tokens:
        return False
    firsts = {tokens[0]}
    if len(tokens[0]) == 1 and len(tokens) > 2:
        firsts.add(tokens[1])
    for given in list(firsts):
        firsts.update(NICKNAMES.get(given, ()))
    firsts.update(a for a in aliases if a)
    lasts = {t for t in tokens[1:] if len(t) >= 2}
    if len(tokens) > 2:
        lasts.add(''.join(tokens[-2:]))
    firsts = {f for f in firsts if f}
    candidates = set(firsts) | lasts
    initials = ''.join(t[0] for t in tokens)
    candidates.update({initials, tokens[0][0] + tokens[-1][0]} if len(tokens) > 1 else set())
    for f in firsts:
        for l in lasts:
            candidates.update({f + l, l + f, f[0] + l, f + l[0], l + f[0], l[0] + f})
            if len(tokens) > 2:
                candidates.add(f[0] + ''.join(t[0] for t in tokens[1:-1]) + l)
    if local in candidates:
        return True
    if any(len(f) >= 3 and local.startswith(f) for f in firsts) or any(len(f) >= 4 and f in local for f in firsts):
        return True
    if len(tokens) > 1 and len(local) == 3 and local[0] == tokens[0][0] and local[-1] == tokens[-1][0]:
        return True  # first + unknown middle + last initial
    first_initials = {f[0] for f in firsts}
    if len(local) >= 5 and local[0] in first_initials and any(len(l) >= 5 and local[1:] == l[:len(local) - 1] for l in lasts):
        return True  # bdiff <- Bill Diffenderfer
    return any(len(l) >= 4 and l in local for l in lasts) or any(len(l) == 3 and local.endswith(l) for l in lasts)


def _cctld_signal(host, target):
    if not host or is_reserved_domain(host):
        return False
    tld = _tld(host)
    return len(tld) == 2 and tld not in GENERIC_CCTLDS and tld != target.country.lower()


def _country_is_target(country, target):
    value = fold(country).strip()
    if not value:
        return True
    if target.country.upper() == 'US':
        return value in US_COUNTRY_ALIASES
    return value == target.country.lower()


def _org_excluded(company, target):
    text = ' ' + re.sub(r'[^a-z0-9& -]+', ' ', fold(company)) + ' '
    return any(re.search(r'(?<![a-z0-9])%s(?![a-z0-9])' % re.escape(fold(k)), text)
               for k in target.exclude_org_keywords)


# ---------------------------------------------------------------------------
# Public: assess
# ---------------------------------------------------------------------------

def assess(record, target=None):
    """Score one lead and decide its tier.

    Points (max 100):
      * title: CEO / founder / owner / president / chairman 30, partner /
        principal / managing or designated broker / managing director 26,
        other chief officers 22 (vp 12, director 10, manager 6 only matter when
        decision makers are not required)
      * clean company 10 (5 when derived from a bare domain)
      * valid website 10, +5 when the domain matches the company name
      * valid LinkedIn profile 10
      * work email 20, +8 domain matches website, +4 name-based local part,
        +3 provider verified (valid / provider_valid / deliverable / verified)
    Soft issues subtract ``SOFT_PENALTIES`` (3-15 each).

    Hard rejects: missing or non-person name, missing title, non decision maker
    (when required), multiple people, excluded organisation, strong non-US
    signal (ccTLD, explicit country), and no way to reach or verify the person
    (no LinkedIn, no work email, no website).

    Tiers: A >= 80, B 60-79, C < 60, 'reject' when any hard reason applies.
    """
    target = target or Target()
    r = dict(record) if 'full_name' in record and 'fixes' in record else normalize_record(record)
    for key in ('score', 'tier', 'issues', 'reject_reasons', 'decision_maker', 'seniority'):
        r.pop(key, None)
    issues, reasons = [], []
    flags = list(r.get('flags', ()))
    score = 0

    # name ---------------------------------------------------------------
    if not r.get('full_name'):
        _add(reasons, 'missing_full_name')
    for flag in ('name_not_person', 'multiple_people'):
        if flag in flags:
            _add(reasons, flag)
            _add(issues, flag)
    if 'name_truncated' in flags:
        _add(issues, 'name_truncated')
    if 'name_incomplete' in flags and r.get('full_name'):
        _add(issues, 'name_incomplete')

    # title --------------------------------------------------------------
    info = classify_title(r.get('title', ''))
    r['seniority'], r['decision_maker'] = info['seniority'], info['decision_maker']
    if not r.get('title'):
        _add(reasons, 'missing_title')
    elif target.require_decision_maker and not info['decision_maker']:
        _add(reasons, 'not_decision_maker')
    score += info['points']

    # company ------------------------------------------------------------
    company = r.get('company', '')
    if not company:
        _add(issues, 'missing_company')
    else:
        for flag in ('company_generic', 'company_is_domain', 'company_truncated'):
            if flag in flags:
                _add(issues, flag)
        if 'company_generic' in flags:
            pass
        elif 'company_is_domain' in flags:
            score += 5
        else:
            score += 10
        if _org_excluded(company, target):
            _add(reasons, 'non_target_org')
            _add(issues, 'non_target_org')

    # website ------------------------------------------------------------
    website = r.get('company_website', '')
    problem = website_problem(website, company)
    if problem:
        _add(issues, problem)
        r['rejected_website'] = website
        website = ''
    if 'website_invalid' in flags:
        _add(issues, 'website_invalid')

    # email --------------------------------------------------------------
    email = r.get('email', '')
    if 'email_invalid' in flags:
        _add(issues, 'email_invalid')
    verification = fold(r.get('email_verification', '')).strip().replace(' ', '_').replace('-', '_')
    if email and verification in ('invalid', 'undeliverable', 'bounced', 'bounce', 'rejected', 'provider_invalid'):
        _add(issues, 'email_invalid')
        r['rejected_email'] = email
        r['email'] = email = ''
    kind = email_kind(email)
    email_domain = registrable_domain(email.rsplit('@', 1)[1]) if email else ''
    franchise_email = kind == 'work' and email_domain in FRANCHISE_PARENTS and \
        email_domain not in _brand_parent_hosts(company)
    if not website and kind == 'work' and not franchise_email and not website_problem(email_domain, company) \
            and website_matches_company(email_domain, company, r.get('full_name', '')):
        website = email_domain
        _add(r.setdefault('fixes', []), 'website_from_email')
    r['company_website'] = website
    r['email_kind'] = kind
    r['website_company_match'] = website_matches_company(website, company, r.get('full_name', ''))
    if website:
        score += 10
        if r['website_company_match']:
            score += 5
        elif company:
            _add(issues, 'website_company_mismatch')
    else:
        _add(issues, 'missing_website')

    r['email_name_match'] = email_matches_name(email, r.get('full_name', ''), r.get('name_aliases', ()))
    if kind == 'personal':
        _add(issues, 'personal_email')
    elif kind == 'role':
        _add(issues, 'role_email')
    elif not email:
        _add(issues, 'missing_email')
    if kind == 'work' or (kind == 'personal' and target.allow_personal_email):
        score += 20
        if franchise_email:
            _add(issues, 'email_franchise_domain')
        elif website and _domains_related(email_domain, website, company):
            score += 8
        elif website:
            _add(issues, 'email_domain_mismatch')
        if r['email_name_match']:
            score += 4
        if verification in ('valid', 'provider_valid', 'deliverable', 'verified', 'ok'):
            score += 3
    if email and kind != 'role' and not r['email_name_match']:
        _add(issues, 'email_not_name_based')
    if email and verification in ('accept_all', 'catch_all', 'catchall', 'unknown', 'risky', 'unverifiable'):
        _add(issues, 'email_unverified_risky')

    # linkedin -----------------------------------------------------------
    if r.get('linkedin_url'):
        score += 10
    else:
        _add(issues, 'missing_linkedin')
        if 'linkedin_invalid' in flags:
            _add(issues, 'linkedin_invalid')

    # geography / industry -----------------------------------------------
    strong_geo = any(_cctld_signal(h, target) for h in (website, r.get('rejected_website', ''), email_domain)) \
        or not _country_is_target(r.get('country', ''), target)
    location = r.get('location', '')
    if location and not strong_geo:
        last_part = location.split(',')[-1].strip()
        if NON_US_TEXT.fullmatch(last_part):
            strong_geo = True
    soft_geo = bool(NON_US_TEXT.search(' '.join((r.get('title', ''), company, location))))
    if strong_geo:
        _add(reasons, 'non_us_signal')
        _add(issues, 'non_us_signal')
    elif soft_geo:
        _add(issues, 'non_us_signal')
    haystack = ' '.join((fold(company), fold(r.get('title', '')), _domain_label(website), _domain_label(email_domain)))
    if target.industry_keywords and company and not any(
            re.search(r'(?<![a-z])%s(?![a-z])' % re.escape(fold(k)), haystack) or
            (len(k) > 4 and fold(k).replace(' ', '') in haystack) for k in target.industry_keywords):
        _add(issues, 'industry_unclear')

    # reachability -------------------------------------------------------
    if not r.get('linkedin_url') and kind != 'work' and not website:
        _add(reasons, 'no_contact_path')

    for code in issues:
        if code == 'non_us_signal' and strong_geo:
            continue
        score -= SOFT_PENALTIES.get(code, 0)
    score = max(0, min(100, score))
    r['score'] = score
    r['issues'] = issues
    r['reject_reasons'] = reasons
    r['tier'] = 'reject' if reasons else ('A' if score >= 80 else 'B' if score >= 60 else 'C')
    return r


# ---------------------------------------------------------------------------
# Public: dedupe
# ---------------------------------------------------------------------------

def _record_keys(r):
    keys = []
    slug = linkedin_slug(r.get('linkedin_url', ''))
    if slug:
        keys.append('li:' + slug)
    if r.get('email'):
        keys.append('em:' + r['email'])
    if r.get('full_name') and r.get('company_website'):
        keys.append('nw:%s|%s' % (' '.join(re.findall(r'[a-z0-9]+', fold(r['full_name']))), r['company_website']))
    return keys


def dedupe(records):
    """Merge duplicate leads.

    Records sharing a LinkedIn slug, an email, or (accent-folded name, website)
    are merged (transitively). Within a group members are ranked by score
    (``assess`` is run when no score is present) and completeness; each field
    takes the best member's value, preferring valid websites and work emails.
    ``fixes``/``flags``/``name_aliases`` are unioned, ``source`` values joined.
    Output order follows each group's first appearance.
    """
    records = list(records)
    parent = list(range(len(records)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner = {}
    for i, r in enumerate(records):
        for key in _record_keys(r):
            if key in owner:
                a, b = find(owner[key]), find(i)
                if a != b:
                    parent[max(a, b)] = min(a, b)
            else:
                owner[key] = i
    groups = {}
    for i in range(len(records)):
        groups.setdefault(find(i), []).append(i)
    merged = []
    for root in sorted(groups):
        members = groups[root]
        if len(members) == 1:
            merged.append(records[members[0]])
            continue
        merged.append(_merge([records[i] for i in members], members))
    return merged


def _merge(group, indexes):
    def rank(item):
        pos, r = item
        score = r['score'] if isinstance(r.get('score'), int) else assess(r)['score']
        filled = sum(1 for k in ('full_name', 'title', 'company', 'company_website', 'linkedin_url', 'email')
                     if r.get(k))
        return (-score, -filled, pos)

    ordered = [r for _, r in sorted(zip(indexes, group), key=rank)]
    out = dict(ordered[0])
    for field in ('full_name', 'first_name', 'last_name', 'title', 'company', 'linkedin_url') + PASSTHROUGH_KEYS:
        if field == 'source':
            continue
        for r in ordered:
            if r.get(field):
                out[field] = r[field]
                break
    websites = [r.get('company_website', '') for r in ordered if r.get('company_website')]
    good = [w for w in websites if not website_problem(w, out.get('company', ''))]
    out['company_website'] = (good or websites or [''])[0]
    emails = [r.get('email', '') for r in ordered if r.get('email')]
    order = {'work': 0, 'role': 1, 'personal': 2}
    emails.sort(key=lambda e: (order.get(email_kind(e), 3), not email_matches_name(e, out.get('full_name', ''))))
    out['email'] = emails[0] if emails else ''
    if out['email']:
        donor = next(r for r in ordered if r.get('email') == out['email'])
        for key in ('email_source', 'email_confidence', 'email_verification', 'email_evidence_url'):
            if donor.get(key):
                out[key] = donor[key]
            else:
                out.pop(key, None)
    sources = []
    for r in ordered:
        for s in re.split(r'\s*;\s*', r.get('source', '') or ''):
            if s and s not in sources:
                sources.append(s)
    if sources:
        out['source'] = '; '.join(sources)
    for key in ('fixes', 'flags', 'name_aliases'):
        combined = []
        for r in ordered:
            for code in r.get(key, ()):
                _add(combined, code)
        out[key] = combined
    # company flags follow the record the company value came from
    company_donor = next((r for r in ordered if r.get('company') == out.get('company')), ordered[0])
    for flag in ('company_is_domain', 'company_generic', 'company_truncated'):
        if flag not in company_donor.get('flags', ()) and flag in out['flags']:
            out['flags'].remove(flag)
    _add(out['fixes'], 'duplicate_merged')
    out['merged_count'] = sum(r.get('merged_count', 1) for r in group)
    for key in ('score', 'tier', 'issues', 'reject_reasons'):
        out.pop(key, None)
    return out


# ---------------------------------------------------------------------------
# Public: clean_records / write_outputs / clean_csv
# ---------------------------------------------------------------------------

def _sort_key(r):
    return (TIER_ORDER.get(r['tier'], 9), -r['score'], fold(r.get('full_name', '')))


def output_row(r, mode='clean', target=None):
    """Values for ``HEADERS``. Clean mode blanks anything that failed validation."""
    target = target or Target()
    email = r.get('email', '')
    company = r.get('company', '')
    if mode == 'clean':
        allowed = r.get('email_kind') == 'work' or (r.get('email_kind') == 'personal' and target.allow_personal_email)
        email = email if allowed else ''
        if 'company_generic' in r.get('issues', ()):
            company = ''
    return [r.get('full_name', ''), r.get('title', ''), company, r.get('company_website', ''),
            r.get('linkedin_url', ''), email]


def clean_records(raws, target=None):
    """Normalize, dedupe, assess and split rows into clean / review / rejected."""
    target = target or Target()
    if target.min_tier not in ('A', 'B', 'C'):
        raise ValueError('min_tier must be A, B or C')
    raws = list(raws)
    normalized = [normalize_record(raw) for raw in raws]
    merged = dedupe(normalized)
    assessed = [assess(r, target) for r in merged]
    limit = TIER_ORDER[target.min_tier]
    clean = sorted((r for r in assessed if r['tier'] != 'reject' and TIER_ORDER[r['tier']] <= limit), key=_sort_key)
    review = sorted((r for r in assessed if r['tier'] != 'reject' and TIER_ORDER[r['tier']] > limit), key=_sort_key)
    rejected = [r for r in assessed if r['tier'] == 'reject']
    clean_rows = [output_row(r, 'clean', target) for r in clean]
    issue_counts = Counter(code for r in assessed for code in r['issues'])
    summary = {
        'input_rows': len(raws),
        'duplicates_merged': len(normalized) - len(merged),
        'unique_leads': len(merged),
        'clean': len(clean),
        'review': len(review),
        'rejected': len(rejected),
        'with_work_email': sum(1 for row in clean_rows if row[5]),
        'with_linkedin': sum(1 for row in clean_rows if row[4]),
        'with_website': sum(1 for row in clean_rows if row[3]),
        'by_tier': {tier: sum(1 for r in assessed if r['tier'] == tier) for tier in ('A', 'B', 'C', 'reject')},
        'top_issues': dict(issue_counts.most_common(15)),
        'reject_reasons': dict(Counter(code for r in rejected for code in r['reject_reasons']).most_common()),
        'min_tier': target.min_tier,
    }
    return {'clean': clean, 'review': review, 'rejected': rejected, 'summary': summary, 'target': target}


FORMULA_TRIGGERS = ('=', '+', '-', '@', '＝', '＋', '－', '＠')


def escape_cell(value):
    """Prefix ``'`` so spreadsheets do not evaluate a cell as a formula."""
    value = '' if value is None else str(value)
    if not value:
        return value
    if value[0] in ('\t', '\r') or value.lstrip().startswith(FORMULA_TRIGGERS):
        return "'" + value
    return value


def _write_private(path, writer_fn):
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix='.tmp-', dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as handle:
            writer_fn(handle)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _write_csv(path, headers, rows):
    def body(handle):
        writer = csv.writer(handle, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(headers)
        for row in rows:
            writer.writerow([escape_cell(v) for v in row])
    _write_private(path, body)


def write_outputs(result, output_dir):
    """Write the four output files (UTF-8, no BOM, 0600 files in a 0700 dir)."""
    target = result.get('target') or Target()
    output_dir = os.path.abspath(os.fspath(output_dir))
    if not os.path.isdir(output_dir):
        os.makedirs(output_dir, mode=0o700, exist_ok=True)
        os.chmod(output_dir, 0o700)
    paths = {
        'clean': os.path.join(output_dir, 'leads-clean.csv'),
        'review': os.path.join(output_dir, 'leads-review.csv'),
        'rejected': os.path.join(output_dir, 'leads-rejected.csv'),
        'summary': os.path.join(output_dir, 'quality-summary.json'),
    }
    _write_csv(paths['clean'], HEADERS, [output_row(r, 'clean', target) for r in result['clean']])
    _write_csv(paths['review'], REVIEW_HEADERS, [
        output_row(r, 'review', target) + [r['tier'], r['score'], '; '.join(r['issues']), r.get('email_source', '')]
        for r in result['review']])
    _write_csv(paths['rejected'], REJECTED_HEADERS, [
        output_row(r, 'rejected', target) + ['; '.join(r['reject_reasons']), '; '.join(r['issues'])]
        for r in result['rejected']])
    _write_private(paths['summary'], lambda h: h.write(json.dumps(result['summary'], indent=2, sort_keys=True) + '\n'))
    return {'paths': paths, 'summary': result['summary']}


def read_rows(input_path):
    """Read CSV (utf-8-sig) or JSON / JSONL lists of dicts."""
    input_path = os.fspath(input_path)
    with open(input_path, encoding='utf-8-sig', newline='') as handle:
        text = handle.read()
    stripped = text.lstrip()
    lower = input_path.lower()
    if lower.endswith(('.jsonl', '.ndjson')):
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    if lower.endswith('.json') or stripped.startswith(('[', '{')):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return [json.loads(line) for line in text.splitlines() if line.strip()]
        if isinstance(data, dict):
            for key in ('leads', 'records', 'rows', 'data'):
                if isinstance(data.get(key), list):
                    return data[key]
            return [data]
        return data
    return list(csv.DictReader(io.StringIO(text)))


def clean_csv(input_path, output_dir, target=None):
    """Read ``input_path``, clean it, write outputs; returns summary + paths."""
    result = clean_records(read_rows(input_path), target or Target())
    return write_outputs(result, output_dir)
