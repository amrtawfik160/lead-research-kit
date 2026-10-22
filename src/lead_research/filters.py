"""Named, pinned lead filters. CLI is the source of truth; the dashboard only shows pins."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from .daily import load_daily_records
from .models import PolicyError
from .ops import OPS_FILE, ops_key as _ops_key
from .targeting import TARGET_FIELDS, match_targeting, normalize_targeting
from .team import lead_row, load_leads, load_state

FILTER_FILE = 'saved-filters.json'
FILTER_NAME_MAX = 40
LISTING_FIELDS = ('stage', 'owner', 'area', 'source', 'risk', 'date', 'verified', 'found_by')
FILTER_FIELDS = LISTING_FIELDS + TARGET_FIELDS
STAGES = ('new', 'contacted', 'replied', 'qualified', 'disqualified')
VERIFIED_VALUES = ('verified', 'unverified')


def now_iso(clock=time.time):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(clock()))


def normalize_filter_name(name):
    text = ' '.join(str(name or '').split())
    if not text:
        raise PolicyError('filter_name_required')
    if len(text) > FILTER_NAME_MAX:
        raise PolicyError('filter_name_too_long')
    if not re.fullmatch(r'[A-Za-z0-9$][A-Za-z0-9 _.$+&()-]*', text):
        raise PolicyError('invalid_filter_name')
    return text


def normalize_query(raw):
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise PolicyError('invalid_filter_query')
    unknown = set(raw) - set(FILTER_FIELDS)
    if unknown:
        raise PolicyError('unknown_filter_field')
    query = {}
    for field in LISTING_FIELDS:
        if field not in raw or raw[field] in (None, '', 'all'):
            continue
        value = ' '.join(str(raw[field]).split())
        if not value:
            continue
        if field == 'stage':
            value = value.lower()
            if value not in STAGES:
                raise PolicyError('unknown_lead_stage')
        if field == 'verified':
            value = value.lower()
            if value not in VERIFIED_VALUES:
                raise PolicyError('unknown_verified_filter')
        if field == 'risk':
            value = value.lower()
            if value not in ('low', 'medium', 'high'):
                raise PolicyError('unknown_risk_filter')
        if field == 'date' and value != 'team' and not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:-[A-Za-z0-9-]+)?', value):
            raise PolicyError('invalid_filter_date')
        query[field] = value
    query.update(normalize_targeting({field: raw.get(field) for field in TARGET_FIELDS if field in raw}))
    return query


def _load(work):
    path = Path(work) / FILTER_FILE
    if not path.exists():
        return {'filters': []}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise PolicyError('saved_filters_invalid') from None
    if not isinstance(data, dict) or not isinstance(data.get('filters'), list):
        raise PolicyError('saved_filters_invalid')
    return data


def _save(work, data):
    path = Path(work) / FILTER_FILE
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    os.chmod(path, 0o600)
    return path


def _public(item):
    return {
        'name': item['name'],
        'pinned': bool(item.get('pinned')),
        'query': dict(item.get('query') or {}),
        'created_at': item.get('created_at'),
        'updated_at': item.get('updated_at'),
    }


def _find(data, name):
    key = name.casefold()
    for item in data['filters']:
        if str(item.get('name') or '').casefold() == key:
            return item
    return None


def list_filters(work):
    data = _load(work)
    rows = [_public(item) for item in data['filters'] if isinstance(item, dict) and item.get('name')]
    rows.sort(key=lambda item: (not item['pinned'], item['name'].casefold()))
    return {
        'filters': rows,
        'pinned': [item for item in rows if item['pinned']],
        'count': len(rows),
    }


def save_filter(work, name, query=None, pin=False, clock=time.time):
    name = normalize_filter_name(name)
    query = normalize_query(query)
    data = _load(work)
    now = now_iso(clock)
    item = _find(data, name)
    if item:
        item['query'] = query
        item['updated_at'] = now
        if pin:
            item['pinned'] = True
    else:
        data['filters'].append({
            'name': name,
            'pinned': bool(pin),
            'query': query,
            'created_at': now,
            'updated_at': now,
        })
    _save(work, data)
    return {'saved': _public(_find(data, name)), **list_filters(work)}


def pin_filter(work, name, pinned=True, clock=time.time):
    name = normalize_filter_name(name)
    data = _load(work)
    item = _find(data, name)
    if not item:
        raise PolicyError('unknown_filter')
    item['pinned'] = bool(pinned)
    item['updated_at'] = now_iso(clock)
    _save(work, data)
    return {'filter': _public(item), **list_filters(work)}


def unpin_filter(work, name, clock=time.time):
    return pin_filter(work, name, pinned=False, clock=clock)


def _ops_map(work):
    path = Path(work) / OPS_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _as_row(record, ops=None, batch_date=''):
    if 'Full Name' in record or 'Email' in record:
        row = dict(record)
    else:
        row = lead_row(record)
        row['Email Risk'] = record.get('email_risk') or row.get('Email Risk') or ''
        row['Email Verification'] = record.get('email_verification') or row.get('Email Verification') or ''
        row['Source'] = record.get('email_source') or record.get('source') or row.get('Source') or ''
        row['City'] = record.get('city') or row.get('City') or ''
        row['State'] = record.get('state') or row.get('State') or ''
    extra = (ops or {}).get(_ops_key(row)) or {}
    row.setdefault('Stage', extra.get('stage') or 'new')
    row.setdefault('Owner', extra.get('owner') or '')
    if extra.get('stage'):
        row['Stage'] = extra.get('stage')
    if extra.get('owner') is not None and extra.get('owner') != '':
        row['Owner'] = extra.get('owner')
    if batch_date and not row.get('Batch Date'):
        row['Batch Date'] = batch_date
    return row


def collect_leads(work, date=None):
    ops = _ops_map(work)
    rows = []
    if date == 'team' or date is None:
        for lead in load_leads(work):
            rows.append(_as_row(lead, ops, lead.get('batch_date') or 'team'))
        if date == 'team':
            return rows
    root = Path(work) / 'daily'
    if not root.is_dir():
        return rows
    folders = []
    if date and date != 'team':
        folder = root / date
        if folder.is_dir():
            folders = [folder]
    else:
        folders = [p for p in sorted(root.iterdir()) if p.is_dir() and not p.name.endswith('-preview')]
    seen = set()
    for folder in folders:
        for record in load_daily_records(folder):
            row = _as_row(record, ops, folder.name)
            key = (str(row.get('Email') or '').lower(), str(row.get('Full Name') or '').lower(), str(row.get('Company') or '').lower())
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    return rows


def _is_verified(row):
    verification = str(row.get('Email Verification') or row.get('email_verification') or '').lower()
    if verification in ('valid', 'verified', 'provider_valid'):
        return True
    return str(row.get('Email Risk') or row.get('email_risk') or '').lower() == 'low'


def match_lead(row, query):
    query = normalize_query(query)
    if query.get('stage') and str(row.get('Stage') or 'new').lower() != query['stage']:
        return False
    if query.get('owner'):
        owner = str(row.get('Owner') or '').strip()
        wanted = query['owner']
        if wanted.lower() == 'assigned' and not owner:
            return False
        if wanted.lower() == 'unassigned' and owner:
            return False
        if wanted.lower() not in ('assigned', 'unassigned') and owner.casefold() != wanted.casefold():
            return False
    if query.get('area') and str(row.get('Area') or '').casefold() != query['area'].casefold():
        return False
    if query.get('source'):
        source = str(row.get('Source') or row.get('Email Source') or '').casefold()
        if source != query['source'].casefold():
            return False
    if query.get('risk') and str(row.get('Email Risk') or '').lower() != query['risk']:
        return False
    if query.get('date') and query['date'] != 'team':
        batch = str(row.get('Batch Date') or '')
        if batch and batch != query['date']:
            return False
    if query.get('verified') == 'verified' and not _is_verified(row):
        return False
    if query.get('verified') == 'unverified' and _is_verified(row):
        return False
    if query.get('found_by') and str(row.get('Found By') or '').casefold() != query['found_by'].casefold():
        return False
    return match_targeting(row, {field: query.get(field) for field in TARGET_FIELDS if field in query})


def apply_filter(work, name):
    name = normalize_filter_name(name)
    item = _find(_load(work), name)
    if not item:
        raise PolicyError('unknown_filter')
    query = normalize_query(item.get('query'))
    rows = collect_leads(work, query.get('date'))
    matched = [row for row in rows if match_lead(row, query)]
    return {
        'filter': _public(item),
        'matched': len(matched),
        'leads': matched,
        'me': load_state(work).get('member'),
    }
