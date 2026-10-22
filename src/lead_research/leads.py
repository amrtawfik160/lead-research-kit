"""List and export delivered / team leads from the CLI."""
from __future__ import annotations

import csv
from pathlib import Path

from .filters import LISTING_FIELDS, apply_filter, collect_leads, match_lead, normalize_query
from .ops import get_ops, ops_key
from .targeting import TARGET_FIELDS, match_targeting, normalize_targeting

CSV_HEADERS = (
    'Full Name', 'Title', 'Company', 'Company Website', 'LinkedIn URL', 'Email',
    'Source', 'Email Risk', 'Email Verification', 'City', 'State',
    'Stage', 'Owner', 'Notes', 'Found By', 'Area',
)


def _source(row):
    return row.get('Source') or row.get('Email Source') or row.get('email_source') or ''


def _with_notes(work, row):
    record = dict(row)
    extra = get_ops(work, key=ops_key(record))
    ops = extra.get('ops') or {}
    record.setdefault('Stage', ops.get('stage') or 'new')
    record.setdefault('Owner', ops.get('owner') or '')
    record['Notes'] = ops.get('notes') or record.get('Notes') or ''
    record['Source'] = _source(record)
    return record


def list_leads(work, *, date=None, filter_name=None, query=None):
    if filter_name:
        applied = apply_filter(work, filter_name)
        rows = [_with_notes(work, row) for row in applied['leads']]
        return {
            'date': (applied.get('filter') or {}).get('query', {}).get('date') or date or 'all',
            'filter': applied.get('filter'),
            'count': len(rows),
            'leads': rows,
        }
    raw = query or {}
    targeting = normalize_targeting({field: raw.get(field) for field in TARGET_FIELDS if field in raw})
    listing = normalize_query({field: raw.get(field) for field in LISTING_FIELDS if field in raw})
    rows = []
    for row in collect_leads(work, listing.get('date') or date):
        record = _with_notes(work, row)
        if listing and not match_lead(record, listing):
            continue
        if targeting and not match_targeting(record, targeting):
            continue
        rows.append(record)
    return {
        'date': listing.get('date') or date or 'all',
        'count': len(rows),
        'leads': rows,
        'query': {**listing, **targeting},
    }


def export_leads(work, output, *, date=None, filter_name=None, query=None):
    payload = list_leads(work, date=date, filter_name=filter_name, query=query)
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_HEADERS), extrasaction='ignore')
        writer.writeheader()
        for row in payload['leads']:
            writer.writerow({header: row.get(header) or '' for header in CSV_HEADERS})
    path.chmod(0o600)
    return {
        'output': str(path),
        'count': payload['count'],
        'date': payload.get('date'),
        'filter': payload.get('filter'),
        'headers': list(CSV_HEADERS),
    }
