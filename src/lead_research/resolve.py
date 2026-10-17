"""Firm name -> company website, from web-search results (Serper / Brave jobs through the ledger).

Public records give a firm name and a place but no website. This module builds one deterministic
search query per firm, turns queries into the existing ``search`` job format (so every request still
goes through dry-run planning, confirmed budgets and the per-input cache), and picks the firm's own
domain from organic results, skipping portals, directories, social sites and franchise parents.
Nothing here touches the network except ``verify_domain`` through a caller-supplied fetcher.
"""
from __future__ import annotations

import html
import re
import socket
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from .discovery import SEARCH_PROVIDERS, search_jobs, serper_batch_jobs
from .models import PolicyError
from .policy import MAX_BATCH_QUERIES, US_STATES, normalize_job, search_query
from .quality import (FRANCHISE_PARENTS, _brand_parent_hosts, normalize_host, website_matches_company,
                      website_problem)

__all__ = ['DIRECTORY_DOMAINS', 'firm_core', 'resolution_query', 'resolution_queries', 'resolution_jobs',
           'pick_domain', 'verify_domain', 'resolve_from_store', 'company_key', 'resolution_batch_jobs',
           'exa_resolution_query', 'exa_resolution_jobs', 'resolve_from_hunter', 'firm_name_key',
           'searched_queries', 'guess_domains', 'verify_guess', 'resolve_by_guess']

DEFAULT_THRESHOLD = 0.5

# Portals, directories, data brokers and social sites: never a firm's own website.
DIRECTORY_DOMAINS = frozenset({
    'zillow.com', 'realtor.com', 'trulia.com', 'redfin.com', 'homes.com', 'yelp.com', 'facebook.com',
    'linkedin.com', 'instagram.com', 'bbb.org', 'mapquest.com', 'yellowpages.com', 'loopnet.com', 'crexi.com',
    'costar.com', 'opencorporates.com', 'bizapedia.com', 'buzzfile.com', 'dnb.com', 'manta.com',
    'chamberofcommerce.com', 'nextdoor.com', 'google.com', 'apple.com', 'youtube.com', 'twitter.com', 'x.com',
    'tiktok.com', 'pinterest.com', 'threads.net', 'homesnap.com', 'movoto.com', 'har.com', 'realtor.org',
    'nar.realtor', 'alignable.com', 'zoominfo.com', 'rocketreach.co', 'apollo.io', 'crunchbase.com',
    'bloomberg.com', 'sec.gov', 'opengovus.com', 'opengovny.com', 'corporationwiki.com', 'bizprofile.net',
    'angi.com', 'houzz.com', 'thumbtack.com', 'wikipedia.org', 'apartments.com', 'rent.com', 'zumper.com',
    'hotpads.com', 'realtytrac.com', 'fastpeoplesearch.com', 'truepeoplesearch.com', 'spokeo.com',
    'whitepages.com', 'superpages.com', 'citysearch.com', 'foursquare.com', 'merchantcircle.com',
    'hometownlocator.com', 'birdeye.com', 'porch.com', 'expertise.com', 'fastexpert.com', 'homelight.com',
    'upnest.com', 'clever.com', 'listwithclever.com', 'rate-my-agent.com', 'ratemyagent.com', 'trec.texas.gov',
    'texas.gov', 'ny.gov', 'colorado.gov', 'myfloridalicense.com', 'sunbiz.org', 'indeed.com', 'glassdoor.com',
    'ziprecruiter.com', 'mls.com', 'remine.com', 'propertyshark.com', 'showcase.com', 'cityfeet.com',
    'rentals.com', 'realestate.com', 'century21global.com', 'kvcore.com', 'zipcodes.com', 'issuu.com',
    'prnewswire.com', 'businesswire.com', 'reddit.com', 'quora.com', 'medium.com', 'yahoo.com', 'bing.com',
    'mapsus.net', 'cylex.us.com', 'n49.com', 'brownbook.net', 'hotfrog.com', 'yellowbook.com', 'local.com',
    'dexknows.com', 'ezlocal.com', 'storeboard.com', 'bisprofiles.com', 'emis.com', 'pitchbook.com',
    'signalhire.com', 'lusha.com', 'contactout.com', 'datanyze.com', 'leadiq.com', 'seamless.ai',
})

_LEGAL = re.compile(r'(?i)(?:,?\s+(?:l\.?\s?l\.?\s?c|l\.?\s?l\.?\s?p|p\.?\s?l\.?\s?l\.?\s?c|inc|incorporated|corp|'
                    r'corporation|co|ltd|limited|lp|l\.p|lllp|pc|p\.c|pa|p\.a|company)\.?)+\s*$')
_LEGAL_WORDS = frozenset({'llc', 'l', 'c', 'inc', 'incorporated', 'corp', 'corporation', 'co', 'ltd', 'limited', 'lp',
                          'llp', 'lllp', 'pllc', 'pc', 'pa', 'company', 'dba'})
_GENERIC = frozenset({'real', 'estate', 'realty', 'group', 'properties', 'property', 'homes', 'home', 'company',
                      'the', 'and', 'of', 'services', 'team', 'llc', 'inc', 'brokerage', 'brokers', 'broker',
                      'partners', 'associates', 'management', 'investments', 'commercial', 'residential',
                      'holdings', 'international', 'solutions', 'advisors', 'capital', 'development', 'co',
                      'realtors', 'agency', 'land', 'texas', 'tx', 'ny', 'new', 'york', 'colorado', 'florida',
                      'usa', 'us', 'america', 'american', 'national', 'city', 'county', 'llp', 'lp', 'corp'})


def _fold(text):
    return re.findall(r'[a-z0-9]+', (text or '').lower().replace("'", '').replace('’', ''))


def firm_core(name):
    """Brand used for search: the DBA part when present, legal suffixes and punctuation removed."""
    text = ' '.join(str(name or '').split())
    parts = re.split(r'(?i)\s+(?:d/b/a|dba|d\.b\.a\.?|a/k/a|aka)[:\s]+', text, maxsplit=1)
    brand = parts[-1] if len(parts) > 1 and parts[-1].strip() else parts[0]
    brand = re.sub(r'\([^)]*\)', ' ', brand)
    brand = _LEGAL.sub('', brand).strip(' ,.-&')
    brand = re.sub(r'["“”]', '', brand)
    if not [t for t in _fold(brand) if t not in _LEGAL_WORDS]:
        return ''
    return ' '.join(brand.split())


def company_key(company):
    """Stable identity for a records company (source + license number, else name + state)."""
    source = company.get('source') or ''
    number = company.get('license_number') or ''
    if number:
        return '%s:%s' % (source, number)
    return '%s:%s|%s' % (source, ' '.join(_fold(company.get('organization'))), company.get('state') or '')


def resolution_query(company):
    """'"Omada Real Estate" Bexar County Texas real estate' (TX has county only) or
    '"Corra Realty Group" New York NY real estate'."""
    firm = firm_core(company.get('organization') or company.get('company') or '')
    if not firm or not re.search(r'[A-Za-z]', firm):
        return ''
    state = (company.get('state') or '').upper()
    city = ' '.join(str(company.get('city') or '').split())
    county = ' '.join(str(company.get('county') or '').split())
    county = re.sub(r'(?i)\s+county$', '', county)
    if company.get('source') == 'tx-trec' or (state == 'TX' and not city):
        place = '%s County Texas' % county if county else 'Texas'
    elif city:
        place = '%s %s' % (city, state) if state else city
    elif county and state:
        place = '%s County %s' % (county, US_STATES.get(state, state))
    else:
        place = US_STATES.get(state, state)
    query = ('"%s" %s real estate' % (firm, place)).strip()
    query = ' '.join(query.split())
    try:
        return search_query(query)
    except PolicyError:
        return ''


def exa_resolution_query(company):
    """Natural-language Exa 'company' query (Exa ignores quotes/operators):
    'Harborlight Homes real estate company in Bexar County, Texas'."""
    firm = firm_core(company.get('organization') or company.get('company') or '')
    if not firm or not re.search(r'[A-Za-z]', firm):
        return ''
    state = (company.get('state') or '').upper()
    city = ' '.join(str(company.get('city') or '').split())
    county = re.sub(r'(?i)\s+county$', '', ' '.join(str(company.get('county') or '').split()))
    state_name = US_STATES.get(state, state)
    if city:
        place = '%s, %s' % (city, state_name) if state_name else city
    elif county:
        place = '%s County, %s' % (county, state_name) if state_name else '%s County' % county
    else:
        place = state_name
    query = ' '.join(('%s real estate company%s' % (firm, ' in ' + place if place else '')).split())
    try:
        return search_query(query)
    except PolicyError:
        return ''


def _query_for(provider):
    return exa_resolution_query if provider == 'exa' else resolution_query


def resolution_queries(companies):
    """[(company_key, query)] in input order, one per distinct company with a usable query."""
    out, seen = [], set()
    for company in companies:
        key = company_key(company)
        query = resolution_query(company)
        if not query or key in seen:
            continue
        seen.add(key)
        out.append((key, query))
    return out


def resolution_jobs(companies, provider='serper'):
    """First-page 'search' jobs (discovery.search_jobs format), validated by policy.

    provider: 'serper', 'brave', 'tavily', 'serpapi' or 'firecrawl' (single searches whose
    stored results rows carry 'link'), or 'exa' for Exa 'company_search' jobs (see
    exa_resolution_jobs). resolve_from_store(provider=...) reads the same provider's answers.
    """
    if provider == 'exa':
        return exa_resolution_jobs(companies)
    if provider not in SEARCH_PROVIDERS:
        raise PolicyError('unsupported_resolution_provider')
    queries = list(dict.fromkeys(q for _, q in resolution_queries(companies)))
    jobs = search_jobs(provider, queries, pages=1)
    if provider == 'brave':
        for job in jobs:
            job['payload']['count'] = 10
    for job in jobs:
        normalize_job(job)  # raises PolicyError on anything the ledger would refuse
    return jobs


def exa_resolution_jobs(companies):
    """One Exa 'company_search' job per distinct company query (category 'company', 10 results)."""
    queries = []
    for company in companies:
        query = exa_resolution_query(company)
        if query and query not in queries:
            queries.append(query)
    jobs = [{'provider': 'exa', 'operation': 'company_search', 'payload': {'query': q}} for q in queries]
    for job in jobs:
        normalize_job(job)
    return jobs


def searched_queries(store, provider='serper'):
    """Queries that already have a usable completed answer (single search, batch entry, or Exa)."""
    return set(_answers_by_query(store, provider))


def resolution_batch_jobs(companies, size=MAX_BATCH_QUERIES, store=None):
    """Serper mini-batch jobs for firm resolution: up to ``size`` (<= 100) queries per request.

    The queries are exactly resolution_query() strings, so resolve_from_store matches the batch
    entries back to firms. With a ``store``, queries already answered (by a single search or an
    earlier batch entry) are left out so no query is billed twice.
    """
    done = searched_queries(store, 'serper') if store is not None else set()
    queries = [q for q in dict.fromkeys(q for _, q in resolution_queries(companies)) if q not in done]
    jobs = serper_batch_jobs(queries, size=size) if queries else []
    for job in jobs:
        normalize_job(job)
    return jobs


def _host(link):
    try:
        host = (urlsplit(link).hostname or '').lower() if isinstance(link, str) else ''
    except ValueError:
        return ''
    return host


def _blocked(host, domain, company):
    """Reason the result host can't be the firm's own website, else ''."""
    for blocked in DIRECTORY_DOMAINS:
        if host == blocked or host.endswith('.' + blocked):
            return 'directory'
    if host.endswith(('.gov', '.mil', '.edu')) or '.gov.' in host:
        return 'government'
    if domain in FRANCHISE_PARENTS and domain not in _brand_parent_hosts(company):
        return 'franchise_parent'
    problem = website_problem(domain, company)
    return problem or ''


def _token_weights(name):
    tokens = [t for t in _fold(name) if len(t) >= 2]
    if not tokens:
        return {}
    distinctive = [t for t in tokens if t not in _GENERIC]
    weights = {}
    for t in tokens:
        weights[t] = 1.0 if (t in distinctive or not distinctive) else 0.25
    return weights


def _label(domain):
    return domain.split('.')[0].replace('-', '') if domain else ''


def _score(company, domain, title, snippet):
    firm = firm_core(company.get('organization') or '')
    legal = company.get('organization') or ''
    weights = _token_weights(firm)
    if not weights:
        return 0.0, []
    total = sum(weights.values())
    label = _label(domain)
    reasons = []
    in_label = [t for t in weights if len(t) >= 3 and t in label]
    domain_score = sum(weights[t] for t in in_label) / total
    squashed = ''.join(t for t in _fold(firm))
    if label and (label == squashed or (len(label) >= 5 and label in squashed)):
        domain_score = 1.0
    if domain_score == 0 and (website_matches_company(domain, firm) or website_matches_company(domain, legal)):
        domain_score = 0.5  # acronym / abbreviation spelled from the firm name
        reasons.append('label_spells_firm')
    if domain_score:
        reasons.append('domain:%s' % ','.join(in_label) if in_label else 'domain:whole_name')
    title_words = set(_fold(title))
    in_title = [t for t in weights if t in title_words]
    title_score = sum(weights[t] for t in in_title) / total
    if in_title:
        reasons.append('title:%d/%d' % (len(in_title), len(weights)))
    text = ' '.join(_fold((title or '') + ' ' + (snippet or '')))
    place = 0.0
    city = ' '.join(_fold(company.get('city')))
    county = ' '.join(_fold(re.sub(r'(?i)\s+county$', '', company.get('county') or '')))
    state = (company.get('state') or '').upper()
    if city and (' ' + city + ' ') in (' ' + text + ' '):
        place += 0.1
        reasons.append('city')
    elif county and (' ' + county + ' ') in (' ' + text + ' '):
        place += 0.08
        reasons.append('county')
    state_name = ' '.join(_fold(US_STATES.get(state, '')))
    if state and ((' ' + state.lower() + ' ') in (' ' + text + ' ') or (state_name and state_name in text)):
        place += 0.05
        reasons.append('state')
    if domain_score == 0:
        return 0.0, reasons  # the firm's name must show up in its own domain
    return min(1.0, 0.6 * domain_score + 0.3 * title_score + place), reasons


def pick_domain(company, results, threshold=DEFAULT_THRESHOLD):
    """Best own-website domain among organic results, or None when nothing clears ``threshold``.

    ``results`` rows look like {'title', 'link', 'snippet'} (the stored Serper/Brave outcome shape).
    """
    best = None
    seen = set()
    for rank, row in enumerate(results or []):
        if not isinstance(row, dict):
            continue
        link = row.get('link') or row.get('url')  # Serper/Brave 'link', Exa company rows 'url'
        host = _host(link)
        domain = normalize_host(host) if host else ''
        if not domain or domain in seen:
            continue
        seen.add(domain)
        if _blocked(host, domain, company.get('organization') or ''):
            continue
        score, reasons = _score(company, domain, row.get('title') or '', row.get('snippet') or '')
        score = round(max(0.0, score - 0.01 * rank), 3)  # earlier organic positions break ties
        if best is None or score > best['confidence']:
            best = {'domain': domain, 'confidence': score, 'reason': ';'.join(reasons) or 'weak',
                    'link': link}
    if best is None or best['confidence'] < threshold:
        return None
    return best


_TITLE = re.compile(r'<title[^>]*>(.*?)</title>', re.I | re.S)
_META = re.compile(r'<meta\s[^>]*>', re.I)
_ATTR = re.compile(r'''([a-zA-Z:_-]+)\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)''')


def _page_parts(text):
    text = text or ''
    match = _TITLE.search(text[:200000])
    title = html.unescape(' '.join(match.group(1).split())) if match else ''
    site_name = ''
    for tag in _META.findall(text[:200000]):
        attrs = {k.lower(): v.strip('"\'') for k, v in _ATTR.findall(tag)}
        if attrs.get('property', '').lower() in ('og:site_name', 'og:title') or attrs.get('name', '').lower() in (
                'application-name', 'og:site_name'):
            site_name += ' ' + html.unescape(attrs.get('content', ''))
    body = re.sub(r'(?is)<(script|style|noscript)[^>]*>.*?</\1>', ' ', text[:500000])
    body = html.unescape(re.sub(r'<[^>]+>', ' ', body))
    return title, site_name.strip(), ' '.join(body.split())


def verify_domain(company, domain, fetcher):
    """Fetch the homepage via a SafeFetcher-compatible object and look for the firm's name tokens.

    Returns ``(ok, evidence)``. ok needs half (at least one) of the distinctive tokens in the
    <title>/og:site_name, or all of them in the visible body text, and the final URL must stay on
    the same registrable domain.
    """
    evidence = {'url': 'https://%s/' % domain, 'final_url': None, 'matched': [], 'where': None, 'reason': None}
    try:
        result = fetcher.get(evidence['url'])
    except Exception:  # noqa: BLE001 - fetchers report errors in the result, never raise details
        evidence['reason'] = 'fetch_error'
        return False, evidence
    if not getattr(result, 'ok', False):
        evidence['reason'] = getattr(result, 'error', None) or 'fetch_failed'
        return False, evidence
    final = getattr(result, 'final_url', None) or evidence['url']
    evidence['final_url'] = final
    if normalize_host(_host(final)) != domain:
        evidence['reason'] = 'redirected_off_domain'
        return False, evidence
    weights = _token_weights(firm_core(company.get('organization') or ''))
    distinctive = [t for t, w in weights.items() if w == 1.0]
    if not distinctive:
        evidence['reason'] = 'no_name_tokens'
        return False, evidence
    title, site_name, body = _page_parts(getattr(result, 'text', '') or '')
    head_words = set(_fold(title + ' ' + site_name))
    body_words = set(_fold(body))
    in_head = [t for t in distinctive if t in head_words]
    need = max(1, (len(distinctive) + 1) // 2)
    if len(in_head) >= need:
        evidence.update(matched=in_head, where='title')
        return True, evidence
    in_body = [t for t in distinctive if t in body_words]
    if len(in_body) == len(distinctive):
        evidence.update(matched=in_body, where='body')
        return True, evidence
    evidence.update(matched=sorted(set(in_head) | set(in_body)), reason='name_not_on_homepage')
    return False, evidence


_RESOLUTION_OPERATIONS = {'exa': ('company_search',), 'serper': ('search', 'search_batch')}


def _answers_by_query(store, provider):
    """{query: [(attempt, results)]} from completed single, batch and Exa company searches."""
    by_query = {}
    operations = _RESOLUTION_OPERATIONS.get(provider, ('search',))
    for attempt in store.attempts():
        if attempt.get('provider') != provider or attempt.get('operation') not in operations \
                or attempt.get('state') not in ('completed', 'reviewed'):
            continue
        outcome = attempt.get('outcome') or {}
        if outcome.get('error') or outcome.get('assessment') not in ('results', 'not_found'):
            continue
        fields = outcome.get('fields') or {}
        if attempt['operation'] == 'search_batch':
            sent = (attempt.get('payload') or {}).get('queries') or []
            for entry in fields.get('batches') or []:
                # Only entries keyed to a query that was actually sent in this attempt.
                if not isinstance(entry, dict) or entry.get('error') or entry.get('query') not in sent:
                    continue
                by_query.setdefault(entry['query'], []).append((attempt, entry.get('results') or []))
            continue
        query = (attempt.get('payload') or {}).get('query')
        if isinstance(query, str):
            by_query.setdefault(query, []).append((attempt, fields.get('results') or []))
    return by_query


def resolve_from_store(store, companies, provider='serper', *, threshold=DEFAULT_THRESHOLD, fetcher=None):
    """Attach company_website + resolution evidence from completed search attempts in the Store.

    An attempt matches a company when its payload query equals the company's resolution query (the
    same string ``resolution_jobs`` submits, so policy's cache identity matches too). For Serper,
    mini-batch ('search_batch') entries count too, matched by their query string. provider 'exa'
    reads 'company_search' attempts keyed by exa_resolution_query. Companies with no completed
    search or no confident domain are returned with company_website ''. When a ``fetcher`` is
    given, picked domains must also pass ``verify_domain``.
    """
    by_query = _answers_by_query(store, provider)
    query_for = _query_for(provider)
    out = []
    for company in companies:
        row = dict(company)
        query = query_for(company)
        row.update(company_website='', resolution={'query': query, 'status': 'not_searched'})
        matches = by_query.get(query) if query else None
        if matches:
            results = [r for _, attempt_results in matches for r in attempt_results if isinstance(r, dict)]
            pick = pick_domain(company, results, threshold)
            attempt_ids = [a['id'] for a, _ in matches]
            if pick is None:
                row['resolution'] = {'query': query, 'status': 'no_confident_domain', 'attempt_ids': attempt_ids}
            else:
                resolution = {'query': query, 'status': 'resolved', 'domain': pick['domain'],
                              'confidence': pick['confidence'], 'reason': pick['reason'],
                              'evidence_link': pick.get('link'), 'attempt_ids': attempt_ids,
                              'provider': provider}
                if fetcher is not None:
                    ok, evidence = verify_domain(company, pick['domain'], fetcher)
                    resolution['verified'] = ok
                    resolution['verification'] = evidence
                    if not ok:
                        resolution['status'] = 'verification_failed'
                if resolution['status'] == 'resolved':
                    row['company_website'] = pick['domain']
                row['resolution'] = resolution
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Free resolution from Hunter Discover (organization.name batches).
# ---------------------------------------------------------------------------

_NAME_FILLER = frozenset({'the', 'and'})


def firm_name_key(name):
    """Near-exact firm-name key: firm_core, case/punctuation folded, '&'/'and'/'the' ignored.

    'Corraway Realty Group, LLC' == 'The Corraway Realty Group' == 'corraway realty group'.
    """
    core = firm_core(name)
    tokens = [t for t in _fold(core.replace('&', ' and ')) if t not in _NAME_FILLER]
    return ''.join(tokens)


def _attempt_state(filters):
    """The single US state a Discover attempt was filtered to, else None."""
    hq = (filters or {}).get('headquarters_location') or {}
    states = {loc.get('state') for loc in hq.get('include') or [] if isinstance(loc, dict)}
    if len(states) == 1 and None not in states:
        return next(iter(states))
    return None


def resolve_from_hunter(store, companies):
    """Attach company_website from free Hunter Discover results matched by firm name.

    Every completed Hunter 'discover' attempt contributes its (organization, domain) pairs; an
    attempt filtered to exactly one US state labels its companies with that state. A company
    matches when firm_name_key(organization) is equal on both sides and, when both states are
    known, the states agree. Nothing is guessed: a key shared by two input firms, or one key
    mapping to two different domains, is skipped as ambiguous; directory, franchise-parent and
    other non-own domains are skipped too. Rows that already carry a company_website are
    returned unchanged. Resolved rows get resolution {'method': 'hunter_discover_name',
    'status': 'resolved', 'domain', 'attempt_id', 'matched_organization', 'state_checked'}.
    """
    observed = {}
    for attempt in store.attempts():
        if attempt.get('provider') != 'hunter' or attempt.get('operation') != 'discover' \
                or attempt.get('state') not in ('completed', 'reviewed'):
            continue
        outcome = attempt.get('outcome') or {}
        if outcome.get('error') or outcome.get('assessment') != 'results':
            continue
        state = _attempt_state((attempt.get('payload') or {}).get('filters'))
        for row in (outcome.get('fields') or {}).get('companies') or []:
            if not isinstance(row, dict) or not row.get('domain') or not row.get('organization'):
                continue
            key = firm_name_key(row['organization'])
            domain = normalize_host(str(row['domain']))
            if key and domain:
                observed.setdefault(key, []).append({'domain': domain, 'state': state, 'attempt_id': attempt['id'],
                                                     'organization': row['organization']})
    by_key = {}
    for company in companies:
        if company.get('company_website'):
            continue
        key = firm_name_key(company.get('organization') or company.get('company') or '')
        if key:
            firms = by_key.setdefault(key, {})
            firms.setdefault(company_key(company), (company.get('state') or '').upper() or None)
    out = []
    for company in companies:
        row = dict(company)
        if company.get('company_website'):
            out.append(row)
            continue
        key = firm_name_key(company.get('organization') or company.get('company') or '')
        state = (company.get('state') or '').upper() or None
        resolution = {'method': 'hunter_discover_name', 'status': 'no_match'}
        row['company_website'] = ''
        # Other input firms with the same name key; distinct known states can still be told apart.
        others = [s for k, s in (by_key.get(key) or {}).items() if k != company_key(company)]
        if not key:
            resolution['status'] = 'no_firm_name'
        elif any(not state or not other or other == state for other in others):
            resolution['status'] = 'ambiguous_firm_name'
        elif key in observed:
            seen = observed[key]
            confirmed = [h for h in seen if state and h['state'] == state]
            elsewhere = [h for h in seen if state and h['state'] and h['state'] != state]
            unlabelled = [h for h in seen if not h['state']]
            if confirmed:
                hits = confirmed
            elif others:  # the same name exists in another state: only a state-labelled result counts
                hits = []
            elif elsewhere and unlabelled:  # an unlabelled result might be the other-state firm
                hits = elsewhere + unlabelled
            else:
                hits = unlabelled if state else seen
            domains = sorted({h['domain'] for h in hits})
            if len(domains) > 1:
                resolution.update(status='ambiguous_domain', candidates=domains)
            elif domains:
                hit = next(h for h in hits if h['domain'] == domains[0])
                organization = company.get('organization') or company.get('company') or ''
                # Franchise parents (kw.com, remax.com, ...) are never a franchisee's own website here.
                if hit['domain'] in FRANCHISE_PARENTS or _blocked(hit['domain'], hit['domain'], organization):
                    resolution.update(status='blocked_domain', domain=hit['domain'])
                else:
                    row['company_website'] = hit['domain']
                    resolution.update(status='resolved', domain=hit['domain'], attempt_id=hit['attempt_id'],
                                      attempt_ids=sorted({h['attempt_id'] for h in hits}),
                                      matched_organization=hit['organization'],
                                      state_checked=bool(state and any(h['state'] == state for h in hits)))
            else:
                resolution['status'] = 'state_mismatch'
        row['resolution'] = resolution
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Free resolution by guessing the domain from the firm name (no search spend).
# ---------------------------------------------------------------------------

# Trailing words a firm often leaves out of its domain ('Harbor Realty Group' -> harborrealty.com).
_DROPPABLE_TAIL = frozenset({'group', 'realty', 'properties', 'property', 'homes', 'company', 'team', 'partners',
                             'associates', 'services', 'brokerage', 'brokers', 'realtors', 'agency', 'management',
                             'investments', 'holdings', 'advisors', 'solutions', 'international', 'residential',
                             'commercial', 'development'})
# Words that already make a name read as real estate, so no extra 'realty' try is needed.
_RE_WORDS = frozenset({'realty', 'properties', 'property', 'homes', 'home', 'realtors', 'brokerage', 'brokers', 'broker'})
_GUESS_TLDS = ('net', 'us', 'co')
MAX_GUESSES = 8
_MIN_LABEL = 5  # shorter labels ('abc.com') are almost always somebody else's brand
# 'kellerwilliamsrealty.net' is never a franchisee's own site; leave those firms to search.
_FRANCHISE_LABELS = tuple(sorted({d.split('.')[0] for d in FRANCHISE_PARENTS if len(d.split('.')[0]) >= 6}
                                  - {'onereal', 'joinreal'} | {'remax'}))

# Parked, for-sale, placeholder and default-server pages: never a firm's website.
_PARKED = re.compile(
    r'(?i)domain (?:name )?(?:is |may be )?for sale|this domain may be for sale|buy this domain|'
    r'make an offer on this domain|(?:this )?(?:web ?page|domain) is parked|parked (?:free|domain|by)|'
    r'domain parking|parkingcrew|sedoparking|\bsedo\.com|hugedomains|\bdan\.com|afternic|\bbodis\.com|'
    r'undeveloped\.com|courtesy of godaddy|godaddy\.com/domains|this domain is registered|'
    r'domain has expired|domain (?:name )?has been registered|account (?:has been )?suspended|'
    r'welcome to nginx|apache2? (?:ubuntu )?default page|default web site page')
_COMING_SOON = re.compile(r'(?i)coming soon|under construction|launching soon')
_H1 = re.compile(r'<h1[^>]*>(.*?)</h1>', re.I | re.S)
_COPYRIGHT = re.compile(r'(?:©|&copy;|&#169;|\(c\)|copyright)\s*(?:\d{4}(?:\s*[-–]\s*\d{4})?)?[^<\n]{0,120}', re.I)


def _guess_tokens(name):
    text = firm_core(name).lower().replace('&', ' and ').replace('+', ' and ')
    return [t for t in _fold(text) if t not in ('the',) and t not in _LEGAL_WORDS]


def _labels_for(name):
    tokens = _guess_tokens(name)
    distinctive = [t for t in tokens if t not in _GENERIC and len(t) >= 2]
    if not distinctive:
        return []
    labels = []

    def add(parts):
        label = ''.join(parts)
        if len(label) >= _MIN_LABEL and len(label) <= 63 and label not in labels:
            labels.append(label)

    add(tokens)
    plain = [t for t in tokens if t != 'and']
    add(plain)
    tail = list(plain)
    while len(tail) > 1:
        if tail[-2:] == ['real', 'estate']:
            add(tail[:-2] + ['re'])
            tail = tail[:-2]
        elif tail[-1] in _DROPPABLE_TAIL:
            tail = tail[:-1]
        else:
            break
        if tail and tail != plain:
            if len(tail) == 1 and len(tail[0]) < 7:
                break  # a bare short word ('harbor.com') is rarely the firm
            add(tail)
    if not (set(plain) & _RE_WORDS) and 'estate' not in plain:
        add(tail + ['realty'])  # the single extra try for names without a real-estate word
    if len(plain) > 1 and len(plain) <= 3:
        add(['-'.join(plain)])
    return labels


def guess_domains(company, limit=MAX_GUESSES):
    """Ranked candidate domains (<= ``limit``) for a records firm, built from its name and DBA.

    .com variants come first; .net/.us/.co only for the top two labels. Generic labels (no
    distinctive word), portals, franchise parents and other non-own domains are left out.
    """
    names = [company.get('organization') or company.get('company') or '']
    if company.get('dba') and not str(company['dba']).upper().startswith('SEE '):
        names.insert(0, company['dba'])
    labels = []
    for name in names:
        for label in _labels_for(name):
            if label not in labels:
                labels.append(label)
    organization = company.get('organization') or company.get('company') or ''
    out = []

    def push(domain):
        host = normalize_host(domain)
        if host and host not in out and not _blocked(host, host, organization) and host not in FRANCHISE_PARENTS \
                and not host.replace('-', '').startswith(_FRANCHISE_LABELS):
            out.append(host)

    for label in labels:
        push(label + '.com')
    for label in labels[:2]:
        for tld in _GUESS_TLDS:
            push('%s.%s' % (label, tld))
    return out[:limit]


def _dns_exists(domain, dns):
    for host in (domain, 'www.' + domain):
        try:
            if dns(host, 443, type=socket.SOCK_STREAM):
                return True
        except (OSError, UnicodeError, ValueError):
            continue
    return False


def _brand_zones(text):
    """(zone name, folded text) for title, og/site-name meta, <h1> headings and © lines."""
    title, site_name, body = _page_parts(text)
    zones = [('title', title), ('site_name', site_name)]
    for match in _H1.findall((text or '')[:300000])[:5]:
        zones.append(('h1', html.unescape(re.sub(r'<[^>]+>', ' ', match))))
    for match in _COPYRIGHT.findall(body)[:5]:
        zones.append(('copyright', match))
    return [(name, ' '.join(value.split())) for name, value in zones if value and value.strip()], title, body


def _location_hit(company, body):
    """Which of city / county / state the homepage mentions ('' when none; None when company has no place)."""
    words = ' ' + ' '.join(_fold(body)) + ' '
    city = ' '.join(_fold(company.get('city')))
    county = ' '.join(_fold(re.sub(r'(?i)\s+county$', '', company.get('county') or '')))
    state = (company.get('state') or '').upper()
    if not (city or county or state):
        return None
    if city and ' %s ' % city in words:
        return 'city'
    if county and ' %s ' % county in words:
        return 'county'
    state_name = ' '.join(_fold(US_STATES.get(state, '')))
    if state_name and ' %s ' % state_name in words:
        return 'state'
    if state and re.search(r'(?:,|\s)\s*%s(?:\s+\d{5}|\b)' % re.escape(state), body):
        return 'state'
    return ''


def verify_guess(company, domain, fetcher):
    """Strict check that a guessed domain is the firm's own live website.

    Every distinctive firm-name word must appear in a brand zone (title, og:site_name, <h1> or
    the © line), the page must stay on ``domain``, must not be parked / for sale / a placeholder,
    and must mention the firm's city, county or state when the firm has one. Returns ``(ok, evidence)``.
    """
    url = 'https://%s/' % domain
    evidence = {'method': 'guess', 'url': url, 'final_url': None, 'matched': [], 'where': [], 'matched_text': None,
                'location': None, 'reason': None}
    try:
        result = fetcher.get(url)
    except Exception:  # noqa: BLE001 - fetchers report errors in the result
        evidence['reason'] = 'fetch_error'
        return False, evidence
    if not getattr(result, 'ok', False):
        evidence['reason'] = getattr(result, 'error', None) or 'fetch_failed'
        return False, evidence
    final = getattr(result, 'final_url', None) or url
    evidence['final_url'] = final
    if normalize_host(_host(final)) != domain:
        evidence['reason'] = 'redirected_off_domain'
        return False, evidence
    text = getattr(result, 'text', '') or ''
    zones, title, body = _brand_zones(text)
    if _PARKED.search(title + ' ' + body[:20000]) or title.lower().startswith('index of /'):
        evidence['reason'] = 'parked_or_for_sale'
        return False, evidence
    if len(body.split()) < 60 and _COMING_SOON.search(title + ' ' + body):
        evidence['reason'] = 'placeholder_page'
        return False, evidence
    names = [company.get('dba'), company.get('organization') or company.get('company')]
    best = None
    for name in names:
        if not name or str(name).upper().startswith('SEE '):
            continue
        distinctive = [t for t, w in _token_weights(firm_core(name)).items() if w == 1.0]
        if not distinctive:
            continue
        hits, where, sample = set(), [], None
        for zone, value in zones:
            words = set(_fold(value.replace('&', ' and ')))
            found = [t for t in distinctive if t in words]
            if found:
                hits.update(found)
                where.append(zone)
                if sample is None or len(found) == len(distinctive):
                    sample = value[:160]
        if len(hits) == len(distinctive):
            best = (distinctive, sorted(set(where)), sample)
            break
        if best is None and hits:
            evidence.update(matched=sorted(hits), where=sorted(set(where)))
    if best is None:
        evidence['reason'] = 'name_not_in_brand_zones'
        return False, evidence
    location = _location_hit(company, body)
    evidence.update(matched=best[0], where=best[1], matched_text=best[2], location=location)
    if location == '':
        evidence['reason'] = 'location_not_on_homepage'
        return False, evidence
    return True, evidence


def resolve_by_guess(companies, fetcher, *, dns=socket.getaddrinfo, max_candidates=6, workers=16):
    """Attach company_website by guessing the domain from the firm name and confirming the homepage.

    For each company (rows that already have a company_website are returned unchanged): build
    ``guess_domains``; skip candidates with no DNS record (``dns`` is getaddrinfo-compatible, pass
    None to skip the DNS precheck); fetch at most ``max_candidates`` homepages and stop at the first
    that passes ``verify_guess``. Resolved rows get resolution {'method': 'guess', 'status':
    'resolved', 'domain', 'evidence', 'tried'}; others {'method': 'guess', 'status': 'no_guess_match'
    | 'no_candidates', 'tried'}. Results keep input order.
    """
    def one(company):
        row = dict(company)
        if company.get('company_website'):
            return row
        row['company_website'] = ''
        candidates = guess_domains(company)
        if not candidates:
            row['resolution'] = {'method': 'guess', 'status': 'no_candidates', 'tried': []}
            return row
        tried, rejected = [], []
        for domain in candidates:
            if len(tried) >= max_candidates:
                break
            if dns is not None and not _dns_exists(domain, dns):
                continue
            tried.append(domain)
            ok, evidence = verify_guess(company, domain, fetcher)
            if ok:
                row['company_website'] = domain
                row['resolution'] = {'method': 'guess', 'status': 'resolved', 'domain': domain,
                                     'evidence': evidence, 'tried': tried}
                return row
            rejected.append({'domain': domain, 'reason': evidence.get('reason')})
        row['resolution'] = {'method': 'guess', 'status': 'no_guess_match', 'tried': tried, 'rejected': rejected}
        return row

    companies = list(companies)
    if not companies:
        return []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(companies)))) as pool:
        return list(pool.map(one, companies))

