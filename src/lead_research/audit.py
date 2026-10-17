"""Daily spot-check: re-check a random sample of delivered leads against their evidence.

For each sampled row of ``daily/<date>/leads-details.csv``:

* the evidence page (Source URL) is fetched again, through the polite SafeFetcher (robots.txt
  respected; a cached copy is reused only when it is less than a day old)
* the person's name must still be on that page
* a ``website_published`` email must still be on that page
* the title should roughly match (any meaningful title word on the page)
* the company website must still answer
* a ``pattern_verified`` email must have a passing verifier result in the ledger
* a LinkedIn URL must have a slug consistent with the name (LinkedIn itself is never fetched)

Results: ``audit.json`` and ``audit.md`` in the day folder with an accuracy score
(% fully confirmed, % partially confirmed, % failed, with reasons).
"""
from __future__ import annotations

import csv
import html as html_lib
import json
import os
import random
import re
import time
from pathlib import Path

from . import quality
from .models import PolicyError

DEFAULT_SAMPLE = 20
CACHE_MAX_AGE = 86400
VALID_VERIFY = {'valid', 'provider_valid', 'deliverable'}
TITLE_STOPWORDS = {'and', 'the', 'of', 'for', 'at', 'in', 'co', 'vice', 'senior', 'sr', 'jr', 'chief', 'officer', 'real', 'estate'}


def _page_text(raw):
    text = re.sub(r'(?is)<(script|style|noscript)[^>]*>.*?</\1>', ' ', raw or '')
    text = re.sub(r'(?s)<[^>]+>', ' ', text)
    return re.sub(r'\s+', ' ', html_lib.unescape(text))


def _page_emails(raw):
    from .extract import decode_cfemail, deobfuscate_emails, find_emails
    found = {e.lower() for e in find_emails(deobfuscate_emails(_page_text(raw)))}
    found |= {m.lower() for m in re.findall(r'mailto:([^"\'?>\s]+)', raw or '', re.I)}
    for hex_string in re.findall(r'data-cfemail="([0-9a-fA-F]+)"', raw or ''):
        decoded = decode_cfemail(hex_string)
        if decoded:
            found.add(decoded.lower())
    return found


def _name_parts(full_name):
    tokens = [t for t in re.findall(r'[a-z]+', quality.fold(full_name or '')) if len(t) > 1]
    return (tokens[0], tokens[-1]) if len(tokens) >= 2 else None


def name_on_page(full_name, text):
    parts = _name_parts(full_name)
    if not parts:
        return False
    folded = quality.fold(text)
    first, last = parts
    return bool(re.search(rf'\b{re.escape(last)}\b', folded) and
                (re.search(rf'\b{re.escape(first)}\b', folded) or re.search(rf'\b{re.escape(first[0])}\.?\s+{re.escape(last)}\b', folded)))


def title_on_page(title, text):
    words = [w for w in re.findall(r'[a-z]{3,}', quality.fold(title or '')) if w not in TITLE_STOPWORDS]
    if not words:
        return None
    folded = quality.fold(text)
    return any(re.search(rf'\b{re.escape(w[:6])}', folded) for w in words)


def linkedin_consistent(full_name, url):
    slug = quality.linkedin_slug(quality.normalize_linkedin(url or '') or '')
    if not slug:
        return False
    parts = _name_parts(full_name)
    if not parts:
        return False
    compact = re.sub(r'[^a-z]', '', quality.fold(slug))
    first, last = parts
    return last in compact or (first in compact and len(first) >= 3)


def _verifier_index(store):
    """email -> best verifier assessment from the ledger (completed attempts only)."""
    index = {}
    if store is None:
        return index
    for attempt in store.attempts():
        if attempt.get('operation') != 'verify' or attempt.get('state') != 'completed':
            continue
        email = ((attempt.get('payload') or {}).get('email') or '').lower()
        outcome = attempt.get('outcome') or {}
        if not email or outcome.get('error'):
            continue
        status = outcome.get('assessment')
        if status in VALID_VERIFY or email not in index:
            index[email] = {'status': status, 'provider': attempt.get('provider'),
                            'catch_all': 'catch_all' in (outcome.get('risk_flags') or [])}
    return index


def _fetch(fetcher, url, cache):
    if url not in cache:
        try:
            cache[url] = fetcher.get(url)
        except Exception:
            cache[url] = None
    return cache[url]


def check_lead(row, fetcher, verifiers, cache):
    full_name = row.get('Full Name') or ''
    email = (row.get('Email') or '').strip().lower()
    email_source = (row.get('Email Source') or '').strip()
    website = quality.normalize_host(row.get('Company Website') or '')
    source_url = (row.get('Source URL') or '').strip()
    linkedin = (row.get('LinkedIn URL') or '').strip()
    checks, reasons = {}, []

    homepage = _fetch(fetcher, f'https://{website}/', cache) if website else None
    checks['website_reachable'] = bool(homepage is not None and homepage.ok) if website else None
    if website and not checks['website_reachable']:
        reasons.append('company website did not answer' + (f' ({homepage.error})' if homepage is not None and homepage.error else ''))

    page = None
    if source_url.startswith(('http://', 'https://')) and 'linkedin.com' not in source_url:
        page = _fetch(fetcher, source_url, cache)
        if page is None or not page.ok:
            checks['evidence_page'] = False
            error = page.error if page is not None else 'fetch_failed'
            reasons.append(f'evidence page unavailable ({error or "http " + str(page.status)})')
        else:
            checks['evidence_page'] = True
    else:
        checks['evidence_page'] = None

    text = _page_text(page.text) if page is not None and page.ok else ''
    if checks['evidence_page']:
        checks['name_on_page'] = name_on_page(full_name, text)
        if not checks['name_on_page']:
            reasons.append('name no longer on the evidence page')
        checks['title_matches'] = title_on_page(row.get('Title'), text)
        if checks['title_matches'] is False:
            reasons.append('title not found on the evidence page')
    else:
        checks['name_on_page'] = checks['title_matches'] = None

    checks['email_on_page'] = checks['email_verified'] = None
    if email and email_source == 'website_published':
        if checks['evidence_page']:
            checks['email_on_page'] = email in _page_emails(page.text)
            if not checks['email_on_page']:
                reasons.append('published email no longer on the evidence page')
    elif email and email_source == 'pattern_verified':
        found = verifiers.get(email)
        checks['email_verified'] = bool(found and found['status'] in VALID_VERIFY)
        if not checks['email_verified']:
            reasons.append('no passing verifier result in the ledger')
        elif found.get('catch_all'):
            reasons.append('verified on a catch-all domain (riskier)')
    elif email:
        found = verifiers.get(email)
        if found:
            checks['email_verified'] = found['status'] in VALID_VERIFY
            if not checks['email_verified']:
                reasons.append(f'verifier said {found["status"]}')

    if linkedin:
        checks['linkedin_consistent'] = linkedin_consistent(full_name, linkedin)
        if not checks['linkedin_consistent']:
            reasons.append('LinkedIn URL does not match the name')
    else:
        checks['linkedin_consistent'] = None

    applicable = {k: v for k, v in checks.items() if v is not None}
    confirming = any(applicable.get(k) for k in ('name_on_page', 'email_on_page', 'email_verified'))
    if (checks['website_reachable'] is False or checks['name_on_page'] is False or checks['email_verified'] is False
            or (checks['evidence_page'] is False and not confirming)):
        result = 'failed'
    elif not applicable:
        result = 'unchecked'
    elif all(applicable.values()):
        result = 'confirmed'
    else:
        result = 'partial'
    return {'full_name': full_name, 'company_website': website, 'email_source': email_source, 'source_url': source_url,
            'result': result, 'checks': checks, 'reasons': reasons}


def _read_details(path):
    with open(path, encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def run_audit(day_dir, fetcher, store=None, sample=DEFAULT_SAMPLE, seed=None, clock=time.time):
    """Audit ``day_dir/leads-details.csv``; writes audit.json + audit.md; returns the report."""
    day_dir = Path(day_dir)
    details = day_dir / 'leads-details.csv'
    if not details.exists():
        raise PolicyError('leads_details_missing')
    rows = _read_details(details)
    rng = random.Random(seed if seed is not None else day_dir.name)
    picked = rows if len(rows) <= sample else rng.sample(rows, sample)
    verifiers = _verifier_index(store)
    cache = {}
    leads = [check_lead(row, fetcher, verifiers, cache) for row in picked]
    counts = {k: sum(1 for l in leads if l['result'] == k) for k in ('confirmed', 'partial', 'failed', 'unchecked')}
    total = len(leads) or 1
    reasons = {}
    for lead in leads:
        for reason in lead['reasons']:
            key = re.sub(r' \(.*\)$', '', reason)
            reasons[key] = reasons.get(key, 0) + 1
    report = {'date': day_dir.name, 'audited_at': clock(), 'rows_in_file': len(rows), 'sampled': len(leads),
              'seed': seed, 'counts': counts,
              'score': {'confirmed_pct': round(100 * counts['confirmed'] / total, 1),
                        'partial_pct': round(100 * counts['partial'] / total, 1),
                        'failed_pct': round(100 * counts['failed'] / total, 1)},
              'top_reasons': dict(sorted(reasons.items(), key=lambda kv: -kv[1])), 'leads': leads}
    write_private(day_dir / 'audit.json', json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    write_private(day_dir / 'audit.md', render_audit(report))
    return report


def write_private(path, text):
    Path(path).write_text(text, encoding='utf-8')
    os.chmod(path, 0o600)


def score_line(report):
    s = report['score']
    return (f"Spot-check ({report['sampled']} leads): {s['confirmed_pct']}% fully confirmed, "
            f"{s['partial_pct']}% partly, {s['failed_pct']}% failed")


def render_audit(report):
    lines = [f"Audit {report['date']}", '', score_line(report), '']
    if report['top_reasons']:
        lines.append('Most common problems:')
        lines += [f'- {k}: {v}' for k, v in report['top_reasons'].items()]
        lines.append('')
    lines.append('| Result | Name | Company | Email source | Problems |')
    lines.append('|---|---|---|---|---|')
    for lead in report['leads']:
        problems = '; '.join(lead['reasons']).replace('|', '/') or '-'
        lines.append(f"| {lead['result']} | {lead['full_name']} | {lead['company_website']} | {lead['email_source'] or '-'} | {problems} |")
    return '\n'.join(lines) + '\n'


def latest_score(day_dir):
    """Score line from ``day_dir/audit.json`` (or None)."""
    path = Path(day_dir) / 'audit.json'
    if not path.exists():
        return None
    try:
        return score_line(json.loads(path.read_text(encoding='utf-8')))
    except (ValueError, KeyError, TypeError):
        return None
