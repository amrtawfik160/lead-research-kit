"""Free public licensing / filing records -> normalized decision-maker prospects.

Sources (field names and type strings confirmed against the live APIs, Oct 2026):

- ``tx-trec``  Texas Real Estate Commission licensees, Texas Open Data Portal (Socrata)
  https://data.texas.gov/d/s7ft-44qi . 'Broker Company' rows carry the firm (full_name) and link
  to its designated broker via related_license_type='Broker Individual' / related_license_full_name.
  Dates are MM/DD/YYYY text; the ``updated`` column is a real timestamp used for deltas
  (``:updated_at`` is reset when the portal reloads the dataset, so it is useless for deltas).
- ``ny-dos``   NY Department of State "Active Real Estate Salespersons and Brokers"
  https://data.ny.gov/d/yg7h-zjbf . Types are upper case ('CORPORATE BROKER', 'LIMITED LIABILITY
  BROKER', 'PARTNERSHIP BROKER', 'TRADENAME BROKER', 'INDIVIDUAL BROKER', 'REAL ESTATE PRINCIPAL
  OFFICE', ...). 'REAL ESTATE PRINCIPAL OFFICE' rows have no license_holder_name (office only), so
  people come from the broker rows; license_holder_name is 'LAST FIRST M' without a comma.
- ``co-dora``  Colorado DORA "Licensed Real Estate Professionals in Colorado" (Public Domain)
  https://data.colorado.gov/d/4zse-6bnw . Responsible Broker rows have no entityname; firms are
  separate 'Real Estate Company - Employing/Independent' rows. They are joined heuristically.
- ``sec-form-d`` SEC EDGAR Form D (daily form index + primary_doc.xml), real-estate industry groups.
- ``fl-dbpr-file`` a Florida DBPR real-estate extract CSV the user downloaded (no automated fetch).

Excluded on purpose: California DRE (lists not for mass mailing), Washington DOL (commercial use
prohibited).  Nothing here sends email or writes to any service; it only reads public records.
"""
from __future__ import annotations

import csv
import http.client
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from datetime import date, datetime, timedelta, timezone

from .policy import US_STATES

try:  # the quality engine decides what counts as a decision-maker title
    from .quality import classify_title as _classify_title
except Exception:  # noqa: BLE001
    _classify_title = None

__all__ = ['ALLOWED_ORIGINS', 'SOURCES', 'RecordsError', 'RecordsGetter', 'socrata_fetch', 'fetch_source',
           'load_local_file', 'person_name', 'company_name', 'parse_tx_rows', 'parse_ny_rows', 'parse_co_rows',
           'parse_sec_index', 'parse_form_d', 'parse_fl_rows', 'sec_filing_urls', 'dedupe']

ALLOWED_ORIGINS = frozenset({'https://data.texas.gov', 'https://data.ny.gov', 'https://data.colorado.gov',
                             'https://data.ct.gov', 'https://www.sec.gov'})
SOCRATA_ORIGINS = ALLOWED_ORIGINS - {'https://www.sec.gov'}
MAX_RESPONSE_BYTES = 20 * 1024 * 1024
SPACING = {'socrata': 0.5, 'sec': 0.25}  # seconds between requests to one origin (SEC limit is 10/s)
MAX_SEC_DAYS = 5
DEFAULT_MAX_ROWS = 60000
DEFAULT_MAX_FILINGS = 400

TX_URL = 'https://data.texas.gov/resource/s7ft-44qi.json'
NY_URL = 'https://data.ny.gov/resource/yg7h-zjbf.json'
CO_URL = 'https://data.colorado.gov/resource/4zse-6bnw.json'
CT_URL = 'https://data.ct.gov/resource/6tja-6vdt.json'
SEC_INDEX = 'https://www.sec.gov/Archives/edgar/daily-index/{y}/QTR{q}/form.{ymd}.idx'
FL_URL = 'https://www2.myfloridalicense.com/real-estate-commission/public-records/'

NY_BROKER_TYPES = {'CORPORATE BROKER': 'Principal Broker', 'LIMITED LIABILITY BROKER': 'Principal Broker',
                   'PARTNERSHIP BROKER': 'Principal Broker', 'TRADENAME BROKER': 'Broker/Owner',
                   'INDIVIDUAL BROKER': 'Broker/Owner'}
CO_RB_TYPES = ('Employing Level Real Estate Broker - Responsible Broker',
               'Independent Level Real Estate Broker - Responsible Broker')
CO_COMPANY_TYPES = ('Real Estate Company - Employing', 'Real Estate Company - Independent')
# Form D industryGroupType values in the "Real Estate" family (Form D XML technical spec; 'Residential'
# confirmed in a live filing).
SEC_REAL_ESTATE_GROUPS = frozenset({'Commercial', 'Construction', 'REITS and Finance', 'Residential',
                                    'Other Real Estate'})

SOURCES = {
    'tx-trec': {
        'description': 'Texas Real Estate Commission licensees (Texas Open Data Portal, updated daily)',
        'url': 'https://data.texas.gov/d/s7ft-44qi',
        'terms_note': 'Public TREC license data on data.texas.gov (portal terms of use; no dataset license '
                      'field). Licensing data, not consent: email outreach must follow CAN-SPAM.',
        'decision_maker_rule': "Active 'Broker Company' rows joined to their related 'Broker Individual' "
                               "(title 'Designated Broker'). Broker individuals without a firm are skipped.",
        'delta': "since -> updated >= since (the row-level 'updated' timestamp)",
    },
    'ny-dos': {
        'description': 'NY Department of State active real estate brokers (data.ny.gov)',
        'url': 'https://data.ny.gov/d/yg7h-zjbf',
        'terms_note': 'Open NY data (data.ny.gov terms of use). Licensing data, not consent: email outreach '
                      'must follow CAN-SPAM.',
        'decision_maker_rule': "Corporate/LLC/Partnership broker rows: license_holder_name is the entity's "
                               "representative broker (title 'Principal Broker'). Individual/Tradename broker "
                               "rows are sole-proprietor licenses (associate brokers and salespersons are "
                               "separate types), so the holder owns the business (title 'Broker/Owner'). "
                               "Principal Office rows carry no person and are not used.",
        'delta': "since -> license_expiration_date >= since + 2 years (2-year NY terms: issued or renewed "
                 "on/after since); the dataset has no per-row change timestamp",
    },
    'co-dora': {
        'description': 'Colorado DORA licensed real estate professionals (data.colorado.gov)',
        'url': 'https://data.colorado.gov/d/4zse-6bnw',
        'terms_note': "Dataset license: Public Domain. Licensing data, not consent: email outreach must "
                      "follow CAN-SPAM.",
        'decision_maker_rule': "Active Employing/Independent 'Responsible Broker' rows (title 'Responsible "
                               "Broker'). Company is joined from a 'Real Estate Company' row with the same zip "
                               "and first-issue date and an adjacent license number; else left blank.",
        'delta': 'since -> licensefirstissuedate in each day since..today (text MM/DD/YYYY, max 92 days)',
    },
    'sec-form-d': {
        'description': 'SEC EDGAR Form D filings by US real-estate issuers (daily form index)',
        'url': 'https://www.sec.gov/edgar/search/',
        'terms_note': 'EDGAR is public; SEC fair-access policy requires a declared User-Agent with contact '
                      'email (SEC_USER_AGENT) and <= 10 requests/second (this client uses 4/s).',
        'decision_maker_rule': "Form D (not D/A unless asked) with a real-estate industryGroupType and a US "
                               "issuer; related persons with relationship 'Executive Officer' (title from "
                               "relationshipClarification when it names a decision-maker role) or "
                               "'Promoter' whose clarification names a decision-maker role.",
        'delta': 'since -> daily index files since..today (default yesterday..today, max 5 days)',
    },
    'fl-dbpr-file': {
        'description': 'Florida DBPR real estate licensee extract CSV downloaded by the user',
        'url': FL_URL,
        'terms_note': 'Florida public records (Ch. 119 F.S.). Download the CSV yourself; it is behind '
                      'Cloudflare and is never fetched automatically. Email outreach must follow CAN-SPAM.',
        'decision_maker_rule': "Current/active 'Broker' rank (not sales associate / broker sales associate): "
                               "with an employer -> 'Qualifying Broker' of that firm; self-proprietor or DBA "
                               "-> 'Broker/Owner'. Corporation rows become companies only.",
        'delta': 'none (local file)',
    },
}
EXCLUDED_SOURCES = {
    'ca-dre': 'California DRE: lists may not be used for mass mailing.',
    'wa-dol': 'Washington DOL: commercial use of licensee lists is prohibited.',
}


class RecordsError(RuntimeError):
    """Public-safe error code. Never carries a URL, token, header or server body."""


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _origin(url):
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        return ''
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or port not in (None, 443):
        return ''
    return 'https://' + parts.hostname.lower()


class _SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 3

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        if _origin(target) != _origin(req.full_url):
            raise RecordsError('redirect_blocked')
        return super().redirect_request(req, fp, code, msg, headers, target)


def _ssl_context():
    from .tls import trusted_context
    return trusted_context()


def build_opener():
    return urllib.request.build_opener(_SameOriginRedirect(), urllib.request.HTTPSHandler(context=_ssl_context()))


class RecordsGetter:
    """GET-only client for the fixed public-record origins.

    ``opener`` needs ``.open(request, timeout=...)`` returning a response with ``.status`` (or
    ``.getcode()``), ``.headers``, ``.read(n)`` and optionally ``.geturl()``. ``clock``/``sleep`` are
    injectable so tests never wait.
    """

    def __init__(self, env=None, *, opener=None, clock=time.monotonic, sleep=time.sleep, timeout=60.0,
                 max_bytes=MAX_RESPONSE_BYTES):
        env = os.environ if env is None else env
        self._token = (env.get('SOCRATA_APP_TOKEN') or '').strip()
        self._sec_agent = (env.get('SEC_USER_AGENT') or '').strip()
        self._opener = opener or build_opener()
        self._clock, self._sleep = clock, sleep
        self.timeout, self.max_bytes = float(timeout), int(max_bytes)
        self._last = {}
        self.requests = 0

    def _check(self, url):
        if not isinstance(url, str) or len(url) > 4096 or any(ord(c) < 33 for c in url):
            raise RecordsError('invalid_url')
        origin = _origin(url)
        if origin not in ALLOWED_ORIGINS:
            raise RecordsError('origin_not_allowed')
        return origin

    def headers_for(self, origin):
        headers = {'Accept-Encoding': 'gzip', 'User-Agent': 'lead-research-kit public-records reader'}
        if origin == 'https://www.sec.gov':
            if not self._sec_agent or '@' not in self._sec_agent or len(self._sec_agent) > 200 \
                    or any(ord(c) < 32 for c in self._sec_agent):
                raise RecordsError('sec_user_agent_required')
            headers['User-Agent'] = self._sec_agent
        elif self._token:
            headers['X-App-Token'] = self._token
        return headers

    def _space(self, origin):
        delay = SPACING['sec' if origin == 'https://www.sec.gov' else 'socrata']
        last = self._last.get(origin)
        if last is not None:
            wait = last + delay - self._clock()
            if wait > 0:
                self._sleep(wait)
        self._last[origin] = self._clock()

    def get(self, url, accept='application/json'):
        """Return ``(status, body_bytes)`` for 2xx/404, raise RecordsError otherwise."""
        origin = self._check(url)
        headers = self.headers_for(origin)
        headers['Accept'] = accept
        request = urllib.request.Request(url, headers=headers, method='GET')
        self._space(origin)
        self.requests += 1
        try:
            response = self._opener.open(request, timeout=self.timeout)
        except RecordsError:
            raise
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                exc.close()
            except Exception:  # noqa: BLE001 - closing a failed response must not leak details
                pass
            if status == 404:
                return 404, b''
            raise RecordsError('http_%d' % status) from None
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException):
            raise RecordsError('network_error') from None
        try:
            final = response.geturl() if hasattr(response, 'geturl') else url
            if final and _origin(final) != origin:
                raise RecordsError('redirect_blocked')
            status = getattr(response, 'status', None) or response.getcode()
            if status == 404:
                return 404, b''
            if not 200 <= int(status) < 300:
                raise RecordsError('http_%d' % int(status))
            rheaders = response.headers
            length = rheaders.get('Content-Length') if rheaders is not None else None
            if length and length.isdigit() and int(length) > self.max_bytes:
                raise RecordsError('response_too_large')
            body = _read_capped(response, self.max_bytes)
            encoding = (rheaders.get('Content-Encoding') or '').lower() if rheaders is not None else ''
            if encoding == 'gzip' or body[:2] == b'\x1f\x8b':
                body = _gunzip(body, self.max_bytes)
            return int(status), body
        except RecordsError:
            raise
        except (OSError, ValueError, http.client.HTTPException):
            raise RecordsError('network_error') from None
        finally:
            try:
                response.close()
            except Exception:  # noqa: BLE001
                pass

    def get_json(self, url):
        status, body = self.get(url)
        if status == 404:
            raise RecordsError('not_found')
        try:
            return json.loads(body.decode('utf-8'))
        except (UnicodeError, ValueError):
            raise RecordsError('invalid_json') from None

    def get_text(self, url, accept='text/plain, application/xml;q=0.9, */*;q=0.1'):
        """Text body, or None for 404 (missing daily index / document)."""
        status, body = self.get(url, accept=accept)
        if status == 404:
            return None
        return body.decode('utf-8', errors='replace')


def _read_capped(response, limit):
    chunks, total = [], 0
    while True:
        chunk = response.read(min(1 << 16, limit + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            raise RecordsError('response_too_large')
    return b''.join(chunks)


def _gunzip(body, limit):
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        out = decoder.decompress(body, limit + 1)
    except zlib.error:
        raise RecordsError('invalid_gzip') from None
    if len(out) > limit or decoder.unconsumed_tail:
        raise RecordsError('response_too_large')
    return out


def socrata_fetch(dataset_url, where=None, select=None, order=':id', page_size=5000, max_rows=None, *, getter=None):
    """Yield rows from a Socrata ``/resource/<id>.json`` endpoint with stable $order paging."""
    origin = _origin(dataset_url)
    if origin not in SOCRATA_ORIGINS or not re.fullmatch(r'/resource/[a-z0-9]{4}-[a-z0-9]{4}\.json',
                                                         urllib.parse.urlsplit(dataset_url).path) \
            or urllib.parse.urlsplit(dataset_url).query:
        raise RecordsError('invalid_dataset_url')
    if type(page_size) is not int or not 1 <= page_size <= 50000:
        raise RecordsError('invalid_page_size')
    getter = getter or RecordsGetter()
    offset, seen = 0, 0
    while max_rows is None or seen < max_rows:
        limit = page_size if max_rows is None else min(page_size, max_rows - seen)
        params = {'$limit': str(limit), '$offset': str(offset), '$order': order or ':id'}
        if where:
            params['$where'] = where
        if select:
            params['$select'] = select
        rows = getter.get_json(dataset_url + '?' + urllib.parse.urlencode(params))
        if not isinstance(rows, list):
            raise RecordsError('invalid_response')
        for row in rows[:limit]:
            if isinstance(row, dict):
                yield row
                seen += 1
        if len(rows) < limit:
            break
        offset += len(rows)


# ---------------------------------------------------------------------------
# Name / company normalization
# ---------------------------------------------------------------------------

_SUFFIXES = {'JR': 'Jr', 'SR': 'Sr', 'II': 'II', 'III': 'III', 'IV': 'IV'}
_UPPER_COMPANY = {'LLC', 'INC', 'LLP', 'LP', 'PLLC', 'PC', 'PA', 'LTD', 'DBA', 'USA', 'US', 'NY', 'NYC', 'TX',
                  'FL', 'II', 'III', 'IV', 'RE/MAX', 'REIT', 'LLLP', 'CRE', 'HOA', 'PLC', 'DC'}
_LOWER_COMPANY = {'of', 'and', 'the', 'at', 'in', 'by', 'for', 'on', 'a', 'an', 'to'}
_NOT_INITIALISMS = {'DR', 'ST', 'MT', 'FT', 'JR', 'SR', 'MR', 'MS', 'CO'}


def _cap(piece):
    if not piece:
        return piece
    low = piece.lower()
    if low.startswith('mc') and len(piece) > 3 and piece[2:].isalpha():
        return 'Mc' + piece[2].upper() + piece[3:].lower()
    return piece[0].upper() + piece[1:].lower()


def _case_word(word):
    if re.fullmatch(r'(?:[A-Za-z]\.){2,}[A-Za-z]?\.?,?', word):
        return word.upper()  # initials: J.B. / L.L.C.
    out = []
    for part in word.split('-'):
        pieces = re.split(r"(['’])", part)
        cased = []
        for i, p in enumerate(pieces):
            if p in ("'", '’'):
                cased.append(p)
            elif i >= 2 and p.lower() in ('s', 't', 'll', 're', 'd', 've'):
                cased.append(p.lower())  # possessive / contraction: SOTHEBY'S -> Sotheby's
            else:
                cased.append(_cap(p))
        out.append(''.join(cased))
    return '-'.join(out)


def _needs_case(text):
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and (all(c.isupper() for c in letters) or all(c.islower() for c in letters))


def _clean(value, max_len=200):
    if value is None:
        return ''
    text = ''.join(' ' if ord(c) < 32 or 127 <= ord(c) < 160 else c for c in str(value))
    text = ' '.join(text.split())
    return text[:max_len].strip()


def _is_suffix(token):
    return token.upper().strip('.') in _SUFFIXES


def _pull_suffixes(tokens):
    core = [t for t in tokens if not _is_suffix(t)]
    if len(core) < 1 or len(core) == len(tokens):
        return tokens, []
    return core, [t for t in tokens if _is_suffix(t)]


def person_name(raw, order='auto'):
    """'GARZA, JEFFREY D' / 'JEFFREY D GARZA' -> 'Jeffrey D Garza'.

    ``order='last_first'`` reads comma-less input as 'LAST FIRST M' (NY DOS holder names). Mixed-case
    tokens keep their casing; generational suffixes move to the end ('SMITH, JOHN JR' -> 'John Smith Jr').
    Single letters (including 'V') are initials, never suffixes.
    """
    text = _clean(raw, 120).strip(' ,.')
    if not text:
        return ''
    if ',' in text:
        last, rest = text.split(',', 1)
        last_tokens, last_suffix = _pull_suffixes(last.split())
        rest_tokens, rest_suffix = _pull_suffixes(rest.replace(',', ' ').split())
        if not rest_tokens and rest_suffix:  # 'JOHN SMITH, JR'
            tokens, suffixes = last_tokens, rest_suffix
        else:
            tokens, suffixes = rest_tokens + last_tokens, last_suffix + rest_suffix
    else:
        tokens, suffixes = _pull_suffixes(text.split())
        if order == 'last_first' and len(tokens) >= 2:
            tokens = tokens[1:] + tokens[:1]
    out = []
    for token in tokens:
        out.append(_case_word(token) if _needs_case(token) else token)
    out.extend(_SUFFIXES[t.upper().strip('.')] for t in suffixes)
    return ' '.join(out)


def company_name(raw):
    """Sensible title case for ALL CAPS firm names; legal suffixes stay upper ('Omada Real Estate LLC')."""
    text = _clean(raw, 200).strip(' ,')
    if not text:
        return ''
    if not _needs_case(text):
        return re.sub(r'(?i)\b(llc|llp|pllc|lp)\b', lambda m: m.group(1).upper(), text)
    words = text.split(' ')
    out = []
    for i, word in enumerate(words):
        core = word.strip(',.()').upper()
        if core in _UPPER_COMPANY or core.replace('.', '') in {'LLC', 'INC', 'LP', 'LLP'}:
            out.append(word.upper())
        elif i and core.lower() in _LOWER_COMPANY:
            out.append(word.lower())
        elif re.search(r'\d', core):
            out.append(word.lower())
        elif core.isalpha() and len(core) <= 3 and not re.search(r'[AEIOUY]', core) \
                and core not in _NOT_INITIALISMS:
            out.append(word.upper())  # initialisms: 'NNN', 'JMR'
        elif '&' in word and len(core) <= 5:
            out.append(word.upper())  # 'A&M'
        else:
            out.append(_case_word(word))
    return ' '.join(out)


def _iso_from_mdy(value):
    text = _clean(value, 40)
    m = re.fullmatch(r'(\d{1,2})/(\d{1,2})/(\d{4})', text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(1)), int(m.group(2))).isoformat()
        except ValueError:
            return ''
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return ''
    m = re.fullmatch(r'(\d{4})(\d{2})(\d{2})', text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return ''
    return ''


def _not_expired(value, today):
    iso = _iso_from_mdy(value)
    return not iso or iso >= today.isoformat()


def _state(value):
    code = _clean(value, 40).upper()
    if code in US_STATES:
        return code
    for c, name in US_STATES.items():
        if name.upper() == code:
            return c
    return ''


def _location(city, state):
    city = company_name(city) if _needs_case(_clean(city)) else _clean(city)
    return ', '.join(p for p in (city, state) if p)


def _prospect(source, full_name, title, company, *, city='', state='', county='', license_number='',
              source_url='', record_date=''):
    return {'full_name': full_name, 'title': title, 'company': company, 'company_website': '',
            'location': _location(city, state) if city else (county + ' County, ' + state if county and state else state),
            'state': state, 'county': county, 'license_number': license_number, 'source': source,
            'source_url': source_url, 'record_date': record_date}


def _company(source, organization, decision_maker, *, city='', state='', county='', phone='', license_number=''):
    city = company_name(city) if _needs_case(_clean(city)) else _clean(city)
    return {'organization': organization, 'city': city, 'state': state, 'county': county, 'phone': phone,
            'license_number': license_number, 'source': source, 'decision_maker': decision_maker}


def _fold(value):
    return ' '.join(re.findall(r'[a-z0-9]+', (value or '').lower()))


def dedupe(items, keys):
    seen, out = set(), []
    for item in items:
        key = tuple(_fold(item.get(k)) for k in keys)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _looks_like_person(name):
    tokens = name.split()
    if len(tokens) < 2 or len(tokens) > 6:
        return False
    return not re.search(r'(?i)\b(llc|inc|corp|corporation|company|realty|properties|group|trust|fund|lp|llp|'
                         r'ltd|partners|holdings|management|capital|bank|associates)\b', name)


# ---------------------------------------------------------------------------
# Source parsers (pure)
# ---------------------------------------------------------------------------

def _tx_active(status):
    status = _clean(status)
    return status == 'Active' or (status.endswith(' - Active') and 'Suspension' not in status)


def parse_tx_rows(rows, today=None):
    """TREC rows -> (companies, prospects). Needs Broker Company rows with related_* columns."""
    today = today or date.today()
    url = SOURCES['tx-trec']['url']
    companies, prospects = [], []
    for row in rows:
        if _clean(row.get('license_type')) != 'Broker Company' or not _tx_active(row.get('status')) \
                or not _not_expired(row.get('license_expiration_date'), today):
            continue
        if _clean(row.get('related_license_type')) != 'Broker Individual':
            continue
        person = person_name(row.get('related_license_full_name'))
        firm = company_name(row.get('full_name'))
        if not person or not firm or not _looks_like_person(person):
            continue
        county = company_name(row.get('county')) if _clean(row.get('county')) else ''
        license_number = _clean(row.get('license_number'), 40)
        companies.append(_company('tx-trec', firm, person, county=county, state='TX', license_number=license_number))
        prospects.append(_prospect('tx-trec', person, 'Designated Broker', firm, state='TX', county=county,
                                   license_number=_clean(row.get('related_license_number'), 40) or license_number,
                                   source_url=url,
                                   record_date=_iso_from_mdy(row.get('original_license_date'))))
    return dedupe(companies, ('organization', 'state')), dedupe(prospects, ('full_name', 'company'))


def parse_ny_rows(rows, today=None):
    today = today or date.today()
    url = SOURCES['ny-dos']['url']
    companies, prospects = [], []
    for row in rows:
        kind = _clean(row.get('license_type')).upper()
        title = NY_BROKER_TYPES.get(kind)
        if not title or not _not_expired(row.get('license_expiration_date'), today):
            continue
        person = person_name(row.get('license_holder_name'), order='last_first')
        firm_raw = _clean(row.get('business_name'))
        if not person or not firm_raw or not _looks_like_person(person):
            continue
        if _fold(firm_raw) == _fold(row.get('license_holder_name')):
            firm = person  # sole proprietor licensed in their own name
        else:
            firm = company_name(firm_raw)
        state = _state(row.get('business_state'))
        county = _clean(row.get('county'))
        county = '' if county.upper() in ('NONE', 'N/A', 'UNKNOWN') else company_name(county)
        city = _clean(row.get('business_city'))
        license_number = _clean(row.get('license_number'), 40)
        companies.append(_company('ny-dos', firm, person, city=city, state=state, county=county,
                                  license_number=license_number))
        prospects.append(_prospect('ny-dos', person, title, firm, city=city, state=state, county=county,
                                   license_number=license_number, source_url=url, record_date=''))
    return dedupe(companies, ('organization', 'state')), dedupe(prospects, ('full_name', 'company'))


def _co_number(row):
    text = _clean(row.get('licensenumber'), 20)
    return int(text) if text.isdigit() else None


def parse_co_rows(rows, today=None):
    """Responsible Broker rows (+ Real Estate Company rows for the adjacent-license join)."""
    today = today or date.today()
    url = SOURCES['co-dora']['url']
    firms = {}
    brokers = []
    for row in rows:
        kind = _clean(row.get('licensetype'))
        if _clean(row.get('licensestatus')) != 'Active' or not _not_expired(row.get('licenseexpirationdate'), today):
            continue
        if kind in CO_COMPANY_TYPES and _clean(row.get('entityname')):
            key = (_clean(row.get('zipcode'))[:5], _iso_from_mdy(row.get('licensefirstissuedate')))
            firms.setdefault(key, []).append(row)
        elif kind in CO_RB_TYPES:
            brokers.append(row)
    companies, prospects = [], []
    for row in brokers:
        parts = [row.get('firstname'), row.get('middlename'), row.get('lastname'), row.get('suffix')]
        person = person_name(' '.join(_clean(p) for p in parts if _clean(p)))
        if not person or not _looks_like_person(person):
            continue
        issued = _iso_from_mdy(row.get('licensefirstissuedate'))
        number = _co_number(row)
        firm = ''
        candidates = firms.get((_clean(row.get('zipcode'))[:5], issued), [])
        near = [c for c in candidates if number is not None and _co_number(c) is not None
                and abs(_co_number(c) - number) <= 3]
        if len(near) == 1:
            firm = company_name(near[0].get('entityname'))
        state = _state(row.get('state'))
        city = _clean(row.get('city'))
        license_number = (_clean(row.get('licenseprefix'), 8) + '.' if _clean(row.get('licenseprefix')) else '') \
            + _clean(row.get('licensenumber'), 20)
        prospects.append(_prospect('co-dora', person, 'Responsible Broker', firm, city=city, state=state,
                                   license_number=license_number, source_url=url, record_date=issued))
        if firm:
            companies.append(_company('co-dora', firm, person, city=city, state=state,
                                      license_number=_clean(near[0].get('licenseprefix'), 8) + '.'
                                      + _clean(near[0].get('licensenumber'), 20)))
    return dedupe(companies, ('organization', 'state')), dedupe(prospects, ('full_name', 'company'))


# form.YYYYMMDD.idx: Form Type is a fixed 17-character column; the rest is space-separated with the
# company name followed by >= 2 spaces, then CIK, Date Filed (YYYYMMDD) and File Name.
_IDX_REST = re.compile(r'^(?P<company>\S.*?)\s{2,}(?P<cik>\d{1,10})\s+(?P<date>\d{8})\s+'
                       r'(?P<file>edgar/data/\d{1,10}/[\w.-]+\.txt)\s*$')


def parse_sec_index(text, forms=('D',)):
    """Rows of an EDGAR daily form.YYYYMMDD.idx file whose form type is in ``forms``."""
    out = []
    for line in (text or '').splitlines():
        if len(line) < 40 or line[:1].isspace():
            continue
        form = line[:17].strip()
        m = _IDX_REST.match(line[17:].lstrip())
        if not m or form not in forms:
            continue
        out.append({'form_type': form, 'company': _clean(m.group('company')), 'cik': m.group('cik'),
                    'date_filed': _iso_from_mdy(m.group('date')), 'file_name': m.group('file')})
    return out


def sec_filing_urls(file_name):
    """edgar/data/CIK/0001234567-26-000123.txt -> (primary_doc.xml URL, filing index URL)."""
    m = re.fullmatch(r'edgar/data/(\d{1,10})/(\d{10}-\d{2}-\d{6})\.txt', file_name or '')
    if not m:
        raise RecordsError('invalid_file_name')
    cik, accession = m.group(1), m.group(2)
    base = 'https://www.sec.gov/Archives/edgar/data/%s/%s/' % (cik, accession.replace('-', ''))
    return base + 'primary_doc.xml', base + accession + '-index.htm'


def _x(node, path):
    if node is None:
        return ''
    found = node.find(path)
    return _clean(found.text if found is not None else '')


_DM_WORDS = re.compile(r'(?i)\b(ceo|chief|president|owner|founder|principal|managing|manager of|member|partner|'
                       r'chair|director of|executive|coo|cfo|cio|vice president|vp|general partner|sponsor)\b')


def _sec_title(relationships, clarification):
    clar = _clean(clarification, 120)

    def is_dm(text):
        if _classify_title is not None:
            return bool(_classify_title(text).get('decision_maker'))
        return bool(_DM_WORDS.search(text))

    if clar:
        short = re.sub(r'(?i)\s+(?:of|for|at)\s+(?:the\s+)?(?:issuer|company|fund|[A-Z0-9].*)$', '', clar).strip(' ,;-')
        for candidate in (short, clar):
            if candidate and len(candidate) <= 80 and is_dm(candidate):
                return candidate
    if 'Executive Officer' in relationships:
        return 'Executive Officer'
    return ''


def parse_form_d(xml_text, filing=None, *, allow_amendments=False):
    """Form D primary_doc.xml -> (companies, prospects); empty when not a US real-estate issuer."""
    filing = filing or {}
    if not xml_text or re.search(r'<!(?:DOCTYPE|ENTITY)', xml_text[:5000], re.I):
        return [], []
    try:
        root = ET.fromstring(xml_text.encode('utf-8') if isinstance(xml_text, str) else xml_text)
    except ET.ParseError:
        return [], []
    submission = _x(root, 'submissionType')
    if submission != 'D' and not (allow_amendments and submission == 'D/A'):
        return [], []
    if _x(root, 'offeringData/typeOfFiling/newOrAmendment/isAmendment').lower() == 'true' and not allow_amendments:
        return [], []
    group = _x(root, 'offeringData/industryGroup/industryGroupType')
    if group not in SEC_REAL_ESTATE_GROUPS:
        return [], []
    issuer = root.find('primaryIssuer')
    state = _x(issuer, 'issuerAddress/stateOrCountry').upper()
    if state not in US_STATES:
        return [], []
    entity = _x(issuer, 'entityName')
    firm = company_name(entity)
    city = _x(issuer, 'issuerAddress/city')
    phone = re.sub(r'[^\d+()\- .x]', '', _x(issuer, 'issuerPhoneNumber'))[:30]
    file_name = filing.get('file_name') or ''
    source_url = ''
    if file_name:
        try:
            source_url = sec_filing_urls(file_name)[1]
        except RecordsError:
            source_url = ''
    record_date = filing.get('date_filed') or _x(root, 'offeringData/signatureBlock/signature/signatureDate')[:10]
    companies, prospects = [], []
    for info in root.findall('relatedPersonsList/relatedPersonInfo'):
        relationships = [_clean(r.text) for r in info.findall('relatedPersonRelationshipList/relationship')]
        if 'Executive Officer' not in relationships and 'Promoter' not in relationships:
            continue
        first, middle, last = (_x(info, 'relatedPersonName/' + k) for k in ('firstName', 'middleName', 'lastName'))
        if not first or not last or first.upper() in ('N/A', 'NA', 'NONE', '-'):
            continue
        person = person_name(' '.join(p for p in (first, middle, last) if p))
        if not _looks_like_person(person):
            continue
        title = _sec_title(relationships, _x(info, 'relationshipClarification'))
        if not title:
            continue
        prospects.append(_prospect('sec-form-d', person, title, firm, city=city, state=state,
                                   license_number='CIK ' + (filing.get('cik') or _x(issuer, 'cik').lstrip('0')),
                                   source_url=source_url, record_date=record_date))
        if not companies:
            companies.append(_company('sec-form-d', firm, person, city=city, state=state, phone=phone,
                                      license_number='CIK ' + (filing.get('cik') or _x(issuer, 'cik').lstrip('0'))))
    for company in companies:
        company['industry_group'] = group
        company['entity_type'] = _x(issuer, 'entityType')
    return companies, dedupe(prospects, ('full_name', 'company'))


# --- Florida DBPR (local file) ---------------------------------------------

FL_LAYOUT = ('license_code', 'licensee_name', 'dba_name', 'rank', 'address_1', 'address_2', 'address_3', 'city',
             'state', 'zip', 'county', 'license_number', 'primary_status', 'secondary_status',
             'original_license_date', 'status_effective_date', 'license_expiration_date',
             'alternate_license_number', 'self_proprietor_name', 'employer_name', 'employer_license_number')
_FL_ALIASES = {
    'license_code': ('license code', 'board number', 'occupation code', 'lic code'),
    'licensee_name': ('licensee name', 'name', 'licensee', 'full name'),
    'dba_name': ('dba name', 'dba', 'doing business as name', 'doing business as'),
    'rank': ('rank', 'class code', 'license rank', 'rank code'),
    'city': ('city',), 'state': ('state',), 'zip': ('zip', 'zip code', 'zipcode'),
    'county': ('county name', 'county', 'county code'),
    'license_number': ('license number', 'lic #', 'license #', 'license no', 'license'),
    'primary_status': ('primary status', 'status'),
    'secondary_status': ('secondary status',),
    'original_license_date': ('original license date', 'original licensure date', 'licensure date'),
    'license_expiration_date': ('license expiration date', 'expiration date', 'lic expire date'),
    'self_proprietor_name': ("self proprietor's name", 'self proprietor name', 'self proprietors name',
                             'sole proprietor name', 'self proprietor'),
    'employer_name': ("employer's name", 'employer name', 'employers name', 'employer'),
    'employer_license_number': ("employer's license number", 'employer license number',
                                'employers license number', 'employer license'),
}


def _header_key(text):
    return ' '.join(re.sub(r"[^a-z0-9#' ]+", ' ', (text or '').lower().replace('’', "'")).split())


def _map_header(header):
    lookup = {}
    for field, aliases in _FL_ALIASES.items():
        for alias in aliases:
            lookup.setdefault(_header_key(alias), field)
    mapping = {}
    for i, cell in enumerate(header):
        field = lookup.get(_header_key(cell)) or lookup.get(_header_key(cell).replace("'", ''))
        if field and field not in mapping.values():
            mapping[i] = field
    return mapping


def _fl_rank(rank):
    r = _header_key(rank).upper()
    if r in ('BK', 'BROKER', 'REAL ESTATE BROKER', 'BROKER REAL ESTATE'):
        return 'broker'
    if any(w in r for w in ('SALES', 'ASSOC', 'SL', 'BL', 'INSTRUCTOR', 'SCHOOL', 'BRANCH', 'TEMP')):
        return 'other'
    if any(w in r for w in ('CORP', 'LLC', 'LIMITED LIABILITY', 'PARTNERSHIP', 'CQ', 'PROF')):
        return 'company'
    return 'broker' if 'BROKER' in r else 'other'


def _fl_active(row):
    primary = _clean(row.get('primary_status')).upper()
    secondary = _clean(row.get('secondary_status')).upper()
    ok_primary = primary in ('C', 'CURRENT', 'ACTIVE', '') or primary.startswith('CURRENT')
    ok_secondary = secondary in ('A', 'ACTIVE', '') or 'ACTIVE' in secondary and 'INACTIVE' not in secondary
    return ok_primary and ok_secondary and bool(primary or secondary)


def parse_fl_rows(rows, today=None):
    """Mapped DBPR rows (dicts keyed by FL_LAYOUT names) -> (companies, prospects)."""
    today = today or date.today()
    companies, prospects = [], []
    for row in rows:
        if not _fl_active(row) or not _not_expired(row.get('license_expiration_date'), today):
            continue
        rank = _fl_rank(row.get('rank'))
        state = _state(row.get('state')) or 'FL'
        city = _clean(row.get('city'))
        county = _clean(row.get('county'))
        county = company_name(county) if county and not county.isdigit() else ''
        license_number = _clean(row.get('license_number'), 40)
        if rank == 'company':
            firm = company_name(_clean(row.get('dba_name')) or _clean(row.get('licensee_name')))
            if firm:
                companies.append(_company('fl-dbpr-file', firm, '', city=city, state=state, county=county,
                                          license_number=license_number))
            continue
        if rank != 'broker':
            continue
        person = person_name(row.get('licensee_name'))
        if not person or not _looks_like_person(person):
            continue
        employer = _clean(row.get('employer_name'))
        if employer:
            firm, title = company_name(employer), 'Qualifying Broker'
        elif _clean(row.get('dba_name')) or _clean(row.get('self_proprietor_name')):
            firm = company_name(_clean(row.get('dba_name')) or _clean(row.get('self_proprietor_name')))
            title = 'Broker/Owner'
        else:
            continue
        if _fold(firm) == _fold(_clean(row.get('licensee_name'))):
            firm = person
        companies.append(_company('fl-dbpr-file', firm, person, city=city, state=state, county=county,
                                  license_number=_clean(row.get('employer_license_number'), 40) or license_number))
        prospects.append(_prospect('fl-dbpr-file', person, title, firm, city=city, state=state, county=county,
                                   license_number=license_number, source_url=FL_URL,
                                   record_date=_iso_from_mdy(row.get('original_license_date'))))
    # A company row without a decision maker inherits one when a broker names it.
    named = {_fold(c['organization']): c['decision_maker'] for c in companies if c['decision_maker']}
    for c in companies:
        if not c['decision_maker']:
            c['decision_maker'] = named.get(_fold(c['organization']), '')
    companies.sort(key=lambda c: not c['decision_maker'])
    return dedupe(companies, ('organization', 'state')), dedupe(prospects, ('full_name', 'company'))


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _since_date(since, today):
    if since in (None, ''):
        return None
    try:
        value = date.fromisoformat(str(since)[:10])
    except ValueError:
        raise RecordsError('invalid_since') from None
    if value > today:
        raise RecordsError('invalid_since')
    return value


def _quote(text):
    return "'" + str(text).replace("'", "''") + "'"


def _now():
    return datetime.now(timezone.utc)


def fetch_source(name, *, since=None, max_rows=DEFAULT_MAX_ROWS, env=None, getter=None, today=None,
                 allow_amendments=False, max_filings=DEFAULT_MAX_FILINGS):
    """Fetch one live source and return normalized companies/prospects plus the next ``since`` cursor."""
    if name not in SOURCES:
        raise RecordsError('unknown_source')
    if name == 'fl-dbpr-file':
        raise RecordsError('local_file_only')
    env = os.environ if env is None else env
    getter = getter or RecordsGetter(env)
    today = today or _now().date()
    start = _since_date(since, today)
    if type(max_rows) is not int or max_rows < 1:
        raise RecordsError('invalid_max_rows')
    cursor = today.isoformat()
    if name == 'tx-trec':
        where = "license_type='Broker Company' AND status in ('Active','Probation - Active')"
        if start:
            where += " AND updated >= " + _quote(start.isoformat() + 'T00:00:00')
        select = ('license_type,license_number,full_name,status,original_license_date,license_expiration_date,'
                  'county,related_license_type,related_license_number,related_license_full_name,updated')
        rows = list(socrata_fetch(TX_URL, where, select, 'updated DESC, license_number', max_rows=max_rows,
                                  getter=getter))
        companies, prospects = parse_tx_rows(rows, today)
        stamps = [_iso_from_mdy(r.get('updated')) for r in rows if r.get('updated')]
        cursor = max([s for s in stamps if s] or [start.isoformat() if start else today.isoformat()])
    elif name == 'ny-dos':
        where = 'license_type in (%s)' % ','.join(_quote(t) for t in NY_BROKER_TYPES)
        if start:
            try:
                horizon = start.replace(year=start.year + 2)
            except ValueError:  # Feb 29
                horizon = start.replace(year=start.year + 2, day=28)
            where += ' AND license_expiration_date >= ' + _quote(horizon.isoformat() + 'T00:00:00')
        rows = list(socrata_fetch(NY_URL, where, None, ':id', max_rows=max_rows, getter=getter))
        companies, prospects = parse_ny_rows(rows, today)
    elif name == 'co-dora':
        types = CO_RB_TYPES + CO_COMPANY_TYPES
        where = "licensestatus='Active' AND licensetype in (%s)" % ','.join(_quote(t) for t in types)
        if start:
            days = (today - start).days
            if days > 92:
                raise RecordsError('since_too_old')
            stamps = [(start + timedelta(days=i)).strftime('%m/%d/%Y') for i in range(days + 1)]
            where += ' AND licensefirstissuedate in (%s)' % ','.join(_quote(s) for s in stamps)
        select = ('firstname,middlename,lastname,suffix,entityname,city,state,zipcode,licenseprefix,licensenumber,'
                  'licensetype,licensefirstissuedate,licenseexpirationdate,licensestatus')
        rows = list(socrata_fetch(CO_URL, where, select, 'licensenumber', max_rows=max_rows, getter=getter))
        companies, prospects = parse_co_rows(rows, today)
    else:  # sec-form-d
        getter.headers_for('https://www.sec.gov')  # refuse early without SEC_USER_AGENT
        first = start or (today - timedelta(days=1))
        first = max(first, today - timedelta(days=MAX_SEC_DAYS - 1))
        forms = ('D', 'D/A') if allow_amendments else ('D',)
        companies, prospects, filings = [], [], []
        day = first
        last_seen = None
        while day <= today:
            if day.weekday() < 5:
                url = SEC_INDEX.format(y=day.year, q=(day.month - 1) // 3 + 1, ymd=day.strftime('%Y%m%d'))
                text = getter.get_text(url)
                if text is not None:
                    filings.extend(parse_sec_index(text, forms))
                    last_seen = day
            day += timedelta(days=1)
        limit = min(max_filings, max_rows)
        for filing in filings[:limit]:
            doc_url, _ = sec_filing_urls(filing['file_name'])
            xml_text = getter.get_text(doc_url, accept='application/xml, text/xml;q=0.9')
            if xml_text is None:
                continue
            c, p = parse_form_d(xml_text, filing, allow_amendments=allow_amendments)
            companies.extend(c)
            prospects.extend(p)
        companies = dedupe(companies, ('organization', 'state'))
        prospects = dedupe(prospects, ('full_name', 'company'))
        cursor = min(last_seen + timedelta(days=1), today).isoformat() if last_seen else first.isoformat()
        if len(filings) > limit:
            cursor = filings[limit]['date_filed'] or cursor  # resume at the first unprocessed day
    return {'source': name, 'fetched_at': _now().replace(microsecond=0).isoformat(),
            'terms_note': SOURCES[name]['terms_note'], 'companies': companies, 'prospects': prospects,
            'cursor': cursor, 'requests': getattr(getter, 'requests', None)}


def _read_rows(path):
    with open(path, newline='', encoding='utf-8-sig', errors='replace') as handle:
        sample = handle.read(65536)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=',\t|')
        except csv.Error:
            dialect = csv.excel
        yield from csv.reader(handle, dialect)


_GENERIC_ALIASES = {
    'full_name': ('full_name', 'full name', 'name', 'person', 'contact name'),
    'title': ('title', 'job title', 'position', 'role'),
    'company': ('company', 'organization', 'firm', 'business name', 'company name'),
    'city': ('city',), 'state': ('state',), 'county': ('county',),
    'license_number': ('license_number', 'license number', 'license'),
    'source_url': ('source_url', 'source url', 'url'),
    'record_date': ('record_date', 'record date', 'date'),
}


def load_local_file(name, path, *, today=None, max_rows=2_000_000):
    """Parse a user-provided CSV: 'fl-dbpr-file' (DBPR extract, with or without a header row) or
    'generic-csv' (columns like full_name,title,company,city,state)."""
    if name not in ('fl-dbpr-file', 'generic-csv'):
        raise RecordsError('unknown_local_source')
    today = today or date.today()
    reader = _read_rows(path)
    first = next(reader, None)
    if first is None:
        return {'source': name, 'fetched_at': _now().replace(microsecond=0).isoformat(),
                'terms_note': SOURCES.get(name, {}).get('terms_note', ''), 'companies': [], 'prospects': [],
                'cursor': None}
    if name == 'fl-dbpr-file':
        mapping = _map_header(first)
        has_header = 'licensee_name' in mapping.values() and 'rank' in mapping.values()
        if not has_header:
            if len(first) < 17:
                raise RecordsError('unrecognized_fl_layout')
            mapping = dict(enumerate(FL_LAYOUT))

        def rows():
            if not has_header:
                yield {mapping[i]: v for i, v in enumerate(first) if i in mapping}
            for n, cells in enumerate(reader):
                if n >= max_rows:
                    break
                yield {mapping[i]: v for i, v in enumerate(cells) if i in mapping}
        companies, prospects = parse_fl_rows(rows(), today)
    else:
        lookup = {_header_key(a): f for f, aliases in _GENERIC_ALIASES.items() for a in aliases}
        mapping = {i: lookup[_header_key(c)] for i, c in enumerate(first) if _header_key(c) in lookup}
        if 'full_name' not in mapping.values():
            raise RecordsError('generic_csv_requires_name_column')
        companies, prospects = [], []
        for n, cells in enumerate(reader):
            if n >= max_rows:
                break
            row = {mapping[i]: _clean(v) for i, v in enumerate(cells) if i in mapping}
            person = person_name(row.get('full_name'))
            if not person:
                continue
            firm = company_name(row.get('company'))
            state = _state(row.get('state'))
            prospects.append(_prospect('generic-csv', person, row.get('title', ''), firm, city=row.get('city', ''),
                                       state=state, county=row.get('county', ''),
                                       license_number=row.get('license_number', ''),
                                       source_url=row.get('source_url', '').split('?')[0],
                                       record_date=_iso_from_mdy(row.get('record_date'))))
            if firm:
                companies.append(_company('generic-csv', firm, person, city=row.get('city', ''), state=state,
                                          county=row.get('county', '')))
        companies = dedupe(companies, ('organization', 'state'))
        prospects = dedupe(prospects, ('full_name', 'company'))
    return {'source': name, 'fetched_at': _now().replace(microsecond=0).isoformat(),
            'terms_note': SOURCES.get(name, {}).get('terms_note', 'User-provided file; confirm you may use it.'),
            'companies': companies, 'prospects': prospects, 'cursor': None}
