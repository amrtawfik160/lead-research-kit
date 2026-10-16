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
- Apollo People API Search / Enrichment: https://docs.apollo.io/reference/people-api-search,
  https://docs.apollo.io/reference/people-enrichment
- Prospeo Search / Enrich Person: https://prospeo.io/api-docs/search-person, /api-docs/enrich-person
- Google Places Text Search (New):
  https://developers.google.com/maps/documentation/places/web-service/text-search
- Exa search (category 'people' / 'company'): https://docs.exa.ai/reference/search
- Serper mini-batch: POST /search with a JSON array of up to 100 {q, ...} objects -> array of
  responses in request order, one credit each (serper.dev Playground 'Mini-batch (up to 100 queries)')
- Tomba LinkedIn Finder: https://docs.tomba.io/api/finder
- Tavily Search: https://docs.tavily.com/documentation/api-reference/endpoint/search -> results[]
  {title, url, content}; SerpApi: https://serpapi.com/search-api -> organic_results[] {title, link,
  snippet}; Firecrawl search/scrape: https://docs.firecrawl.dev/api-reference/endpoint/search and
  /endpoint/scrape (data.web[] {url, title, description}; data.html)
- People Data Labs Person Enrichment: https://docs.peopledatalabs.com/docs/reference-person-enrichment-api
"""
from __future__ import annotations

import base64
import html
import re
import zlib
from urllib.parse import quote, unquote, urlsplit

from .models import PolicyError
from .policy import (HUNTER_HEADCOUNTS, HUNTER_POSTED_DATES, MAX_BATCH_QUERIES, OSM_DEFAULT_TAGS, US_STATES,
                     SEARCH_DOMAIN_FILTER_PROVIDERS, normalize_discover_filters, normalize_domain, normalize_email,
                     scrape_url, search_domains, search_query, us_state_code, validate_evidence_url)

__all__ = ['US_STATES', 'DEFAULT_DM_TITLES', 'HUNTER_REAL_ESTATE_INDUSTRIES', 'DISCOVERY_OPERATIONS',
           'canonical_linkedin', 'parse_linkedin_result', 'build_xray_queries', 'xray_jobs',
           'hunter_discover_jobs', 'hunter_domain_jobs', 'osm_jobs', 'collect', 'clean_text', 'safe_link',
           'apollo_jobs', 'apollo_enrich_jobs', 'prospeo_jobs', 'prospeo_enrich_jobs', 'tomba_linkedin_jobs',
           'places_queries', 'places_jobs', 'exa_people_queries', 'exa_jobs', 'PLACES_QUERY_TEMPLATES',
           'FINDER_OPERATIONS', 'hunter_discover_city_jobs', 'hunter_discover_keyword_jobs',
           'hunter_name_resolution_jobs', 'serper_batch_jobs', 'HUNTER_NAME_BATCH', 'SEARCH_PROVIDERS',
           'search_jobs', 'firecrawl_scrape_jobs', 'pdl_enrich_jobs', 'scraped_html', 'scraped_pages',
           'encode_html', 'decode_html', 'FIRECRAWL_MAX_HTML']

DEFAULT_DM_TITLES = ('CEO', 'Founder', 'Owner', 'President', 'Principal', 'Managing Partner',
                     'Managing Broker', 'Broker/Owner', 'Designated Broker', 'Managing Director')

# Exact strings from https://hunter.io/files/industries.json (real-estate subset).
# 'Real Estate and Equipment Rental Services' is deliberately excluded: it also covers equipment rental.
HUNTER_REAL_ESTATE_INDUSTRIES = ('Real Estate', 'Real Estate Agents and Brokers', 'Commercial Real Estate',
                                 'Leasing Residential Real Estate', 'Leasing Non-residential Real Estate')

DISCOVERY_OPERATIONS = ('discover', 'domain_search', 'search', 'search_batch', 'places', 'people_search',
                        'text_search')
# Firm names per Hunter Discover organization.name job: one free response holds at most 100
# companies, so 50 names leave room for a couple of same-name matches per firm.
HUNTER_NAME_BATCH = 50
# Single-person operations whose 'candidate' email (and enriched identity) become prospects.
FINDER_OPERATIONS = ('find', 'people_enrich', 'enrich', 'linkedin_email', 'person_enrich')
# Providers whose single-query 'search' operation returns {results, prospects} rows (Exa: 'people').
SEARCH_PROVIDERS = ('serper', 'brave', 'tavily', 'serpapi', 'exa', 'firecrawl')
FIRECRAWL_MAX_HTML = 500_000  # bytes of UTF-8 HTML kept per scraped page
PLACES_QUERY_TEMPLATES = ('real estate brokerage in {city}, {state}', 'commercial real estate firm in {city}, {state}',
                          'property management company in {city}, {state}')
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


def _discover_job(filters):
    normalize_discover_filters(filters)  # fail fast on anything the ledger would refuse
    return {'provider': 'hunter', 'operation': 'discover', 'payload': {'filters': filters}}


def _hiring_filter(hiring):
    """None -> no job filter; True -> currently hiring; dict -> validated job_openings filter."""
    if hiring is None or hiring is False:
        return None
    if hiring is True:
        return {'hiring': True}
    if isinstance(hiring, dict):
        if 'posted_date' in hiring and hiring['posted_date'] not in HUNTER_POSTED_DATES:
            raise PolicyError('invalid_filters')
        return dict(hiring)
    raise PolicyError('invalid_filters')


def hunter_discover_city_jobs(metros, headcounts=('1-10', '11-50', '51-200'),
                              industries=HUNTER_REAL_ESTATE_INDUSTRIES, keywords=None, hiring=None, limit=100):
    """One free Hunter Discover job per (city, state) x headcount.

    City-level headquarters filters split a state's companies into many more distinct
    100-company pages than state-level jobs (free plans cannot page). Each metro is a
    (city, state) tuple or {'city', 'state'} dict; Hunter requires country with a city, so
    every location is {'city', 'country': 'US', 'state'}. ``hiring`` is None, True (currently
    hiring) or a job_openings dict such as {'hiring': True, 'posted_date': 'last30d'}.
    """
    jobs, seen = [], set()
    job_filter = _hiring_filter(hiring)
    for item in metros:
        city, state = _metro(item)
        if not city or not state:
            raise PolicyError('invalid_metro')
        code = us_state_code(state)
        if (city.casefold(), code) in seen:
            continue
        seen.add((city.casefold(), code))
        for headcount in headcounts:
            if headcount not in HUNTER_HEADCOUNTS:
                raise PolicyError('invalid_filters')
            filters = {'headquarters_location': {'include': [{'city': city, 'country': 'US', 'state': code}]},
                       'headcount': [headcount], 'limit': limit}
            if industries:
                filters['industry'] = {'include': list(industries)}
            if keywords:
                filters['keywords'] = {'include': list(keywords), 'match': 'any'}
            if job_filter:
                filters['job_openings'] = dict(job_filter)
            jobs.append(_discover_job(filters))
    return jobs


def hunter_discover_keyword_jobs(states, keyword_sets, headcounts=None, industries=None, limit=100):
    """One free Hunter Discover job per state x keyword set (keywords match 'any').

    Keyword sets (e.g. ('property management',), ('commercial brokerage', 'investment sales'))
    surface companies that industry filters misfile. Optional headcount / industry narrowing.
    """
    jobs = []
    sets = []
    for keywords in keyword_sets:
        group = [keywords] if isinstance(keywords, str) else list(keywords)
        if not group:
            raise PolicyError('invalid_filters')
        if group not in sets:
            sets.append(group)
    for state in dict.fromkeys(us_state_code(s) for s in states):
        for group in sets:
            filters = {'headquarters_location': {'include': [{'country': 'US', 'state': state}]},
                       'keywords': {'include': group, 'match': 'any'}, 'limit': limit}
            if headcounts:
                filters['headcount'] = list(headcounts)
            if industries:
                filters['industry'] = {'include': list(industries)}
            jobs.append(_discover_job(filters))
    return jobs


_HUNTER_NAME = re.compile(r"[\w .,&'()/+-]+")


def hunter_name_resolution_jobs(companies, batch_size=HUNTER_NAME_BATCH):
    """Free Hunter Discover jobs that look up firm domains by organization.name.

    Companies are grouped by state (records give one) and each group is cut into batches of at
    most ``batch_size`` (<= 50) distinct firm names (resolve.firm_core). When every name in a
    batch shares a state, the batch also filters headquarters to that state; companies without
    a state go in unfiltered batches. Names Hunter's filter syntax cannot carry are skipped.
    Map the results back with resolve.resolve_from_hunter.
    """
    from .resolve import firm_core  # resolve imports this module at load time
    if type(batch_size) is not int or not 1 <= batch_size <= HUNTER_NAME_BATCH:
        raise PolicyError('invalid_batch_size')
    groups = {}
    for company in companies:
        name = ' '.join(firm_core(company.get('organization') or company.get('company') or '').split())
        if not 2 <= len(name) <= 100 or not _HUNTER_NAME.fullmatch(name) or not re.search(r'[A-Za-z]', name):
            continue
        state = company.get('state')
        try:
            code = us_state_code(state) if state else None
        except PolicyError:
            code = None
        bucket = groups.setdefault(code, {})
        bucket.setdefault(name.casefold(), name)
    jobs = []
    for code in sorted(groups, key=lambda c: (c is None, c or '')):
        names = sorted(groups[code].values(), key=str.casefold)
        for chunk in _chunks(names, batch_size):
            filters = {'organization': {'name': chunk}, 'limit': 100}
            if code:
                filters['headquarters_location'] = {'include': [{'country': 'US', 'state': code}]}
            jobs.append(_discover_job(filters))
    return jobs


def serper_batch_jobs(queries, size=MAX_BATCH_QUERIES):
    """Serper mini-batch jobs: distinct queries in input order, ``size`` (<= 100) per request.

    Each job is billed len(queries) query units but is a single HTTP request.
    """
    if type(size) is not int or not 1 <= size <= MAX_BATCH_QUERIES:
        raise PolicyError('invalid_batch_size')
    clean = list(dict.fromkeys(search_query(q) for q in queries))
    return [{'provider': 'serper', 'operation': 'search_batch', 'payload': {'queries': chunk}}
            for chunk in _chunks(clean, size)]


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


def _chunks(items, size):
    return [items[i:i + size] for i in range(0, len(items), size)]


def _root_domain_list(domains):
    out = []
    for domain in domains:
        clean = normalize_domain(domain)
        clean = clean[4:] if clean.startswith('www.') else clean
        if clean not in out:
            out.append(clean)
    return out


def _metro(item):
    if isinstance(item, dict):
        return _term(item.get('city')), _term(item.get('state'))
    if isinstance(item, (tuple, list)) and len(item) == 2:
        return _term(item[0]), _term(item[1])
    raise PolicyError('invalid_metro')


def apollo_jobs(titles, locations, seniorities=('owner', 'founder', 'c_suite', 'partner'), pages=1,
                employee_ranges=('1,10', '11,50', '51,200'), location_field='organization_locations',
                keywords=None, include_similar_titles=None, per_page=100):
    """Apollo People API Search jobs (0 credits each): one per location per page.

    location_field is 'organization_locations' (company HQ) or 'person_locations'.
    Results carry apollo_id and obfuscated last names; finish them with apollo_enrich_jobs.
    """
    if location_field not in ('organization_locations', 'person_locations') or type(pages) is not int or not 1 <= pages <= 500:
        raise PolicyError('invalid_apollo_jobs')
    jobs = []
    for location in list(dict.fromkeys(_term(l) for l in locations)) or [None]:
        for page in range(1, pages + 1):
            criteria = {'page': page, 'per_page': per_page}
            if titles:
                criteria['titles'] = list(titles)
            if seniorities:
                criteria['seniorities'] = list(seniorities)
            if employee_ranges:
                criteria['employee_ranges'] = list(employee_ranges)
            if location:
                criteria[location_field] = [location]
            if keywords:
                criteria['keywords'] = keywords
            if include_similar_titles is not None:
                criteria['include_similar_titles'] = include_similar_titles
            jobs.append({'provider': 'apollo', 'operation': 'people_search', 'payload': {'criteria': criteria}})
    return jobs


def _enrich_jobs(provider, operation, id_key, payload_key, prospects):
    jobs, seen = [], set()
    for row in prospects:
        if not isinstance(row, dict) or row.get('email'):
            continue
        if row.get(id_key):
            payload = {payload_key: row[id_key]}
        elif row.get('linkedin_url'):
            payload = {'linkedin_url': row['linkedin_url']}
        elif row.get('full_name') and row.get('company_website'):
            payload = {'name': row['full_name'], 'domain': row['company_website']}
        else:
            continue
        key = tuple(sorted(payload.items()))
        if key in seen:
            continue
        seen.add(key)
        if row.get('lead_id'):
            payload['lead_id'] = str(row['lead_id'])
        jobs.append({'provider': provider, 'operation': operation, 'payload': payload})
    return jobs


def apollo_enrich_jobs(prospects):
    """One Apollo people/match job per prospect still lacking an email (1 person per job)."""
    return _enrich_jobs('apollo', 'people_enrich', 'apollo_id', 'apollo_id', prospects)


def prospeo_enrich_jobs(prospects):
    """One Prospeo enrich-person job per prospect still lacking an email."""
    return _enrich_jobs('prospeo', 'enrich', 'prospeo_id', 'person_id', prospects)


def prospeo_jobs(domains, titles=None, seniorities=('Founder/Owner', 'C-Suite', 'Partner'), pages=1,
                 title_match_mode='CONTAINS', max_person_per_company=None, chunk=500):
    """Prospeo search-person jobs over company websites, in chunks of <= 500 root domains."""
    if type(chunk) is not int or not 1 <= chunk <= 500 or type(pages) is not int or not 1 <= pages <= 1000:
        raise PolicyError('invalid_prospeo_jobs')
    jobs = []
    for group in _chunks(_root_domain_list(domains), chunk):
        for page in range(1, pages + 1):
            criteria = {'websites': group, 'page': page}
            if titles:
                criteria.update(titles=list(titles), title_match_mode=title_match_mode)
            if seniorities:
                criteria['seniorities'] = list(seniorities)
            if max_person_per_company:
                criteria['max_person_per_company'] = max_person_per_company
            jobs.append({'provider': 'prospeo', 'operation': 'people_search', 'payload': {'criteria': criteria}})
    return jobs


def tomba_linkedin_jobs(items):
    """Tomba LinkedIn Finder jobs from prospects (dicts with linkedin_url) or profile URLs."""
    jobs, seen = [], set()
    for item in items:
        if isinstance(item, dict):
            if item.get('email'):
                continue
            url, lead_id = item.get('linkedin_url'), item.get('lead_id')
        else:
            url, lead_id = item, None
        url = canonical_linkedin(url) if isinstance(url, str) else None
        if not url or url in seen:
            continue
        seen.add(url)
        payload = {'linkedin_url': url}
        if lead_id:
            payload['lead_id'] = str(lead_id)
        jobs.append({'provider': 'tomba', 'operation': 'linkedin_email', 'payload': payload})
    return jobs


def places_queries(metros, templates=PLACES_QUERY_TEMPLATES):
    """Default Text Search queries per (city, state) metro, e.g. 'real estate brokerage in Austin, TX'."""
    out = []
    for metro in metros:
        city, state = _metro(metro)
        for template in templates:
            query = template.format(city=city, state=state)
            if query not in out:
                out.append(query)
    return out


def places_jobs(items, page_size=20, included_type=None):
    """Google Places text_search jobs from query strings and/or (city, state) metros.

    Metros expand through places_queries(). Next pages: re-run with the attempt's
    'next_page_cursor' as payload 'page_cursor' (same text_query and options).
    """
    queries = []
    for item in items:
        for query in ([item] if isinstance(item, str) else places_queries([item])):
            if query not in queries:
                queries.append(query)
    jobs = []
    for query in queries:
        payload = {'text_query': query, 'page_size': page_size}
        if included_type:
            payload['included_type'] = included_type
        jobs.append({'provider': 'google_places', 'operation': 'text_search', 'payload': payload})
    return jobs


def exa_people_queries(titles, locations, industry='real estate'):
    """Natural-language Exa 'people' queries (Exa ignores site: operators)."""
    out = []
    for location in list(dict.fromkeys(_term(l) for l in locations)) or [None]:
        for title in dict.fromkeys(_term(t) for t in titles):
            query = f'{title} at a {_term(industry)} company' + (f' in {location}' if location else '')
            if query not in out:
                out.append(query)
    return out


def exa_jobs(queries):
    return [{'provider': 'exa', 'operation': 'search', 'payload': {'query': q}} for q in dict.fromkeys(queries)]


def search_jobs(provider, queries, pages=1, include_domains=None):
    """Generic single-query 'search' jobs for any SEARCH_PROVIDERS entry, distinct queries in order.

    Paging: serper / serpapi use 'page' (1..10), brave 'offset' (0..9); tavily, exa and firecrawl
    are first-page only (pages must be 1). include_domains (<= 20 hosts) is accepted only by
    tavily and firecrawl, whose APIs document a domain allowlist.
    """
    if provider not in SEARCH_PROVIDERS or type(pages) is not int or not 1 <= pages <= 10:
        raise PolicyError('invalid_search_jobs')
    if pages != 1 and provider not in ('serper', 'brave', 'serpapi'):
        raise PolicyError('paging_not_supported_by_provider')
    domains = None
    if include_domains:
        if provider not in SEARCH_DOMAIN_FILTER_PROVIDERS:
            raise PolicyError('domain_filter_not_supported_by_provider')
        domains = search_domains(include_domains)
    jobs = []
    for query in dict.fromkeys(search_query(q) for q in queries):
        for index in range(pages):
            payload = {'query': query}
            if index and provider == 'brave':
                payload['offset'] = index
            elif index:
                payload['page'] = index + 1
            if domains:
                payload['include_domains'] = list(domains)
            jobs.append({'provider': provider, 'operation': 'search', 'payload': payload})
    return jobs


def firecrawl_scrape_jobs(urls):
    """One Firecrawl 'scrape' job per distinct public page URL (canonical https; see policy.scrape_url).

    Invalid, private, non-http(s) or LinkedIn URLs raise PolicyError before any job is built.
    """
    jobs, seen = [], set()
    for url in urls:
        clean = scrape_url(url)
        if clean in seen:
            continue
        seen.add(clean)
        jobs.append({'provider': 'firecrawl', 'operation': 'scrape', 'payload': {'url': clean}})
    return jobs


def deepline_find_jobs(records, skip_with_email=True):
    """One Deepline 'find' job per record with a two-part name and a company domain.

    Accepts name/full_name (or first_name + last_name) and domain/company_website/website.
    The domain evidence URL is the company's own homepage, https://{domain}/. Records that
    already carry an email are skipped by default; duplicates (name + domain) are dropped.
    """
    from .policy import normalize_domain
    jobs, seen = [], set()
    for row in records:
        if not isinstance(row, dict) or (skip_with_email and row.get('email')):
            continue
        name = row.get('name') or row.get('full_name')
        if not isinstance(name, str) and isinstance(row.get('first_name'), str) and isinstance(row.get('last_name'), str):
            name = row['first_name'] + ' ' + row['last_name']
        name = ' '.join(name.split()) if isinstance(name, str) else ''
        raw = next((row[k] for k in ('domain', 'company_website', 'website') if isinstance(row.get(k), str) and row[k].strip()), '')
        host = urlsplit(raw.strip() if '://' in raw else 'https://' + raw.strip()).hostname or ''
        try:
            domain = normalize_domain(host)
        except PolicyError:
            continue
        domain = domain[4:] if domain.startswith('www.') else domain
        if len(name.split()) < 2 or len(name) > 120 or any(ord(c) < 32 for c in name):
            continue
        key = (name.lower(), domain)
        if key in seen:
            continue
        seen.add(key)
        payload = {'name': name, 'domain': domain, 'domain_evidence_url': f'https://{domain}/'}
        if row.get('lead_id'):
            payload['lead_id'] = str(row['lead_id'])
        jobs.append({'provider': 'deepline', 'operation': 'find', 'payload': payload})
    return jobs


def pdl_enrich_jobs(prospects):
    """One People Data Labs person_enrich job per prospect: LinkedIn profile, else name + company.

    Prospects that already have a LinkedIn URL and title are still eligible (PDL returns the
    current employer), but rows lacking both a LinkedIn URL and a name + company are skipped.
    """
    jobs, seen = [], set()
    for row in prospects:
        if not isinstance(row, dict):
            continue
        if row.get('linkedin_url'):
            payload = {'linkedin_url': row['linkedin_url']}
        elif row.get('full_name') and (row.get('company_website') or row.get('company')):
            payload = {'name': row['full_name']}
            if row.get('company_website'):
                payload['domain'] = row['company_website']
            else:
                payload['company'] = row['company']
        else:
            continue
        key = tuple(sorted((k, str(v).casefold()) for k, v in payload.items()))
        if key in seen:
            continue
        seen.add(key)
        if row.get('lead_id'):
            payload['lead_id'] = str(row['lead_id'])
        jobs.append({'provider': 'pdl', 'operation': 'person_enrich', 'payload': payload})
    return jobs


def encode_html(text):
    """Compact, redact-safe storage for scraped HTML: zlib + unpadded urlsafe base64.

    The ledger's redact() replaces any string containing '://' that is not an evidence URL,
    which would erase raw HTML; this alphabet has no ':', '=' or whitespace, so it survives.
    """
    return base64.urlsafe_b64encode(zlib.compress(text.encode('utf-8'), 6)).decode('ascii').rstrip('=')


def decode_html(value, limit=FIRECRAWL_MAX_HTML):
    """Inverse of encode_html; None for anything malformed or larger than ``limit`` bytes."""
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]*', value):
        return None
    try:
        raw = base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
        inflater = zlib.decompressobj()
        data = inflater.decompress(raw, limit + 1)
        if len(data) > limit or inflater.unconsumed_tail:
            return None
        return data.decode('utf-8', 'replace')
    except (ValueError, zlib.error):
        return None


def scraped_html(outcome):
    """The HTML of a Firecrawl 'scrape' outcome (Outcome or stored dict), or None."""
    fields = getattr(outcome, 'fields', None)
    if fields is None and isinstance(outcome, dict):
        fields = outcome.get('fields')
    if not isinstance(fields, dict):
        return None
    return decode_html(fields.get('html_zb64'))


def scraped_pages(store):
    """[{url, requested_url, html, page_status, truncated, attempt_id, observed_at}] from completed
    Firecrawl scrapes, ready for extract.extract_people(html, url). Missing pages are omitted."""
    pages = []
    for attempt in store.attempts():
        outcome = attempt.get('outcome') or {}
        if attempt.get('provider') != 'firecrawl' or attempt.get('operation') != 'scrape' \
                or attempt.get('state') not in ('completed', 'reviewed') or outcome.get('error') \
                or outcome.get('assessment') != 'results':
            continue
        html_text = scraped_html(outcome)
        if html_text is None:
            continue
        fields = outcome.get('fields') or {}
        requested = fields.get('requested_url') or (attempt.get('payload') or {}).get('url')
        pages.append({'url': fields.get('url') or requested, 'requested_url': requested, 'html': html_text,
                      'page_status': fields.get('page_status'), 'truncated': fields.get('truncated') is True,
                      'attempt_id': attempt['id'], 'observed_at': attempt.get('finished_at') or attempt.get('sent_at')})
    return pages


def _merge(target, row):
    if row.get('name_complete') is True and target.get('name_complete') is False:
        # A full name from enrichment replaces a partial (obfuscated) search name.
        target['name_complete'] = True
        target['last_name_obfuscated'] = None
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
    for key in ('apollo_id', 'prospeo_id'):
        if row.get(key):
            keys.append((key, row[key]))
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
    if row.get('place_id'):
        return [('place', row['place_id'])]
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


_FINDER_VERIFICATION = ('valid', 'accept_all', 'unknown', 'invalid')


def _finder_prospect(attempt, outcome):
    """A prospect from a finder/enrichment attempt: its candidate email plus known identity.

    'not_found' enrichments still contribute a completed identity (e.g. Apollo full name);
    plain finder misses contribute nothing.
    """
    fields = outcome.get('fields') or {}
    base = fields.get('prospect') if isinstance(fields.get('prospect'), dict) else {}
    assessment = outcome.get('assessment')
    if assessment != 'candidate' and not (assessment in ('not_found', 'enriched') and base and attempt['operation'] != 'find'):
        return None
    payload = attempt.get('payload') or {}
    row = {k: v for k, v in base.items() if v not in (None, '', [])}
    name = payload.get('name') if isinstance(payload.get('name'), str) else None
    if not row.get('full_name') and name:
        row['full_name'] = ' '.join(name.split())
    if not row.get('company_website') and payload.get('domain'):
        row['company_website'] = payload['domain']
    for key, target in (('linkedin_url', 'linkedin_url'), ('apollo_id', 'apollo_id'), ('person_id', 'prospeo_id')):
        if payload.get(key) and not row.get(target):
            row[target] = payload[key]
    if payload.get('lead_id'):
        row.setdefault('lead_id', str(payload['lead_id']))
    if assessment == 'candidate' and outcome.get('email'):
        row['email'] = outcome['email']
        row['email_source'] = attempt['provider'] + '_finder'
        status = row.get('email_verification') or fields.get('finder_verification_status')
        row['email_verification'] = status if status in _FINDER_VERIFICATION else None
        if outcome.get('risk_flags'):
            row['email_risk_flags'] = list(outcome['risk_flags'])
    row.setdefault('source', attempt['provider'])
    if not (row.get('full_name') or row.get('linkedin_url') or row.get('email') or row.get('apollo_id') or row.get('prospeo_id')):
        return None
    return row


def collect(store):
    """Flatten completed lead-source attempts into deduplicated prospects and companies.

    Each row carries provider, attempt_id and observed_at, plus a 'sources' list when
    merged from several attempts. Prospects dedupe by linkedin_url or email; companies by domain.
    """
    prospects, companies = [], []
    for attempt in store.attempts():
        outcome = attempt.get('outcome') or {}
        if attempt.get('state') not in ('completed', 'reviewed') or outcome.get('error'):
            continue
        meta = {'provider': attempt['provider'], 'attempt_id': attempt['id'],
                'observed_at': attempt.get('finished_at') or attempt.get('sent_at')}
        source = {'provider': attempt['provider'], 'attempt_id': attempt['id'], 'operation': attempt['operation']}
        if attempt.get('operation') in FINDER_OPERATIONS:
            row = _finder_prospect(attempt, outcome)
            if row:
                prospects.append({**row, **meta, 'sources': [source]})
            continue
        if attempt.get('operation') not in DISCOVERY_OPERATIONS or outcome.get('assessment') != 'results':
            continue
        fields = outcome.get('fields') or {}
        for row in fields.get('prospects') or []:
            if isinstance(row, dict):
                prospects.append({**row, **meta, 'sources': [source]})
        for batch in fields.get('batches') or []:  # Serper mini-batch: prospects per query
            if not isinstance(batch, dict) or batch.get('error'):
                continue
            for row in batch.get('prospects') or []:
                if isinstance(row, dict):
                    prospects.append({**row, **meta, 'sources': [{**source, 'query': batch.get('query')}]})
        for row in fields.get('companies') or []:
            if isinstance(row, dict):
                companies.append({**row, **meta, 'sources': [source]})
        if attempt['operation'] == 'domain_search' and fields.get('domain'):
            companies.append({'domain': fields['domain'], 'organization': fields.get('organization'),
                              'email_pattern': fields.get('pattern'), 'accept_all': fields.get('accept_all'),
                              **meta, 'sources': [source]})
    return {'prospects': _dedupe(prospects, _prospect_keys), 'companies': _dedupe(companies, _company_keys)}
