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
from urllib.parse import urlsplit

from .discovery import xray_jobs
from .models import PolicyError
from .policy import US_STATES, normalize_job, search_query
from .quality import (FRANCHISE_PARENTS, _brand_parent_hosts, normalize_host, website_matches_company,
                      website_problem)

__all__ = ['DIRECTORY_DOMAINS', 'firm_core', 'resolution_query', 'resolution_queries', 'resolution_jobs',
           'pick_domain', 'verify_domain', 'resolve_from_store', 'company_key']

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
    """Search jobs (first page only) in the same format discovery.xray_jobs builds, validated by policy."""
    queries = list(dict.fromkeys(q for _, q in resolution_queries(companies)))
    jobs = xray_jobs(provider, queries, pages=1)
    if provider == 'brave':
        for job in jobs:
            job['payload']['count'] = 10
    for job in jobs:
        normalize_job(job)  # raises PolicyError on anything the ledger would refuse
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
        host = _host(row.get('link'))
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
                    'link': row.get('link')}
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


def resolve_from_store(store, companies, provider='serper', *, threshold=DEFAULT_THRESHOLD, fetcher=None):
    """Attach company_website + resolution evidence from completed search attempts in the Store.

    An attempt matches a company when its payload query equals the company's resolution query (the
    same string ``resolution_jobs`` submits, so policy's cache identity matches too). Companies with
    no completed search or no confident domain are returned with company_website ''. When a
    ``fetcher`` is given, picked domains must also pass ``verify_domain``.
    """
    by_query = {}
    for attempt in store.attempts():
        if attempt.get('provider') != provider or attempt.get('operation') != 'search' \
                or attempt.get('state') not in ('completed', 'reviewed'):
            continue
        outcome = attempt.get('outcome') or {}
        if outcome.get('error') or outcome.get('assessment') not in ('results', 'not_found'):
            continue
        query = (attempt.get('payload') or {}).get('query')
        if isinstance(query, str):
            results = (outcome.get('fields') or {}).get('results') or []
            by_query.setdefault(query, []).append((attempt, results))
    out = []
    for company in companies:
        row = dict(company)
        query = resolution_query(company)
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
