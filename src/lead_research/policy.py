"""Conservative input, exact accounting, and persistence boundaries."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl, urlsplit

from .models import PolicyError

LEGACY_PAYLOAD_KEYS = frozenset({'name', 'domain', 'domain_evidence_url', 'email', 'lead_id', 'provenance'})
LEGACY_OPERATIONS = frozenset({'verify', 'signals', 'find'})
# Lead-source operations return lists. Each declares the only payload keys it accepts.
DISCOVERY_PAYLOAD_KEYS = {
    'discover': frozenset({'filters'}),
    'domain_search': frozenset({'domain', 'limit', 'decision_maker', 'required_field', 'seniority', 'department'}),
    'search': frozenset({'query', 'num', 'page', 'count', 'offset'}),
    'places': frozenset({'area', 'state', 'tags'}),
}
DISCOVERY_OPERATIONS = frozenset(DISCOVERY_PAYLOAD_KEYS)
COMMON_PAYLOAD_KEYS = frozenset({'lead_id', 'provenance'})
PAYLOAD_KEYS = LEGACY_PAYLOAD_KEYS.union(*DISCOVERY_PAYLOAD_KEYS.values())
SECRET_KEY = re.compile(r'(password|passwd|secret|token|authorization|cookie|api.?key|credential|header|request|environment)', re.I)


def parse_units(value, *, positive=False):
    """Decimal strings become integer millionths, never binary floats."""
    if isinstance(value, (float, bool)):
        raise PolicyError('amount_must_be_decimal_string')
    try:
        amount = Decimal(str(value))
        scaled = amount * 1_000_000
        if not amount.is_finite() or scaled != scaled.to_integral_value() or amount < 0:
            raise ValueError
        units = int(scaled)
    except (InvalidOperation, ValueError, OverflowError):
        raise PolicyError('invalid_amount') from None
    if positive and units <= 0:
        raise PolicyError('positive_amount_required')
    return units


def format_units(units):
    return format(Decimal(units) / Decimal(1_000_000), 'f')


def normalize_email(email):
    if not isinstance(email, str):
        raise PolicyError('invalid_email')
    email = email.strip().lower()
    if len(email) > 254 or not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,63}", email):
        raise PolicyError('invalid_email')
    local, domain = email.rsplit('@', 1)
    if local.startswith('.') or local.endswith('.') or '..' in local:
        raise PolicyError('invalid_email')
    return local + '@' + normalize_domain(domain)


def normalize_domain(domain):
    if not isinstance(domain, str):
        raise PolicyError('invalid_domain')
    domain = domain.strip().lower().rstrip('.')
    try:
        domain = domain.encode('idna').decode('ascii')
        ipaddress.ip_address(domain)
    except ValueError:
        pass
    else:
        raise PolicyError('public_domain_required')
    if len(domain) > 253 or '.' not in domain or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in domain.split('.')):
        raise PolicyError('invalid_domain')
    if domain.endswith(('.local', '.localhost', '.internal', '.test', '.invalid')):
        raise PolicyError('public_domain_required')
    return domain


def validate_evidence_url(url):
    if not isinstance(url, str) or any(ord(c) < 33 for c in url):
        raise PolicyError('invalid_evidence_url')
    try:
        parts = urlsplit(url)
        if parts.scheme != 'https' or parts.username is not None or parts.password is not None or parts.port not in (None, 443):
            raise ValueError
        normalize_domain(parts.hostname or '')
        if parts.fragment or any(SECRET_KEY.search(k) or k.lower() in {'key', 'auth', 'signature', 'sig', 'code', 'state', 'session', 'sessionid', 'sid', 'jwt'} for k, _ in parse_qsl(parts.query, keep_blank_values=True)):
            raise ValueError
    except (ValueError, PolicyError):
        raise PolicyError('unsafe_evidence_url') from None
    return url


def redact(value):
    """Drop credential-shaped keys and redact URLs/free text containing secrets."""
    if isinstance(value, dict):
        return {str(k): redact(v) for k, v in value.items() if not SECRET_KEY.search(str(k))}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        if re.search(r'(?i)(bearer\s+|(?:api[_-]?key|token|password|secret|authorization)\s*[:=])', value):
            return '[redacted]'
        if '://' in value:
            try:
                validate_evidence_url(value)
            except PolicyError:
                return '[redacted]'
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def sanitize_payload(payload):
    clean = {k: v for k, v in dict(payload).items() if k in PAYLOAD_KEYS}
    if 'email' in clean:
        clean['email'] = normalize_email(clean['email'])
    if 'domain' in clean:
        clean['domain'] = normalize_domain(clean['domain'])
    if 'domain_evidence_url' in clean:
        clean['domain_evidence_url'] = validate_evidence_url(clean['domain_evidence_url'])
    if 'lead_id' in clean:
        clean['lead_id'] = str(clean['lead_id'])
    _sanitize_discovery_keys(clean)
    return redact(clean)


def normalize_job(job):
    job = dict(job)
    operation = job.get('operation', 'verify')
    if operation in DISCOVERY_OPERATIONS:
        return _normalize_discovery_job(job, operation)
    raw = job.get('payload', job)
    # Legacy operations keep their exact historical payload surface.
    payload = sanitize_payload({k: v for k, v in dict(raw).items() if k in LEGACY_PAYLOAD_KEYS})
    if operation in {'verify', 'signals'}:
        if not payload.get('email'):
            raise PolicyError('verification_requires_email')
    elif operation == 'find':
        if not isinstance(payload.get('name'), str) or not payload['name'].strip() or not payload.get('domain') or not payload.get('domain_evidence_url'):
            raise PolicyError('finding_requires_name_domain_evidence')
        payload['name'] = ' '.join(payload['name'].split())
        evidence_host = urlsplit(payload['domain_evidence_url']).hostname
        if evidence_host != payload['domain'] and not evidence_host.endswith('.' + payload['domain']):
            raise PolicyError('domain_evidence_mismatch')
    else:
        raise PolicyError('unsupported_operation')
    return {'provider': str(job.get('provider', '')), 'operation': operation,
            'payload': payload, 'crosscheck': job.get('crosscheck') is True}


def input_key(operation, payload):
    payload = sanitize_payload(payload)
    if operation in {'verify', 'signals'}:
        identity = {'email': payload.get('email')}
    elif operation in DISCOVERY_OPERATIONS:
        identity = discovery_identity(operation, payload)
    else:
        identity = {'name': ' '.join(payload.get('name', '').casefold().split()), 'domain': payload.get('domain')}
    return hashlib.sha256(json.dumps({'operation': operation, **identity}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Lead-source (list-returning) operations: strict, canonical, provider-safe input.
# ---------------------------------------------------------------------------

US_STATES = {
    'AL': 'Alabama', 'AK': 'Alaska', 'AZ': 'Arizona', 'AR': 'Arkansas', 'CA': 'California',
    'CO': 'Colorado', 'CT': 'Connecticut', 'DE': 'Delaware', 'DC': 'District of Columbia',
    'FL': 'Florida', 'GA': 'Georgia', 'HI': 'Hawaii', 'ID': 'Idaho', 'IL': 'Illinois',
    'IN': 'Indiana', 'IA': 'Iowa', 'KS': 'Kansas', 'KY': 'Kentucky', 'LA': 'Louisiana',
    'ME': 'Maine', 'MD': 'Maryland', 'MA': 'Massachusetts', 'MI': 'Michigan', 'MN': 'Minnesota',
    'MS': 'Mississippi', 'MO': 'Missouri', 'MT': 'Montana', 'NE': 'Nebraska', 'NV': 'Nevada',
    'NH': 'New Hampshire', 'NJ': 'New Jersey', 'NM': 'New Mexico', 'NY': 'New York',
    'NC': 'North Carolina', 'ND': 'North Dakota', 'OH': 'Ohio', 'OK': 'Oklahoma', 'OR': 'Oregon',
    'PA': 'Pennsylvania', 'RI': 'Rhode Island', 'SC': 'South Carolina', 'SD': 'South Dakota',
    'TN': 'Tennessee', 'TX': 'Texas', 'UT': 'Utah', 'VT': 'Vermont', 'VA': 'Virginia',
    'WA': 'Washington', 'WV': 'West Virginia', 'WI': 'Wisconsin', 'WY': 'Wyoming',
}
_STATE_BY_NAME = {name.casefold(): code for code, name in US_STATES.items()}

HUNTER_HEADCOUNTS = ('1-10', '11-50', '51-200', '201-500', '501-1000', '1001-5000', '5001-10000', '10001+')
HUNTER_COMPANY_TYPES = frozenset({'educational', 'educational institution', 'government agency', 'non profit',
                                  'partnership', 'privately held', 'public company', 'self employed',
                                  'self owned', 'sole proprietorship'})
HUNTER_CONTINENTS = frozenset({'Europe', 'Asia', 'North America', 'Africa', 'Antarctica', 'South America', 'Oceania'})
HUNTER_BUSINESS_REGIONS = frozenset({'AMER', 'EMEA', 'APAC', 'LATAM'})
HUNTER_SENIORITIES = ('junior', 'senior', 'executive')
HUNTER_DEPARTMENTS = ('executive', 'it', 'finance', 'management', 'sales', 'legal', 'support', 'hr', 'marketing',
                      'communication', 'education', 'design', 'health', 'operations', 'product', 'research',
                      'consulting', 'administrative', 'procurement')
HUNTER_REQUIRED_FIELDS = ('full_name', 'position', 'phone_number')
DOMAIN_SEARCH_DEFAULTS = {'limit': 10, 'decision_maker': True, 'required_field': ['full_name', 'position'],
                          'seniority': [], 'department': []}
# Documented OpenStreetMap tags for real-estate offices; never raw Overpass QL.
OSM_TAG_ALLOWLIST = ('office=estate_agent', 'office=property_management', 'shop=estate_agent')
OSM_DEFAULT_TAGS = ('office=estate_agent',)
SEARCH_PAGING_DEFAULTS = {'num': 10, 'page': 1, 'count': 20, 'offset': 0}
SEARCH_PAGING_BY_PROVIDER = {'serper': frozenset({'num', 'page'}), 'brave': frozenset({'count', 'offset'})}


def _controls(value):
    return any(ord(c) < 32 or 127 <= ord(c) < 160 for c in value)


def _bounded_text(value, max_len, *, min_len=1, error='invalid_payload'):
    if not isinstance(value, str) or _controls(value):
        raise PolicyError(error)
    value = ' '.join(value.split())
    if not min_len <= len(value) <= max_len:
        raise PolicyError(error)
    return value


def _strict_int(value, low, high, error='invalid_payload'):
    if type(value) is not int or not low <= value <= high:
        raise PolicyError(error)
    return value


def _enum_list(value, allowed, *, error='invalid_payload', allow_empty=True):
    """Accept a list or comma string of enum values; return them in canonical allowed order."""
    if isinstance(value, str):
        value = [v.strip() for v in value.split(',') if v.strip()]
    if not isinstance(value, (list, tuple)) or len(value) > len(allowed):
        raise PolicyError(error)
    if any(not isinstance(v, str) or v not in allowed for v in value):
        raise PolicyError(error)
    if not value and not allow_empty:
        raise PolicyError(error)
    return [v for v in allowed if v in value]


def us_state_code(value):
    """Return a two-letter US state code for a code or full state name."""
    if not isinstance(value, str) or _controls(value):
        raise PolicyError('invalid_state')
    text = ' '.join(value.split())
    if text.upper() in US_STATES:
        return text.upper()
    code = _STATE_BY_NAME.get(text.casefold())
    if not code:
        raise PolicyError('invalid_state')
    return code


def _filter_terms(value, *, max_items=50, max_len=100, allowed=None):
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= max_items:
        raise PolicyError('invalid_filters')
    out = []
    for item in value:
        if allowed is not None:
            if not isinstance(item, str) or item not in allowed:
                raise PolicyError('invalid_filters')
            text = item
        else:
            text = _bounded_text(item, max_len, error='invalid_filters')
            if not re.fullmatch(r"[\w .,&'()/+-]+", text):
                raise PolicyError('invalid_filters')
        if text not in out:
            out.append(text)
    return sorted(out)


def _include_exclude(value, *, allowed=None, match=False):
    if not isinstance(value, dict) or not value:
        raise PolicyError('invalid_filters')
    keys = {'include', 'exclude'} | ({'match'} if match else set())
    if set(value) - keys or not ({'include', 'exclude'} & set(value)):
        raise PolicyError('invalid_filters')
    out = {k: _filter_terms(value[k], allowed=allowed) for k in ('include', 'exclude') if k in value}
    if match and 'match' in value:
        if value['match'] not in ('any', 'all'):
            raise PolicyError('invalid_filters')
        out['match'] = value['match']
    return out


def _location(value):
    if not isinstance(value, dict) or not value or set(value) - {'country', 'state', 'city', 'continent', 'business_region'}:
        raise PolicyError('invalid_filters')
    out = {}
    if 'continent' in value or 'business_region' in value:
        # Hunter: either a continent, or a business region, or country/state/city.
        if len(value) != 1:
            raise PolicyError('invalid_filters')
        key, item = next(iter(value.items()))
        if item not in (HUNTER_CONTINENTS if key == 'continent' else HUNTER_BUSINESS_REGIONS):
            raise PolicyError('invalid_filters')
        return {key: item}
    country = value.get('country')
    if not isinstance(country, str) or not re.fullmatch(r'[A-Z]{2}', country):
        raise PolicyError('invalid_filters')
    out['country'] = country
    if 'state' in value:
        if country != 'US' or not isinstance(value['state'], str) or value['state'] not in US_STATES:
            raise PolicyError('invalid_filters')
        out['state'] = value['state']
    if 'city' in value:
        city = _bounded_text(value['city'], 80, min_len=2, error='invalid_filters')
        if not all(c.isalpha() or c in " .,'-" for c in city):
            raise PolicyError('invalid_filters')
        out['city'] = city
    return out


def normalize_discover_filters(filters):
    """Validate Hunter Discover filters (free-plan subset, no offset) into a canonical dict.

    Source: https://hunter.io/api-documentation/v2#discover
    """
    if not isinstance(filters, dict) or not filters:
        raise PolicyError('invalid_filters')
    allowed = {'query', 'headquarters_location', 'industry', 'headcount', 'company_type', 'keywords', 'limit'}
    if set(filters) - allowed:
        raise PolicyError('invalid_filters')
    out = {}
    if 'query' in filters:
        out['query'] = _bounded_text(filters['query'], 500, min_len=3, error='invalid_filters')
    if 'headquarters_location' in filters:
        hq = filters['headquarters_location']
        if not isinstance(hq, dict) or not hq or set(hq) - {'include', 'exclude'}:
            raise PolicyError('invalid_filters')
        out['headquarters_location'] = {}
        for key in ('include', 'exclude'):
            if key in hq:
                items = hq[key]
                if not isinstance(items, (list, tuple)) or not 1 <= len(items) <= 60:
                    raise PolicyError('invalid_filters')
                locations = []
                for item in items:
                    location = _location(item)
                    if location not in locations:
                        locations.append(location)
                out['headquarters_location'][key] = sorted(locations, key=lambda d: json.dumps(d, sort_keys=True))
    if 'industry' in filters:
        out['industry'] = _include_exclude(filters['industry'])
    if 'headcount' in filters:
        out['headcount'] = _enum_list(filters['headcount'], HUNTER_HEADCOUNTS, error='invalid_filters', allow_empty=False)
    if 'company_type' in filters:
        out['company_type'] = _include_exclude(filters['company_type'], allowed=HUNTER_COMPANY_TYPES)
    if 'keywords' in filters:
        out['keywords'] = _include_exclude(filters['keywords'], match=True)
    if 'limit' in filters:
        out['limit'] = _strict_int(filters['limit'], 1, 100, 'invalid_filters')
    if not set(out) - {'limit'}:
        raise PolicyError('discover_requires_query_or_filter')
    return out


def search_query(value):
    """A web-search query: ≤400 chars, ≤50 words, no control characters."""
    query = _bounded_text(value, 400, error='invalid_query')
    if len(query.split()) > 50:
        raise PolicyError('invalid_query')
    return query


def overpass_area(value):
    """Area names are letters plus space . , ' - only, so they can never carry QL."""
    area = _bounded_text(value, 80, min_len=2, error='invalid_area')
    if not all(c.isalpha() or c in " .,'-" for c in area) or sum(c.isalpha() for c in area) < 2:
        raise PolicyError('invalid_area')
    return area


def _sanitize_discovery_keys(clean):
    if 'filters' in clean:
        clean['filters'] = normalize_discover_filters(clean['filters'])
    if 'limit' in clean:
        clean['limit'] = _strict_int(clean['limit'], 1, 10)
    if 'decision_maker' in clean and type(clean['decision_maker']) is not bool:
        raise PolicyError('invalid_payload')
    for key, allowed in (('required_field', HUNTER_REQUIRED_FIELDS), ('seniority', HUNTER_SENIORITIES),
                         ('department', HUNTER_DEPARTMENTS)):
        if key in clean:
            clean[key] = _enum_list(clean[key], allowed)
    if 'query' in clean:
        clean['query'] = search_query(clean['query'])
    if 'num' in clean:
        # Serper bills 2 credits when num > 10, but ledger charges are fixed per operation.
        if type(clean['num']) is not int or clean['num'] != 10:
            raise PolicyError('invalid_payload')
    if 'page' in clean:
        clean['page'] = _strict_int(clean['page'], 1, 10)
    if 'count' in clean:
        clean['count'] = _strict_int(clean['count'], 1, 20)
    if 'offset' in clean:
        clean['offset'] = _strict_int(clean['offset'], 0, 9)
    if 'area' in clean:
        clean['area'] = overpass_area(clean['area'])
    if 'state' in clean:
        clean['state'] = us_state_code(clean['state'])
    if 'tags' in clean:
        clean['tags'] = _enum_list(clean['tags'], OSM_TAG_ALLOWLIST, allow_empty=False)


def domain_search_options(payload):
    """Canonical Hunter Domain Search options with documented defaults applied."""
    options = {}
    for key, default in DOMAIN_SEARCH_DEFAULTS.items():
        options[key] = payload.get(key, default)
    options['limit'] = _strict_int(options['limit'], 1, 10)
    if type(options['decision_maker']) is not bool:
        raise PolicyError('invalid_payload')
    options['required_field'] = _enum_list(options['required_field'], HUNTER_REQUIRED_FIELDS)
    options['seniority'] = _enum_list(options['seniority'], HUNTER_SENIORITIES)
    options['department'] = _enum_list(options['department'], HUNTER_DEPARTMENTS)
    return options


def search_paging(payload):
    """Drop provider defaults so equivalent searches share one cache identity."""
    return {k: payload[k] for k in ('num', 'page', 'count', 'offset')
            if k in payload and payload[k] != SEARCH_PAGING_DEFAULTS[k]}


def _normalize_discovery_job(job, operation):
    raw = job.get('payload', job)
    if not isinstance(raw, dict):
        raise PolicyError('invalid_payload')
    allowed = DISCOVERY_PAYLOAD_KEYS[operation] | COMMON_PAYLOAD_KEYS
    ignorable = set() if 'payload' in job else {'provider', 'operation', 'crosscheck'}
    if set(raw) - allowed - ignorable:
        raise PolicyError('unexpected_payload_key')
    payload = sanitize_payload({k: v for k, v in raw.items() if k in allowed})
    if operation == 'discover':
        if 'filters' not in payload:
            raise PolicyError('discover_requires_filters')
    elif operation == 'domain_search':
        if not payload.get('domain'):
            raise PolicyError('domain_search_requires_domain')
        if payload['domain'].startswith('www.'):
            payload['domain'] = normalize_domain(payload['domain'][4:])
        payload.update(domain_search_options(payload))
    elif operation == 'search':
        if 'query' not in payload:
            raise PolicyError('search_requires_query')
        provider = str(job.get('provider', ''))
        paging = {k for k in ('num', 'page', 'count', 'offset') if k in payload}
        if provider in SEARCH_PAGING_BY_PROVIDER and paging - SEARCH_PAGING_BY_PROVIDER[provider]:
            raise PolicyError('paging_not_supported_by_provider')
        for key in paging:
            if payload[key] == SEARCH_PAGING_DEFAULTS[key]:
                del payload[key]
    elif operation == 'places':
        if 'area' not in payload:
            raise PolicyError('places_requires_area')
        payload.setdefault('tags', list(OSM_DEFAULT_TAGS))
    return {'provider': str(job.get('provider', '')), 'operation': operation,
            'payload': payload, 'crosscheck': False}


def discovery_identity(operation, payload):
    """Cache identity for list operations (the cache is already scoped per provider)."""
    if operation == 'discover':
        return {'filters': normalize_discover_filters(payload.get('filters'))}
    if operation == 'domain_search':
        domain = payload.get('domain') or ''
        if domain.startswith('www.'):
            domain = domain[4:]
        return {'domain': domain, 'options': domain_search_options(payload)}
    if operation == 'search':
        return {'query': payload.get('query'), 'paging': search_paging(payload)}
    return {'area': (payload.get('area') or '').casefold(), 'state': payload.get('state'),
            'tags': list(payload.get('tags') or OSM_DEFAULT_TAGS)}
