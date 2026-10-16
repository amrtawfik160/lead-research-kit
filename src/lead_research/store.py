"""Private SQLite ledger. Every debit precedes network use and is audited."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from .models import BudgetError, BusyError, Outcome, PolicyError
from .policy import format_units, normalize_email, parse_units, redact, sanitize_payload


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


class Store:
    def __init__(self, db_path):
        self.path = str(db_path)
        if self.path != ':memory:':
            directory = Path(self.path).absolute().parent
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(directory, 0o700)
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(fd)
            os.chmod(self.path, 0o600)
        self.db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS records(kind TEXT,id TEXT,email TEXT,lead_id TEXT,data TEXT NOT NULL,PRIMARY KEY(kind,id));
          CREATE INDEX IF NOT EXISTS records_email ON records(email);
          CREATE INDEX IF NOT EXISTS records_lead ON records(lead_id);
          CREATE TABLE IF NOT EXISTS provider_states(provider TEXT PRIMARY KEY,data TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS budgets(provider TEXT,pool TEXT,remaining INTEGER NOT NULL,confirmed_at REAL NOT NULL,source TEXT NOT NULL,PRIMARY KEY(provider,pool));
          CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY,provider TEXT NOT NULL,operation TEXT NOT NULL,input_key TEXT NOT NULL,email TEXT,independence TEXT,state TEXT NOT NULL,data TEXT NOT NULL);
          CREATE UNIQUE INDEX IF NOT EXISTS attempt_input ON attempts(provider,operation,input_key) WHERE state != 'cancelled';
          CREATE UNIQUE INDEX IF NOT EXISTS one_active ON attempts((1)) WHERE state IN ('reserved','sent','pending');
          CREATE INDEX IF NOT EXISTS attempts_email ON attempts(email);
          CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY AUTOINCREMENT,event TEXT NOT NULL,previous TEXT NOT NULL,digest TEXT NOT NULL);
        ''')
        self._private_files()

    def _private_files(self):
        if self.path != ':memory:':
            for suffix in ('', '-wal', '-shm'):
                try:
                    os.chmod(self.path + suffix, 0o600)
                except FileNotFoundError:
                    pass

    def close(self):
        self.db.close()

    @contextmanager
    def _tx(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise
        finally:
            self._private_files()

    def _audit(self, action, data):
        last = self.db.execute('SELECT digest FROM audit ORDER BY seq DESC LIMIT 1').fetchone()
        previous = last['digest'] if last else '0' * 64
        event = _json({'action': action, 'at': time.time(), 'data': redact(data)})
        digest = hashlib.sha256((previous + event).encode()).hexdigest()
        self.db.execute('INSERT INTO audit(event,previous,digest) VALUES(?,?,?)', (event, previous, digest))

    def verify_audit(self):
        if self.db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            return False
        previous = '0' * 64
        for row in self.db.execute('SELECT * FROM audit ORDER BY seq'):
            if row['previous'] != previous or row['digest'] != hashlib.sha256((previous + row['event']).encode()).hexdigest():
                return False
            previous = row['digest']
        return True

    def _put(self, kind, record):
        record = redact(dict(record))
        record['id'] = str(record['id'] if record.get('id') is not None else uuid.uuid4().hex)
        if 'lead_id' in record:
            record['lead_id'] = str(record['lead_id'])
        if record.get('email'):
            record['email'] = normalize_email(record['email'])
        with self._tx():
            self.db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?,?,?)', (kind, record['id'], record.get('email'), record.get('lead_id'), _json(record)))
            self._audit('put_' + kind, record)
        return record

    def _records(self, kind, lead_id=None):
        query = 'SELECT data FROM records WHERE kind=?'
        params = [kind]
        if lead_id is not None:
            query += ' AND lead_id=?'
            params.append(str(lead_id))
        return [json.loads(r[0]) for r in self.db.execute(query + ' ORDER BY rowid', params)]

    def put_contact(self, record):
        if record.get('id') is None:
            raise PolicyError('contact_id_required')
        return self._put('contact', record)

    def contacts(self):
        return self._records('contact')

    def put_evidence(self, record):
        return self._put('evidence', record)

    def evidence(self, lead_id=None):
        return self._records('evidence', lead_id)

    def put_candidate(self, record):
        return self._put('candidate', {**record, 'ownership_verified': False})

    def candidates(self, lead_id=None):
        return self._records('candidate', lead_id)

    def hold_contact(self, id, reason, source):
        rows = self.db.execute("SELECT data FROM records WHERE kind='contact' AND id=?", (str(id),)).fetchone()
        if not rows:
            raise PolicyError('contact_not_found')
        record = json.loads(rows[0])
        return self.put_contact({**record, 'held': True, 'hold_reason': redact(reason), 'hold_source': redact(source)})

    def provider_state(self, id):
        row = self.db.execute('SELECT data FROM provider_states WHERE provider=?', (id,)).fetchone()
        return json.loads(row[0]) if row else {'provider': id, 'paused': False, 'last_request_at': None}

    def _state(self, id, data):
        self.db.execute('INSERT OR REPLACE INTO provider_states VALUES(?,?)', (id, _json(data)))

    def pause(self, id, reason):
        with self._tx():
            state = self.provider_state(id)
            state.update(paused=True, pause_reason=redact(reason), paused_at=time.time())
            self._state(id, state)
            self._audit('pause', state)
        return state

    def review_provider(self, id, reason, evidence):
        if not str(reason).strip() or not evidence:
            raise PolicyError('review_reason_and_evidence_required')
        with self._tx():
            state = self.provider_state(id)
            if any(a['provider'] == id and a['state'] in {'reserved', 'sent', 'pending'} for a in self.attempts()):
                raise BusyError('active_attempt_cannot_be_reviewed')
            active = [a for a in self.attempts() if a['provider'] == id and a['state'] == 'uncertain']
            cutoff = max([state.get('paused_at', 0)] + [a.get('sent_at', a['created_at']) for a in active])
            pools = {p for a in active for p in a['charges']}
            if not pools:
                pools = {r['pool'] for r in self.db.execute('SELECT pool FROM budgets WHERE provider=?', (id,))}
            confirmations = {r['pool']: r['confirmed_at'] for r in self.db.execute('SELECT * FROM budgets WHERE provider=?', (id,))}
            if not pools or any(confirmations.get(p, 0) <= cutoff for p in pools):
                raise BudgetError('fresh_budget_confirmation_required')
            for attempt in active:
                attempt.update(state='reviewed', review={'reason': redact(reason), 'evidence': redact(evidence)})
                self._save_attempt(attempt)
            state.update(paused=False, review_reason=redact(reason), review_evidence=redact(evidence), reviewed_at=time.time())
            self._state(id, state)
            self._audit('explicit_provider_review', {'provider': id, 'reason': reason, 'evidence': evidence, 'attempts': [a['id'] for a in active]})
        return state

    def confirm_budget(self, provider, pool, amountstr, source, attested_free=True, confirmed_at=None):
        units = parse_units(amountstr)
        if attested_free is not True or not str(source).strip():
            raise PolicyError('explicit_free_attestation_required')
        now = time.time()
        confirmed_at = now if confirmed_at is None else float(confirmed_at)
        if confirmed_at > now + 60 or now - confirmed_at > 86400:
            raise PolicyError('fresh_confirmation_required')
        with self._tx():
            previous = self.db.execute('SELECT confirmed_at,remaining FROM budgets WHERE provider=? AND pool=?', (provider, pool)).fetchone()
            unsettled = any(a['provider'] == provider and a['state'] in {'reserved', 'sent', 'pending', 'uncertain'} for a in self.attempts())
            if unsettled and units > (previous['remaining'] if previous else 0):
                raise BudgetError('cannot_increase_budget_with_unsettled_requests')
            if previous and confirmed_at <= previous[0]:
                raise PolicyError('newer_confirmation_required')
            self.db.execute('INSERT OR REPLACE INTO budgets VALUES(?,?,?,?,?)', (provider, pool, units, confirmed_at, str(redact(source))))
            self._audit('confirm_budget', {'provider': provider, 'pool': pool, 'remaining_units': units, 'source': source, 'confirmed_at': confirmed_at})
        return self.budgets()

    def budgets(self):
        return [{**dict(r), 'remaining_units': r['remaining'], 'remaining': format_units(r['remaining'])} for r in self.db.execute('SELECT * FROM budgets ORDER BY provider,pool')]

    def attempts(self):
        return [json.loads(r[0]) for r in self.db.execute('SELECT data FROM attempts ORDER BY rowid')]

    def get_attempt(self, id):
        row = self.db.execute('SELECT data FROM attempts WHERE id=?', (str(id),)).fetchone()
        if not row:
            raise PolicyError('attempt_not_found')
        return json.loads(row[0])

    def get_cached(self, provider, operation, input_key):
        row = self.db.execute("SELECT data FROM attempts WHERE provider=? AND operation=? AND input_key=? AND state!='cancelled'", (provider, operation, input_key)).fetchone()
        return json.loads(row[0]) if row else None

    def _save_attempt(self, attempt):
        self.db.execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?,?,?,?,?,?)', (attempt['id'], attempt['provider'], attempt['operation'], attempt['input_key'], attempt['payload'].get('email'), attempt['independence'], attempt['state'], _json(attempt)))

    def reserve(self, provider, operation, input_key, payload, chargesdict, independence, items=1):
        """Debit before sending. ``items`` > 1 is a per-item charge: each pool's amount must be
        exactly items x a fixed unit (0.5 or 1), for at most 100 items in one request."""
        payload = sanitize_payload(payload)
        charges = {str(p): parse_units(v, positive=True) for p, v in chargesdict.items()}
        if not charges:
            raise PolicyError('charges_required')
        if type(items) is not int or not 1 <= items <= 100:
            raise PolicyError('unsupported_charge')
        if any(v % items or v // items not in {500000, 1000000} for v in charges.values()):
            raise PolicyError('unsupported_charge')
        with self._tx():
            if self.provider_state(provider).get('paused'):
                raise PolicyError('provider_paused')
            if self.db.execute("SELECT 1 FROM attempts WHERE state IN ('reserved','sent','pending')").fetchone():
                raise BusyError('active_attempt_requires_review')
            if self.get_cached(provider, operation, input_key):
                raise PolicyError('input_already_attempted')
            if operation == 'verify':
                prior = [a for a in self.attempts() if a['operation'] == 'verify' and a['payload'].get('email') == payload.get('email') and a['state'] != 'cancelled']
                if any(a['independence'] == independence for a in prior) or len({a['independence'] for a in prior}) >= 2:
                    raise PolicyError('independence_limit')
            for pool, units in charges.items():
                row = self.db.execute('SELECT remaining FROM budgets WHERE provider=? AND pool=?', (provider, pool)).fetchone()
                if not row or row[0] < units:
                    raise BudgetError('insufficient_confirmed_free_budget')
            for pool, units in charges.items():
                self.db.execute('UPDATE budgets SET remaining=remaining-? WHERE provider=? AND pool=?', (units, provider, pool))
            attempt = {'id': uuid.uuid4().hex, 'provider': provider, 'operation': operation, 'input_key': input_key, 'payload': payload, 'charges': charges, 'independence': independence, 'state': 'reserved', 'created_at': time.time(), 'poll_count': 0, 'sent_at': None, 'outcome': None}
            if items != 1:
                attempt['charge_items'] = items
            self._save_attempt(attempt)
            self._audit('reserve_and_debit', attempt)
        return attempt

    def mark_sent(self, attempt_id, at=None):
        with self._tx():
            attempt = self.get_attempt(attempt_id)
            if attempt['state'] != 'reserved':
                raise PolicyError('attempt_not_reserved')
            at = time.time() if at is None else at
            attempt.update(state='sent', sent_at=at)
            self._save_attempt(attempt)
            state = self.provider_state(attempt['provider'])
            state['last_request_at'] = at
            self._state(attempt['provider'], state)
            self._audit('mark_sent', {'id': attempt_id, 'at': at})
        return attempt

    def note_poll(self, attempt_id, at=None):
        with self._tx():
            attempt = self.get_attempt(attempt_id)
            if attempt['state'] != 'pending' or attempt['poll_count'] >= 12:
                raise PolicyError('poll_limit_or_not_pending')
            attempt['poll_count'] += 1
            attempt['state'] = 'sent'  # Claim the existing lease before the GET; crash requires review
            attempt['last_poll_at'] = time.time() if at is None else at
            self._save_attempt(attempt)
            state = self.provider_state(attempt['provider'])
            state['last_request_at'] = attempt['last_poll_at']
            self._state(attempt['provider'], state)
            self._audit('poll_sent', {'id': attempt_id, 'poll_count': attempt['poll_count']})
        return attempt

    def finish(self, attempt_id, outcome, state='completed', http_status=None):
        if state not in {'completed', 'pending', 'uncertain'}:
            raise PolicyError('invalid_finish_state')
        with self._tx():
            attempt = self.get_attempt(attempt_id)
            if attempt['state'] not in {'sent', 'pending'}:
                raise PolicyError('attempt_not_sent')
            data = asdict(outcome) if isinstance(outcome, Outcome) else dict(outcome)
            data = redact(data)
            if data.get('email'):
                data['email'] = normalize_email(data['email'])
            attempt.update(state=state, outcome=data, http_status=http_status, finished_at=time.time())
            self._save_attempt(attempt)
            self._audit('finish', {'id': attempt_id, 'state': state, 'outcome': data, 'http_status': http_status})
        self.capture_finder(attempt)
        return attempt

    def capture_finder(self, attempt):
        """Idempotently materialize a finder observation, including after a cached run."""
        outcome = attempt.get('outcome') or {}
        if attempt['operation'] != 'find' or outcome.get('assessment') != 'candidate' or not outcome.get('email'):
            return None
        email = normalize_email(outcome['email'])
        lead_id = str(attempt['payload'].get('lead_id') or '')
        key = 'candidate_' + hashlib.sha256((lead_id + '|' + email).encode()).hexdigest()[:24]
        prior = next((r for r in self.candidates() if r.get('email') == email and str(r.get('lead_id') or '') == lead_id), {})
        flags = set(prior.get('risk_flags', [])) | set(outcome.get('risk_flags', []))
        domain = attempt['payload'].get('domain')
        if domain and email.rsplit('@', 1)[-1] != domain:
            flags.add('company_domain_mismatch')
        origins = list(dict.fromkeys([*prior.get('origins', []), *([prior['kind']] if prior.get('kind') else []), 'provider_finder']))
        row = {**prior, 'id': prior.get('id', key), 'lead_id': lead_id, 'email': email, 'kind': 'provider_finder', 'origins': origins, 'provider': attempt['provider'], 'attempt_id': attempt['id'], 'risk_flags': sorted(flags), 'domain_evidence_url': attempt['payload'].get('domain_evidence_url'), 'identity_confirmed': False, 'ownership_verified': False}
        if row == prior:
            return prior
        return self.put_candidate(row)

    def cancel_reserved(self, id, reason):
        if not str(reason).strip():
            raise PolicyError('cancellation_reason_required')
        with self._tx():
            attempt = self.get_attempt(id)
            if attempt['state'] != 'reserved' or attempt.get('sent_at') is not None:
                raise PolicyError('only_never_sent_reservation_can_cancel')
            for pool, units in attempt['charges'].items():
                self.db.execute('UPDATE budgets SET remaining=remaining+? WHERE provider=? AND pool=?', (units, attempt['provider'], pool))
            attempt.update(state='cancelled', cancellation_reason=redact(reason))
            self._save_attempt(attempt)
            self._audit('cancel_and_refund_never_sent', {'id': id, 'reason': reason, 'charges': attempt['charges']})
        return attempt

    def assess(self, email):
        email = normalize_email(email)
        votes, risks = {}, set()
        for attempt in self.attempts():
            outcome = attempt.get('outcome') or {}
            if (outcome.get('email') or attempt['payload'].get('email')) == email and attempt['state'] in {'completed', 'reviewed'}:
                risks.update(outcome.get('risk_flags', []))
            if attempt['operation'] != 'verify' or attempt['payload'].get('email') != email or attempt['state'] not in {'completed', 'reviewed'} or not attempt.get('outcome'):
                continue
            outcome = attempt['outcome']
            risks.update(outcome.get('risk_flags', []))
            assessment = outcome.get('assessment', 'unknown')
            if outcome.get('error') or assessment not in {'provider_valid', 'provider_invalid', 'unknown', 'catch_all'}:
                continue
            votes[attempt['independence']] = assessment
            if assessment == 'catch_all':
                risks.add('catch_all')
        positive = sum(v == 'provider_valid' for v in votes.values())
        negative = sum(v == 'provider_invalid' for v in votes.values())
        if positive and negative:
            assessment = 'conflict'
            risks.add('provider_conflict')
        elif negative:
            assessment = 'provider_invalid'
        elif positive and not risks:
            assessment = 'provider_valid'
        else:
            assessment = 'unknown' if votes else 'not_tested'
        vote_rows = [{'provider_id': a['provider'], 'independence': a['independence'], 'assessment': a['outcome']['assessment'], 'risk_flags': a['outcome'].get('risk_flags', [])} for a in self.attempts() if a['operation'] == 'verify' and a['payload'].get('email') == email and a.get('outcome') and not a['outcome'].get('error') and a['outcome'].get('assessment') in {'provider_valid', 'provider_invalid', 'unknown', 'catch_all'} and a['state'] in {'completed', 'reviewed'}]
        return {'email': email, 'assessment': assessment, 'votes': vote_rows, 'positive_votes': positive, 'valid_provider_count': positive, 'negative_votes': negative, 'risk_flags': sorted(risks), 'ownership_verified': False, 'identity_confirmed': False, 'delivery_proven': False}
