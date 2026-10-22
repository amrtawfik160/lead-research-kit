"""Read a private workspace through the kit. Secrets never leave the process."""
from __future__ import annotations

import csv
import io
import json
import os
import time
from pathlib import Path

from lead_research import __version__
from lead_research.configuration import default_config, load_config, save_config
from lead_research.daily import History, render_report, today_iso
from lead_research.intake import research_queue
from lead_research.models import PolicyError
from lead_research.policy import format_units, parse_units
from lead_research.providers import PROVIDERS, get_provider
from lead_research.quality import Target, clean_records, read_rows
from lead_research.store import Store

from .sample import seed_sample

DASHBOARD_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = DASHBOARD_ROOT / '.data'
SAMPLE_DIR = DATA_DIR / 'sample-workspace'
SETTINGS_PATH = DATA_DIR / 'settings.json'
OPS_FILENAME = 'veinore-ops.json'
LEAD_STAGES = ('new', 'contacted', 'replied', 'qualified', 'disqualified')
MAX_DAILY_TARGET = 200
OPS_NOTES_MAX = 4000
OPS_OWNER_MAX = 80
OPS_KEY_MAX = 240


def env_workspace_locked():
    return bool((os.environ.get('LEAD_RESEARCH_WORKSPACE') or '').strip())


def validate_workspace_path(raw):
    """A local directory. Reject traversal and paths that are not folders."""
    text = str(raw or '').strip()
    if not text:
        raise PolicyError('workspace_required')
    candidate = Path(text).expanduser()
    if '..' in candidate.parts:
        raise PolicyError('invalid_workspace_path')
    path = candidate.resolve()
    if not path.is_dir():
        raise PolicyError('workspace_not_found')
    return path


def resolve_workspace(settings=None):
    """Pick the operator workspace or the opt-in synthetic sample.

    LEAD_RESEARCH_WORKSPACE wins over the saved settings file. Sample mode is
    the default. An explicit sample:false without a real directory is an error,
    not a silent return to the sample workspace.
    """
    settings = settings if settings is not None else load_settings()
    env_ws = (os.environ.get('LEAD_RESEARCH_WORKSPACE') or '').strip()
    env_sample = (os.environ.get('LEAD_RESEARCH_SAMPLE') or '').strip().lower()
    if env_ws:
        return Path(env_ws).expanduser().resolve(), env_sample in ('1', 'true', 'yes')
    if env_sample in ('0', 'false', 'no') and str(settings.get('workspace') or '').strip():
        return validate_workspace_path(settings.get('workspace')), False
    if settings.get('sample') is False:
        return validate_workspace_path(settings.get('workspace')), False
    return SAMPLE_DIR.resolve(), True


def _public_settings(settings):
    if not isinstance(settings, dict):
        return {'sample': True}
    public = {'sample': settings.get('sample') is not False}
    workspace = settings.get('workspace')
    if isinstance(workspace, str) and workspace.strip():
        public['workspace'] = workspace.strip()
    return public


def settings_view():
    """Settings for the UI. Always readable, even when the saved path is broken."""
    settings = load_settings()
    error = None
    try:
        meta = Dashboard().meta()
    except PolicyError as exc:
        error = str(exc)
        meta = {
            'kit_version': __version__,
            'workspace': '',
            'sample': False,
            'initialized': False,
            'today': today_iso(),
        }
    return {
        'settings': _public_settings(settings),
        'env_locked': env_workspace_locked(),
        'settings_error': error,
        **meta,
    }


def update_settings(body):
    """Switch sample mode or a real directory. Does not store secrets."""
    if not isinstance(body, dict):
        raise PolicyError('expected_object')
    settings = load_settings()
    if not isinstance(settings, dict):
        settings = {'sample': True}
    workspace = str(body.get('workspace') or '').strip() if 'workspace' in body else ''
    if workspace:
        settings['workspace'] = str(validate_workspace_path(workspace))
        settings['sample'] = False
    elif 'sample' in body:
        if bool(body['sample']):
            settings['sample'] = True
            seed_sample(SAMPLE_DIR, force=False)
        else:
            settings['workspace'] = str(validate_workspace_path(settings.get('workspace')))
            settings['sample'] = False
    else:
        raise PolicyError('workspace_required')
    save_settings(settings)
    return settings_view()


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
        payload = self.daily(latest['date']) if latest else None
        report = (payload or {}).get('report')
        research = self.research()
        return {
            'meta': self.meta(),
            'status': status,
            'history': history,
            'latest_daily': latest,
            'report': report,
            'research_queue_count': len(research['queue']),
            'advice': (report or {}).get('advice') or [],
            'charts': self._charts(batches, report, (payload or {}).get('leads') or [], status),
            'duplicates': (payload or {}).get('duplicates') or [],
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
        for folder in root.iterdir():
            if not folder.is_dir():
                continue
            report_path = folder / 'report.json'
            if not report_path.exists():
                continue
            try:
                report = json.loads(report_path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            rows.append({
                'date': folder.name,
                'target': report.get('target'),
                'delivered': report.get('delivered'),
                'execute': report.get('execute'),
                'shortfall': report.get('shortfall'),
                'has_audit': (folder / 'audit.json').exists(),
                'has_leads': (folder / 'leads.csv').exists(),
            })
        # Executed batches first so a same-day dry preview cannot hide delivery.
        rows.sort(key=lambda row: (bool(row.get('execute')), str(row.get('date') or '')), reverse=True)
        team = self.team_view()
        if team.get('leads_count'):
            rows.append({
                'date': 'team',
                'target': team['leads_count'],
                'delivered': team['leads_count'],
                'execute': True,
                'shortfall': 0,
                'has_audit': False,
                'has_leads': True,
            })
        return rows

    def daily(self, date=None):
        batches = self.daily_list()
        if date is None:
            date = next((row['date'] for row in batches if row.get('date') != 'team'), None)
            date = date or (batches[0]['date'] if batches else today_iso())
        if date == 'team':
            return self._team_leads_payload(batches)
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
        leads = self._attach_team(self._attach_ops(self._read_daily_leads(date)))
        payload = {
            'date': date,
            'report': report,
            'markdown': markdown,
            'audit': audit,
            'leads': leads,
            'batches': batches,
            'duplicates': self._duplicate_warnings(leads, date),
        }
        payload.update(self._filter_payload())
        payload['team'] = self.team_view(include_leads=False)
        return payload

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
            raw_delivered = [dict(r) for r in history.db.execute(
                'SELECT key, batch, full_name, company_website, email, linkedin_url, email_source, source FROM delivered ORDER BY batch DESC LIMIT 400'
            )]
            grouped_delivered = {}
            for row in raw_delivered:
                person_key = (
                    row.get('batch') or '',
                    (row.get('email') or row.get('full_name') or row.get('key') or '').lower(),
                )
                entry = grouped_delivered.get(person_key)
                key_prefix = str(row.get('key') or '').split(':', 1)[0]
                if entry is None:
                    entry = {
                        **row,
                        'keys': [key_prefix] if key_prefix else [],
                        'key_count': 1,
                    }
                    grouped_delivered[person_key] = entry
                else:
                    entry['key_count'] = int(entry.get('key_count') or 1) + 1
                    if key_prefix and key_prefix not in entry['keys']:
                        entry['keys'].append(key_prefix)
            delivered = [
                {**item, 'keys': ', '.join(item.get('keys') or [])}
                for item in grouped_delivered.values()
            ]
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
        return self.daily_run(target=target, execute=False, sources=sources, preview_cap=500)

    def daily_run(self, target=20, execute=False, sources=None, preview_cap=None):
        """Plan or write a daily file. Execute stays dry unless explicitly True.

        Live execute is local-only: stored sites/prospects (`build`), no provider
        transport, no MX DNS. Target is capped so the dashboard cannot launch a
        large spend run.
        """
        if not (self.workspace / 'providers.json').exists():
            raise PolicyError('run_init_first')
        from lead_research.daily import DEFAULT_SOURCES, OPTIONAL_SOURCES, run_daily
        store, config = self._store()
        execute = bool(execute)
        cap = MAX_DAILY_TARGET if execute else (preview_cap or MAX_DAILY_TARGET)
        try:
            target_n = max(1, min(int(target), cap))
        except (TypeError, ValueError):
            raise PolicyError('invalid_daily_target') from None
        if execute:
            chosen = ('build',)
        else:
            chosen = tuple(sources) if sources else ('build',)
            allowed = set(DEFAULT_SOURCES) | set(OPTIONAL_SOURCES)
            if set(chosen) - allowed:
                raise PolicyError('unknown_daily_source')
            if 'build' not in chosen:
                chosen = chosen + ('build',)

        def _offline(_request):
            raise PolicyError('dashboard_run_is_local_only')

        try:
            return run_daily(
                self.workspace, store, config, target=target_n,
                execute=execute, sources=chosen, max_domains=20, workers=1,
                record_source_names=(), mx_check=False,
                transport=_offline if execute else None,
            )
        finally:
            store.close()

    def team_view(self, include_leads=True):
        from lead_research.team import list_team
        return list_team(self.workspace, include_leads=include_leads)

    def filters_view(self):
        from lead_research.filters import list_filters
        return list_filters(self.workspace)

    def _filter_payload(self):
        filters = self.filters_view()
        return {'filters': filters.get('filters') or [], 'pinned_filters': filters.get('pinned') or []}

    def _team_leads_payload(self, batches):
        from lead_research.filters import collect_leads
        leads = self._attach_ops(collect_leads(self.workspace, 'team'))
        team = self.team_view()
        payload = {
            'date': 'team',
            'report': {
                'date': 'team',
                'execute': True,
                'target': team.get('leads_count') or 0,
                'delivered': team.get('leads_count') or 0,
                'shortfall': 0,
            },
            'markdown': 'Team list: %s leads · %s people' % (
                team.get('leads_count') or 0, len(team.get('members') or []),
            ),
            'audit': None,
            'leads': leads,
            'batches': batches,
            'duplicates': self._duplicate_warnings(leads, 'team'),
            'team': team,
        }
        payload.update(self._filter_payload())
        return payload

    def _attach_team(self, leads):
        from lead_research.daily import History
        from lead_research.team import lookup_attribution
        index = lookup_attribution(self.workspace)
        if not index:
            return leads
        attached = []
        for row in leads:
            record = dict(row)
            extra = None
            for key in History.keys_for(row):
                extra = index.get(key)
                if extra:
                    break
            if extra:
                record.setdefault('Found By', extra.get('Found By') or '')
                record.setdefault('Area', extra.get('Area') or '')
                record.setdefault('Area Kind', extra.get('Area Kind') or '')
                record.setdefault('Found By Id', extra.get('Found By Id') or '')
            attached.append(record)
        return attached

    def ops(self):
        return {'ops': self._load_ops(), 'stages': list(LEAD_STAGES)}

    def update_ops(self, body):
        if not isinstance(body, dict):
            raise PolicyError('expected_object')
        key = str(body.get('key') or '').strip().lower()
        if not key:
            raise PolicyError('ops_key_required')
        if len(key) > OPS_KEY_MAX:
            raise PolicyError('ops_key_too_long')
        current = dict(self._load_ops().get(key) or {})
        if 'stage' in body:
            stage = str(body.get('stage') or '').strip().lower()
            if stage not in LEAD_STAGES:
                raise PolicyError('unknown_lead_stage')
            current['stage'] = stage
        if 'notes' in body:
            notes = str(body.get('notes') or '')
            if len(notes) > OPS_NOTES_MAX:
                raise PolicyError('notes_too_long')
            current['notes'] = notes
        if 'owner' in body:
            owner = str(body.get('owner') or '').strip()
            if len(owner) > OPS_OWNER_MAX:
                raise PolicyError('owner_too_long')
            current['owner'] = owner
        current.setdefault('stage', 'new')
        current['updated_at'] = int(time.time())
        data = self._load_ops()
        data[key] = current
        self._save_ops(data)
        return {'key': key, 'ops': current}

    def budget_view(self):
        providers = self.providers() if (self.workspace / 'providers.json').exists() else {
            'providers': [], 'refills': [], 'budgets': [],
        }
        spent = self._spent_units()
        rows = []
        for budget in providers.get('budgets') or []:
            key = (budget.get('provider'), budget.get('pool'))
            spent_units = spent.get(key, 0)
            remaining_units = int(budget.get('remaining_units') or 0)
            rows.append({
                **budget,
                'spent': format_units(spent_units),
                'spent_units': spent_units,
                'quota_units': remaining_units + spent_units,
                'quota': format_units(remaining_units + spent_units),
            })
        rows.sort(key=lambda row: (-int(row.get('spent_units') or 0), -int(row.get('quota_units') or 0), str(row.get('provider') or '')))
        return {
            'budgets': rows,
            'refills': providers.get('refills') or [],
            'providers': providers.get('providers') or [],
            'spent': [
                {'provider': provider, 'pool': pool, 'spent': format_units(units), 'spent_units': units}
                for (provider, pool), units in sorted(spent.items())
            ],
        }

    def _ops_path(self):
        return self.workspace / OPS_FILENAME

    def _load_ops(self):
        path = self._ops_path()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_ops(self, data):
        self.workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self._ops_path()
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        os.chmod(path, 0o600)
        return data

    def _read_daily_leads(self, date):
        folder = self.workspace / 'daily' / str(date)
        details = folder / 'leads-details.csv'
        leads_path = folder / 'leads.csv'
        if details.exists():
            return read_rows(details)
        if leads_path.exists():
            return read_rows(leads_path)
        return []

    def _attach_ops(self, leads):
        ops = self._load_ops()
        attached = []
        for row in leads:
            record = dict(row)
            key = ops_key(record)
            extra = ops.get(key) or {}
            record['ops_key'] = key
            record['Stage'] = extra.get('stage') or 'new'
            record['Notes'] = extra.get('notes') or ''
            record['Owner'] = extra.get('owner') or ''
            if extra.get('updated_at') is not None:
                record['ops_updated_at'] = extra.get('updated_at')
            attached.append(record)
        return attached

    def _duplicate_warnings(self, leads, date):
        seen = {}
        ranked = {}
        for row in leads:
            email = _lead_email(row)
            if not email:
                continue
            name = row.get('Full Name') or row.get('full_name')
            if email in seen:
                _keep_warning(ranked, {
                    'kind': 'within_file',
                    'email': email,
                    'name': name,
                    'also_in': date,
                })
            else:
                seen[email] = name
        other = {}
        for batch in self.daily_list():
            other_date = batch.get('date')
            if other_date == date:
                continue
            for row in self._read_daily_leads(other_date):
                email = _lead_email(row)
                if email:
                    other.setdefault(email, other_date)
        for email, name in seen.items():
            if email in other:
                _keep_warning(ranked, {
                    'kind': 'across_batches',
                    'email': email,
                    'name': name,
                    'also_in': other[email],
                })
        history = self._history()
        if history is not None:
            try:
                hist = {}
                for row in history.db.execute(
                    'SELECT email, batch FROM delivered WHERE email IS NOT NULL AND email != ""'
                ):
                    email = str(row['email'] or '').strip().lower()
                    batch = str(row['batch'] or '')
                    if email and batch != str(date or ''):
                        hist.setdefault(email, batch)
                for email, name in seen.items():
                    if email in hist:
                        _keep_warning(ranked, {
                            'kind': 'history',
                            'email': email,
                            'name': name,
                            'also_in': hist[email],
                        })
            finally:
                history.close()
        return list(ranked.values())

    def _spent_units(self):
        if not (self.workspace / 'providers.json').exists():
            return {}
        store, _config = self._store()
        try:
            spent = {}
            for attempt in store.attempts():
                if attempt.get('state') not in ('completed', 'reviewed'):
                    continue
                provider = attempt.get('provider')
                for pool, amount in (attempt.get('charges') or {}).items():
                    try:
                        units = _ledger_units(amount)
                    except (PolicyError, TypeError, ValueError):
                        continue
                    key = (provider, pool)
                    spent[key] = spent.get(key, 0) + units
            return spent
        finally:
            store.close()

    def _charts(self, batches, report, leads, status):
        days = []
        for batch in sorted(batches, key=lambda row: str(row.get('date') or '')):
            if not batch.get('execute'):
                continue
            days.append({
                'date': batch.get('date'),
                'delivered': int(batch.get('delivered') or 0),
                'target': int(batch.get('target') or 0),
            })
        verified = 0
        unverified = 0
        sources = {}
        for row in leads:
            if _is_verified(row):
                verified += 1
            else:
                unverified += 1
            src = str(row.get('Email Source') or row.get('Source') or row.get('email_source') or 'unknown')
            sources[src] = sources.get(src, 0) + 1
        report_sources = (report or {}).get('delivered_by_source') or {}
        if report_sources:
            sources = {str(key): int(value) for key, value in report_sources.items()}
        total = verified + unverified
        spent = self._spent_units()
        quota = []
        for provider in (status.get('providers') or {}).values():
            for budget in provider.get('budgets') or []:
                remaining_units = int(budget.get('remaining_units') or 0)
                spent_units = spent.get((provider.get('id'), budget.get('pool')), 0)
                quota.append({
                    'id': provider.get('id'),
                    'pool': budget.get('pool'),
                    'remaining': budget.get('remaining'),
                    'remaining_units': remaining_units,
                    'spent': format_units(spent_units),
                    'spent_units': spent_units,
                    'quota': format_units(remaining_units + spent_units),
                    'quota_units': remaining_units + spent_units,
                })
        quota.sort(key=lambda row: (-int(row.get('spent_units') or 0), -int(row.get('quota_units') or 0), str(row.get('id'))))
        budget_spent = [
            {
                'provider': provider,
                'pool': pool,
                'spent': format_units(units),
                'spent_units': units,
            }
            for (provider, pool), units in sorted(spent.items(), key=lambda item: -item[1])
        ]
        return {
            'leads_per_day': days[-14:],
            'verification': {
                'verified': verified,
                'unverified': unverified,
                'rate': round(100 * verified / total) if total else 0,
            },
            'provider_quota': quota[:8],
            'sources': [{'name': name, 'count': count} for name, count in sorted(sources.items(), key=lambda item: -item[1])],
            'budget_spent': budget_spent,
        }

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
    required = tuple(getattr(adapter, 'auth_env', ()) or ())
    optional = tuple(getattr(adapter, 'optional_auth_env', ()) or ())
    keys = required or optional
    state = store.provider_state(name)
    budgets = [b for b in store.budgets() if b['provider'] == name]
    return {
        'id': name,
        'enabled': bool(entry.get('enabled')),
        'operations': list(getattr(adapter, 'operations', ()) or ()),
        'auth_env': list(keys),
        'key_present': True if not required else any(bool(os.environ.get(k)) for k in required),
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


def ops_key(row):
    email = _lead_email(row)
    if email:
        return email
    name = str(row.get('Full Name') or row.get('full_name') or row.get('name') or '').strip().lower()
    company = str(row.get('Company') or row.get('company') or '').strip().lower()
    return f'{name}|{company}'


def _ledger_units(amount):
    """Store attempts keep charges as integer millionths already."""
    if isinstance(amount, bool):
        raise PolicyError('invalid_amount')
    if isinstance(amount, int):
        return amount
    text = str(amount).strip()
    if text.isdigit():
        return int(text)
    return parse_units(text)


def _lead_email(row):
    return str(row.get('Email') or row.get('email') or '').strip().lower()


def _is_verified(row):
    verification = str(row.get('Email Verification') or row.get('email_verification') or '').lower()
    if verification in ('valid', 'verified', 'provider_valid'):
        return True
    risk = str(row.get('Email Risk') or row.get('email_risk') or '').lower()
    return risk == 'low'


_WARNING_RANK = {'within_file': 0, 'across_batches': 1, 'history': 2}


def _keep_warning(ranked, warning):
    email = warning['email']
    current = ranked.get(email)
    if current is None or _WARNING_RANK[warning['kind']] < _WARNING_RANK[current['kind']]:
        ranked[email] = warning
