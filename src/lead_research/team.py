"""Offline team roster, territories, and a shared lead-list bundle.

Teammates run on their own machines. They merge into one list by exchanging a
JSON bundle file (email it, commit it, or keep it in a shared folder). There
is no hosted server. Identity keys match History: email, LinkedIn slug, or
name + company website. Each lead keeps who found it and which area they own.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

from .daily import History, load_daily_records
from .models import PolicyError
from . import quality

BUNDLE_VERSION = 1
BUNDLE_KIND = 'veinore-team-bundle'
BUNDLE_NAME = 'veinore-team.json'
LOCAL_FILE = 'team.json'
LEADS_FILE = 'team-leads.json'
AREA_KINDS = ('industry', 'region', 'source', 'icp')
MEMBER_NAME_MAX = 80
AREA_VALUE_MAX = 80
TEAM_NAME_MAX = 80


def now_iso(clock=time.time):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(clock()))


def normalize_area(kind, value):
    kind = str(kind or '').strip().lower()
    if kind not in AREA_KINDS:
        raise PolicyError('unknown_area_kind')
    value = ' '.join(str(value or '').split())
    if not value:
        raise PolicyError('area_required')
    if len(value) > AREA_VALUE_MAX:
        raise PolicyError('area_too_long')
    return {'kind': kind, 'value': value}


def area_key(area):
    if not area:
        return ''
    return f"{area['kind']}:{area['value'].casefold()}"


def normalize_member_name(name):
    text = ' '.join(str(name or '').split())
    if not text:
        raise PolicyError('member_name_required')
    if len(text) > MEMBER_NAME_MAX:
        raise PolicyError('member_name_too_long')
    return text


def _slug(text):
    folded = re.sub(r'[^a-z0-9]+', '-', str(text or '').casefold()).strip('-')
    return (folded[:32] or 'member')


def member_id_for(name, workspace, explicit=None):
    if explicit:
        value = _slug(explicit)
        if not value:
            raise PolicyError('member_id_required')
        return value
    digest = hashlib.sha256(str(Path(workspace).resolve()).encode('utf-8')).hexdigest()[:8]
    return f'{_slug(name)}-{digest}'


def identity_keys(record):
    return list(History.keys_for(record))


def resolve_shared_path(raw):
    if not raw:
        return None
    path = Path(raw).expanduser()
    if path.exists() and path.is_dir():
        return path / BUNDLE_NAME
    if path.suffix.lower() != '.json':
        return path / BUNDLE_NAME
    return path


def _read_json(path, default):
    path = Path(path)
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise PolicyError('team_bundle_invalid') from None
    return data


def _write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    os.chmod(path, 0o600)
    return path


def empty_state():
    return {'team_id': '', 'team_name': '', 'member': None, 'shared_path': None}


def load_state(work):
    data = _read_json(Path(work) / LOCAL_FILE, None)
    if data is None:
        return empty_state()
    if not isinstance(data, dict):
        raise PolicyError('team_bundle_invalid')
    member = data.get('member') if isinstance(data.get('member'), dict) else None
    return {
        'team_id': str(data.get('team_id') or ''),
        'team_name': str(data.get('team_name') or ''),
        'member': member,
        'shared_path': data.get('shared_path') or None,
        'members': [m for m in (data.get('members') or []) if isinstance(m, dict)],
    }


def save_state(work, state):
    payload = {
        'team_id': state.get('team_id') or '',
        'team_name': state.get('team_name') or '',
        'member': state.get('member'),
        'shared_path': state.get('shared_path') or None,
        'members': list(state.get('members') or []),
    }
    return _write_json(Path(work) / LOCAL_FILE, payload)


def load_leads(work):
    data = _read_json(Path(work) / LEADS_FILE, {'leads': []})
    if not isinstance(data, dict) or not isinstance(data.get('leads'), list):
        raise PolicyError('team_bundle_invalid')
    return [lead for lead in data['leads'] if isinstance(lead, dict)]


def save_leads(work, leads):
    return _write_json(Path(work) / LEADS_FILE, {'leads': list(leads)})


def load_bundle(path):
    path = Path(path)
    if not path.exists():
        raise PolicyError('team_bundle_not_found')
    data = _read_json(path, None)
    if not isinstance(data, dict):
        raise PolicyError('team_bundle_invalid')
    if data.get('kind') not in (None, BUNDLE_KIND):
        raise PolicyError('team_bundle_invalid')
    if int(data.get('version') or BUNDLE_VERSION) != BUNDLE_VERSION:
        raise PolicyError('unsupported_team_bundle_version')
    members = [m for m in (data.get('members') or []) if isinstance(m, dict)]
    leads = [lead for lead in (data.get('leads') or []) if isinstance(lead, dict)]
    team_id = str(data.get('team_id') or '').strip()
    if not team_id:
        raise PolicyError('team_id_required')
    return {
        'version': BUNDLE_VERSION,
        'kind': BUNDLE_KIND,
        'team_id': team_id,
        'team_name': str(data.get('team_name') or ''),
        'exported_at': data.get('exported_at'),
        'exported_by': data.get('exported_by'),
        'members': members,
        'leads': leads,
    }


def write_bundle(path, bundle):
    payload = {
        'version': BUNDLE_VERSION,
        'kind': BUNDLE_KIND,
        'team_id': bundle['team_id'],
        'team_name': bundle.get('team_name') or '',
        'exported_at': bundle.get('exported_at') or now_iso(),
        'exported_by': bundle.get('exported_by'),
        'members': list(bundle.get('members') or []),
        'leads': list(bundle.get('leads') or []),
    }
    return _write_json(path, payload)


def _public_member(member):
    area = member.get('area') if isinstance(member.get('area'), dict) else {}
    return {
        'id': str(member.get('id') or ''),
        'name': str(member.get('name') or ''),
        'area': {'kind': area.get('kind'), 'value': area.get('value')} if area else None,
        'joined_at': member.get('joined_at'),
    }


def _lead_from_record(record, member, day=None, found_at=None):
    rec = record if 'company_website' in record or 'full_name' in record else quality.normalize_record(record)
    if 'full_name' not in rec and ('Full Name' in record or 'Email' in record):
        rec = quality.normalize_record(record)
    area = member.get('area') or {}
    return {
        'full_name': rec.get('full_name') or record.get('Full Name') or '',
        'title': rec.get('title') or record.get('Title') or '',
        'company': rec.get('company') or record.get('Company') or '',
        'company_website': rec.get('company_website') or '',
        'linkedin_url': rec.get('linkedin_url') or record.get('LinkedIn URL') or '',
        'email': rec.get('email') or record.get('Email') or '',
        'city': rec.get('city') or record.get('City') or '',
        'state': rec.get('state') or record.get('State') or '',
        'source': rec.get('email_source') or rec.get('source') or record.get('Source') or record.get('Email Source') or '',
        'email_risk': rec.get('email_risk') or record.get('Email Risk') or '',
        'email_verification': rec.get('email_verification') or record.get('Email Verification') or '',
        'found_by': member.get('id'),
        'found_by_name': member.get('name'),
        'area_kind': area.get('kind'),
        'area': area.get('value'),
        'found_at': found_at or now_iso(),
        'batch_date': day or rec.get('batch_date') or record.get('Batch Date') or '',
    }


def lead_row(lead):
    """Dashboard / filter-facing headers. Core 6 columns stay first."""
    return {
        'Full Name': lead.get('full_name') or '',
        'Title': lead.get('title') or '',
        'Company': lead.get('company') or '',
        'Company Website': lead.get('company_website') or '',
        'LinkedIn URL': lead.get('linkedin_url') or '',
        'Email': lead.get('email') or '',
        'City': lead.get('city') or '',
        'State': lead.get('state') or '',
        'Source': lead.get('source') or '',
        'Email Risk': lead.get('email_risk') or '',
        'Email Verification': lead.get('email_verification') or '',
        'Found By': lead.get('found_by_name') or '',
        'Found By Id': lead.get('found_by') or '',
        'Area': lead.get('area') or '',
        'Area Kind': lead.get('area_kind') or '',
        'Batch Date': lead.get('batch_date') or '',
    }


def _merge_members(existing, incoming, *, allow_same_member_area_change=True):
    by_id = {}
    claimed = {}
    for member in existing:
        public = _public_member(member)
        if not public['id']:
            continue
        by_id[public['id']] = public
        key = area_key(public.get('area'))
        if key:
            claimed[key] = public['id']
    conflicts = []
    for member in incoming:
        public = _public_member(member)
        if not public['id']:
            continue
        key = area_key(public.get('area'))
        owner = claimed.get(key)
        if key and owner and owner != public['id']:
            conflicts.append({
                'area': public['area'],
                'held_by': owner,
                'claimed_by': public['id'],
            })
            continue
        prior = by_id.get(public['id'])
        if prior and not allow_same_member_area_change and area_key(prior.get('area')) != key:
            conflicts.append({
                'area': public['area'],
                'held_by': public['id'],
                'claimed_by': public['id'],
            })
            continue
        if prior and prior.get('area') and key and area_key(prior.get('area')) != key:
            old = area_key(prior.get('area'))
            if claimed.get(old) == public['id']:
                del claimed[old]
        by_id[public['id']] = public
        if key:
            claimed[key] = public['id']
    if conflicts:
        error = PolicyError('area_already_claimed')
        error.details = {'conflicts': conflicts}
        raise error
    return list(by_id.values())


def _merge_leads(existing, incoming):
    merged, seen = [], set()
    existing_keys = set()
    for lead in existing or []:
        existing_keys |= set(identity_keys(lead))
    for lead in list(existing or []) + list(incoming or []):
        keys = set(identity_keys(lead))
        if not keys or keys & seen:
            continue
        seen |= keys
        merged.append(lead)
    known = set(existing_keys)
    added = 0
    for lead in incoming or []:
        keys = set(identity_keys(lead))
        if keys and not (keys & known):
            added += 1
            known |= keys
    duplicates = max(0, len(list(existing or [])) + len(list(incoming or [])) - len(merged))
    return merged, added, duplicates


def collect_local_daily_leads(work):
    root = Path(work) / 'daily'
    rows = []
    if not root.is_dir():
        return rows
    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or folder.name.endswith('-preview'):
            continue
        for record in load_daily_records(folder):
            record = dict(record)
            record['batch_date'] = folder.name
            rows.append(record)
    return rows


def require_member(work):
    state = load_state(work)
    if not state.get('member') or not state.get('team_id'):
        raise PolicyError('team_join_required')
    return state


def _mark_foreign_history(work, leads, member_id):
    foreign = [lead for lead in leads if lead.get('found_by') and lead.get('found_by') != member_id]
    if not foreign:
        return 0
    history = History(Path(work) / 'lead-history.sqlite3')
    try:
        history.mark_delivered(foreign, f'team:{member_id or "shared"}')
        return len(foreign)
    finally:
        history.close()


def join(work, *, name, area_kind, area, team_id=None, team_name=None, shared=None, member_id=None, clock=time.time):
    work = Path(work)
    member_name = normalize_member_name(name)
    area = normalize_area(area_kind, area)
    mid = member_id_for(member_name, work, member_id)
    state = load_state(work)
    shared_path = resolve_shared_path(shared or state.get('shared_path'))
    bundle = None
    if shared_path and shared_path.exists():
        bundle = load_bundle(shared_path)
    if state.get('team_id') and bundle and bundle['team_id'] != state['team_id']:
        raise PolicyError('team_id_mismatch')
    if team_id and bundle and bundle['team_id'] != team_id:
        raise PolicyError('team_id_mismatch')
    if team_id and state.get('team_id') and state['team_id'] != team_id:
        raise PolicyError('team_id_mismatch')
    resolved_id = (bundle or {}).get('team_id') or state.get('team_id') or team_id or f'veinore-{hashlib.sha256((member_name + now_iso(clock)).encode()).hexdigest()[:8]}'
    resolved_name = team_name or (bundle or {}).get('team_name') or state.get('team_name') or 'Veinore team'
    resolved_name = ' '.join(str(resolved_name).split())[:TEAM_NAME_MAX] or 'Veinore team'
    member = {
        'id': mid,
        'name': member_name,
        'area': area,
        'joined_at': (state.get('member') or {}).get('joined_at') or now_iso(clock),
    }
    roster = list((bundle or {}).get('members') or state.get('members') or [])
    if state.get('member'):
        roster = [m for m in roster if m.get('id') != state['member'].get('id')] + [state['member']]
    members = _merge_members(roster, [member])
    state = {
        'team_id': resolved_id,
        'team_name': resolved_name,
        'member': member,
        'shared_path': str(shared_path) if shared_path else state.get('shared_path'),
        'members': members,
    }
    save_state(work, state)
    if shared_path:
        leads = list((bundle or {}).get('leads') or load_leads(work))
        write_bundle(shared_path, {
            'team_id': resolved_id,
            'team_name': resolved_name,
            'exported_at': now_iso(clock),
            'exported_by': mid,
            'members': members,
            'leads': leads,
        })
        if leads:
            local_leads, _added, _dupes = _merge_leads(load_leads(work), leads)
            save_leads(work, local_leads)
            _mark_foreign_history(work, local_leads, mid)
    return list_team(work)


def export_team(work, *, output=None, shared=None, clock=time.time):
    state = require_member(work)
    member = state['member']
    dest = resolve_shared_path(output or shared or state.get('shared_path'))
    if dest is None:
        dest = Path(work) / 'exports' / BUNDLE_NAME
    local_finds = [_lead_from_record(row, member, day=row.get('batch_date')) for row in collect_local_daily_leads(work)]
    existing = load_leads(work)
    incoming_bundle = load_bundle(dest) if dest.exists() else None
    if incoming_bundle and incoming_bundle['team_id'] != state['team_id']:
        raise PolicyError('team_id_mismatch')
    members = _merge_members(
        list((incoming_bundle or {}).get('members') or []) + list(state.get('members') or []),
        [member],
    )
    leads, added, duplicates = _merge_leads(
        list((incoming_bundle or {}).get('leads') or []) + existing,
        local_finds,
    )
    save_state(work, {**state, 'members': members, 'shared_path': str(dest) if (shared or state.get('shared_path')) else state.get('shared_path')})
    save_leads(work, leads)
    write_bundle(dest, {
        'team_id': state['team_id'],
        'team_name': state['team_name'],
        'exported_at': now_iso(clock),
        'exported_by': member['id'],
        'members': members,
        'leads': leads,
    })
    return {
        **list_team(work),
        'output': str(dest),
        'exported': len(leads),
        'added_from_local': added,
        'duplicates_skipped': duplicates,
    }


def import_team(work, source=None, *, shared=None, clock=time.time):
    state = load_state(work)
    path = resolve_shared_path(source or shared or state.get('shared_path'))
    if path is None:
        raise PolicyError('team_bundle_not_found')
    bundle = load_bundle(path)
    if state.get('team_id') and state['team_id'] != bundle['team_id']:
        raise PolicyError('team_id_mismatch')
    local_member = state.get('member')
    members = _merge_members(list(state.get('members') or []) + ([local_member] if local_member else []), bundle['members'])
    if local_member:
        mine = next((m for m in members if m.get('id') == local_member.get('id')), None)
        if mine:
            local_member = {**local_member, 'area': mine.get('area') or local_member.get('area')}
    leads, added, duplicates = _merge_leads(load_leads(work), bundle['leads'])
    state = {
        'team_id': bundle['team_id'],
        'team_name': bundle.get('team_name') or state.get('team_name') or 'Veinore team',
        'member': local_member,
        'shared_path': str(path) if (shared or state.get('shared_path')) else state.get('shared_path'),
        'members': members,
    }
    save_state(work, state)
    save_leads(work, leads)
    marked = _mark_foreign_history(work, leads, (local_member or {}).get('id'))
    view = list_team(work)
    view.update({
        'source': str(path),
        'added': added,
        'duplicates': duplicates,
        'marked_owned_by_teammates': marked,
    })
    return view


def merge_team(work, source=None, **kwargs):
    return import_team(work, source, **kwargs)


def pull_shared(work):
    state = load_state(work)
    path = resolve_shared_path(state.get('shared_path'))
    if path is None or not path.exists():
        return None
    try:
        return import_team(work, path)
    except PolicyError:
        return None


def list_team(work, include_leads=True):
    state = load_state(work)
    leads = load_leads(work)
    by_finder = {}
    for lead in leads:
        name = lead.get('found_by_name') or lead.get('found_by') or 'unknown'
        by_finder[name] = by_finder.get(name, 0) + 1
    members = list(state.get('members') or [])
    if state.get('member') and not any(m.get('id') == state['member'].get('id') for m in members):
        members.append(_public_member(state['member']))
    payload = {
        'team_id': state.get('team_id') or '',
        'team_name': state.get('team_name') or '',
        'joined': bool(state.get('member')),
        'me': _public_member(state['member']) if state.get('member') else None,
        'shared_path': state.get('shared_path'),
        'members': [_public_member(m) for m in members],
        'areas': [
            {'member': m.get('name'), 'member_id': m.get('id'), **(m.get('area') or {})}
            for m in members if m.get('area')
        ],
        'leads_count': len(leads),
        'by_finder': by_finder,
    }
    if include_leads:
        payload['leads'] = [lead_row(lead) for lead in leads]
    return payload


def owned_key_set(work, except_member=None):
    keys = set()
    member_id = except_member
    if member_id is None:
        state = load_state(work)
        member_id = (state.get('member') or {}).get('id')
    for lead in load_leads(work):
        if lead.get('found_by') and lead.get('found_by') == member_id:
            continue
        if not lead.get('found_by'):
            continue
        keys.update(identity_keys(lead))
    return keys


def partition_owned(records, work, except_member=None):
    owned = owned_key_set(work, except_member=except_member)
    if not owned:
        return list(records), []
    fresh, skipped = [], []
    for record in records:
        keys = set(identity_keys(record))
        if keys and keys & owned:
            skipped.append(record)
        else:
            fresh.append(record)
    return fresh, skipped


def record_finds(work, records, day=None, clock=time.time):
    state = load_state(work)
    member = state.get('member')
    if not member or not records:
        return 0
    incoming = [_lead_from_record(record, member, day=day, found_at=now_iso(clock)) for record in records]
    merged, added, _duplicates = _merge_leads(load_leads(work), incoming)
    save_leads(work, merged)
    if state.get('members') is None:
        state['members'] = []
    if not any(m.get('id') == member.get('id') for m in state['members']):
        state['members'] = list(state['members']) + [_public_member(member)]
        save_state(work, state)
    return added


def lookup_attribution(work):
    """Map identity keys to Found By / Area for dashboard rows."""
    index = {}
    for lead in load_leads(work):
        row = lead_row(lead)
        for key in identity_keys(lead):
            index[key] = row
    return index
