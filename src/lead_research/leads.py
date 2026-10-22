"""List and export delivered / team leads from the CLI."""
from __future__ import annotations

import csv
from pathlib import Path

from .filters import LISTING_FIELDS, apply_filter, collect_leads, match_lead, normalize_query
from .ops import get_ops, ops_key
from .targeting import TARGET_FIELDS, match_targeting, normalize_targeting

# HeyReach "Import from CSV" layout first (Profile URL is required there), then our extra fields
HEYREACH_HEADERS = (
    'Profile URL', 'First Name', 'Last Name', 'Full Name', 'Headline', 'Email Address', 'Job Title',
    'Location', 'Company', 'Company URL',
    'Company Website', 'City', 'State', 'Company Type', 'Source', 'Email Risk', 'Email Verification', 'Confidence',
)
EXPORT_FORMATS = ('default', 'heyreach')

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


def _get(row, *keys):
    for key in keys:
        value = row.get(key)
        if value not in (None, ''):
            return str(value).strip()
    return ''


def heyreach_row(row):
    """One lead (CSV row or pipeline record) in HeyReach's import layout plus our extra fields."""
    from .quality import clean_name, company_type
    full = _get(row, 'Full Name', 'full_name')
    first, last = _get(row, 'First Name', 'first_name'), _get(row, 'Last Name', 'last_name')
    if full and not (first and last):
        _full, first_c, last_c, *_ = clean_name(full)
        first, last = first or first_c or '', last or last_c or ''
    title, company = _get(row, 'Title', 'title'), _get(row, 'Company', 'company')
    website = _get(row, 'Company Website', 'company_website')
    city, state = _get(row, 'City', 'city'), _get(row, 'State', 'state')
    location = ', '.join(x for x in (city, state) if x) or 'United States'
    kind = _get(row, 'Company Type', 'company_type') or company_type({'company': company, 'company_website': website, 'title': title})
    company_url = _get(row, 'Company LinkedIn URL', 'company_linkedin_url')
    if not company_url and website:
        company_url = website if website.startswith('http') else f'https://{website}'
    return {
        'Profile URL': _get(row, 'LinkedIn URL', 'linkedin_url'),
        'First Name': first, 'Last Name': last, 'Full Name': full,
        'Headline': f'{title} at {company}' if title and company else title or company,
        'Email Address': _get(row, 'Email', 'email'),
        'Job Title': title, 'Location': location, 'Company': company, 'Company URL': company_url,
        'Company Website': website, 'City': city, 'State': state, 'Company Type': kind,
        'Source': _get(row, 'Source', 'Email Source', 'email_source', 'source'),
        'Email Risk': _get(row, 'Email Risk', 'email_risk'),
        'Email Verification': _get(row, 'Email Verification', 'email_verification'),
        'Confidence': _get(row, 'Confidence', 'confidence'),
    }


def write_heyreach(rows, path):
    """HeyReach needs a LinkedIn Profile URL on every row: rows with one go to ``path``;
    the rest go to ``<stem>-no-linkedin.csv`` (same columns, for email-only outreach)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    built = [heyreach_row(r) for r in rows]
    with_li = [r for r in built if r['Profile URL']]
    without = [r for r in built if not r['Profile URL']]
    files = {'heyreach': str(path)}
    targets = [(path, with_li)]
    if without:
        rest = path.with_name(path.stem + '-no-linkedin' + path.suffix)
        targets.append((rest, without))
        files['no_linkedin'] = str(rest)
    for target, data in targets:
        with target.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(HEYREACH_HEADERS))
            writer.writeheader()
            writer.writerows(data)
        target.chmod(0o600)
    return {'files': files, 'with_linkedin': len(with_li), 'without_linkedin': len(without)}


def export_leads(work, output, *, date=None, filter_name=None, query=None, fmt='default'):
    payload = list_leads(work, date=date, filter_name=filter_name, query=query)
    if fmt == 'heyreach':
        written = write_heyreach(payload['leads'], output)
        return {'output': written['files']['heyreach'], **written, 'count': payload['count'], 'format': 'heyreach',
                'date': payload.get('date'), 'filter': payload.get('filter'), 'headers': list(HEYREACH_HEADERS)}
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
