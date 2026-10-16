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
OPTIONAL_SOURCES = ('hunter-domains', 'apollo', 'xray-linkedin', 'pdl')
DEFAULT_RECORD_SOURCES = ('tx-trec', 'ny-dos', 'co-dora', 'sec-form-d')
DEFAULT_CAPS = {'records': 3000, 'resolve': 600, 'resolve-hunter': 1500, 'hunter-discover': 90, 'google-places': 30, 'osm': 6,
                'apollo': 20, 'apollo-enrich': 50, 'hunter-domains': 25, 'find-emails': 60, 'verify': 300, 'xray-linkedin': 300,
                'render-js': 25, 'pdl': 3}
KEYWORD_SETS = (['property management'], ['commercial real estate'], ['multifamily'], ['real estate investment'],
                ['real estate development'], ['brokerage'], ['luxury real estate'], ['land'])
SEARCH_FALLBACK = ('serper', 'tavily', 'exa')
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
OUTCOMES = {'replied', 'interested', 'meeting', 'not_interested', 'bounced', 'unsubscribed', 'do_not_contact', 'wrong_person', 'customer'}
OUTCOME_ALIASES = {'reply': 'replied', 'positive': 'interested', 'booked': 'meeting', 'bounce': 'bounced', 'hard_bounce': 'bounced',
                   'invalid': 'bounced', 'unsubscribe': 'unsubscribed', 'opt_out': 'unsubscribed', 'optout': 'unsubscribed',
                   'dnc': 'do_not_contact', 'negative': 'not_interested', 'wrong_contact': 'wrong_person'}
AUTO_SUPPRESS = {'bounced', 'unsubscribed', 'do_not_contact', 'wrong_person', 'customer'}
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
        stats = {src: {'delivered': n, 'bounced': 0, 'replied': 0} for src, n in delivered.items()}
        for r in self.db.execute("SELECT COALESCE(email_source,'unknown') AS src, outcome, COUNT(DISTINCT batch || key) AS n FROM outcomes WHERE key LIKE 'em:%' GROUP BY src, outcome"):
            entry = stats.setdefault(r['src'], {'delivered': 0, 'bounced': 0, 'replied': 0})
            if r['outcome'] == 'bounced':
                entry['bounced'] += r['n']
            elif r['outcome'] in ('replied', 'meeting', 'interested'):
                entry['replied'] += r['n']
        for entry in stats.values():
            base = max(entry['delivered'], 1)
            entry['bounce_rate'] = round(entry['bounced'] / base, 3)
            entry['reply_rate'] = round(entry['replied'] / base, 3)
        return stats

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
        return [json.loads(r['data']) for r in self.db.execute('SELECT data FROM sites WHERE crawled_at >= ?', (cutoff,))]

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
                'firms_resolved': q("SELECT COUNT(*) FROM firms WHERE status='resolved'")}


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


def _selection_key(record, source_quality=None):
    """Best first: tier, has email, sources that bounce less (once feedback exists), LinkedIn, score."""
    bounce = ((source_quality or {}).get(record.get('email_source') or 'unknown') or {}).get('bounce_rate', 0)
    return (quality.TIER_ORDER[record['tier']], 0 if record.get('email') else 1, 1 if bounce > 0.05 else 0,
            0 if record.get('linkedin_url') else 1, -record['score'])


def run_daily(work, store, config, *, target=500, execute=False, day=None, sources=DEFAULT_SOURCES,
              output_root=None, max_domains=None, workers=24, require_email=True, min_tier='B',
              caps=None, env=None, transport=None, fetcher=None, record_sources=(), record_source_names=DEFAULT_RECORD_SOURCES,
              search_provider='serper', records_getter=None, clock=time.time):
    """One daily cycle. Returns a report; writes files under ``output_root/day``."""
    work = Path(work)
    env = os.environ if env is None else env
    day = day or today_iso(clock)
    caps = {**DEFAULT_CAPS, **(caps or {})}
    max_domains = max_domains or int(target / EXPECTED_LEADS_PER_DOMAIN * 1.2)
    history = History(work / 'lead-history.sqlite3')
    report = {'date': day, 'execute': execute, 'target': target, 'steps': {}}
    try:
        from . import discovery
        from .policy import US_STATES

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
            report['steps']['resolve'] = _resolve_firms(history, store, config, execute, env, transport, caps, search_provider)

        backlog = history.backlog()
        report['backlog_before'] = backlog
        if backlog < max_domains and 'hunter-discover' in sources:
            # Free calls; rotate cities (most distinct results), states x sizes, and keyword niches
            cap = caps['hunter-discover']
            per_city = len(HEADCOUNTS)
            cities = history.next_markets('hunter_city', list(METROS), max(1, int(cap * 0.6) // per_city))
            states = history.next_markets('hunter_state', list(US_STATES), max(1, int(cap * 0.2) // per_city))
            kw_states = history.next_markets('hunter_keyword_state', list(US_STATES), 1)
            kw_sets = history.next_markets('hunter_keyword', [k[0] for k in KEYWORD_SETS], max(1, int(cap * 0.2) // per_city))
            jobs = (discovery.hunter_discover_city_jobs(cities, headcounts=HEADCOUNTS)
                    + discovery.hunter_discover_jobs(states, headcounts=HEADCOUNTS)
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
        if history.backlog() < max_domains and 'google-places' in sources:
            metros = history.next_markets('places_metro', list(METROS), max(1, caps['google-places'] // 3))
            step = run('google-places', discovery.places_jobs(metros), caps['google-places'])
            step['markets'] = [':'.join(m) for m in metros]
            if execute and step['sent']:
                for metro in metros:
                    history.mark_market('places_metro', metro, day)
        if history.backlog() < max_domains and 'osm' in sources:
            areas = history.next_markets('osm_area', list(METROS), caps['osm'])
            step = run('osm', discovery.osm_jobs(areas), caps['osm'])
            step['markets'] = [':'.join(a) for a in areas]
            if execute and step['sent']:
                for area in areas:
                    history.mark_market('osm_area', area, day)
        if 'apollo' in sources:
            states = history.next_markets('apollo_state', list(US_STATES), 1)
            step = run('apollo', discovery.apollo_jobs(list(discovery.DEFAULT_DM_TITLES), [US_STATES[s] + ', US' for s in states], pages=caps['apollo']), caps['apollo'])
            if execute and step['sent']:
                history.mark_market('apollo_state', states[0], day)
                partial = [p for p in history.prospects() if p.get('apollo_id') and not p.get('email')]
                run('apollo-enrich', discovery.apollo_enrich_jobs(partial), caps['apollo-enrich'])

        if 'crawl' in sources:
            domains = history.domains_to_crawl(max_domains)
            step = {'domains_planned': len(domains)}
            if execute and domains:
                from .extract import crawl_domains
                from .webfetch import SafeFetcher
                own = fetcher is None
                active = fetcher or SafeFetcher(cache_path=work / 'web-cache.sqlite3', per_host_delay=1.0)
                try:
                    sites = crawl_domains(domains, active, max_pages=6, workers=workers)
                finally:
                    if own:
                        active.close()
                history.save_sites(sites)
                step.update(sites_ok=sum(s['status'] == 'ok' for s in sites),
                            people=sum(len(s['people']) for s in sites),
                            people_with_email=sum(1 for s in sites for p in s['people'] if p.get('email')))
            report['steps']['crawl'] = step
        if 'render-js' in sources:
            report['steps']['render-js'] = _render_js_sites(history, store, config, execute, env, transport, caps['render-js'])
        if 'hunter-domains' in sources:
            rows = history.db.execute("SELECT domain FROM domains WHERE last_crawled IS NOT NULL AND emails=0 "
                                      "ORDER BY last_crawled DESC LIMIT ?", (caps['hunter-domains'],)).fetchall()
            run('hunter-domains', discovery.hunter_domain_jobs([r['domain'] for r in rows]), caps['hunter-domains'])

        target_cfg = quality.Target(min_tier=min_tier)
        result = build(prospects=history.prospects(), site_results=history.recent_sites(), store=store, target=target_cfg)
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
            emails = [c['Candidate Email'] for c in result['candidates']]
            emails += [p['email'] for p in history.prospects() if p.get('email') and str(p.get('email_source', '')).endswith('_finder')
                       and p.get('email_verification') not in ('valid', 'provider_valid')]
            jobs = [{'provider': 'auto', 'operation': 'verify', 'payload': {'email': e}} for e in dict.fromkeys(e.lower() for e in emails)]
            run('verify', jobs, caps['verify'])
        if execute and ({'find-emails', 'verify'} & set(sources)):
            result = build(prospects=history.prospects(), site_results=history.recent_sites(), store=store, target=target_cfg)

        pool = [r for r in result['clean'] if (r.get('email') or not require_email) and history.is_fresh(r)]
        source_quality = history.source_quality()
        pool.sort(key=lambda r: _selection_key(r, source_quality))
        picked, seen = [], set()
        for record in pool:
            keys = set(History.keys_for(record))
            if keys & seen:
                continue
            seen |= keys
            picked.append(record)
            if len(picked) >= target:
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

        report['fresh_available'] = len(pool)
        report['delivered'] = len(picked)
        report['shortfall'] = max(0, target - len(picked))
        report['delivered_by_source'] = _count_by(picked, 'email_source')
        if report['shortfall']:
            report['advice'] = shortfall_advice(report['shortfall'], history)
        out_dir = Path(output_root or work / 'daily') / (day if execute else day + '-preview')
        report['files'] = write_daily(picked, out_dir)
        if execute:
            history.mark_delivered(picked, day)
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


SEARCH_POOLS = {'serper': 'queries', 'tavily': 'credits', 'exa': 'queries', 'brave': 'queries'}


def _resolve_firms(history, store, config, execute, env, transport, caps, provider='serper'):
    """Find websites for public-record firms.

    1. Hunter Discover by firm name (free, 50 names per call) - exact name/state matches only
    2. Search engines for the rest, cheapest first: Serper batches of 100, then Tavily, then Exa
    Saved results are always reused before anything is spent.
    """
    from .resolve import (exa_resolution_jobs, resolution_batch_jobs, resolution_jobs, resolve_from_hunter,
                          resolve_from_store, searched_queries)
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

    firms = history.pending_firms(max(caps['resolve-hunter'], caps['resolve']))
    step['pending_checked'] = len(firms)
    if not firms:
        return step
    # 1. Hunter by name (free)
    if _funded(store, config, 'hunter', 'discover_requests'):
        names = firms[:caps['resolve-hunter']]
        jobs = hunter_name_resolution_jobs(names)
        step['hunter'] = _run_jobs(store, config, jobs, execute, env, transport, len(jobs))
    left = apply(resolve_from_hunter(store, firms), 'hunter_name', final=False)
    # 2. Search engines for what is left, within the daily cap
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
    by = report.get('delivered_by_source') or {}
    if by:
        lines.append('Email sources: ' + ', '.join(f'{k} {v}' for k, v in sorted(by.items(), key=lambda kv: -kv[1])))
    steps = report.get('steps', {})
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


def write_daily(records, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(out_dir, 0o700)
    leads = out_dir / 'leads.csv'
    details = out_dir / 'leads-details.csv'
    quality._write_csv(str(leads), quality.HEADERS, [quality.output_row(r) for r in records])
    quality._write_csv(str(details), quality.HEADERS + ['Tier', 'Score', 'Email Source', 'Source URL', 'Issues'],
                       [quality.output_row(r) + [r['tier'], r['score'], r.get('email_source', ''),
                                                 r.get('email_evidence_url') or r.get('source_url', ''), '; '.join(r['issues'])]
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
