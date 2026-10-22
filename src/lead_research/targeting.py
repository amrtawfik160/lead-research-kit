"""ICP / targeting rules for saved filters and daily runs.

Unknown country or revenue stays unknown: a missing value does not fail the
minimum, and a conflicting known value does. Title rules are explicit.
"""
from __future__ import annotations

import re

from .models import PolicyError
from .policy import US_STATES
from .quality import classify_title

TARGET_FIELDS = (
    'country', 'revenue_min', 'industry', 'include_titles', 'exclude_titles', 'exclude_junior',
)

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
    return query


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
    for name in ('Revenue', 'revenue', 'ARR', 'arr', 'Annual Revenue', 'annual_revenue', 'revenue_usd'):
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


def title_matches_include(title, include_titles):
    if not include_titles:
        return True
    text = _title_phrases(title)
    info = classify_title(title)
    for phrase in include_titles:
        needle = ' '.join(phrase.casefold().split())
        if needle and needle in text:
            return True
        compact = needle.replace(' of ', ' ').replace(' / ', ' ')
        if compact and compact in text:
            return True
        if needle in ('ceo', 'chief executive officer') and info['label'] == 'ceo':
            return True
        if needle in ('coo', 'cfo', 'cto', 'cio') and info['label'] == needle:
            return True
        if needle in ('evp', 'svp', 'avp', 'vp') and info['seniority'] == 'vp':
            return True
        if 'vice president' in needle and info['seniority'] == 'vp':
            return True
        if needle in ('founder', 'co-founder', 'cofounder') and info['seniority'] == 'founder':
            return True
        if needle in ('owner',) and info['seniority'] == 'owner':
            return True
        if 'partner' in needle and info['seniority'] == 'partner':
            return True
        if needle in ('president',) and info['seniority'] == 'president':
            return True
        if needle in ('principal',) and info['seniority'] == 'principal':
            return True
        if 'managing director' in needle and info['seniority'] == 'managing_director':
            return True
    return False


def title_matches_exclude(title, exclude_titles):
    if not exclude_titles:
        return False
    text = _title_phrases(title)
    for phrase in exclude_titles:
        needle = ' '.join(phrase.casefold().split())
        if needle and needle in text:
            return True
    return False


def industry_matches(row, keywords):
    if not keywords:
        return True
    blob = _text_blob(
        row,
        'Company', 'company', 'Industry', 'industry', 'Company Type', 'company_type',
        'Area', 'area', 'Source', 'source',
    ).casefold()
    if not blob.strip():
        return True
    return any(keyword.casefold() in blob for keyword in keywords)


def match_targeting(row, query):
    query = normalize_targeting(query)
    if not query:
        return True
    title = _field(row, 'Title', 'title')
    if query.get('exclude_titles') and title_matches_exclude(title, query['exclude_titles']):
        return False
    if query.get('exclude_junior') and is_junior_title(title):
        return False
    if query.get('include_titles') and not title_matches_include(title, query['include_titles']):
        return False
    wanted = query.get('country')
    if wanted:
        known = lead_country(row)
        if known and known != wanted:
            return False
    minimum = query.get('revenue_min')
    if minimum is not None:
        revenue = lead_revenue(row)
        if revenue is not None and revenue < minimum:
            return False
    if query.get('industry') and not industry_matches(row, query['industry']):
        return False
    return True


def example_cre_targeting():
    return {
        'country': 'US',
        'revenue_min': 5_000_000,
        'industry': list(DEFAULT_INDUSTRY_KEYWORDS),
        'include_titles': list(DEFAULT_ICP_TITLES),
        'exclude_junior': True,
    }
