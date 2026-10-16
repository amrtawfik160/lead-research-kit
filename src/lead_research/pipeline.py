"""Turn lead sources into one scored, deduplicated list in the clean 6-column format.

Sources are merged in this order of trust:

1. Company websites (``crawl``): names, titles and emails the company itself publishes
2. Hunter Domain Search prospects: work emails with Hunter's own confidence/verification
3. Search-engine LinkedIn results (X-ray): name, title, company and profile URL only
4. Imported rows (CSV/JSON exports from any tool)

A guessed email (from a company's proven pattern) never goes into the Email column
until a verifier marks it valid. Guesses are written to ``email-candidates.csv`` and
``verify-jobs.json`` so they can be checked with ``run --execute`` and rebuilt.
"""
from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path

from . import quality
from .extract import apply_pattern, classify_email

VALID_STATUSES = {'valid', 'provider_valid', 'deliverable'}
RISKY_STATUSES = {'catch_all', 'accept_all', 'unknown', 'risky'}
MIN_PATTERN_SUPPORT = 2
MIN_PATTERN_CONFIDENCE = 0.6


def read_any(path):
    """Read CSV, JSON (list or {'leads'|'records'|'rows'|'data'|'prospects'|'companies'|'sites'}) or JSONL."""
    path = Path(path)
    text = path.read_text(encoding='utf-8-sig')
    if path.suffix.lower() == '.jsonl':
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    if path.suffix.lower() == '.json':
        data = json.loads(text)
        if isinstance(data, dict):
            for key in ('leads', 'records', 'rows', 'data', 'prospects', 'companies', 'sites', 'results'):
                if isinstance(data.get(key), list):
                    return data[key]
            return [data]
        return data if isinstance(data, list) else []
    if path.suffix.lower() == '.txt':
        return [{'domain': line.strip()} for line in text.splitlines() if line.strip() and not line.startswith('#')]
    return list(csv.DictReader(text.splitlines()))


def domains_from(rows):
    """Usable company domains from any rows: website/domain columns first, then work-email domains."""
    seen, result = set(), []
    for row in rows:
        if isinstance(row, str):
            row = {'domain': row}
        if not isinstance(row, dict):
            continue
        keyed = {quality._key(k): v for k, v in row.items() if k}
        company = quality._text(keyed.get('company') or keyed.get('organization') or keyed.get('company_name'))
        host = ''
        for key in ('company_website', 'website', 'domain', 'company_domain', 'url'):
            host = quality.normalize_host(keyed.get(key))
            if host:
                break
        if not host:
            email = quality.normalize_email_address(keyed.get('email') or keyed.get('work_email'))
            if email and quality.email_kind(email) == 'work':
                host = email.rsplit('@', 1)[1]
        if not host or quality.website_problem(host, company) or host in seen:
            continue
        seen.add(host)
        result.append(host)
    return result


def _set(row, **values):
    """Overwrite a field however the input spelled it ('Email', 'email', ...)."""
    for key, value in values.items():
        for existing in [k for k in row if k and quality._key(k) == key]:
            del row[existing]
        row[key] = value


NICKNAMES = {
    'tony': 'anthony', 'tim': 'timothy', 'jeff': 'jeffrey', 'geoff': 'geoffrey', 'jim': 'james', 'jimmy': 'james',
    'jamie': 'james', 'bob': 'robert', 'rob': 'robert', 'bobby': 'robert', 'bill': 'william', 'will': 'william',
    'billy': 'william', 'mike': 'michael', 'mick': 'michael', 'dave': 'david', 'dan': 'daniel', 'danny': 'daniel',
    'chris': 'christopher', 'matt': 'matthew', 'steve': 'steven', 'stephen': 'steven', 'joe': 'joseph',
    'tom': 'thomas', 'tommy': 'thomas', 'nick': 'nicholas', 'rick': 'richard', 'rich': 'richard', 'dick': 'richard',
    'ted': 'edward', 'ed': 'edward', 'eddie': 'edward', 'andy': 'andrew', 'drew': 'andrew', 'ben': 'benjamin',
    'sam': 'samuel', 'alex': 'alexander', 'greg': 'gregory', 'pat': 'patrick', 'pete': 'peter', 'ron': 'ronald',
    'don': 'donald', 'ken': 'kenneth', 'larry': 'lawrence', 'jon': 'jonathan', 'jack': 'john',
    'kate': 'katherine', 'katie': 'katherine', 'kathy': 'katherine', 'cathy': 'katherine', 'liz': 'elizabeth',
    'beth': 'elizabeth', 'betsy': 'elizabeth', 'jen': 'jennifer', 'jenny': 'jennifer', 'sue': 'susan',
    'susie': 'susan', 'suzie': 'susan', 'deb': 'deborah', 'debbie': 'deborah', 'barb': 'barbara',
    'meg': 'margaret', 'maggie': 'margaret', 'peggy': 'margaret', 'patty': 'patricia', 'trish': 'patricia',
    'vicky': 'victoria', 'nate': 'nathan', 'zach': 'zachary', 'zack': 'zachary', 'josh': 'joshua',
    'charlie': 'charles', 'chuck': 'charles', 'hank': 'henry', 'harry': 'henry', 'fred': 'frederick',
    'gene': 'eugene', 'jerry': 'gerald', 'terry': 'terrence', 'manny': 'manuel', 'abe': 'abraham',
}


def _name_key(name):
    """(first, last) with common nicknames folded, so 'Tony Chereso' matches 'Anthony Chereso'."""
    parts = [p for p in re.findall(r'[a-z0-9]+', quality.fold(name or '')) if len(p) > 1 or p.isdigit()]
    if len(parts) < 2:
        return None
    first = NICKNAMES.get(parts[0], parts[0])
    return (first, parts[-1])


def _usable_pattern(pattern):
    return bool(pattern and pattern.get('pattern') and pattern.get('support', 0) >= MIN_PATTERN_SUPPORT
                and pattern.get('confidence', 0) >= MIN_PATTERN_CONFIDENCE)


def site_records(site_results):
    """Flatten crawl results into lead rows. Only person emails at the company's own domain count."""
    rows = []
    for site in site_results or []:
        if not isinstance(site, dict) or not site.get('domain'):
            continue
        domain = site['domain']
        company = (site.get('company') or {}).get('name') or ''
        pattern = site.get('pattern') if _usable_pattern(site.get('pattern')) else None
        for person in site.get('people') or []:
            row = {'full_name': person.get('full_name'), 'title': person.get('title') or '', 'company': company,
                   'company_website': domain, 'linkedin_url': person.get('linkedin_url') or '',
                   'source': 'website', 'source_url': person.get('evidence_url') or ''}
            email = (person.get('email') or '').lower()
            if email and classify_email(email, domain) == 'person':
                row.update(email=email, email_source='website_published', email_evidence_url=person.get('evidence_url') or '')
            elif pattern:
                guess = apply_pattern(pattern['pattern'], person.get('full_name') or '', domain)
                if guess:
                    row.update(email_candidate=guess, email_candidate_basis=f"site pattern {pattern['pattern']} ({pattern['support']}/{pattern['total']} published emails)")
            rows.append(row)
    return rows


def _site_index(site_results):
    index, patterns = {}, {}
    for row in site_records(site_results):
        key = (row['company_website'], _name_key(row['full_name']))
        if key[1]:
            index.setdefault(key, row)
    for site in site_results or []:
        if isinstance(site, dict) and site.get('domain') and _usable_pattern(site.get('pattern')):
            patterns[site['domain']] = site['pattern']
    return index, patterns


def _verification_index(store):
    """Latest provider assessment per email from completed verify attempts."""
    result = {}
    if store is None:
        return result
    for attempt in store.attempts():
        if attempt.get('operation') != 'verify' or attempt.get('state') != 'completed':
            continue
        outcome = attempt.get('outcome') or {}
        email = (attempt.get('payload') or {}).get('email')
        if email and not outcome.get('error'):
            flags = set(outcome.get('risk_flags') or [])
            status = 'catch_all' if 'catch_all' in flags or 'catchall_domain' in flags else outcome.get('assessment')
            result[email.lower()] = {'status': status, 'provider': attempt.get('provider')}
    return result


def merge_sources(*, imports=(), prospects=(), site_results=(), companies=(), store=None):
    """Combine every source into raw lead rows (before quality scoring)."""
    site_index, patterns = _site_index(site_results)
    for company in companies or []:
        if isinstance(company, dict) and company.get('domain') and company.get('email_pattern'):
            patterns.setdefault(company['domain'], {'pattern': company['email_pattern'], 'support': MIN_PATTERN_SUPPORT,
                                                    'total': MIN_PATTERN_SUPPORT, 'confidence': 1.0, 'basis': 'hunter'})
    rows = []
    for row in imports or []:
        rows.append({**row, 'source': row.get('source') or 'import'})
    for row in prospects or []:
        if isinstance(row, dict):
            rows.append(dict(row))
    used_site_keys = set()
    enriched = []
    for raw in rows:
        record = quality.normalize_record(raw)
        merged = dict(raw)
        key = (record['company_website'], _name_key(record['full_name']))
        site = site_index.get(key) if key[1] else None
        if site:
            used_site_keys.add(key)
            if site.get('email') and quality.email_kind(record['email']) != 'work':
                _set(merged, email=site['email'], email_source='website_published', email_evidence_url=site.get('email_evidence_url'))
            if not record['linkedin_url'] and site.get('linkedin_url'):
                _set(merged, linkedin_url=site['linkedin_url'])
            if not record['title'] and site.get('title'):
                _set(merged, title=site['title'])
        domain = record['company_website']
        if domain in patterns and not merged.get('email') and not record['email']:
            guess = apply_pattern(patterns[domain]['pattern'], record['full_name'], domain)
            if guess:
                merged['email_candidate'] = guess
                merged['email_candidate_basis'] = f"pattern {patterns[domain]['pattern']} ({patterns[domain].get('basis', 'site')})"
        enriched.append(merged)
    for key, row in site_index.items():
        if key not in used_site_keys:
            enriched.append(dict(row))
    verified = _verification_index(store)
    for row in enriched:
        email = quality.normalize_record(row)['email']
        if email and email in verified and not row.get('email_verification'):
            _set(row, email_verification=verified[email]['status'])
        guess = (row.get('email_candidate') or '').lower()
        if guess and not email and verified.get(guess, {}).get('status') in VALID_STATUSES:
            _set(row, email=guess, email_source='pattern_verified', email_verification='valid')
            row.pop('email_candidate', None)
    return enriched


def build(*, imports=(), prospects=(), site_results=(), companies=(), store=None, target=None):
    """Merge sources, score them, and return quality.clean_records output plus email candidates."""
    rows = merge_sources(imports=imports, prospects=prospects, site_results=site_results, companies=companies, store=store)
    result = quality.clean_records(rows, target or quality.Target())
    published = {r.get('email') for r in result['clean'] + result['review'] if r.get('email')}
    candidates, seen = [], set()
    by_identity = {}
    for row in rows:
        record = quality.normalize_record(row)
        if row.get('email_candidate') and not record['email']:
            by_identity.setdefault((record['company_website'], _name_key(record['full_name'])), row)
    for record in result['clean'] + result['review']:
        source = by_identity.get((record['company_website'], _name_key(record['full_name'])))
        if not source or record.get('email'):
            continue
        guess = source['email_candidate'].lower()
        if guess in published or guess in seen:
            continue
        seen.add(guess)
        candidates.append({'Full Name': record['full_name'], 'Company Website': record['company_website'],
                           'Candidate Email': guess, 'Basis': source.get('email_candidate_basis', ''),
                           'Tier': record['tier']})
    result['candidates'] = candidates
    result['summary']['email_candidates_to_verify'] = len(candidates)
    result['summary']['sources'] = _source_counts(rows)
    return result


def _source_counts(rows):
    counts = {}
    for row in rows:
        key = row.get('source') or row.get('provider') or 'import'
        counts[key] = counts.get(key, 0) + 1
    return counts


def write_build(result, output_dir):
    """Write the quality outputs plus email-candidates.csv and verify-jobs.json."""
    paths = quality.write_outputs(result, output_dir)
    out = Path(output_dir)
    cand_path = out / 'email-candidates.csv'
    headers = ['Full Name', 'Company Website', 'Candidate Email', 'Basis', 'Tier']

    def write_candidates(handle):
        writer = csv.writer(handle)
        writer.writerow(headers)
        for row in result['candidates']:
            writer.writerow([quality.escape_cell(row[h]) for h in headers])
    quality._write_private(cand_path, write_candidates)
    jobs = [{'provider': 'auto', 'operation': 'verify', 'payload': {'email': row['Candidate Email']}}
            for row in result['candidates']]
    jobs_path = out / 'verify-jobs.json'
    jobs_path.write_text(json.dumps(jobs, indent=2) + '\n', encoding='utf-8')
    os.chmod(jobs_path, 0o600)
    paths['paths'].update(email_candidates=str(cand_path), verify_jobs=str(jobs_path))
    return paths
