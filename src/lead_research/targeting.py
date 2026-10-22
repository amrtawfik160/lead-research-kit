"""ICP / targeting rules for saved filters and daily runs.

Unknown country, revenue, or industry stays unknown: a missing value does not
fail the rule, and a conflicting known value does. Title rules are explicit.
"""
from __future__ import annotations

import re

from .models import PolicyError
from .policy import US_STATES
from .quality import classify_title, fold, us_state_code

TARGET_FIELDS = (
    'country', 'revenue_min', 'industry', 'include_titles', 'exclude_titles', 'exclude_junior',
    'states', 'revenue_unknown',
)
REVENUE_UNKNOWN = ('keep', 'drop')

DEFAULT_ICP_TITLES = (
    'CEO', 'COO', 'CFO', 'President', 'Partner', 'Managing Partner', 'Managing Director',
    'VP of Strategy', 'President of Strategy', 'Founder', 'Owner', 'Principal', 'EVP', 'SVP',
)

DEFAULT_INDUSTRY_KEYWORDS = (
    'real estate development', 'real estate', 'commercial real estate',
    'property management', 'construction',
)

JUNIOR_TITLE = re.compile(
    r'\b(intern|internship|trainee|apprentice|assistant|coordinator|receptionist|'
    r'clerk|junior|jr\.?|associate|specialist|representative|rep|analyst|'
    r'sales associate|marketing coordinator)\b',
    re.I,
)
JUNIOR_SENIORITIES = frozenset({'agent'})
ICP_SENIORITIES = frozenset({
    'owner', 'founder', 'c_level', 'president', 'partner', 'principal',
    'managing_broker', 'managing_director', 'vp',
})
COUNTRY_ALIASES = {
    'us': 'US', 'usa': 'US', 'u.s.': 'US', 'u.s.a.': 'US',
    'united states': 'US', 'united states of america': 'US', 'america': 'US',
}

_MONEY = re.compile(
    r'^\s*\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*([kmb])?\s*\+?\s*$',
    re.I,
)


def parse_money(value, *, field='revenue_min'):
    if value in (None, '', 'all'):
        return None
    if isinstance(value, bool):
        raise PolicyError('invalid_revenue_min')
    if isinstance(value, (int, float)):
        amount = int(value)
        if amount < 0:
            raise PolicyError('invalid_revenue_min')
        return amount
    text = ' '.join(str(value).split())
    match = _MONEY.match(text.replace('USD', '').replace('usd', ''))
    if not match:
        raise PolicyError('invalid_revenue_min')
    number = float(match.group(1).replace(',', ''))
    suffix = (match.group(2) or '').lower()
    scale = {'k': 1_000, 'm': 1_000_000, 'b': 1_000_000_000}.get(suffix, 1)
    amount = int(number * scale)
    if amount < 0:
        raise PolicyError(f'invalid_{field}')
    return amount


def parse_country(value):
    if value in (None, '', 'all'):
        return None
    text = ' '.join(str(value).split()).casefold()
    if text in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[text]
    if re.fullmatch(r'[a-z]{2}', text):
        return text.upper()
    raise PolicyError('unknown_country')


def parse_title_list(value):
    if value in (None, '', 'all'):
        return None
    if isinstance(value, (list, tuple)):
        items = [str(item).strip() for item in value]
    else:
        items = [part.strip() for part in str(value).replace(';', ',').split(',')]
    titles = [item for item in items if item]
    if not titles:
        return None
    if len(titles) > 40:
        raise PolicyError('too_many_titles')
    if any(len(item) > 80 for item in titles):
        raise PolicyError('title_too_long')
    return titles


def parse_keywords(value):
    if value in (None, '', 'all'):
        return None
    if isinstance(value, (list, tuple)):
        items = [str(item).strip() for item in value]
    else:
        items = [part.strip() for part in str(value).replace(';', ',').split(',')]
    keywords = [item for item in items if item]
    if not keywords:
        return None
    if len(keywords) > 30:
        raise PolicyError('too_many_industry_keywords')
    return keywords


def parse_bool(value):
    if value in (None, '', 'all'):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().casefold()
    if text in ('1', 'true', 'yes', 'on'):
        return True
    if text in ('0', 'false', 'no', 'off'):
        return False
    raise PolicyError('invalid_bool_flag')


def normalize_targeting(raw):
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise PolicyError('invalid_filter_query')
    unknown = set(raw) - set(TARGET_FIELDS)
    if unknown:
        raise PolicyError('unknown_filter_field')
    query = {}
    country = parse_country(raw.get('country'))
    if country:
        query['country'] = country
    revenue = parse_money(raw.get('revenue_min'))
    if revenue is not None:
        query['revenue_min'] = revenue
    industry = parse_keywords(raw.get('industry'))
    if industry:
        query['industry'] = industry
    include_titles = parse_title_list(raw.get('include_titles'))
    if include_titles:
        query['include_titles'] = include_titles
    exclude_titles = parse_title_list(raw.get('exclude_titles'))
    if exclude_titles:
        query['exclude_titles'] = exclude_titles
    junior = parse_bool(raw.get('exclude_junior'))
    if junior is not None:
        query['exclude_junior'] = junior
    states = parse_states(raw.get('states'))
    if states:
        query['states'] = states
        query.setdefault('country', 'US')
    unknown_revenue = raw.get('revenue_unknown')
    if unknown_revenue not in (None, '', 'all'):
        unknown_revenue = str(unknown_revenue).strip().lower()
        if unknown_revenue not in REVENUE_UNKNOWN:
            raise PolicyError('invalid_revenue_unknown')
        query['revenue_unknown'] = unknown_revenue
    return query


def parse_states(value):
    """'CA', ['California', 'NY'], 'CA,TX' -> ['CA', 'NY', ...] (US state codes)."""
    if value in (None, '', 'all'):
        return None
    items = value if isinstance(value, (list, tuple)) else str(value).replace(';', ',').split(',')
    codes = []
    for item in items:
        text = ' '.join(str(item).split())
        if not text:
            continue
        code = us_state_code(text)
        if not code:
            raise PolicyError('unknown_us_state')
        if code not in codes:
            codes.append(code)
    if len(codes) > 60:
        raise PolicyError('too_many_states')
    return codes or None


def merge_targeting(base, override):
    merged = dict(base or {})
    for key, value in (override or {}).items():
        if value in (None, '', [], False) and key != 'exclude_junior':
            if value in (None, '', []):
                continue
        merged[key] = value
    return normalize_targeting(merged)


def _field(row, *names):
    for name in names:
        value = row.get(name)
        if value not in (None, ''):
            return value
    return ''


def _text_blob(row, *names):
    return ' '.join(str(_field(row, name) or '') for name in names)


def lead_country(row):
    raw = _field(row, 'Country', 'country', 'country_code', 'Country Code')
    if raw:
        try:
            return parse_country(raw)
        except PolicyError:
            text = ' '.join(str(raw).split()).casefold()
            if text in COUNTRY_ALIASES:
                return COUNTRY_ALIASES[text]
            if re.fullmatch(r'[a-z]{2}', text):
                return text.upper()
            return str(raw).strip().upper()
    state = str(_field(row, 'State', 'state') or '').strip().upper()
    if state in US_STATES:
        return 'US'
    return None


def lead_revenue(row):
    for name in ('Revenue', 'revenue', 'ARR', 'arr', 'Annual Revenue', 'annual_revenue', 'annual_revenue_usd', 'revenue_usd'):
        value = row.get(name)
        if value in (None, ''):
            continue
        try:
            return parse_money(value)
        except PolicyError:
            continue
    return None


def is_junior_title(title):
    text = str(title or '')
    if not text.strip():
        return False
    info = classify_title(text)
    if info['seniority'] in ICP_SENIORITIES:
        return False
    if info['seniority'] in JUNIOR_SENIORITIES:
        return True
    return bool(JUNIOR_TITLE.search(text))


def _title_phrases(title):
    text = ' '.join(str(title or '').casefold().split())
    return text


ABBREVIATIONS = {'ceo': 'chief executive officer', 'coo': 'chief operating officer', 'cfo': 'chief financial officer',
                 'cso': 'chief strategy officer', 'cio': 'chief investment officer', 'cto': 'chief technology officer',
                 'evp': 'executive vice president', 'svp': 'senior vice president', 'avp': 'assistant vice president',
                 'vp': 'vice president', 'md': 'managing director', 'cofounder': 'co founder'}
_STOPWORDS = {'of', 'the', 'and', 'for', 'to', 'at', 'in'}


def _title_words(text):
    tokens = re.findall(r'[a-z0-9]+', fold(str(text or '')).replace('&', ' and ').replace('-', ' '))
    out = []
    for token in tokens:
        out.extend(ABBREVIATIONS.get(token, token).split())
    return [t for t in out if t not in _STOPWORDS]


def title_matches(title, phrase):
    """Whole-word title match with abbreviations: 'CEO' = 'Chief Executive Officer',
    'VP of Strategy' = 'Vice President, Strategy'. 'President' never matches 'Vice President'."""
    words, needle = _title_words(title), _title_words(phrase)
    if not needle:
        return False
    for i in range(len(words) - len(needle) + 1):
        if words[i:i + len(needle)] != needle:
            continue
        if needle[0] != 'vice' and i > 0 and words[i - 1] == 'vice':
            continue
        return True
    return False


def title_matches_include(title, include_titles):
    if not include_titles:
        return True
    info = classify_title(title)
    is_vice = 'vice' in _title_words(title)
    for phrase in include_titles:
        if title_matches(title, phrase):
            return True
        needle = ' '.join(phrase.casefold().split())
        # Seniority fallbacks for spellings the word match misses ("Co-Founder/CEO", "Owner/Broker").
        # VP titles are never broadened: "EVP" or "VP of Strategy" must match the words themselves.
        if needle in ('founder', 'co-founder', 'cofounder') and info['seniority'] == 'founder':
            return True
        if needle == 'owner' and info['seniority'] == 'owner':
            return True
        if needle in ('partner', 'managing partner') and info['seniority'] == 'partner' and not is_vice:
            return needle == 'partner' or 'managing' in str(title).casefold()
        if needle == 'president' and info['seniority'] == 'president' and not is_vice:
            return True
        if needle == 'principal' and info['seniority'] == 'principal':
            return True
        if needle == 'managing director' and info['seniority'] == 'managing_director':
            return True
        if needle in ('ceo', 'coo', 'cfo', 'cto', 'cio') and info['label'] == needle:
            return True
    return False


def title_matches_exclude(title, exclude_titles):
    if not exclude_titles:
        return False
    return any(title_matches(title, phrase) for phrase in exclude_titles)


def industry_matches(row, keywords):
    if not keywords:
        return True
    declared = _text_blob(row, 'Industry', 'industry').casefold()
    extra = _text_blob(
        row,
        'Company Type', 'company_type', 'Area', 'area',
        'Company', 'company', 'Source', 'source',
    ).casefold()
    blob = f'{declared} {extra}'.strip()
    if any(keyword.casefold() in blob for keyword in keywords):
        return True
    words = ' ' + ' '.join(_title_words(blob)) + ' '
    for keyword in keywords:
        options = INDUSTRY_SYNONYMS.get(' '.join(_title_words(keyword)), ())
        if any(' ' + ' '.join(_title_words(o)) + ' ' in words for o in options):
            return True
    # Only a declared Industry value can miss. company_type is an internal label
    # (brokerage/other/...) and does not count as known industry.
    if declared.strip():
        return False
    return True


def targeting_reason(row, query):
    """None when the row passes, else the first failing rule (for report counts)."""
    query = normalize_targeting(query)
    if not query:
        return None
    title = _field(row, 'Title', 'title')
    if query.get('exclude_titles') and title_matches_exclude(title, query['exclude_titles']):
        return 'excluded_title'
    if query.get('exclude_junior') and is_junior_title(title):
        return 'junior_title'
    if query.get('include_titles') and not title_matches_include(title, query['include_titles']):
        return 'title'
    wanted = query.get('country')
    if wanted:
        known = lead_country(row)
        if known and known != wanted:
            return 'country'
    if query.get('states'):
        # A state filter drives discovery, so the lead's state must be known and listed
        state = us_state_code(_field(row, 'State', 'state'))
        if not state:
            return 'state_unknown'
        if state not in query['states']:
            return 'state'
    minimum = query.get('revenue_min')
    if minimum is not None:
        revenue = lead_revenue(row)
        if revenue is None and query.get('revenue_unknown') == 'drop':
            return 'revenue_unknown'
        if revenue is not None and revenue < minimum:
            return 'revenue'
    if query.get('industry') and not industry_matches(row, query['industry']):
        return 'industry'
    return None


def match_targeting(row, query):
    return targeting_reason(row, query) is None


def filter_records(records, query):
    """(kept, step). Kept records with a revenue rule but no revenue data get revenue_status='unknown'."""
    query = normalize_targeting(query)
    kept, dropped, unknown = [], {}, 0
    for record in records:
        reason = targeting_reason(record, query)
        if reason:
            dropped[reason] = dropped.get(reason, 0) + 1
            continue
        if query.get('revenue_min') is not None and lead_revenue(record) is None:
            record['revenue_status'] = 'unknown'
            unknown += 1
        kept.append(record)
    return kept, {'checked': len(records), 'kept': len(kept), 'dropped': dropped,
                  'notes': {'revenue_unknown': unknown} if unknown else {}}


# Single-state public license feeds; sec-form-d and generic files span all states
RECORD_SOURCE_STATES = {'tx-trec': 'TX', 'ny-dos': 'NY', 'co-dora': 'CO', 'ct-dls': 'CT', 'fl-dbpr-file': 'FL', 'de-dpr': 'DE'}
# Industry keyword -> extra Hunter Discover industries (names from hunter.io/files/industries.json)
HUNTER_INDUSTRY_HINTS = {
    'construction': ('Construction', 'Building Construction', 'Residential Building Construction',
                     'Nonresidential Building Construction'),
    'commercial real estate': ('Commercial Real Estate',),
}
INDUSTRY_SYNONYMS = {
    'construction': ('construction', 'builders', 'builder', 'homebuilder', 'homebuilders', 'contractor', 'contractors',
                     'contracting', 'general contractor', 'design build'),
    'real estate': ('realty', 'realtor', 'realtors', 'properties', 'brokerage', 'reit'),
    'commercial real estate': ('cre', 'commercial property', 'commercial properties', 'investment sales'),
    'real estate development': ('development', 'developer', 'developers', 'devco', 'land development'),
    'property management': ('property manager', 'property managers', 'management company', 'property_manager'),
}


def discovery_plan(query, metros, all_states, base_industries):
    """Where and what discovery searches for this targeting (states, Hunter industries, keywords)."""
    query = query or {}
    states = query.get('states') or []
    plan = {'metros': [m for m in metros if not states or us_state_code(m[1]) in states],
            'states': list(states) or list(all_states), 'industries': list(base_industries), 'keywords': []}
    for keyword in query.get('industry') or []:
        for industry in HUNTER_INDUSTRY_HINTS.get(' '.join(_title_words(keyword)), ()):
            if industry not in plan['industries']:
                plan['industries'].append(industry)
        plan['keywords'].append(keyword)
    return plan


def allowed_record_sources(names, query):
    """(kept source names, {skipped: reason}) for the targeting's states."""
    states = (query or {}).get('states')
    if not states:
        return tuple(names), {}
    kept, skipped = [], {}
    for name in names:
        state = RECORD_SOURCE_STATES.get(name)
        if state and state not in states:
            skipped[name] = f'outside filter states ({state})'
        else:
            kept.append(name)
    return tuple(kept), skipped


def example_cre_targeting():
    return {
        'country': 'US',
        'revenue_min': 5_000_000,
        'industry': list(DEFAULT_INDUSTRY_KEYWORDS),
        'include_titles': list(DEFAULT_ICP_TITLES),
        'exclude_junior': True,
    }
