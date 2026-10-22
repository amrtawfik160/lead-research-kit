"""Local outreach notes: stage, owner, notes. Same sidecar the dashboard reads."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .models import PolicyError

OPS_FILE = 'veinore-ops.json'
STAGES = ('new', 'contacted', 'replied', 'qualified', 'disqualified')
OPS_KEY_MAX = 200
OPS_NOTES_MAX = 4000
OPS_OWNER_MAX = 80


def ops_key(row=None, *, email=None, key=None, name=None, company=None):
    if key:
        text = str(key).strip().lower()
        if text:
            return text
    mail = str(email or (row or {}).get('Email') or (row or {}).get('email') or '').strip().lower()
    if mail:
        return mail
    full = str(name or (row or {}).get('Full Name') or (row or {}).get('full_name') or '').strip().lower()
    firm = str(company or (row or {}).get('Company') or (row or {}).get('company') or '').strip().lower()
    joined = f'{full}|{firm}'.strip('|')
    return joined


def _load(work):
    path = Path(work) / OPS_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(work, data):
    path = Path(work) / OPS_FILE
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    os.chmod(path, 0o600)
    return path


def _public(key, item):
    return {
        'key': key,
        'stage': item.get('stage') or 'new',
        'owner': item.get('owner') or '',
        'notes': item.get('notes') or '',
        'updated_at': item.get('updated_at'),
    }


def list_ops(work):
    data = _load(work)
    rows = [_public(key, item) for key, item in data.items() if isinstance(item, dict)]
    rows.sort(key=lambda row: (row['stage'], row['key']))
    return {'ops': rows, 'count': len(rows), 'stages': list(STAGES)}


def get_ops(work, *, key=None, email=None):
    resolved = ops_key(email=email, key=key)
    if not resolved:
        raise PolicyError('ops_key_required')
    item = _load(work).get(resolved)
    if not item:
        return {'key': resolved, 'ops': {'stage': 'new', 'owner': '', 'notes': ''}, 'found': False}
    return {'key': resolved, 'ops': _public(resolved, item), 'found': True}


def set_ops(work, *, key=None, email=None, stage=None, owner=None, notes=None, clock=time.time):
    resolved = ops_key(email=email, key=key)
    if not resolved:
        raise PolicyError('ops_key_required')
    if len(resolved) > OPS_KEY_MAX:
        raise PolicyError('ops_key_too_long')
    current = dict(_load(work).get(resolved) or {})
    if stage is not None:
        value = str(stage).strip().lower()
        if value not in STAGES:
            raise PolicyError('unknown_lead_stage')
        current['stage'] = value
    if notes is not None:
        text = str(notes)
        if len(text) > OPS_NOTES_MAX:
            raise PolicyError('notes_too_long')
        current['notes'] = text
    if owner is not None:
        text = str(owner).strip()
        if len(text) > OPS_OWNER_MAX:
            raise PolicyError('owner_too_long')
        current['owner'] = text
    current.setdefault('stage', 'new')
    current['updated_at'] = int(clock())
    data = _load(work)
    data[resolved] = current
    _save(work, data)
    return {'key': resolved, 'ops': _public(resolved, current)}
