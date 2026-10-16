"""Pure lead-source helpers: LinkedIn X-ray parsing, query/job builders, collection.

Nothing here touches the network. Jobs are plain dicts for Runner/route_run, so
every lead-source request still goes through dry-run planning, confirmed budgets,
the ledger, per-input caching and pause-on-error.

Sources for request/response shapes:
- Hunter Discover / Domain Search: https://hunter.io/api-documentation/v2#discover
  and #domain-search; industries: https://hunter.io/files/industries.json
- Serper: POST https://google.serper.dev/search {q, num, gl, hl, page} -> organic[]
  {title, link, snippet, position}; num > 10 is billed as 2 credits, so num is fixed at 10
- Brave Web Search: GET https://api.search.brave.com/res/v1/web/search
  (q, count<=20, offset<=9, country) -> web.results[] {title, url, description}
- Overpass API: https://wiki.openstreetmap.org/wiki/Overpass_API (map_to_area, out tags center)
"""
from __future__ import annotations

import html
import re
from urllib.parse import quote, unquote, urlsplit

from .models import PolicyError
from .policy import (HUNTER_HEADCOUNTS, OSM_DEFAULT_TAGS, US_STATES, normalize_domain, normalize_email,
                     us_state_code, validate_evidence_url)

__all__ = ['US_STATES', 'DEFAULT_DM_TITLES', 'HUNTER_REAL_ESTATE_INDUSTRIES', 'DISCOVERY_OPERATIONS',
           'canonical_linkedin', 'parse_linkedin_result', 'build_xray_queries', 'xray_jobs',
           'hunter_discover_jobs', 'hunter_domain_jobs', 'osm_jobs', 'collect', 'clean_text', 'safe_link']

DEFAULT_DM_TITLES = ('CEO', 'Founder', 'Owner', 'President', 'Principal', 'Managing Partner',
                     'Managing Broker', 'Broker/Owner', 'Designated Broker', 'Managing Director')

# Exact strings from https://hunter.io/files/industries.json (real-estate subset).
# 'Real Estate and Equipment Rental Services' is deliberately excluded: it also covers equipment rental.
HUNTER_REAL_ESTATE_INDUSTRIES = ('Real Estate', 'Real Estate Agents and Brokers', 'Commercial Real Estate',
                                 'Leasing Residential Real Estate', 'Leasing Non-residential Real Estate')

DISCOVERY_OPERATIONS = ('discover', 'domain_search', 'search', 'places')
MAX_XRAY_QUERY = 300

_LINKEDIN_HOST = re.compile(r'(?:(?:www|m|[a-z]{2})\.)?linkedin\.com')
_URLISH = re.compile(r'\S*://\S*')
_SLUG_CHARS = re.compile(r'[^\W_]|[-_]')
_CREDENTIALS = {'MBA', 'PHD', 'CCIM', 'CRS', 'GRI', 'ABR', 'SRES', 'CPA', 'JD', 'ESQ', 'CIPS', 'SIOR', 'CPM',
                'RRS', 'MRP', 'PSA', 'CRB', 'ALHS', 'EPRO', 'SRS', 'REALTOR', 'CLHMS', 'CDPE', 'MCNE', 'AHWD', 'C2EX'}
_TITLE_WORDS = re.compile(
    r'(?i)\b(ceo|cfo|coo|cto|founder|co-founder|cofounder|owner|president|principal|partner|broker|agent|realtor|'
    r'director|manager|vp|vice president|chief|head|lead|officer|associate|consultant|advisor|executive|'
    r'chair(?:man|woman)?|investor|developer|specialist|team leader|operating)\b')
_COMPANY_WORDS = re.compile(
    r'(?i)\b(realty|llc|inc|corp|corporation|group|properties|homes|company|co\.|partners|associates|'
    r'brokerage|holdings|ventures|capital|international|sotheby|keller williams|re/max|remax|compass|coldwell|'
    r'century 21|exp realty|berkshire)\b')


def clean_text(value, max_len=200):
    """Bounded, single-line provider text without HTML, URLs or control characters."""
    if not isinstance(value, str):
        return None
    text = html.unescape(re.sub(r'<[^>]{0,200}>', '', value))
    text = _URLISH.sub(' ', text)
    text = ''.join(' ' if (ord(c) < 32 or 127 <= ord(c) < 160) else c for c in text)
    text = ' '.join(text.split())
    if len(text) > max_len:
        text = text[:max_len].rstrip()
    return text or None


def safe_link(url):
    """https-only link without query/fragment that passes validate_evidence_url, else None."""
    if not isinstance(url, str) or len(url) > 2048:
        return None
    try:
        parts = urlsplit(url.strip())
        if parts.scheme != 'https' or not parts.hostname:
            return None
        host = normalize_domain(parts.hostname)
        link = 'https://' + host + (quote(unquote(parts.path), safe="/-._~!$&'()*+,;=:@%") or '/')
        return validate_evidence_url(link)
    except (PolicyError, ValueError):
        return None


def canonical_linkedin(url):
    """Return https://www.linkedin.com/in/<slug>/ or None.

    Handles missing scheme, http, country/mobile subdomains, percent-encoding,
    trailing segments (/en, /details/...), queries and fragments.
    """
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 32 for c in url):
        return None
    text = url.strip()
    if '://' not in text:
        text = 'https://' + text.lstrip('/')
    try:
        parts = urlsplit(text)
        host = (parts.hostname or '').lower()
    except ValueError:
        return None
    if parts.scheme not in ('http', 'https') or parts.username or parts.password or not _LINKEDIN_HOST.fullmatch(host):
        return None
    segments = [s for s in parts.path.split('/') if s]
    if len(segments) < 2 or segments[0].lower() != 'in':
        return None
    slug = unquote(segments[1]).strip().lower()
    if not 3 <= len(slug) <= 100 or not all(_SLUG_CHARS.fullmatch(c) for c in slug):
        return None
    return 'https://www.linkedin.com/in/' + quote(slug, safe='-_') + '/'


def linkedin_from_handle(value):
    """Hunter 'linkedin' may be a bare handle or a URL."""
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if '/' not in value and '.' not in value:
        return canonical_linkedin('https://www.linkedin.com/in/' + value)
    return canonical_linkedin(value)


def _clean_name(value):
    text = clean_text(value, 120)
    if not text:
        return None
    text = text.split(',')[0]
    text = re.sub(r'\([^)]*\)|\[[^\]]*\]', ' ', text)
    text = ''.join(c if (c.isalpha() or c in " .'-") else ' ' for c in text)
    tokens = text.split()
    while len(tokens) > 2 and tokens[-1].strip('.').upper() in _CREDENTIALS:
        tokens.pop()
    text = ' '.join(tokens).strip(" .-'")
    if not 2 <= len(text) <= 80 or sum(c.isalpha() for c in text) < 2:
        return None
    return text


def _clean_part(value, max_len=120):
    text = clean_text(value, max_len)
    if not text:
        return None
    text = re.sub(r'\s*(?:\.\.\.|…)\s*$', '', text).strip(' -–—|·•,')
    return text or None


def _looks_like_title(text):
    return bool(_TITLE_WORDS.search(text)) and not _COMPANY_WORDS.search(text)


def _looks_like_location(text):
    if re.search(r'\d', text) or len(text) > 80:
        return False
    if re.search(r'(?i)\b(united states|area|metropolitan|metroplex|county)\b', text):
        return True
    parts = [p.strip() for p in text.split(',')]
    if len(parts) >= 2 and all(p and p[0].isupper() for p in parts):
        return True
    return text.casefold() in {n.casefold() for n in US_STATES.values()}


def _country(location):
    if not location:
        return None
    parts = [p.strip() for p in location.split(',') if p.strip()]
    names = {n.casefold() for n in US_STATES.values()}
    for part in parts:
        if part.casefold() in {'united states', 'usa', 'us', 'united states of america'} or part.casefold() in names \
                or part.upper() in US_STATES and len(part) == 2:
            return 'US'
    if re.search(r'(?i)\b(' + '|'.join(re.escape(n) for n in US_STATES.values()) + r')\b', location):
        return 'US'
    if len(parts) >= 2 and parts[-1].replace(' ', '').isalpha():
        return parts[-1]
    return None


def parse_linkedin_result(title, snippet, url):
    """Parse one Google/Brave SERP row for a linkedin.com/in/ profile.

    Title formats: "Name - Title - Company | LinkedIn", en/em dashes, "Name - Company | LinkedIn"
    (two parts are classified heuristically as title vs company), "Name | LinkedIn".
    Snippet formats: "Location: X · Title · Experience: Company · Education: ..." and
    "City, State, United States · Title at Company · 500+ connections".
    Returns None unless the URL canonicalizes and a clean name is present.
    """
    linkedin_url = canonical_linkedin(url)
    if not linkedin_url or not isinstance(title, str):
        return None
    text = clean_text(title, 300) or ''
    text = re.sub(r'\s*(?:\.\.\.|…)\s*$', '', text)
    text = re.sub(r'(?i)\s*[|\-–—]\s*linkedin\s*$', '', text)
    text = re.sub(r'\s*(?:\.\.\.|…)\s*$', '', text)
    parts = [p.strip() for p in re.split(r'\s+[-–—]\s+|\s*\|\s*|\s+[–—]|[–—]\s+', text) if p.strip()]
    parts = [p for p in parts if p.casefold() not in {'linkedin', 'professional profile'}]
    if not parts:
        return None
    name = _clean_name(parts[0])
    if not name:
        return None
    job_title = company = location = None
    rest = [_clean_part(p) for p in parts[1:]]
    rest = [p for p in rest if p]
    if len(rest) >= 2:
        job_title, company = rest[0], rest[1]
    elif len(rest) == 1:
        if _looks_like_title(rest[0]):
            job_title = rest[0]
        elif _looks_like_location(rest[0]) and not _COMPANY_WORDS.search(rest[0]):
            location = rest[0]
        else:
            company = rest[0]
    body = clean_text(snippet, 600) or ''
    body = re.sub(r'^[A-Z][a-z]{2} \d{1,2}, \d{4}\s*[—–-]\s*', '', body)
    for segment in re.split(r'\s*[·•]\s*|\s+\|\s+', body):
        segment = segment.strip(' .')
        if not segment:
            continue
        label = re.match(r'(?i)(location|experience|education)\s*:\s*(.*)', segment)
        if label:
            kind, value = label.group(1).lower(), _clean_part(label.group(2))
            if kind == 'location' and value and not location:
                location = value
            elif kind == 'experience' and value and not company:
                company = value
            continue
        if re.search(r'(?i)\d[\d,]*\+?\s+(connections|followers)', segment):
            continue
        at = re.match(r'(.{2,100}?)\s+(?:at|@)\s+(.{2,100})$', segment)
        if at and _TITLE_WORDS.search(at.group(1)):
            job_title = job_title or _clean_part(at.group(1))
            company = company or _clean_part(at.group(2))
        elif not location and _looks_like_location(segment):
            location = _clean_part(segment, 80)
        elif not job_title and len(segment) <= 100 and _looks_like_title(segment):
            job_title = _clean_part(segment)
    return {'full_name': name, 'title': job_title, 'company': company, 'location': location,
            'linkedin_url': linkedin_url, 'source': 'xray', 'source_url': linkedin_url,
            'snippet_country': _country(location)}


def _term(value):
    if not isinstance(value, str):
        raise PolicyError('invalid_query_term')
    text = ' '.join(value.replace('"', ' ').split())
    if not text or len(text) > 80 or any(ord(c) < 32 for c in text):
        raise PolicyError('invalid_query_term')
    return text


def _or_group(terms):
    quoted = ['"' + t + '"' for t in terms]
    return quoted[0] if len(quoted) == 1 else '(' + ' OR '.join(quoted) + ')'


def build_xray_queries(titles, locations, industry_terms=('real estate',), extra_terms=(), exclude_terms=('jobs', 'hiring'),
                       max_length=MAX_XRAY_QUERY):
    """Deterministic, deduplicated LinkedIn X-ray queries, each <= max_length characters.

    Example: site:linkedin.com/in ("CEO" OR "Founder") "real estate" "Austin, Texas" -jobs -hiring
    Titles are greedily packed into OR groups per location so every query fits.
    """
    titles = list(dict.fromkeys(_term(t) for t in titles))
    if not titles:
        raise PolicyError('titles_required')
    locations = list(dict.fromkeys(_term(l) for l in locations)) or [None]
    industry = list(dict.fromkeys(_term(t) for t in industry_terms))
    extras = list(dict.fromkeys(_term(t) for t in extra_terms))
    excludes = list(dict.fromkeys(_term(t) for t in exclude_terms))
    queries = []
    for location in locations:
        tail = []
        if industry:
            tail.append(_or_group(industry))
        if location:
            tail.append('"' + location + '"')
        tail.extend('"' + t + '"' for t in extras)
        tail.extend('-' + ('"' + t + '"' if ' ' in t else t) for t in excludes)

        def render(group):
            return ' '.join(['site:linkedin.com/in', _or_group(group), *tail])

        group = []
        for title in titles:
            if group and len(render(group + [title])) > max_length:
                queries.append(render(group))
                group = []
            group.append(title)
            if len(render(group)) > max_length:
                raise PolicyError('query_too_long')
        if group:
            queries.append(render(group))
    return list(dict.fromkeys(queries))


def xray_jobs(provider, queries, pages=1):
    """Search jobs for 'serper' (page 1..10) or 'brave' (offset 0..9); one job per query per page."""
    if provider not in ('serper', 'brave') or type(pages) is not int or not 1 <= pages <= 10:
        raise PolicyError('invalid_xray_jobs')
    jobs = []
    for query in queries:
        for index in range(pages):
            payload = {'query': query}
            if index and provider == 'serper':
                payload['page'] = index + 1
            elif index:
                payload['offset'] = index
            jobs.append({'provider': provider, 'operation': 'search', 'payload': payload})
    return jobs


def hunter_discover_jobs(states, industries=HUNTER_REAL_ESTATE_INDUSTRIES, headcounts=('1-10', '11-50', '51-200'),
                         limit=100, keywords=None, company_type=None):
    """One Hunter Discover job per state x headcount.

    Free plans cannot page (offset must stay 0) and get at most 100 companies per
    response, so splitting by state and headcount yields distinct company sets
    instead of the same first 100 repeatedly.
    """
    jobs = []
    for state in states:
        code = us_state_code(state)
        for headcount in headcounts:
            if headcount not in HUNTER_HEADCOUNTS:
                raise PolicyError('invalid_filters')
            filters = {'headquarters_location': {'include': [{'country': 'US', 'state': code}]},
                       'industry': {'include': list(industries)}, 'headcount': [headcount], 'limit': limit}
            if keywords:
                filters['keywords'] = {'include': list(keywords), 'match': 'any'}
            if company_type:
                filters['company_type'] = {'include': list(company_type)}
            jobs.append({'provider': 'hunter', 'operation': 'discover', 'payload': {'filters': filters}})
    return jobs


def hunter_domain_jobs(domains, limit=10, decision_maker=True, required_field=('full_name', 'position'),
                       seniority=(), department=()):
    """One Hunter Domain Search job per distinct domain (free plan: limit + offset <= 10)."""
    jobs, seen = [], set()
    for domain in domains:
        clean = normalize_domain(domain)
        if clean.startswith('www.'):
            clean = clean[4:]
        if clean in seen:
            continue
        seen.add(clean)
        payload = {'domain': clean, 'limit': limit, 'decision_maker': decision_maker,
                   'required_field': list(required_field), 'seniority': list(seniority), 'department': list(department)}
        jobs.append({'provider': 'hunter', 'operation': 'domain_search', 'payload': payload})
    return jobs


def osm_jobs(areas, tags=OSM_DEFAULT_TAGS):
    """Overpass 'places' jobs. Each area is a name, (name, state) tuple, or {'area', 'state'} dict."""
    jobs = []
    for item in areas:
        if isinstance(item, dict):
            payload = {k: item[k] for k in ('area', 'state') if k in item}
        elif isinstance(item, (tuple, list)) and len(item) == 2:
            payload = {'area': item[0], 'state': item[1]}
        else:
            payload = {'area': item}
        payload['tags'] = list(tags)
        jobs.append({'provider': 'overpass', 'operation': 'places', 'payload': payload})
    return jobs


def _merge(target, row):
    for key, value in row.items():
        if target.get(key) in (None, '', []) and value not in (None, '', []):
            target[key] = value
    for source in row.get('sources', []):
        if source not in target['sources']:
            target['sources'].append(source)


def _prospect_keys(row):
    keys = []
    if row.get('linkedin_url'):
        keys.append(('linkedin', row['linkedin_url']))
    if row.get('email'):
        try:
            keys.append(('email', normalize_email(row['email'])))
        except PolicyError:
            pass
    if not keys and row.get('full_name'):
        keys.append(('name', row['full_name'].casefold(), (row.get('company') or '').casefold()))
    return keys


def _company_keys(row):
    if row.get('domain'):
        return [('domain', row['domain'])]
    if row.get('osm_id'):
        return [('osm', row['osm_id'])]
    if row.get('organization'):
        return [('name', row['organization'].casefold(), (row.get('city') or '').casefold())]
    return []


def _dedupe(rows, keyfunc):
    out, index = [], {}
    for row in rows:
        keys = keyfunc(row)
        if not keys:
            continue
        hit = next((index[k] for k in keys if k in index), None)
        if hit is None:
            hit = len(out)
            out.append({**row, 'sources': list(row.get('sources', []))})
        else:
            _merge(out[hit], row)
        for key in keys + keyfunc(out[hit]):
            index.setdefault(key, hit)
    return out


def collect(store):
    """Flatten completed lead-source attempts into deduplicated prospects and companies.

    Each row carries provider, attempt_id and observed_at, plus a 'sources' list when
    merged from several attempts. Prospects dedupe by linkedin_url or email; companies by domain.
    """
    prospects, companies = [], []
    for attempt in store.attempts():
        outcome = attempt.get('outcome') or {}
        if (attempt.get('operation') not in DISCOVERY_OPERATIONS or attempt.get('state') not in ('completed', 'reviewed')
                or outcome.get('error') or outcome.get('assessment') != 'results'):
            continue
        fields = outcome.get('fields') or {}
        meta = {'provider': attempt['provider'], 'attempt_id': attempt['id'],
                'observed_at': attempt.get('finished_at') or attempt.get('sent_at')}
        source = {'provider': attempt['provider'], 'attempt_id': attempt['id'], 'operation': attempt['operation']}
        for row in fields.get('prospects') or []:
            if isinstance(row, dict):
                prospects.append({**row, **meta, 'sources': [source]})
        for row in fields.get('companies') or []:
            if isinstance(row, dict):
                companies.append({**row, **meta, 'sources': [source]})
        if attempt['operation'] == 'domain_search' and fields.get('domain'):
            companies.append({'domain': fields['domain'], 'organization': fields.get('organization'),
                              'email_pattern': fields.get('pattern'), 'accept_all': fields.get('accept_all'),
                              **meta, 'sources': [source]})
    return {'prospects': _dedupe(prospects, _prospect_keys), 'companies': _dedupe(companies, _company_keys)}
