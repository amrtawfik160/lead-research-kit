"""Daily fresh-lead engine.

Every run:

1. Refills a backlog of company domains from enabled sources (free sources first),
   rotating through markets so each day covers new ground
2. Crawls the oldest uncrawled domains in parallel and stores what it finds
3. Merges everything ever crawled or collected, scores it, and keeps only leads that
   were never delivered before
4. Writes ``<output_root>/<date>/leads.csv`` in the 6-column format and records
   what was delivered, so tomorrow's file never repeats a person

Nothing is fetched, spent or recorded without ``execute=True``. Provider calls go
through the normal budget ledger (``route_run``); public-record and crawl sources
are free but still bounded by daily caps.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import re
import sqlite3
import time
from pathlib import Path

from . import quality
from .pipeline import _name_key, build

DEFAULT_SOURCES = ('records', 'resolve', 'hunter-discover', 'google-places', 'osm', 'crawl', 'render-js', 'find-emails', 'verify', 'build')
OPTIONAL_SOURCES = ('hunter-domains', 'apollo', 'xray-linkedin', 'pdl', 'jina-read')
DEFAULT_RECORD_SOURCES = ('tx-trec', 'ny-dos', 'co-dora', 'ct-dls', 'sec-form-d')
DEFAULT_CAPS = {'records': 3000, 'resolve': 600, 'resolve-hunter': 1500, 'resolve-guess': 1500, 'hunter-discover': 90, 'google-places': 30, 'osm': 6,
                'apollo': 20, 'apollo-enrich': 50, 'hunter-domains': 25, 'find-emails': 60, 'verify': 300, 'xray-linkedin': 300,
                'render-js': 25, 'pdl': 3, 'verify-published': 25, 'jina-read': 12}
KEYWORD_SETS = (['property management'], ['commercial real estate'], ['multifamily'], ['real estate investment'],
                ['real estate development'], ['brokerage'], ['luxury real estate'], ['land'])
SEARCH_FALLBACK = ('serper', 'linkup', 'youcom', 'zenserp', 'tavily', 'exa', 'serpapi', 'firecrawl')
EXPECTED_LEADS_PER_DOMAIN = 0.3  # measured: ~0.3 fresh decision makers with work email per crawled brokerage site

# Large US metros as (place, state) for OpenStreetMap rotation. Official place names
# resolve best; counties work too ("Maricopa County", "AZ")
METROS = (
    ('New York', 'NY'), ('Los Angeles', 'CA'), ('Chicago', 'IL'), ('Houston', 'TX'), ('Phoenix', 'AZ'),
    ('Philadelphia', 'PA'), ('San Antonio', 'TX'), ('San Diego', 'CA'), ('Dallas', 'TX'), ('Austin', 'TX'),
    ('Jacksonville', 'FL'), ('Fort Worth', 'TX'), ('San Jose', 'CA'), ('Columbus', 'OH'), ('Charlotte', 'NC'),
    ('Indianapolis', 'IN'), ('San Francisco', 'CA'), ('Seattle', 'WA'), ('Denver', 'CO'), ('Oklahoma City', 'OK'),
    ('Nashville', 'TN'), ('Washington', 'DC'), ('El Paso', 'TX'), ('Las Vegas', 'NV'), ('Boston', 'MA'),
    ('Detroit', 'MI'), ('Portland', 'OR'), ('Louisville', 'KY'), ('Memphis', 'TN'), ('Baltimore', 'MD'),
    ('Milwaukee', 'WI'), ('Albuquerque', 'NM'), ('Tucson', 'AZ'), ('Fresno', 'CA'), ('Sacramento', 'CA'),
    ('Mesa', 'AZ'), ('Atlanta', 'GA'), ('Kansas City', 'MO'), ('Colorado Springs', 'CO'), ('Omaha', 'NE'),
    ('Raleigh', 'NC'), ('Miami', 'FL'), ('Virginia Beach', 'VA'), ('Long Beach', 'CA'), ('Oakland', 'CA'),
    ('Minneapolis', 'MN'), ('Tulsa', 'OK'), ('Tampa', 'FL'), ('Arlington', 'TX'), ('New Orleans', 'LA'),
    ('Wichita', 'KS'), ('Cleveland', 'OH'), ('Bakersfield', 'CA'), ('Aurora', 'CO'), ('Anaheim', 'CA'),
    ('Honolulu', 'HI'), ('Santa Ana', 'CA'), ('Riverside', 'CA'), ('Corpus Christi', 'TX'), ('Lexington', 'KY'),
    ('Henderson', 'NV'), ('Stockton', 'CA'), ('Saint Paul', 'MN'), ('Cincinnati', 'OH'), ('St. Louis', 'MO'),
    ('Pittsburgh', 'PA'), ('Greensboro', 'NC'), ('Lincoln', 'NE'), ('Anchorage', 'AK'), ('Plano', 'TX'),
    ('Orlando', 'FL'), ('Irvine', 'CA'), ('Newark', 'NJ'), ('Durham', 'NC'), ('Chula Vista', 'CA'),
    ('Toledo', 'OH'), ('Fort Wayne', 'IN'), ('St. Petersburg', 'FL'), ('Laredo', 'TX'), ('Jersey City', 'NJ'),
    ('Chandler', 'AZ'), ('Madison', 'WI'), ('Lubbock', 'TX'), ('Scottsdale', 'AZ'), ('Reno', 'NV'),
    ('Buffalo', 'NY'), ('Gilbert', 'AZ'), ('Glendale', 'AZ'), ('North Las Vegas', 'NV'), ('Winston-Salem', 'NC'),
    ('Chesapeake', 'VA'), ('Norfolk', 'VA'), ('Fremont', 'CA'), ('Garland', 'TX'), ('Irving', 'TX'),
    ('Hialeah', 'FL'), ('Richmond', 'VA'), ('Boise', 'ID'), ('Spokane', 'WA'), ('Baton Rouge', 'LA'),
    ('Tacoma', 'WA'), ('San Bernardino', 'CA'), ('Modesto', 'CA'), ('Fontana', 'CA'), ('Des Moines', 'IA'),
    ('Fort Lauderdale', 'FL'), ('Salt Lake City', 'UT'), ('Charleston', 'SC'), ('Savannah', 'GA'),
    ('Birmingham', 'AL'), ('Knoxville', 'TN'), ('Little Rock', 'AR'), ('Providence', 'RI'), ('Hartford', 'CT'),
    ('Naples', 'FL'), ('Sarasota', 'FL'), ('West Palm Beach', 'FL'), ('Boca Raton', 'FL'), ('Greenville', 'SC'),
    ('Chattanooga', 'TN'), ('Huntsville', 'AL'), ('Asheville', 'NC'), ('Bend', 'OR'), ('Santa Barbara', 'CA'),
)

HEADCOUNTS = ('1-10', '11-50', '51-200')
OUTCOMES = {'replied', 'interested', 'meeting', 'not_interested', 'bounced', 'unsubscribed', 'do_not_contact', 'wrong_person', 'customer',
            'complaint', 'engaged', 'delivered'}
OUTCOME_ALIASES = {'reply': 'replied', 'positive': 'interested', 'booked': 'meeting', 'bounce': 'bounced', 'hard_bounce': 'bounced',
                   'invalid': 'bounced', 'unsubscribe': 'unsubscribed', 'opt_out': 'unsubscribed', 'optout': 'unsubscribed',
                   'dnc': 'do_not_contact', 'negative': 'not_interested', 'wrong_contact': 'wrong_person',
                   'spam': 'complaint', 'opened': 'engaged', 'clicked': 'engaged', 'sent': 'delivered'}
AUTO_SUPPRESS = {'bounced', 'unsubscribed', 'do_not_contact', 'wrong_person', 'customer', 'complaint'}
BACKUPS_KEPT = 14


def today_iso(clock=time.time):
    return dt.datetime.fromtimestamp(clock()).date().isoformat()


class History:
    """Private SQLite memory: delivered leads, the domain backlog, crawls and market rotation."""

    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(path, 0o600)
        self.db = sqlite3.connect(str(path), timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS delivered(key TEXT PRIMARY KEY, batch TEXT NOT NULL, full_name TEXT, company_website TEXT, email TEXT, linkedin_url TEXT);
          CREATE TABLE IF NOT EXISTS domains(domain TEXT PRIMARY KEY, source TEXT, company TEXT, first_seen REAL NOT NULL, last_crawled REAL, status TEXT, people INTEGER DEFAULT 0, emails INTEGER DEFAULT 0);
          CREATE TABLE IF NOT EXISTS sites(domain TEXT PRIMARY KEY, crawled_at REAL NOT NULL, data TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS markets(kind TEXT, market TEXT, last_run TEXT, runs INTEGER DEFAULT 0, yield INTEGER DEFAULT 0, PRIMARY KEY(kind, market));
          CREATE TABLE IF NOT EXISTS prospects(key TEXT PRIMARY KEY, added_at REAL NOT NULL, data TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS firms(key TEXT PRIMARY KEY, added_at REAL NOT NULL, status TEXT NOT NULL, domain TEXT, data TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS suppressed(key TEXT PRIMARY KEY, reason TEXT NOT NULL, added_at REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS outcomes(key TEXT, outcome TEXT NOT NULL, source TEXT, email_source TEXT, batch TEXT, added_at REAL NOT NULL, PRIMARY KEY(key, outcome));
          CREATE TABLE IF NOT EXISTS refills(provider TEXT, pool TEXT, amount TEXT NOT NULL, period TEXT NOT NULL, source TEXT NOT NULL, last_applied TEXT, PRIMARY KEY(provider, pool));
          CREATE TABLE IF NOT EXISTS result_rows(hash TEXT PRIMARY KEY, tool TEXT, email TEXT, outcome TEXT, added_at REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS pattern_flags(domain TEXT PRIMARY KEY, reason TEXT NOT NULL, bounces INTEGER NOT NULL DEFAULT 1, added_at REAL NOT NULL);
        ''')
        cols = {r[1] for r in self.db.execute('PRAGMA table_info(delivered)')}
        for col in ('email_source', 'source'):
            if col not in cols:
                self.db.execute(f'ALTER TABLE delivered ADD COLUMN {col} TEXT')

    def close(self):
        self.db.close()

    # Delivered leads ----------------------------------------------------
    @staticmethod
    def keys_for(record):
        record = record if 'company_website' in record else quality.normalize_record(record)
        keys = []
        slug = quality.linkedin_slug(record.get('linkedin_url') or '')
        if slug:
            keys.append('li:' + slug)
        if record.get('email'):
            keys.append('em:' + record['email'].lower())
        name = _name_key(record.get('full_name'))
        if name and record.get('company_website'):
            keys.append('nd:' + '|'.join(name) + '|' + record['company_website'])
        return keys

    def is_fresh(self, record):
        """Never delivered, and not on the block list (person, email or whole company domain)."""
        keys = self.keys_for(record)
        if not keys:
            return False
        marks = ','.join('?' * len(keys))
        if self.db.execute(f'SELECT 1 FROM delivered WHERE key IN ({marks}) LIMIT 1', keys).fetchone():
            return False
        blocked = keys + (['dm:' + record['company_website']] if record.get('company_website') else [])
        marks = ','.join('?' * len(blocked))
        return self.db.execute(f'SELECT 1 FROM suppressed WHERE key IN ({marks}) LIMIT 1', blocked).fetchone() is None

    # Block list and outcome feedback -------------------------------------
    def suppress(self, rows, reason, clock=time.time):
        """Block people (by LinkedIn/email/name+domain) or whole domains (rows with only a website/domain)."""
        added = 0
        with self.db:
            for row in rows:
                record = row if 'company_website' in row else quality.normalize_record(row)
                keys = self.keys_for(record)
                domain_only = not keys and record.get('company_website')
                if domain_only:
                    keys = ['dm:' + record['company_website']]
                raw_domain = quality.normalize_host(row.get('domain') or row.get('Domain') or '')
                if raw_domain and not keys:
                    keys = ['dm:' + raw_domain]
                for key in keys:
                    added += self.db.execute('INSERT OR IGNORE INTO suppressed VALUES(?,?,?)', (key, reason, clock())).rowcount
        return added

    def record_outcomes(self, rows, clock=time.time):
        """Store replies/bounces/unsubscribes; bounces, unsubscribes and do-not-contact are auto-blocked."""
        counts = {}
        with self.db:
            for row in rows:
                keyed = {quality._key(k): v for k, v in row.items() if k}
                outcome = quality.fold(str(keyed.get('outcome') or keyed.get('status') or keyed.get('result') or '')).strip().replace(' ', '_')
                outcome = OUTCOME_ALIASES.get(outcome, outcome)
                if outcome not in OUTCOMES:
                    counts['ignored'] = counts.get('ignored', 0) + 1
                    continue
                record = quality.normalize_record(row)
                keys = self.keys_for(record)
                if not keys:
                    continue
                marks = ','.join('?' * len(keys))
                origin = self.db.execute(f'SELECT batch, email_source, source FROM delivered WHERE key IN ({marks}) LIMIT 1', keys).fetchone()
                for key in keys:
                    self.db.execute('INSERT OR REPLACE INTO outcomes VALUES(?,?,?,?,?,?)',
                                    (key, outcome, origin['source'] if origin else None, origin['email_source'] if origin else None,
                                     origin['batch'] if origin else None, clock()))
                    if outcome in AUTO_SUPPRESS:
                        self.db.execute('INSERT OR IGNORE INTO suppressed VALUES(?,?,?)', (key, outcome, clock()))
                counts[outcome] = counts.get(outcome, 0) + 1
        return counts

    def source_quality(self):
        """Per email source: delivered people, bounces, replies, and bounce/reply rates (one row per person)."""
        delivered = {}
        for r in self.db.execute("SELECT COALESCE(email_source,'unknown') AS src, COUNT(DISTINCT batch || full_name || IFNULL(company_website,'')) AS n FROM delivered GROUP BY src"):
            delivered[r['src']] = r['n']
        stats = {src: {'delivered': n, 'bounced': 0, 'replied': 0, 'engaged': 0, 'unsubscribed': 0} for src, n in delivered.items()}
        for r in self.db.execute("SELECT COALESCE(email_source,'unknown') AS src, outcome, COUNT(DISTINCT batch || key) AS n FROM outcomes WHERE key LIKE 'em:%' GROUP BY src, outcome"):
            entry = stats.setdefault(r['src'], {'delivered': 0, 'bounced': 0, 'replied': 0, 'engaged': 0, 'unsubscribed': 0})
            if r['outcome'] == 'bounced':
                entry['bounced'] += r['n']
            elif r['outcome'] in ('replied', 'meeting', 'interested'):
                entry['replied'] += r['n']
            elif r['outcome'] == 'engaged':
                entry['engaged'] += r['n']
            elif r['outcome'] in ('unsubscribed', 'complaint'):
                entry['unsubscribed'] += r['n']
        for entry in stats.values():
            base = max(entry['delivered'], 1)
            entry['bounce_rate'] = round(entry['bounced'] / base, 3)
            entry['reply_rate'] = round(entry['replied'] / base, 3)
        return stats

    # Email patterns that produced hard bounces ------------------------------
    def distrust_pattern(self, domain, reason, clock=time.time):
        """Stop guessing emails at ``domain`` (its inferred pattern bounced). True when newly flagged."""
        domain = quality.normalize_host(domain)
        if not domain:
            return False
        with self.db:
            if self.db.execute('UPDATE pattern_flags SET bounces=bounces+1 WHERE domain=?', (domain,)).rowcount:
                return False
            self.db.execute('INSERT INTO pattern_flags VALUES(?,?,1,?)', (domain, reason, clock()))
        return True

    def distrusted_patterns(self):
        return {r['domain']: {'reason': r['reason'], 'bounces': r['bounces'], 'added_at': r['added_at']}
                for r in self.db.execute('SELECT * FROM pattern_flags')}

    def clear_pattern(self, domain):
        with self.db:
            return self.db.execute('DELETE FROM pattern_flags WHERE domain=?', (quality.normalize_host(domain),)).rowcount

    # Recurring free allowances (operator-attested) -------------------------
    def add_refill(self, provider, pool, amount, period, source):
        if period not in ('daily', 'monthly'):
            raise ValueError('period must be daily or monthly')
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO refills VALUES(?,?,?,?,?,NULL)', (provider, pool, str(amount), period, source))

    def refills(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM refills ORDER BY provider, pool')]

    def remove_refill(self, provider, pool):
        with self.db:
            return self.db.execute('DELETE FROM refills WHERE provider=? AND pool=?', (provider, pool)).rowcount

    def apply_refills(self, store, day):
        """Reset each recurring free pool to its attested amount once per day/month. Never adds on top."""
        applied, skipped = [], []
        for r in self.refills():
            period_key = day[:10] if r['period'] == 'daily' else day[:7]
            if (r['last_applied'] or '') == period_key:
                continue
            try:
                store.confirm_budget(r['provider'], r['pool'], r['amount'], f"recurring {r['period']} free allowance: {r['source']}", attested_free=True)
            except Exception as error:
                skipped.append({'provider': r['provider'], 'pool': r['pool'], 'reason': str(error)})
                continue
            with self.db:
                self.db.execute('UPDATE refills SET last_applied=? WHERE provider=? AND pool=?', (period_key, r['provider'], r['pool']))
            applied.append(f"{r['provider']}.{r['pool']}={r['amount']}")
        return {'applied': applied, 'skipped': skipped}

    def mark_delivered(self, records, batch):
        with self.db:
            for record in records:
                for key in self.keys_for(record):
                    self.db.execute('INSERT OR IGNORE INTO delivered(key, batch, full_name, company_website, email, linkedin_url, email_source, source) '
                                    'VALUES(?,?,?,?,?,?,?,?)',
                                    (key, batch, record.get('full_name'), record.get('company_website'), record.get('email'),
                                     record.get('linkedin_url'), record.get('email_source'), record.get('source')))

    def delivered_count(self):
        return self.db.execute("SELECT COUNT(DISTINCT batch || full_name || IFNULL(company_website,'')) FROM delivered").fetchone()[0]

    # Domain backlog -----------------------------------------------------
    def add_domains(self, rows, source, clock=time.time):
        added = 0
        with self.db:
            for row in rows:
                if isinstance(row, str):
                    row = {'domain': row}
                host = quality.normalize_host(row.get('domain') or row.get('website') or '')
                company = row.get('organization') or row.get('company') or ''
                if not host or quality.website_problem(host, company):
                    continue
                cur = self.db.execute('INSERT OR IGNORE INTO domains(domain, source, company, first_seen) VALUES(?,?,?,?)',
                                      (host, source, company, clock()))
                added += cur.rowcount
        return added

    def licensed_domains(self, domains):
        """Subset of ``domains`` whose source is a public license record (never relevance-filtered)."""
        from .relevance import is_licensed_source
        found = set()
        rows = list(dict.fromkeys(d for d in domains if isinstance(d, str)))
        for start in range(0, len(rows), 500):
            chunk = rows[start:start + 500]
            marks = ','.join('?' * len(chunk))
            for r in self.db.execute(f'SELECT domain, source FROM domains WHERE domain IN ({marks})', chunk):
                if is_licensed_source(r['source']):
                    found.add(r['domain'])
        return found

    def domains_to_crawl(self, limit, recrawl_days=90, clock=time.time):
        cutoff = clock() - recrawl_days * 86400
        rows = self.db.execute('SELECT domain FROM domains WHERE last_crawled IS NULL OR last_crawled < ? '
                               'ORDER BY last_crawled IS NOT NULL, first_seen LIMIT ?', (cutoff, limit)).fetchall()
        return [r['domain'] for r in rows]

    def backlog(self, recrawl_days=90, clock=time.time):
        cutoff = clock() - recrawl_days * 86400
        return self.db.execute('SELECT COUNT(*) FROM domains WHERE last_crawled IS NULL OR last_crawled < ?', (cutoff,)).fetchone()[0]

    def save_sites(self, sites, clock=time.time):
        now = clock()
        with self.db:
            for site in sites:
                domain = site.get('domain')
                if not domain:
                    continue
                people = site.get('people') or []
                self.db.execute('INSERT OR REPLACE INTO sites VALUES(?,?,?)', (domain, now, json.dumps(site, ensure_ascii=False)))
                self.db.execute('INSERT OR IGNORE INTO domains(domain, source, first_seen) VALUES(?,?,?)', (domain, 'crawl', now))
                self.db.execute('UPDATE domains SET last_crawled=?, status=?, people=?, emails=? WHERE domain=?',
                                (now, site.get('status'), len(people), sum(1 for p in people if p.get('email')), domain))

    def recent_sites(self, days=100, clock=time.time):
        cutoff = clock() - days * 86400
        flagged = {r['domain'] for r in self.db.execute('SELECT domain FROM pattern_flags')}
        sites = []
        for r in self.db.execute('SELECT data FROM sites WHERE crawled_at >= ?', (cutoff,)):
            site = json.loads(r['data'])
            if site.get('domain') in flagged and site.get('pattern'):
                # A guess from this pattern hard-bounced: no more guesses here until cleared
                site['pattern'] = {**site['pattern'], 'confidence': 0, 'distrusted': True}
            sites.append(site)
        return sites

    # Prospects from APIs ------------------------------------------------
    def add_prospects(self, rows, clock=time.time):
        """Insert new prospects; enrich existing ones with fields they were missing (e.g. a found email)."""
        added = 0
        with self.db:
            for row in rows:
                keys = self.keys_for(row)
                if not keys:
                    continue
                marks = ','.join('?' * len(keys))
                existing = self.db.execute(f'SELECT key, data FROM prospects WHERE key IN ({marks}) LIMIT 1', keys).fetchone()
                if existing:
                    old = json.loads(existing['data'])
                    merged = {**old, **{k: v for k, v in row.items() if v not in (None, '', []) and not old.get(k)}}
                    if row.get('email') and quality.email_kind(quality.normalize_email_address(old.get('email'))) != 'work':
                        for k in ('email', 'email_source', 'email_verification', 'email_evidence_url'):
                            if row.get(k):
                                merged[k] = row[k]
                    if merged != old:
                        self.db.execute('UPDATE prospects SET data=? WHERE key=?', (json.dumps(merged, ensure_ascii=False), existing['key']))
                    continue
                self.db.execute('INSERT INTO prospects VALUES(?,?,?)', (keys[0], clock(), json.dumps(row, ensure_ascii=False)))
                added += 1
        return added

    # Public-record firms awaiting a website --------------------------------
    def add_firms(self, companies, prospects, clock=time.time):
        from .resolve import company_key
        people = {}
        for prospect in prospects:
            people.setdefault(quality.fold(prospect.get('company') or ''), []).append(prospect)
        added = 0
        with self.db:
            for company in companies:
                key = company_key(company)
                if not key:
                    continue
                data = {**company, 'people': people.get(quality.fold(company.get('organization') or ''), [])}
                cur = self.db.execute('INSERT OR IGNORE INTO firms VALUES(?,?,?,?,?)', (key, clock(), 'pending', None, json.dumps(data, ensure_ascii=False)))
                added += cur.rowcount
        return added

    def pending_firms(self, limit):
        rows = self.db.execute("SELECT data FROM firms WHERE status='pending' ORDER BY added_at LIMIT ?", (limit,)).fetchall()
        return [json.loads(r['data']) for r in rows]

    def set_firm(self, company, status, domain=None):
        from .resolve import company_key
        with self.db:
            self.db.execute('UPDATE firms SET status=?, domain=? WHERE key=?', (status, domain, company_key(company)))

    def note_firm(self, company, **fields):
        """Merge ``fields`` into a stored firm's data (e.g. guess_tried) without changing its status."""
        from .resolve import company_key
        key = company_key(company)
        with self.db:
            row = self.db.execute('SELECT data FROM firms WHERE key=?', (key,)).fetchone()
            if row is None:
                return False
            data = {**json.loads(row['data']), **fields}
            self.db.execute('UPDATE firms SET data=? WHERE key=?', (json.dumps(data, ensure_ascii=False), key))
        return True

    def prospects(self, days=90, clock=time.time):
        cutoff = clock() - days * 86400
        return [json.loads(r['data']) for r in self.db.execute('SELECT data FROM prospects WHERE added_at >= ?', (cutoff,))]

    # Market rotation ----------------------------------------------------
    def next_markets(self, kind, candidates, count):
        used = {r['market']: (r['last_run'] or '', r['runs']) for r in self.db.execute('SELECT * FROM markets WHERE kind=?', (kind,))}
        order = sorted(candidates, key=lambda m: (used.get(self._market_key(m), ('', 0))[0], used.get(self._market_key(m), ('', 0))[1]))
        return order[:count]

    def mark_market(self, kind, market, day, found=0):
        with self.db:
            self.db.execute('INSERT INTO markets(kind, market, last_run, runs, yield) VALUES(?,?,?,1,?) '
                            'ON CONFLICT(kind, market) DO UPDATE SET last_run=excluded.last_run, runs=runs+1, yield=yield+excluded.yield',
                            (kind, self._market_key(market), day, found))

    @staticmethod
    def _market_key(market):
        return ':'.join(market) if isinstance(market, (tuple, list)) else str(market)

    def stats(self):
        q = lambda sql: self.db.execute(sql).fetchone()[0]
        return {'delivered_keys': q('SELECT COUNT(*) FROM delivered'), 'batches': q('SELECT COUNT(DISTINCT batch) FROM delivered'),
                'domains_known': q('SELECT COUNT(*) FROM domains'), 'domains_uncrawled': q('SELECT COUNT(*) FROM domains WHERE last_crawled IS NULL'),
                'sites_stored': q('SELECT COUNT(*) FROM sites'), 'prospects_stored': q('SELECT COUNT(*) FROM prospects'),
                'suppressed': q('SELECT COUNT(*) FROM suppressed'), 'outcomes': q('SELECT COUNT(*) FROM outcomes'),
                'firms_pending_website': q("SELECT COUNT(*) FROM firms WHERE status='pending'"),
                'firms_resolved': q("SELECT COUNT(*) FROM firms WHERE status='resolved'"),
                'patterns_distrusted': q('SELECT COUNT(*) FROM pattern_flags'), 'results_rows_imported': q('SELECT COUNT(*) FROM result_rows')}


def _run_jobs(store, config, jobs, execute, env, transport, cap):
    """Send jobs through the budget ledger in chunks; stop at the first provider problem."""
    from .workflow import route_run
    report = {'planned': len(jobs), 'sent': 0, 'stopped': False, 'skipped_reasons': {}}
    jobs = jobs[:cap]
    for start in range(0, len(jobs), 100):
        chunk = jobs[start:start + 100]
        result = route_run(store, config, chunk, execute=execute, limit=len(chunk), env=env, transport=transport)
        report['sent'] += result.get('new_attempts') or 0
        for item in result['results']:
            if item.get('status') == 'skipped':
                reason = item.get('reason', 'skipped')
                report['skipped_reasons'][reason] = report['skipped_reasons'].get(reason, 0) + 1
        if result.get('stopped'):
            report['stopped'] = True
            break
    return report


# Verifiers in the order their free allowance should be spent (recurring daily/monthly first).
VERIFY_LANE_ORDER = ('reoon', 'quickemailverification', 'abstract', 'bouncer', 'myemailverifier',
                     'emailvalidation', 'emaillistverify', 'emailable', 'kickbox', 'millionverifier')


def plan_verify_lanes(store, config, emails, env, order=VERIFY_LANE_ORDER):
    """Pre-assign emails to funded verifiers so each provider can run in its own lane.

    Providers are taken in ``order`` (then any other enabled verifier). A provider is skipped if it
    is unknown to this version, disabled, paused, missing its key, or has no verify charge. Each
    gets at most as many emails as its confirmed free budget covers. Returns (lanes, unassigned)
    where lanes is an ordered dict provider -> [email].
    """
    from .providers import PROVIDERS, get_provider
    from .runner import Runner
    from .policy import parse_units
    probe = Runner(store, config, env=env)
    budgets = {(b['provider'], b['pool']): b['remaining_units'] for b in store.budgets()}
    configured = config.get('providers', {})
    candidates = [p for p in order if p in configured] + [p for p in configured if p not in order]
    lanes, rest = {}, list(dict.fromkeys(emails))
    for pid in candidates:
        if not rest:
            break
        entry = configured.get(pid) or {}
        if pid not in PROVIDERS or not entry.get('enabled'):
            continue
        try:
            provider = get_provider(pid)
        except Exception:
            continue
        if 'verify' not in provider.operations or store.provider_state(pid).get('paused') or not probe._auth(provider):
            continue
        charges = (entry.get('charges') or {}).get('verify') or {}
        try:
            per = {pool: parse_units(v, positive=True) for pool, v in charges.items()}
        except Exception:
            continue
        if not per:
            continue
        capacity = min(budgets.get((pid, pool), 0) // units for pool, units in per.items())
        if capacity <= 0:
            continue
        lanes[pid], rest = rest[:capacity], rest[capacity:]
    return lanes, rest


def _run_verify_lanes(store, config, lanes, execute, env, transport):
    """Run each provider's lane in its own thread and Store connection.

    Ledger guarantees are unchanged per provider: reserve (debit) before send, one active attempt
    per provider, finish/uncertain after, and a provider problem pauses and stops only its lane.
    The hash-chained audit stays consistent because every write is a BEGIN IMMEDIATE transaction.
    """
    import threading
    report = {'planned': sum(len(v) for v in lanes.values()), 'sent': 0, 'stopped': False,
              'skipped_reasons': {}, 'lanes': {}}

    def jobs_for(pid):
        return [{'provider': pid, 'operation': 'verify', 'payload': {'email': e}} for e in lanes[pid]]

    def merge(pid, step):
        report['lanes'][pid] = {k: step[k] for k in ('planned', 'sent', 'stopped')}
        report['sent'] += step['sent']
        report['stopped'] = report['stopped'] or step['stopped']
        for reason, n in step['skipped_reasons'].items():
            report['skipped_reasons'][reason] = report['skipped_reasons'].get(reason, 0) + n

    parallel = execute and len(lanes) > 1 and getattr(store, 'path', ':memory:') != ':memory:'
    if not parallel:
        for pid in lanes:
            merge(pid, _run_jobs(store, config, jobs_for(pid), execute, env, transport, len(lanes[pid])))
        return report
    results, errors = {}, {}

    from .store import Store

    def lane(pid):
        lane_store = Store(store.path, parallel_providers=True)
        try:
            results[pid] = _run_jobs(lane_store, config, jobs_for(pid), True, env, transport, len(lanes[pid]))
        except Exception as exc:  # recorded, never silently dropped
            errors[pid] = type(exc).__name__
        finally:
            lane_store.close()

    threads = [threading.Thread(target=lane, args=(pid,), name=f'verify-{pid}') for pid in lanes]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for pid in lanes:
        if pid in results:
            merge(pid, results[pid])
        else:
            report['lanes'][pid] = {'planned': len(lanes[pid]), 'sent': 0, 'stopped': True, 'error': errors.get(pid)}
            report['stopped'] = True
    return report


def _selection_key(record, source_quality=None):
    """Best first: email risk (low before medium), tier, has email, sources that bounce less
    (once feedback exists), LinkedIn, score."""
    bounce = ((source_quality or {}).get(record.get('email_source') or 'unknown') or {}).get('bounce_rate', 0)
    risk = quality.RISK_ORDER.get(record.get('email_risk') or ('medium' if record.get('email') else ''), 3)
    return (risk, quality.TIER_ORDER[record['tier']], 0 if record.get('email') else 1, 1 if bounce > 0.05 else 0,
            0 if record.get('linkedin_url') else 1, -record['score'])


def _email_domain(email):
    return email.rsplit('@', 1)[1].lower() if email and '@' in email else ''


def _risk_context(store, prospects):
    """Emails/domains a verifier or finder already called catch-all, and domains with any verify result."""
    from .pipeline import _verification_index
    verified = _verification_index(store)
    catch_all_emails = {e for e, v in verified.items() if v.get('status') == 'catch_all'}
    for prospect in prospects:
        email = (prospect.get('email') or '').lower()
        flags = set(prospect.get('email_risk_flags') or ())
        if email and (prospect.get('email_verification') == 'accept_all' or 'catch_all' in flags):
            catch_all_emails.add(email)
    return {'catch_all_emails': catch_all_emails,
            'catch_all_domains': {_email_domain(e) for e in catch_all_emails},
            'checked_domains': {_email_domain(e) for e in verified}}


def discover_locations(store):
    """{domain: (city, state)} from Hunter Discover jobs filtered to one headquarters location.

    The filter is Hunter's own headquarters match, so a company returned for
    "Austin, TX" is headquartered there (state-only jobs give just the state).
    """
    places = {}
    for attempt in store.attempts():
        if attempt.get('provider') != 'hunter' or attempt.get('operation') != 'discover' or attempt.get('state') != 'completed':
            continue
        filters = (attempt.get('payload') or {}).get('filters') or {}
        include = ((filters.get('headquarters_location') or {}).get('include')) or []
        if len(include) != 1 or not isinstance(include[0], dict):
            continue
        city, state = include[0].get('city') or '', quality.us_state_code(include[0].get('state') or '')
        if not state:
            continue
        for company in ((attempt.get('outcome') or {}).get('fields') or {}).get('companies') or []:
            domain = quality.normalize_host((company or {}).get('domain') or '')
            if domain and (city or domain not in places):
                places[domain] = (city, state)
    return places


def fill_locations(records, places):
    """Give records without a state the discovery location of their company domain (in place)."""
    filled = 0
    for record in records:
        city, state = quality.lead_city_state(record)
        place = places.get(record.get('company_website') or '')
        if place and not state:
            record['city'], record['state'] = city or place[0], place[1]
            filled += 1
        elif place and not city and place[0] and place[1] == state:
            record['city'] = place[0]
            filled += 1
    return filled


def annotate_email_risk(records, mx_facts, context):
    """Set email_risk / email_risk_reason / mx_provider / company_type on each record (in place)."""
    for record in records:
        domain = _email_domain(record.get('email'))
        if record.get('email') and (record['email'].lower() in context['catch_all_emails']
                                    or domain in context['catch_all_domains']):
            record['email_catch_all'] = True
        mx = mx_facts.get(domain)
        record['email_risk'], record['email_risk_reason'] = quality.email_risk(record, mx)
        record['mx_provider'] = (mx or {}).get('provider', '')
        record['company_type'] = quality.company_type(record)
    return records


def run_daily(work, store, config, *, target=500, execute=False, day=None, sources=DEFAULT_SOURCES,
              output_root=None, max_domains=None, workers=48, require_email=True, min_tier='B',
              caps=None, env=None, transport=None, fetcher=None, record_sources=(), record_source_names=DEFAULT_RECORD_SOURCES,
              search_provider='serper', records_getter=None, resolver=None, clock=time.time, mx_check=None, mx_resolver=None,
              targeting=None, targeting_name=None):
    """One daily cycle. Returns a report; writes files under ``output_root/day``.

    ``mx_check`` (free DNS lookups of email domains) defaults to on for live runs
    (``execute`` with the real network transport) or when ``mx_resolver`` is given.
    """
    work = Path(work)
    env = os.environ if env is None else env
    day = day or today_iso(clock)
    caps = {**DEFAULT_CAPS, **(caps or {})}
    max_domains = max_domains or int(target / EXPECTED_LEADS_PER_DOMAIN * 1.2)
    history = History(work / 'lead-history.sqlite3')
    report = {'date': day, 'execute': execute, 'target': target, 'steps': {}}
    from . import targeting as targeting_mod
    rules = targeting_mod.normalize_targeting(targeting)
    if rules:
        report['targeting'] = rules
        if targeting_name:
            report['targeting_name'] = targeting_name
        record_source_names, skipped_records = targeting_mod.allowed_record_sources(record_source_names, rules)
        if skipped_records:
            report['targeting_record_sources_skipped'] = skipped_records
    try:
        from . import discovery
        from .policy import US_STATES
        from .team import partition_owned, pull_shared, record_finds

        pulled = pull_shared(work)
        if pulled:
            report['team_pulled'] = {
                'added': pulled.get('added', 0),
                'duplicates': pulled.get('duplicates', 0),
                'leads_count': pulled.get('leads_count', 0),
            }

        def run(label, jobs, cap):
            step = _run_jobs(store, config, jobs, execute, env, transport, cap)
            report['steps'][label] = step
            if execute and step['sent']:
                gathered = discovery.collect(store)
                step['domains_added'] = history.add_domains(gathered['companies'], label)
                step['prospects_added'] = history.add_prospects(gathered['prospects'])
            return step

        if execute:
            report['steps']['refills'] = history.apply_refills(store, day)
            report['steps']['resume-pending'] = _resume_pending(store, config, env, transport)

        if 'records' in sources:
            step = {'domains_added': 0, 'prospects_added': 0, 'firms_added': 0, 'sources': {}}
            for item in record_sources:
                step['domains_added'] += history.add_domains(item.get('companies', []), item.get('source', 'records'))
                with_site = [p for p in item.get('prospects', []) if p.get('company_website')]
                step['prospects_added'] += history.add_prospects(with_site)
                step['firms_added'] += history.add_firms([c for c in item.get('companies', []) if not c.get('domain')],
                                                         [p for p in item.get('prospects', []) if not p.get('company_website')])
            if execute:
                from .records import fetch_source, RecordsError
                for name in record_source_names:
                    if name == 'sec-form-d' and '@' not in (env.get('SEC_USER_AGENT') or ''):
                        step['sources'][name] = 'skipped: set SEC_USER_AGENT="Your Name you@example.com"'
                        continue
                    cursor = history.db.execute("SELECT last_run FROM markets WHERE kind='records_cursor' AND market=?", (name,)).fetchone()
                    try:
                        fetched = fetch_source(name, since=cursor['last_run'] if cursor else None, max_rows=caps['records'], env=env, getter=records_getter)
                    except RecordsError as error:
                        step['sources'][name] = f'error: {error}'
                        continue
                    firms = history.add_firms(fetched['companies'], fetched['prospects'])
                    step['firms_added'] += firms
                    step['sources'][name] = {'companies': len(fetched['companies']), 'prospects': len(fetched['prospects']), 'new_firms': firms}
                    if fetched.get('cursor'):
                        history.mark_market('records_cursor', name, fetched['cursor'])
            report['steps']['records'] = step

        if 'resolve' in sources:
            report['steps']['resolve'] = _resolve_firms(history, store, config, execute, env, transport, caps, search_provider,
                                                        fetcher=fetcher, dns=resolver, work=work, day=day)

        backlog = history.backlog()
        report['backlog_before'] = backlog

        def in_state_queue(limit):
            # With a state filter the crawl queue is the in-state part of the backlog (located by
            # Hunter's headquarters match); other domains wait for an unfiltered run
            places = discover_locations(store)
            pool = history.domains_to_crawl(max(limit * 20, 2000))
            return [d for d in pool if (places.get(d) or ('', ''))[1] in rules['states']][:limit]

        def current_backlog():
            return len(in_state_queue(max_domains)) if rules.get('states') else history.backlog()

        if rules.get('states'):
            backlog = current_backlog()
            report['targeting_state_backlog'] = backlog
        if backlog < max_domains and 'hunter-discover' in sources:
            # Free calls; rotate cities (most distinct results), states x sizes, and keyword niches
            cap = caps['hunter-discover']
            per_city = len(HEADCOUNTS)
            plan = targeting_mod.discovery_plan(rules, METROS, US_STATES, discovery.HUNTER_REAL_ESTATE_INDUSTRIES)
            keyword_pool = list(dict.fromkeys(plan['keywords'] + [k[0] for k in KEYWORD_SETS]))
            cities = history.next_markets('hunter_city', plan['metros'], max(1, int(cap * 0.6) // per_city)) if plan['metros'] else []
            states = history.next_markets('hunter_state', plan['states'], max(1, int(cap * 0.2) // per_city))
            kw_states = history.next_markets('hunter_keyword_state', plan['states'], 1)
            kw_sets = history.next_markets('hunter_keyword', keyword_pool, max(1, int(cap * 0.2) // per_city))
            jobs = (discovery.hunter_discover_city_jobs(cities, headcounts=HEADCOUNTS, industries=plan['industries'])
                    + discovery.hunter_discover_jobs(states, headcounts=HEADCOUNTS, industries=plan['industries'])
                    + discovery.hunter_discover_keyword_jobs(kw_states, [[k] for k in kw_sets], headcounts=HEADCOUNTS))
            step = run('hunter-discover', jobs, cap)
            step['markets'] = {'cities': [':'.join(c) for c in cities], 'states': states, 'keywords': kw_sets, 'keyword_state': kw_states}
            if execute and step['sent']:
                for city in cities:
                    history.mark_market('hunter_city', city, day)
                for state in states:
                    history.mark_market('hunter_state', state, day)
                for state in kw_states:
                    history.mark_market('hunter_keyword_state', state, day)
                for kw in kw_sets:
                    history.mark_market('hunter_keyword', kw, day)
        if current_backlog() < max_domains and 'google-places' in sources:
            metros = history.next_markets('places_metro', targeting_mod.discovery_plan(rules, METROS, US_STATES, ())['metros'] or list(METROS), max(1, caps['google-places'] // 3))
            step = run('google-places', discovery.places_jobs(metros), caps['google-places'])
            step['markets'] = [':'.join(m) for m in metros]
            if execute and step['sent']:
                for metro in metros:
                    history.mark_market('places_metro', metro, day)
        if current_backlog() < max_domains and 'osm' in sources:
            areas = history.next_markets('osm_area', targeting_mod.discovery_plan(rules, METROS, US_STATES, ())['metros'] or list(METROS), caps['osm'])
            step = run('osm', discovery.osm_jobs(areas), caps['osm'])
            step['markets'] = [':'.join(a) for a in areas]
            if execute and step['sent']:
                for area in areas:
                    history.mark_market('osm_area', area, day)
        if 'apollo' in sources:
            states = history.next_markets('apollo_state', list(rules.get('states') or US_STATES), 1)
            step = run('apollo', discovery.apollo_jobs(list(discovery.DEFAULT_DM_TITLES), [US_STATES[s] + ', US' for s in states], pages=caps['apollo']), caps['apollo'])
            if execute and step['sent']:
                history.mark_market('apollo_state', states[0], day)
                partial = [p for p in history.prospects() if p.get('apollo_id') and not p.get('email')]
                run('apollo-enrich', discovery.apollo_enrich_jobs(partial), caps['apollo-enrich'])

        if 'crawl' in sources:
            domains = in_state_queue(max_domains) if rules.get('states') else history.domains_to_crawl(max_domains)
            step = {'domains_planned': len(domains)}
            if execute and domains:
                from .extract import crawl_domains
                from .webfetch import SafeFetcher
                own = fetcher is None
                active = fetcher or SafeFetcher(cache_path=work / 'web-cache.sqlite3', per_host_delay=1.0)
                try:
                    sites = crawl_domains(domains, active, max_pages=6, workers=workers,
                                          trusted=history.licensed_domains(domains))
                finally:
                    if own:
                        active.close()
                history.save_sites(sites)
                step.update(sites_ok=sum(s['status'] == 'ok' for s in sites),
                            off_topic=sum(s['status'] == 'off_topic' for s in sites),
                            people=sum(len(s['people']) for s in sites),
                            people_with_email=sum(1 for s in sites for p in s['people'] if p.get('email')))
            report['steps']['crawl'] = step
        if 'jina-read' in sources:
            report['steps']['jina-read'] = _jina_read_sites(history, store, config, execute, env, transport, caps['jina-read'])
        if 'render-js' in sources:
            report['steps']['render-js'] = _render_js_sites(history, store, config, execute, env, transport, caps['render-js'])
        if 'hunter-domains' in sources:
            rows = history.db.execute("SELECT domain FROM domains WHERE last_crawled IS NOT NULL AND emails=0 "
                                      "ORDER BY last_crawled DESC LIMIT ?", (caps['hunter-domains'],)).fetchall()
            run('hunter-domains', discovery.hunter_domain_jobs([r['domain'] for r in rows]), caps['hunter-domains'])

        target_cfg = quality.Target(min_tier=min_tier)
        result = build(prospects=history.prospects(), site_results=history.recent_sites(), store=store, target=target_cfg)
        from . import emailcheck
        mx_on = mx_check if mx_check is not None else (mx_resolver is not None or (execute and transport is None))
        mx_facts = {}

        def check_mx(emails):
            if not mx_on:
                return
            domains = {_email_domain(e) for e in emails} - set(mx_facts)
            # Reserved test domains only resolve through an injected resolver
            domains = [d for d in domains if d and (mx_resolver is not None or not quality.is_reserved_domain(d))]
            if domains:
                mx_facts.update(emailcheck.check_domains(history.db, domains, resolver=mx_resolver, clock=clock))

        check_mx([r.get('email') for r in result['clean'] + result['review']] + [c['Candidate Email'] for c in result['candidates']])
        if 'find-emails' in sources:
            # Fresh decision makers with a known company site but no published email
            need = [r for r in result['clean'] + result['review'] if not r.get('email') and r.get('company_website')
                    and r.get('decision_maker') and history.is_fresh(r)]
            need.sort(key=_selection_key)
            jobs = [{'provider': 'auto', 'operation': 'find', 'payload': {'name': r['full_name'], 'domain': r['company_website'],
                                                                          'domain_evidence_url': f"https://{r['company_website']}/"}}
                    for r in need[:caps['find-emails']]]
            run('find-emails', jobs, caps['find-emails'])
        if 'verify' in sources:
            # Risk-first: guesses for fresh decision makers, weak published emails, finder results, the rest
            records = result['clean'] + result['review']
            by_person = {(r.get('company_website'), _name_key(r.get('full_name'))): r for r in records}
            by_email = {r['email'].lower(): r for r in records if r.get('email')}
            candidates = []
            for c in result['candidates']:
                r = by_person.get((c['Company Website'], _name_key(c['Full Name']))) or {}
                candidates.append({'email': c['Candidate Email'], 'kind': 'pattern', 'decision_maker': r.get('decision_maker', True),
                                   'fresh': history.is_fresh(r) if r else True, 'tier': c.get('Tier'), 'score': r.get('score')})
            prospects_now = history.prospects()
            for p in prospects_now:
                if p.get('email') and str(p.get('email_source', '')).endswith('_finder') and p.get('email_verification') not in ('valid', 'provider_valid'):
                    r = by_email.get(p['email'].lower()) or {}
                    candidates.append({'email': p['email'], 'kind': 'finder', 'tier': r.get('tier'), 'score': r.get('score'),
                                       'fresh': history.is_fresh(r) if r else True})
            for r in result['clean']:
                if r.get('email') and r.get('email_source') == 'website_published' and r.get('decision_maker') \
                        and not r.get('email_verification') and quality.email_kind(r['email']) == 'work':
                    candidates.append({'email': r['email'], 'kind': 'published', 'name_match': r.get('email_name_match'),
                                       'fresh': history.is_fresh(r), 'tier': r['tier'], 'score': r['score']})
            context = _risk_context(store, prospects_now)
            checked = {((a.get('payload') or {}).get('email') or '').lower() for a in store.attempts() if a.get('operation') == 'verify'}
            no_mail = [d for d, m in mx_facts.items() if m.get('has_mx') is False]
            order = emailcheck.prioritize_verify(candidates, {'checked': checked, 'catch_all_domains': context['catch_all_domains'],
                                                              'checked_domains': context['checked_domains'], 'no_mail_domains': no_mail},
                                                 published_cap=caps['verify-published'])
            # Risk-ordered list feeds per-provider lanes (each verifier in its own thread, within its own budget)
            lanes, unassigned = plan_verify_lanes(store, config, order['emails'][:caps['verify']], env)
            step = _run_verify_lanes(store, config, lanes, execute, env, transport)
            step['planned'] = len(order['emails'])
            step['unassigned_no_budget'] = len(unassigned)
            report['steps']['verify'] = step
            if execute and step['sent']:
                gathered = discovery.collect(store)
                step['domains_added'] = history.add_domains(gathered['companies'], 'verify')
                step['prospects_added'] = history.add_prospects(gathered['prospects'])
            step['already_checked'] = order['skipped'].get('already_checked', 0)
            step['priority'] = order['groups']
            step['not_worth_checking'] = {k: v for k, v in order['skipped'].items() if k != 'already_checked'}
        if execute and ({'find-emails', 'verify'} & set(sources)):
            result = build(prospects=history.prospects(), site_results=history.recent_sites(), store=store, target=target_cfg)

        check_mx([r.get('email') for r in result['clean']])
        annotate_email_risk(result['clean'], mx_facts, _risk_context(store, history.prospects()))
        safety = {'mx_check': mx_on, 'mx': emailcheck.summarize(mx_facts), 'dropped': {},
                  'locations_filled': fill_locations(result['clean'], discover_locations(store))}
        from .targeting import match_targeting
        owned_by_team, team_skipped = partition_owned(result['clean'], work)
        if rules.get('include_titles'):
            review_owned, _review_skip = partition_owned(result.get('review') or [], work)
            seen_keys = set()
            for record in owned_by_team:
                seen_keys |= set(History.keys_for(record))
            for record in review_owned:
                keys = set(History.keys_for(record))
                if keys and not (keys & seen_keys) and match_targeting(record, rules):
                    owned_by_team.append(record)
                    seen_keys |= keys
        report['skipped_team_owned'] = len(team_skipped)
        pool = []
        skipped_targeting = 0
        for r in owned_by_team:
            if not ((r.get('email') or not require_email) and history.is_fresh(r)):
                continue
            if r.get('email_risk') == 'high':
                safety['dropped'][r['email_risk_reason']] = safety['dropped'].get(r['email_risk_reason'], 0) + 1
                if require_email:
                    continue
                r['rejected_email'], r['email'], r['email_risk'] = r['email'], '', ''
            pool.append(r)
        if rules:
            pool, targeting_step = targeting_mod.filter_records(pool, rules)
            skipped_targeting = targeting_step['checked'] - targeting_step['kept']
            report['steps']['targeting'] = {'name': targeting_name, **targeting_step}
        report['skipped_targeting'] = skipped_targeting
        report['steps']['email-safety'] = safety
        source_quality = history.source_quality()
        pool.sort(key=lambda r: _selection_key(r, source_quality))
        out_dir = Path(output_root or work / 'daily') / (day if execute else day + '-preview')
        prior = load_daily_records(out_dir) if execute else []
        slot = max(0, target - len(prior))
        prior_keys = set()
        for record in prior:
            prior_keys |= set(History.keys_for(record))
        picked, seen = [], set()
        for record in pool:
            keys = set(History.keys_for(record))
            if keys & seen or keys & prior_keys:
                continue
            seen |= keys
            picked.append(record)
            if len(picked) >= slot:
                break

        if 'xray-linkedin' in sources:
            missing = [r for r in picked if not r.get('linkedin_url')][:caps['xray-linkedin']]
            engine = next((e for e in [search_provider, *SEARCH_FALLBACK] if e != 'exa' and _funded(store, config, e, SEARCH_POOLS[e]) >= len(missing)), search_provider)
            step = run('xray-linkedin', linkedin_match_jobs(missing, engine), caps['xray-linkedin'])
            step['engine'] = engine
            if execute and step['sent']:
                step['linkedin_filled'] = attach_linkedin(picked, discovery.collect(store)['prospects'])
        if 'pdl' in sources:
            still = [r for r in picked if not r.get('linkedin_url')][:caps['pdl']]
            prospects = [{'full_name': r['full_name'], 'company': r.get('company'), 'company_website': r.get('company_website')} for r in still]
            step = run('pdl', discovery.pdl_enrich_jobs(prospects), caps['pdl'])
            if execute and step['sent']:
                step['linkedin_filled'] = attach_linkedin(picked, discovery.collect(store)['prospects'])

        merged = merge_daily_records(prior, picked) if execute else picked
        report['fresh_available'] = len(pool)
        report['delivered'] = len(merged)
        report['this_run'] = len(picked)
        report['prior_delivered'] = len(prior)
        report['shortfall'] = max(0, target - len(merged))
        report['delivered_by_source'] = _count_by(merged, 'email_source')
        report['delivered_by_risk'] = _count_by(merged, 'email_risk')
        report['delivered_by_company_type'] = _count_by(merged, 'company_type')
        if report['shortfall']:
            report['advice'] = shortfall_advice(report['shortfall'], history)
        if execute and prior:
            stamp = str(int(clock()))
            report['archived'] = [p for p in (
                archive_daily_file(out_dir / 'report.json', 'runs', stamp + '-report.json'),
                archive_daily_file(out_dir / 'report.md', 'runs', stamp + '-report.md'),
                archive_daily_file(out_dir / 'audit.json', 'audits', stamp + '-audit.json'),
                archive_daily_file(out_dir / 'audit.md', 'audits', stamp + '-audit.md'),
            ) if p]
        report['files'] = write_daily(merged, out_dir)
        if execute:
            history.mark_delivered(picked, day)
            report['team_recorded'] = record_finds(work, picked, day)
        report['history'] = history.stats()
        report['source_quality'] = source_quality
        report['budgets_left'] = {f"{b['provider']}.{b['pool']}": b['remaining'] for b in store.budgets()}
        if execute:
            report['backup'] = backup_workspace(work, day)
        (out_dir / 'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        os.chmod(out_dir / 'report.json', 0o600)
        (out_dir / 'report.md').write_text(render_report(report), encoding='utf-8')
        os.chmod(out_dir / 'report.md', 0o600)
        return report
    finally:
        history.close()


def _count_by(rows, key):
    counts = {}
    for row in rows:
        value = row.get(key) or 'none'
        counts[value] = counts.get(value, 0) + 1
    return counts


def _resume_pending(store, config, env, transport):
    """Give each saved pending job (e.g. Icypeas, Verifalia) one GET/read poll before new work."""
    from .runner import Runner
    done = {'polled': 0, 'still_pending': 0, 'errors': 0}
    for attempt in [a for a in store.attempts() if a['state'] == 'pending']:
        try:
            result = Runner(store, config, env=env, transport=transport).resume(attempt['id'], execute=True)
        except Exception:
            done['errors'] += 1
            continue
        done['polled'] += 1
        status = (result.get('results') or [{}])[0].get('status')
        done['still_pending'] += status == 'pending'
    return done


def _funded(store, config, provider, pool):
    entry = config.get('providers', {}).get(provider, {})
    if not entry.get('enabled') or store.provider_state(provider).get('paused'):
        return 0
    return next((b['remaining_units'] // 1_000_000 for b in store.budgets() if b['provider'] == provider and b['pool'] == pool), 0)


SEARCH_POOLS = {'serper': 'queries', 'tavily': 'credits', 'exa': 'queries', 'brave': 'queries',
                'zenserp': 'searches', 'linkup': 'queries', 'youcom': 'queries',
                'serpapi': 'searches', 'firecrawl': 'search_ops'}


def _resolve_firms(history, store, config, execute, env, transport, caps, provider='serper', *, fetcher=None,
                   dns=None, work=None, day=None):
    """Find websites for public-record firms.

    1. Hunter Discover by firm name (free, 50 names per call) - exact name/state matches only
    2. Domain guessing from the firm name (free): DNS check, then the homepage must show the firm's
       name in its title/heading/© line and its city/county/state. Each firm is guessed once.
    3. Search engines for the rest, cheapest first: Serper batches of 100, then Tavily, then Exa
    Saved results are always reused before anything is spent.

    ``fetcher`` is a SafeFetcher-compatible object; without one a workspace SafeFetcher is opened
    (cache work/web-cache.sqlite3) only when there are firms to guess. ``dns`` is getaddrinfo-
    compatible; by default the SafeFetcher's resolver (or socket.getaddrinfo for an own fetcher),
    and no DNS precheck for an injected fetcher without one.
    """
    from .resolve import (exa_resolution_jobs, resolution_batch_jobs, resolution_jobs, resolve_by_guess,
                          resolve_from_hunter, resolve_from_store, searched_queries)
    from .discovery import hunter_name_resolution_jobs
    step = {'pending_checked': 0, 'resolved': 0, 'no_website': 0, 'by_method': {}}

    def apply(rows, method, final):
        left = []
        for row in rows:
            status = (row.get('resolution') or {}).get('status')
            if status == 'resolved' and row.get('company_website'):
                domain = row['company_website']
                history.set_firm(row, 'resolved', domain)
                history.add_domains([{'domain': domain, 'company': row.get('organization')}], row.get('source', 'records'))
                history.add_prospects([{**p, 'company_website': domain} for p in row.get('people', [])])
                step['resolved'] += 1
                step['by_method'][method] = step['by_method'].get(method, 0) + 1
            elif final and status in ('no_confident_domain', 'verification_failed'):
                history.set_firm(row, 'no_website')
                step['no_website'] += 1
            else:
                left.append({k: v for k, v in row.items() if k not in ('resolution',)} | {'company_website': ''})
        return left

    firms = history.pending_firms(max(caps['resolve-hunter'], caps['resolve'], caps.get('resolve-guess', 0)))
    step['pending_checked'] = len(firms)
    if not firms:
        return step
    # 1. Hunter by name (free)
    if _funded(store, config, 'hunter', 'discover_requests'):
        names = firms[:caps['resolve-hunter']]
        jobs = hunter_name_resolution_jobs(names)
        step['hunter'] = _run_jobs(store, config, jobs, execute, env, transport, len(jobs))
    left = apply(resolve_from_hunter(store, firms), 'hunter_name', final=False)
    # 2. Guess the domain from the firm name (free; each firm once)
    guessable = [f for f in left if not f.get('guess_tried')][:caps.get('resolve-guess', 0)]
    step['guess_tried'], step['guess_matched'] = 0, 0
    if guessable and not execute:
        step['guess_planned'] = len(guessable)
    elif guessable:
        active, own = fetcher, False
        if active is None:
            from .webfetch import SafeFetcher
            active, own = SafeFetcher(cache_path=(Path(work) / 'web-cache.sqlite3') if work else None, per_host_delay=1.0), True
        resolver = dns if dns is not None else getattr(active, '_resolver', None)
        try:
            guessed = resolve_by_guess(guessable, active, dns=resolver)
        finally:
            if own:
                active.close()
        step['guess_tried'] = len(guessable)
        stamp = day or today_iso()
        resolved_rows = [r for r in guessed if (r.get('resolution') or {}).get('status') == 'resolved']
        for row in guessed:
            if (row.get('resolution') or {}).get('status') != 'resolved':
                history.note_firm(row, guess_tried=stamp)
        step['guess_matched'] = len(resolved_rows)
        apply(resolved_rows, 'guess', final=False)
        done = {_firm_identity(r) for r in resolved_rows}
        left = [f for f in left if _firm_identity(f) not in done]
    # 3. Search engines for what is left, within the daily cap
    left = left[:caps['resolve']]
    order = [provider] + [p for p in SEARCH_FALLBACK if p != provider]
    for engine in order:
        if not left:
            break
        left = apply(resolve_from_store(store, left, engine), engine, final=True)
        budget = _funded(store, config, engine, SEARCH_POOLS.get(engine, 'queries'))
        if not left or not budget:
            continue
        batch = left[:budget]
        if engine == 'serper':
            jobs = resolution_batch_jobs(batch, store=store)
        elif engine == 'exa':
            jobs = exa_resolution_jobs(batch)
        else:
            jobs = resolution_jobs(batch, engine)
        step.setdefault('search', {})[engine] = _run_jobs(store, config, jobs, execute, env, transport, len(jobs))
        if execute:
            left = apply(resolve_from_store(store, left, engine), engine, final=True)
    return step


def _firm_identity(company):
    from .resolve import company_key
    return company_key(company)


def _render_js_sites(history, store, config, execute, env, transport, cap):
    """Firecrawl renders JavaScript-only sites the crawler saw as empty: homepage first, then its best team page."""
    from .discovery import collect, firecrawl_scrape_jobs, scraped_pages
    from .extract import discover_pages, extract_company, extract_people, merge_people
    rows = history.db.execute("SELECT domain FROM domains WHERE status='no_people' ORDER BY last_crawled DESC LIMIT ?", (cap,)).fetchall()
    sites = {r['domain']: json.loads(d['data']) for r in rows for d in history.db.execute('SELECT data FROM sites WHERE domain=?', (r['domain'],))}
    step = {'sites': len(sites)}
    if not sites or not _funded(store, config, 'firecrawl', 'scrape_credits'):
        return step
    homes = [s.get('homepage_url') or f"https://{d}/" for d, s in sites.items()]
    step['homepages'] = _run_jobs(store, config, firecrawl_scrape_jobs(homes), execute, env, transport, cap)
    if not execute:
        return step
    pages = {p['requested_url']: p for p in scraped_pages(store)}
    followups = []
    for domain, site in sites.items():
        page = pages.get(site.get('homepage_url') or f"https://{domain}/")
        if page and page.get('html'):
            best = discover_pages(page['url'], page['html'], limit=1)
            if best:
                followups.append(best[0])
    step['team_pages'] = _run_jobs(store, config, firecrawl_scrape_jobs(followups), execute, env, transport, cap)
    pages = {p['requested_url']: p for p in scraped_pages(store)}
    updated = []
    for domain, site in sites.items():
        found = []
        for url, page in pages.items():
            if page.get('html') and (url.split('/')[2].removeprefix('www.') == domain):
                found += extract_people(page['html'], page['url'])
                if not site.get('company', {}).get('name'):
                    site['company'] = extract_company(page['html'], page['url'])
        if found:
            site['people'] = merge_people((site.get('people') or []) + found)
            site['status'] = 'ok'
            site.setdefault('errors', []).append('rendered_by_firecrawl')
            updated.append(site)
    history.save_sites(updated)
    step['sites_with_people'] = len(updated)
    step['people'] = sum(len(s['people']) for s in updated)
    return step


def _jina_pages(store):
    """Completed Jina reads as {url, html} rows (HTML already decoded)."""
    from .discovery import decode_html
    pages = []
    for attempt in store.attempts():
        outcome = attempt.get('outcome') or {}
        if attempt.get('provider') != 'jina' or attempt.get('operation') != 'read' \
                or attempt.get('state') not in ('completed', 'reviewed') or outcome.get('error') \
                or outcome.get('assessment') != 'results':
            continue
        html_text = decode_html((outcome.get('fields') or {}).get('html_zb64'))
        if not html_text:
            continue
        fields = outcome.get('fields') or {}
        pages.append({'url': fields.get('url') or (attempt.get('payload') or {}).get('url'),
                      'html': html_text})
    return pages


def _page_host(url):
    if not isinstance(url, str) or '://' not in url:
        return ''
    return url.split('/')[2].removeprefix('www.')


def _jina_read_sites(history, store, config, execute, env, transport, cap):
    """Jina Reader fallback for crawled sites that published no emails (keyless or keyed).

    Homepage first, then the same team-page follow-up Firecrawl render uses. Cached homepage
    reads are not re-debited, so a second same-day run can spend remaining reads on team pages.
    """
    from .discovery import jina_read_jobs
    from .extract import discover_pages, extract_company, extract_people, merge_people
    from .models import PolicyError
    rows = history.db.execute(
        "SELECT domain FROM domains WHERE last_crawled IS NOT NULL AND emails=0 "
        "ORDER BY last_crawled DESC LIMIT ?", (cap,)).fetchall()
    domains = [r['domain'] for r in rows]
    step = {'sites': len(domains)}
    if not domains or not _funded(store, config, 'jina', 'reads'):
        return step
    homes = [f'https://{domain}/' for domain in domains]
    step['reads'] = _run_jobs(store, config, jina_read_jobs(homes), execute, env, transport, cap)
    pages = _jina_pages(store)
    followups = []
    for domain in domains:
        home = next((p for p in pages if _page_host(p.get('url')) == domain), None)
        if not home or not home.get('html'):
            continue
        try:
            best = discover_pages(home['url'] or f'https://{domain}/', home['html'], limit=1)
        except (PolicyError, ValueError, TypeError):
            best = []
        for url in best:
            if url not in followups and url not in homes:
                followups.append(url)
    leftover = max(0, cap - (step['reads'].get('sent') or 0))
    followups = followups[:leftover]
    if followups and _funded(store, config, 'jina', 'reads'):
        step['team_pages'] = _run_jobs(store, config, jina_read_jobs(followups), execute, env, transport, len(followups))
        if execute:
            pages = _jina_pages(store)
    if not execute:
        return step
    updated = []
    for domain in domains:
        found, company = [], None
        for page in pages:
            if _page_host(page.get('url')) != domain:
                continue
            found += extract_people(page['html'], page['url'])
            company = company or extract_company(page['html'], page['url'])
        if not found:
            continue
        site = {'domain': domain, 'status': 'ok', 'homepage_url': f'https://{domain}/',
                'company': company or {'name': ''}, 'people': merge_people(found),
                'errors': ['read_by_jina']}
        updated.append(site)
    if updated:
        history.save_sites(updated)
    step['sites_with_people'] = len(updated)
    step['people'] = sum(len(s.get('people') or []) for s in updated)
    return step


def backup_workspace(work, day):
    """Consistent SQLite copies of the ledger and history; keeps the newest BACKUPS_KEPT days."""
    work = Path(work)
    root = work / 'backups'
    target = root / re.sub(r'[^0-9A-Za-z_-]', '_', day)
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    os.chmod(target, 0o700)
    saved = []
    for name in ('lead-history.sqlite3', 'research.sqlite3'):
        source = work / name
        if not source.exists():
            continue
        destination = target / name
        with sqlite3.connect(str(source)) as src, sqlite3.connect(str(destination)) as dst:
            src.backup(dst)
        os.chmod(destination, 0o600)
        saved.append(name)
    for old in sorted((d for d in root.iterdir() if d.is_dir()), key=lambda d: d.stat().st_mtime)[:-BACKUPS_KEPT]:
        for file in old.iterdir():
            file.unlink()
        old.rmdir()
    return {'folder': str(target), 'files': saved}


def render_report(report):
    """Short human summary for the morning message."""
    lines = [f"Leads {report['date']}: {report['delivered']} of {report['target']} delivered" + (" (preview)" if not report['execute'] else ''), '']
    if report.get('prior_delivered') or report.get('this_run'):
        lines.append(f"This run added {report.get('this_run', 0)} (already on file today: {report.get('prior_delivered', 0)})")
    by = report.get('delivered_by_source') or {}
    if by:
        lines.append('Email sources: ' + ', '.join(f'{k} {v}' for k, v in sorted(by.items(), key=lambda kv: -kv[1])))
    risk = report.get('delivered_by_risk') or {}
    if risk:
        lines.append('Email risk: ' + ', '.join(f'{k or "none"} {risk[k]}' for k in ('low', 'medium', '') if risk.get(k)))
    types = report.get('delivered_by_company_type') or {}
    if types:
        lines.append('Company types: ' + ', '.join(f'{k} {v}' for k, v in sorted(types.items(), key=lambda kv: -kv[1])))
    steps = report.get('steps', {})
    safety = steps.get('email-safety') or {}
    if safety.get('dropped'):
        lines.append('Dropped unsafe emails: ' + ', '.join(f'{k} {v}' for k, v in sorted(safety['dropped'].items(), key=lambda kv: -kv[1])))
    if report.get('skipped_team_owned'):
        lines.append(f"Skipped {report['skipped_team_owned']} leads already owned by a teammate")
    tstep = steps.get('targeting') or {}
    if tstep:
        line = (f"Filter \"{tstep.get('name')}\": " if tstep.get('name') else 'Targeting: ') + f"{tstep.get('kept', 0)} of {tstep.get('checked', 0)} candidates matched"
        if tstep.get('dropped'):
            line += ' (dropped: ' + ', '.join(f'{k} {v}' for k, v in sorted(tstep['dropped'].items(), key=lambda kv: -kv[1])) + ')'
        lines.append(line)
        if (tstep.get('notes') or {}).get('revenue_unknown'):
            lines.append(f"Revenue unknown for {tstep['notes']['revenue_unknown']} matched leads (kept and labeled; revenue is never estimated)")
    elif report.get('skipped_targeting'):
        lines.append(f"Skipped {report['skipped_targeting']} leads that did not match the ICP / targeting filter")
    if report.get('targeting_record_sources_skipped'):
        lines.append('Record feeds skipped by the filter: ' + ', '.join(sorted(report['targeting_record_sources_skipped'])))
    crawl = steps.get('crawl', {})
    if crawl.get('sites_ok') is not None:
        lines.append(f"Crawled {crawl.get('domains_planned', 0)} sites, {crawl.get('sites_ok', 0)} reachable, {crawl.get('people_with_email', 0)} people with emails")
    for name in ('hunter-discover', 'resolve', 'verify', 'xray-linkedin', 'find-emails'):
        step = steps.get(name) or {}
        if step:
            bits = [f"{k}={step[k]}" for k in ('sent', 'domains_added', 'resolved', 'linkedin_filled') if step.get(k)]
            if step.get('stopped'):
                bits.append('STOPPED (provider problem, check status)')
            if step.get('skipped_reasons'):
                bits.append('skipped: ' + ', '.join(f'{k} {v}' for k, v in step['skipped_reasons'].items()))
            lines.append(f"{name}: " + (', '.join(bits) or 'nothing to do'))
    lines.append(f"Fresh leads waiting for tomorrow: {max(0, report.get('fresh_available', 0) - report.get('delivered', 0))}")
    budgets = report.get('budgets_left') or {}
    low = [f'{k} {v}' for k, v in budgets.items() if _as_float(v) < 50]
    if low:
        lines.append('Low free budgets: ' + ', '.join(low))
    for tip in report.get('advice') or []:
        lines.append('Next: ' + tip)
    files = report.get('files') or {}
    if files.get('leads'):
        from .audit import latest_score
        folder = Path(files['leads']).parent
        score = latest_score(folder)
        if score:
            lines.append(score + ' (see audit.md)')
        lines += ['', 'Delivery', f'- Folder: {folder}', f"- leads.csv: {report.get('delivered', 0)} leads (6 core columns plus City, State, Company Type, Source, Confidence)",
                  '- leads-details.csv: same leads plus tier, score, email source, evidence link and email risk', '- report.json: full run details']
        if score:
            lines.append('- audit.md: spot-check of a random sample against its evidence')
    return '\n'.join(lines) + '\n'


def _as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def shortfall_advice(shortfall, history):
    domains = int(shortfall / EXPECTED_LEADS_PER_DOMAIN)
    tips = [f'About {domains} more fresh company domains are needed (roughly {EXPECTED_LEADS_PER_DOMAIN} leads per crawled site)']
    stats = history.stats()
    if stats['domains_uncrawled'] < domains:
        tips.append('Grow the domain supply: enable serper (resolves public-record firms to websites), hunter discover, google_places or raise their caps')
    if stats.get('firms_pending_website'):
        tips.append(f"{stats['firms_pending_website']} public-record firms still need a website lookup; raise --cap resolve=N with a funded search provider")
    tips.append('Enable a finder (icypeas, hunter, tomba) and a cheap verifier (reoon, millionverifier) so decision makers without published emails can be filled')
    return tips


def linkedin_match_jobs(records, provider='serper'):
    """One search per lead: site:linkedin.com/in "Full Name" "Company"."""
    from .discovery import xray_jobs
    queries = []
    for record in records:
        name = re.sub(r'["\\]', '', record.get('full_name') or '')
        company = re.sub(r'["\\]', '', record.get('company') or '')
        if name and company:
            queries.append(f'site:linkedin.com/in "{name}" "{company}"')
    if not queries:
        return []
    if provider == 'serper':
        from .discovery import serper_batch_jobs
        return serper_batch_jobs(queries)
    if provider in ('serper', 'brave'):
        return xray_jobs(provider, queries)
    from .discovery import search_jobs
    return search_jobs(provider, queries)


def attach_linkedin(records, prospects):
    """Fill LinkedIn URLs when a search result has the same person and a matching company."""
    by_name = {}
    for prospect in prospects:
        key = _name_key(prospect.get('full_name'))
        if key and prospect.get('linkedin_url'):
            by_name.setdefault(key, []).append(prospect)
    filled = 0
    for record in records:
        if record.get('linkedin_url'):
            continue
        for prospect in by_name.get(_name_key(record.get('full_name')), []):
            company = quality.fold(prospect.get('company') or '')
            mine = quality.fold(record.get('company') or '')
            tokens = [t for t in re.findall(r'[a-z0-9]{3,}', mine) if t not in {'real', 'estate', 'group', 'realty', 'the', 'inc', 'llc', 'company', 'partners'}]
            if company and tokens and any(t in company for t in tokens):
                record['linkedin_url'] = quality.normalize_linkedin(prospect['linkedin_url'])
                filled += 1
                break
    return filled


DETAIL_EXTRA_HEADERS = ['Tier', 'Score', 'Email Source', 'Source URL', 'Issues', 'Email Risk', 'Email Risk Reason',
                        'MX Provider', 'Email Verification', 'Annual Revenue']


def load_daily_records(out_dir):
    """Reload a previous same-day execute so a second run can merge instead of overwrite."""
    out_dir = Path(out_dir)
    details = out_dir / 'leads-details.csv'
    leads = out_dir / 'leads.csv'
    path = details if details.exists() else leads
    if not path.exists():
        return []
    records = []
    for raw in quality.read_rows(path):
        rec = quality.normalize_record(raw)
        rec['tier'] = raw.get('Tier') or rec.get('tier') or 'B'
        try:
            rec['score'] = int(raw.get('Score') or rec.get('score') or 0)
        except (TypeError, ValueError):
            rec['score'] = rec.get('score') or 0
        rec['email_source'] = raw.get('Email Source') or rec.get('email_source') or ''
        rec['source_url'] = raw.get('Source URL') or rec.get('source_url') or ''
        rec['email_evidence_url'] = rec.get('email_evidence_url') or rec['source_url']
        rec['issues'] = [part for part in str(raw.get('Issues') or '').split('; ') if part]
        rec['email_risk'] = raw.get('Email Risk') or rec.get('email_risk') or ''
        rec['email_risk_reason'] = raw.get('Email Risk Reason') or rec.get('email_risk_reason') or ''
        rec['mx_provider'] = raw.get('MX Provider') or rec.get('mx_provider') or ''
        rec['email_verification'] = raw.get('Email Verification') or rec.get('email_verification') or ''
        rec['company_type'] = raw.get('Company Type') or rec.get('company_type') or rec.get('company_type')
        records.append(rec)
    return records


def merge_daily_records(prior, new):
    seen, merged = set(), []
    for rec in list(prior or []) + list(new or []):
        keys = set(History.keys_for(rec))
        if not keys or keys & seen:
            continue
        seen |= keys
        merged.append(rec)
    return merged


def archive_daily_file(path, folder, name):
    path = Path(path)
    if not path.exists():
        return None
    dest_dir = path.parent / folder
    dest_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    dest = dest_dir / name
    dest.write_bytes(path.read_bytes())
    os.chmod(dest, 0o600)
    return str(dest)


def write_daily(records, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(out_dir, 0o700)
    leads = out_dir / 'leads.csv'
    details = out_dir / 'leads-details.csv'
    quality._write_csv(str(leads), quality.DAILY_HEADERS, [quality.daily_row(r) for r in records])
    quality._write_csv(str(details), quality.DAILY_HEADERS + DETAIL_EXTRA_HEADERS,
                       [quality.daily_row(r) + [r['tier'], r['score'], r.get('email_source', ''),
                                                r.get('email_evidence_url') or r.get('source_url', ''), '; '.join(r['issues']),
                                                r.get('email_risk') or quality.email_risk(r)[0], r.get('email_risk_reason', ''),
                                                r.get('mx_provider', ''), r.get('email_verification', ''),
                                                r.get('annual_revenue_usd') or ('unknown' if r.get('revenue_status') == 'unknown' else '')]
                        for r in records])
    return {'leads': str(leads), 'details': str(details)}


def import_history(work, files, batch='imported'):
    """Mark leads from earlier files as already delivered, so they never come back."""
    history = History(Path(work) / 'lead-history.sqlite3')
    try:
        rows = []
        for file in files:
            rows.extend(quality.read_rows(file))
        normalized = [quality.normalize_record(r) for r in rows]
        history.mark_delivered(normalized, batch)
        # Their domains are also known, so they are not re-queued as "new" companies
        history.add_domains([{'domain': r['company_website'], 'company': r['company']} for r in normalized if r['company_website']], 'history')
        return {'rows': len(rows), **history.stats()}
    finally:
        history.close()
