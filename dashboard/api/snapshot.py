"""Read a private workspace through the kit. Secrets never leave the process."""
from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path

from lead_research import __version__
from lead_research.configuration import default_config, load_config, save_config
from lead_research.daily import History, render_report, today_iso
from lead_research.intake import research_queue
from lead_research.models import PolicyError
from lead_research.providers import PROVIDERS, get_provider
from lead_research.quality import Target, clean_records, read_rows
from lead_research.store import Store

from .sample import seed_sample

DASHBOARD_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = DASHBOARD_ROOT / '.data'
SAMPLE_DIR = DATA_DIR / 'sample-workspace'
SETTINGS_PATH = DATA_DIR / 'settings.json'


def resolve_workspace(settings=None):
    """Pick the operator workspace or the opt-in synthetic sample."""
    settings = settings if settings is not None else load_settings()
    env_ws = (os.environ.get('LEAD_RESEARCH_WORKSPACE') or '').strip()
    env_sample = (os.environ.get('LEAD_RESEARCH_SAMPLE') or '').strip().lower()
    if env_ws:
        sample = env_sample in ('1', 'true', 'yes')
        return Path(env_ws).expanduser().resolve(), sample
    if env_sample in ('0', 'false', 'no') and settings.get('workspace'):
        return Path(settings['workspace']).expanduser().resolve(), False
    if settings.get('sample') is False and settings.get('workspace'):
        return Path(settings['workspace']).expanduser().resolve(), False
    return SAMPLE_DIR, True


def load_settings():
    if not SETTINGS_PATH.exists():
        return {'sample': True}
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'sample': True}
    return data if isinstance(data, dict) else {'sample': True}


def save_settings(data):
    DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    SETTINGS_PATH.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    os.chmod(SETTINGS_PATH, 0o600)
    return data


def clean_rows(rows, min_tier='B', allow_personal_email=False, keep_non_decision_makers=False):
    target = Target(
        min_tier=min_tier,
        allow_personal_email=bool(allow_personal_email),
        require_decision_maker=not keep_non_decision_makers,
    )
    result = clean_records(rows, target)
    return {
        'summary': result['summary'],
        'clean': [_public_assessed(r) for r in result['clean'][:200]],
        'review': [_public_assessed(r) for r in result['review'][:200]],
        'rejected': [_public_assessed(r) for r in result['rejected'][:200]],
    }


def parse_tabular(text):
    stripped = (text or '').lstrip()
    if stripped.startswith(('[', '{')):
        data = json.loads(text)
        if isinstance(data, dict):
            for key in ('leads', 'records', 'rows', 'data'):
                if isinstance(data.get(key), list):
                    return data[key]
            return [data]
        if not isinstance(data, list):
            raise PolicyError('expected_record_array')
        return data
    return list(csv.DictReader(io.StringIO(text)))


class Dashboard:
    def __init__(self, workspace=None, sample=None):
        path, is_sample = resolve_workspace() if workspace is None else (Path(workspace), bool(sample))
        self.workspace = Path(path)
        self.sample = is_sample
        if self.sample:
            seed_sample(self.workspace)

    def meta(self):
        initialized = (self.workspace / 'providers.json').exists()
        return {
            'kit_version': __version__,
            'workspace': str(self.workspace),
            'sample': self.sample,
            'initialized': initialized,
            'today': today_iso(),
        }

    def overview(self):
        status = self.status()
        history = self._history_stats()
        batches = self.daily_list()
        latest = batches[0] if batches else None
        report = self.daily(latest['date'])['report'] if latest else None
        research = self.research()
        return {
            'meta': self.meta(),
            'status': status,
            'history': history,
            'latest_daily': latest,
            'report': report,
            'research_queue_count': len(research['queue']),
            'advice': (report or {}).get('advice') or [],
        }

    def status(self):
        if not (self.workspace / 'providers.json').exists():
            return {'initialized': False, 'contacts': 0, 'held': 0, 'budgets': [], 'providers': {},
                    'unresolved_attempts': [], 'audit_ok': False}
        store, config = self._store()
        try:
            contacts = store.contacts()
            return {
                'initialized': True,
                'contacts': len(contacts),
                'held': sum(bool(r.get('held')) for r in contacts),
                'qualified': sum(bool(r.get('qualified')) for r in contacts if not r.get('held')),
                'with_published_email': sum(1 for r in contacts if r.get('email') and not r.get('held')),
                'budgets': store.budgets(),
                'providers': {name: _provider_row(name, config, store) for name in config['providers']},
                'unresolved_attempts': [
                    {'id': a['id'], 'provider': a['provider'], 'state': a['state'], 'operation': a['operation']}
                    for a in store.attempts() if a['state'] in ('reserved', 'sent', 'pending', 'uncertain')
                ],
                'audit_ok': store.verify_audit(),
            }
        finally:
            store.close()

    def contacts(self):
        store, _config = self._store()
        try:
            rows = []
            for contact in store.contacts():
                rows.append({
                    **_public_contact(contact),
                    'evidence_count': len(store.evidence(contact['id'])),
                    'candidate_count': len(store.candidates(contact['id'])),
                    'in_research_queue': not contact.get('held') and not store.evidence(contact['id']) and not contact.get('research_complete'),
                })
            return {'contacts': rows}
        finally:
            store.close()

    def contact(self, lead_id):
        store, _config = self._store()
        try:
            lead = next((r for r in store.contacts() if str(r['id']) == str(lead_id)), None)
            if lead is None:
                raise PolicyError('contact_not_found')
            evidence = store.evidence(lead_id)
            candidates = store.candidates(lead_id)
            emails = []
            if lead.get('email'):
                emails.append(lead['email'])
            emails.extend(c.get('email') for c in candidates if c.get('email'))
            assessments = {email: store.assess(email) for email in dict.fromkeys(emails) if email}
            return {
                'contact': _public_contact(lead),
                'evidence': evidence,
                'candidates': candidates,
                'assessments': assessments,
            }
        finally:
            store.close()

    def hold(self, lead_id, reason, source):
        store, _config = self._store()
        try:
            store.hold_contact(lead_id, reason, source)
            return self.contact(lead_id)
        finally:
            store.close()

    def research(self):
        store, _config = self._store()
        try:
            queue = [_public_contact(r) for r in research_queue(store, 40)]
            evidence = store.evidence()
            candidates = store.candidates()
            return {'queue': queue, 'evidence': evidence, 'candidates': candidates}
        finally:
            store.close()

    def daily_list(self):
        root = self.workspace / 'daily'
        if not root.is_dir():
            return []
        rows = []
        for folder in sorted((p for p in root.iterdir() if p.is_dir()), reverse=True):
            report_path = folder / 'report.json'
            if not report_path.exists():
                continue
            try:
                report = json.loads(report_path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            rows.append({
                'date': report.get('date') or folder.name,
                'target': report.get('target'),
                'delivered': report.get('delivered'),
                'execute': report.get('execute'),
                'shortfall': report.get('shortfall'),
                'has_audit': (folder / 'audit.json').exists(),
                'has_leads': (folder / 'leads.csv').exists(),
            })
        return rows

    def daily(self, date=None):
        batches = self.daily_list()
        if date is None:
            date = batches[0]['date'] if batches else today_iso()
        folder = self.workspace / 'daily' / date
        report = {}
        if (folder / 'report.json').exists():
            report = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
        markdown = ''
        if (folder / 'report.md').exists():
            markdown = (folder / 'report.md').read_text(encoding='utf-8')
        elif report:
            markdown = render_report(report)
        audit = None
        if (folder / 'audit.json').exists():
            audit = json.loads((folder / 'audit.json').read_text(encoding='utf-8'))
        leads = []
        details = folder / 'leads-details.csv'
        leads_path = folder / 'leads.csv'
        if details.exists():
            leads = read_rows(details)
        elif leads_path.exists():
            leads = read_rows(leads_path)
        return {'date': date, 'report': report, 'markdown': markdown, 'audit': audit, 'leads': leads, 'batches': batches}

    def leads(self, date=None):
        return self.daily(date)

    def companies(self):
        history = self._history()
        if history is None:
            return {'domains': [], 'firms': [], 'prospects': [], 'sites': []}
        try:
            domains = [dict(r) for r in history.db.execute(
                'SELECT domain, source, company, first_seen, last_crawled, status, people, emails FROM domains ORDER BY first_seen DESC LIMIT 500'
            )]
            firms = [dict(r) for r in history.db.execute(
                'SELECT key, added_at, status, domain FROM firms ORDER BY added_at DESC LIMIT 200'
            )]
            prospects = history.prospects()
            sites = []
            for row in history.db.execute('SELECT domain, crawled_at, data FROM sites ORDER BY crawled_at DESC LIMIT 80'):
                site = json.loads(row['data'])
                sites.append({
                    'domain': row['domain'],
                    'crawled_at': row['crawled_at'],
                    'status': site.get('status'),
                    'people': len(site.get('people') or []),
                    'emails': sum(1 for p in site.get('people') or [] if p.get('email')),
                    'pattern': site.get('pattern'),
                    'homepage_url': site.get('homepage_url'),
                })
            return {'domains': domains, 'firms': firms, 'prospects': prospects, 'sites': sites}
        finally:
            history.close()

    def providers(self):
        if not (self.workspace / 'providers.json').exists():
            return {'providers': [], 'refills': []}
        store, config = self._store()
        history = self._history()
        try:
            rows = [_provider_row(name, config, store) for name in config['providers']]
            refills = history.refills() if history else []
            return {'providers': rows, 'refills': refills, 'budgets': store.budgets()}
        finally:
            store.close()
            if history:
                history.close()

    def set_enabled(self, provider, enabled):
        if provider not in PROVIDERS:
            raise PolicyError('unknown_provider')
        cfg = self.workspace / 'providers.json'
        config = load_config(cfg)
        config['providers'][provider]['enabled'] = bool(enabled)
        save_config(cfg, config)
        return self.providers()

    def jobs(self):
        store, _config = self._store()
        try:
            attempts = [_public_attempt(a) for a in store.attempts()]
            return {'attempts': attempts}
        finally:
            store.close()

    def history_view(self):
        history = self._history()
        if history is None:
            return {'stats': {}, 'delivered': [], 'suppressed': [], 'outcomes': [], 'patterns': {}, 'source_quality': {}, 'refills': []}
        try:
            delivered = [dict(r) for r in history.db.execute(
                'SELECT batch, full_name, company_website, email, linkedin_url, email_source, source FROM delivered ORDER BY batch DESC LIMIT 400'
            )]
            suppressed = [dict(r) for r in history.db.execute('SELECT key, reason, added_at FROM suppressed ORDER BY added_at DESC LIMIT 200')]
            outcomes = [dict(r) for r in history.db.execute(
                'SELECT key, outcome, source, email_source, batch, added_at FROM outcomes ORDER BY added_at DESC LIMIT 200'
            )]
            return {
                'stats': history.stats(),
                'delivered': delivered,
                'suppressed': suppressed,
                'outcomes': outcomes,
                'patterns': history.distrusted_patterns(),
                'source_quality': history.source_quality(),
                'refills': history.refills(),
            }
        finally:
            history.close()

    def preview_daily(self, target=50, sources=None):
        if not (self.workspace / 'providers.json').exists():
            raise PolicyError('run_init_first')
        from lead_research.daily import DEFAULT_SOURCES, OPTIONAL_SOURCES, run_daily
        store, config = self._store()
        chosen = tuple(sources) if sources else ('build',)
        allowed = set(DEFAULT_SOURCES) | set(OPTIONAL_SOURCES)
        if set(chosen) - allowed:
            raise PolicyError('unknown_daily_source')
        try:
            return run_daily(
                self.workspace, store, config, target=max(1, min(int(target), 500)),
                execute=False, sources=chosen + ('build',) if 'build' not in chosen else chosen,
                max_domains=20, workers=1, record_source_names=(),
            )
        finally:
            store.close()

    def _store(self):
        cfg = self.workspace / 'providers.json'
        if not cfg.exists():
            raise PolicyError('run_init_first')
        return Store(self.workspace / 'research.sqlite3'), load_config(cfg)

    def _history(self):
        path = self.workspace / 'lead-history.sqlite3'
        if not path.exists():
            return None
        return History(path)

    def _history_stats(self):
        history = self._history()
        if history is None:
            return {}
        try:
            return history.stats()
        finally:
            history.close()


def _provider_row(name, config, store):
    entry = config['providers'][name]
    adapter = get_provider(name)
    keys = tuple(getattr(adapter, 'auth_env', ()) or ())
    state = store.provider_state(name)
    budgets = [b for b in store.budgets() if b['provider'] == name]
    return {
        'id': name,
        'enabled': bool(entry.get('enabled')),
        'operations': list(getattr(adapter, 'operations', ()) or ()),
        'auth_env': list(keys),
        'key_present': True if not keys else any(bool(os.environ.get(k)) for k in keys),
        'paused': bool(state.get('paused')),
        'pause_reason': state.get('pause_reason'),
        'charges': entry.get('charges') or {},
        'budgets': budgets,
        'last_request_at': state.get('last_request_at'),
    }


def _public_contact(record):
    keys = (
        'id', 'name', 'company', 'title', 'profile_url', 'source_channel', 'source_url',
        'qualified', 'held', 'hold_reason', 'hold_source', 'email', 'email_provenance',
        'company_inbox', 'affiliation_review', 'research_complete', 'synthetic',
        'filter_evidence', 'secondary_source_urls',
    )
    return {k: record.get(k) for k in keys if k in record or k in ('id', 'name')}


def _public_assessed(record):
    keys = (
        'full_name', 'title', 'company', 'company_website', 'linkedin_url', 'email',
        'tier', 'score', 'issues', 'reject_reasons', 'email_source', 'email_kind',
        'city', 'state',
    )
    return {k: record.get(k) for k in keys}


def _public_attempt(attempt):
    outcome = attempt.get('outcome') or {}
    return {
        'id': attempt.get('id'),
        'provider': attempt.get('provider'),
        'operation': attempt.get('operation'),
        'state': attempt.get('state'),
        'created_at': attempt.get('created_at'),
        'sent_at': attempt.get('sent_at'),
        'finished_at': attempt.get('finished_at'),
        'email': outcome.get('email') or (attempt.get('payload') or {}).get('email'),
        'lead_id': (attempt.get('payload') or {}).get('lead_id'),
        'assessment': outcome.get('assessment'),
        'risk_flags': outcome.get('risk_flags') or [],
        'error': outcome.get('error'),
        'charges': {k: str(v) for k, v in (attempt.get('charges') or {}).items()},
    }


def default_empty_config():
    return default_config()
