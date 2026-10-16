"""Import user-supplied records and export evidence-labeled contact options."""
import csv
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from .models import PolicyError
from .policy import normalize_email, validate_evidence_url

PUBLIC = {"public_person_linked", "public_source_confirmed_identity_matched"}
CHANNELS = {"linkedin", "public_web", "manual"}
ROLE_MAILBOX = re.compile(r'^(info|contact|hello|office|admin|sales|support|team|press|media|investors|jobs|careers|marketing)@', re.I)
FREE_DOMAINS = {'gmail.com','outlook.com','hotmail.com','yahoo.com','icloud.com','aol.com','proton.me','protonmail.com','live.com'}
LEADERSHIP = re.compile(r"\b(ceo|chief|founder|owner|president|partner|principal|chair|director|cfo|coo|broker.owner)\b", re.I)

def now():
    return datetime.now(timezone.utc).isoformat()

def canonical_url(value):
    if not value:
        return ""
    validate_evidence_url(value)
    p = urlsplit(value)
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip('/'), '', ''))

def stable_id(record):
    if record.get('id') is not None or record.get('lead_id') is not None:
        return str(record.get('id') if record.get('id') is not None else record['lead_id'])
    key = canonical_url(record.get('profile_url', '')) or '|'.join(str(record.get(k, '')).strip().casefold() for k in ('name', 'company', 'source_url'))
    if not key.strip('|'):
        raise PolicyError('record_needs_identity')
    return 'lead_' + hashlib.sha256(key.encode()).hexdigest()[:24]

def load_records(file):
    p = Path(file)
    if p.stat().st_size > 32 * 1024 * 1024:
        raise PolicyError('input_too_large')
    with p.open(encoding='utf-8-sig', newline='') as f:
        if p.suffix.lower() == '.csv':
            return list(csv.DictReader(f))
        if p.suffix.lower() == '.jsonl':
            return [json.loads(line) for line in f if line.strip()]
        data = json.load(f)
    if not isinstance(data, list):
        raise PolicyError('expected_record_array')
    return data

def _bool(value):
    return value is True or str(value).lower() in ('true', '1', 'yes')

def eligible(record):
    if record.get('held') or record.get('is_deceased') or record.get('record_kind') == 'company' or record.get('role_current') is False:
        return False
    if not LEADERSHIP.search(record.get('title', '')) or re.match(r'^\s*(former|retired)\b', record.get('title', ''), re.I):
        return False
    if record.get('source_channel') == 'linkedin':
        filters = record.get('filter_evidence') or {}
        return filters.get('accepted') is True and bool(filters.get('country')) and bool(filters.get('industry'))
    return record.get('qualified') is True and bool(record.get('source_url'))

def add_evidence(store, lead_id, email, classification, source_url, excerpt, person_matched=False, affiliation_matches=None, observed_at=None):
    lead = next((r for r in store.contacts() if str(r['id']) == str(lead_id)), None)
    if not lead:
        raise PolicyError('unknown_lead')
    validate_evidence_url(source_url)
    email = normalize_email(email)
    if classification not in ('public_person_linked', 'company_inbox', 'candidate'):
        raise PolicyError('invalid_evidence_classification')
    if not isinstance(excerpt, str) or email.casefold() not in excerpt.casefold():
        raise PolicyError('email_not_in_source_excerpt')
    if classification == 'public_person_linked' and not person_matched:
        raise PolicyError('person_match_attestation_required')
    if classification == 'public_person_linked' and ROLE_MAILBOX.match(email):
        raise PolicyError('role_mailbox_must_remain_company_inbox')
    evidence = {'id': 'evidence_' + hashlib.sha256(json.dumps([str(lead_id),email,source_url,excerpt,classification]).encode()).hexdigest()[:24], 'lead_id': str(lead_id), 'email': email, 'classification': classification, 'source_url': source_url, 'excerpt': excerpt, 'person_matched': bool(person_matched), 'affiliation_matches': affiliation_matches, 'observed_at': observed_at or now(), 'verification_scope': 'operator-supplied source evidence, not a mailbox test'}
    store.put_evidence(evidence)
    if classification == 'public_person_linked':
        lead['email'] = email
        lead['email_evidence_id'] = evidence['id']
        lead['email_provenance'] = classification
        if affiliation_matches is False:
            lead['affiliation_review'] = True
        store.put_contact(lead)
    elif classification == 'company_inbox':
        lead['company_inbox'] = email
        store.put_contact(lead)
    else:
        store.put_candidate({'id': 'candidate_' + hashlib.sha256((str(lead_id)+'|'+email).encode()).hexdigest()[:24], 'lead_id': str(lead_id), 'email': email, 'kind': 'source_candidate', 'source_url': source_url, 'identity_confirmed': False})
    return evidence

def import_records(store, file, channel=None, filters=None, trust_classifications=False):
    imported = []
    for raw in load_records(file):
        if not isinstance(raw, dict) or not str(raw.get('name', '')).strip():
            raise PolicyError('record_needs_name')
        record = dict(raw)
        source = channel or record.get('source_channel') or record.get('lead_source_type')
        if source == 'linkedin_us_real_estate_search': source = 'linkedin'
        if source in ('public_directory', 'additional_public_source', 'public_company_directory'): source = 'public_web'
        if source not in CHANNELS:
            raise PolicyError('explicit_source_channel_required')
        record['source_channel'] = source
        record['source_url'] = record.get('source_url') or record.get('lead_source_url') or record.get('profile_url') or ''
        if record['source_url']: validate_evidence_url(record['source_url'])
        if filters is not None: record['filter_evidence'] = dict(filters)
        profile = canonical_url(record.get('profile_url', ''))
        existing = next((r for r in store.contacts() if profile and canonical_url(r.get('profile_url', '')) == profile), None)
        record['id'] = existing['id'] if existing else stable_id(record)
        collision = next((r for r in store.contacts() if r['id'] == record['id']), None)
        if collision and profile and canonical_url(collision.get('profile_url', '')) not in ('',profile):
            raise PolicyError('stable_id_collision')
        if existing:
            incoming_source=record.get('source_url')
            record={**existing,**record}
            record['source_channel']=existing['source_channel']
            record['source_url']=existing.get('source_url') or incoming_source
            record['held']=bool(existing.get('held')) or _bool(raw.get('held',False))
            if incoming_source and incoming_source!=record['source_url']:
                record['secondary_source_urls']=list(dict.fromkeys([*existing.get('secondary_source_urls',[]),incoming_source]))
        if profile: record['profile_url'] = profile
        record['held'] = _bool(record.get('held', False))
        if record.get('record_kind')!='person' and re.search(r'\b(LLC|Inc\.?|Corporation|Corp\.?|Limited|Ltd\.?)$|\bReal Estate (Group|Investments)$',record['name'],re.I):
            record['held']=True;record['hold_reason']='brand_or_person_identity_review'
        record.setdefault('annual_revenue_usd',None)
        record.setdefault('revenue_status','not_provided' if record.get('annual_revenue_usd') is None else 'user_supplied_unverified')
        if record.get('is_deceased') is True or record.get('record_kind') == 'company': record['held'] = True
        record['qualified'] = eligible(record)
        supplied = record.pop('email', '')
        # Updates retain previously evidenced primary email; unclassified imports never promote it
        if existing and existing.get('email'):
            record['email'] = existing['email']
            record['email_provenance'] = existing.get('email_provenance')
            record['email_evidence_id'] = existing.get('email_evidence_id')
        store.put_contact(record)
        if supplied:
            supplied = normalize_email(supplied)
            source_url = record.get('evidence_url') or record.get('email_source_url')
            excerpt = record.get('evidence_excerpt') or ''
            status = record.get('email_verification') or record.get('email_provenance')
            if trust_classifications and status in PUBLIC and source_url and supplied.casefold() in excerpt.casefold():
                add_evidence(store, record['id'], supplied, 'public_person_linked', source_url, excerpt, person_matched=True, observed_at=record.get('checked_at'))
            else:
                store.put_candidate({'id':'candidate_'+hashlib.sha256((record['id']+'|'+supplied).encode()).hexdigest()[:24], 'lead_id':record['id'], 'email':supplied, 'kind':'user_supplied_unattributed', 'risk_flags':['role_or_company_inbox'] if ROLE_MAILBOX.match(supplied) else [], 'identity_confirmed':False})
        imported.append(record['id'])
    return {'imported':len(imported), 'unique_ids':len(set(imported)), 'reverified_sources':False}

def generate_candidates(store, lead_id, domain, evidence_url, max_patterns=3):
    from .policy import normalize_domain
    domain = normalize_domain(domain)
    validate_evidence_url(evidence_url)
    host=urlsplit(evidence_url).hostname
    if domain in FREE_DOMAINS or not (host == domain or host.endswith('.'+domain)):
        raise PolicyError('official_work_domain_evidence_required')
    lead = next((r for r in store.contacts() if r['id'] == str(lead_id)), None)
    if not lead or lead.get('held'):
        raise PolicyError('unknown_or_held_lead')
    parts = lead['name'].split()
    if len(parts) != 2 or not all(re.fullmatch(r'[A-Za-z]{2,}', p) for p in parts):
        raise PolicyError('ambiguous_name_requires_manual_mapping')
    first,last = (p.lower() for p in parts)
    results=[]
    for basis,local in [('first',first),('flast',first[0]+last),('first.last',first+'.'+last)][:max(0,min(3,max_patterns))]:
        email=local+'@'+domain
        row={'id':'candidate_'+hashlib.sha256((str(lead_id)+'|'+email).encode()).hexdigest()[:24], 'lead_id':str(lead_id),'email':email,'kind':'name_domain_guess','basis':basis,'domain_evidence_url':evidence_url,'company_pattern_proven':False,'identity_confirmed':False}
        store.put_candidate(row); results.append(row)
    return results

def research_queue(store, limit=40):
    return [r for r in store.contacts() if not r.get('held') and not store.evidence(r['id']) and not r.get('research_complete')][:max(0,min(40,limit))]

def csv_cell(value):
    text=json.dumps(value,ensure_ascii=False) if isinstance(value,(dict,list,tuple)) else str(value if value is not None else '')
    probe=unicodedata.normalize('NFKC', text).lstrip(' \t\r\n')
    return "'"+text if probe.startswith(('=','+','-','@')) or text.startswith(('\t','\r')) else text

def export_workspace(store, directory):
    out=Path(directory);out.mkdir(parents=True,exist_ok=True)
    contacts=store.contacts(); candidates=store.candidates(); options=[]; published=[]
    active=[r for r in contacts if not r.get('held')]
    for r in active:
        choices=[c for c in candidates if c.get('lead_id') == r['id'] and not c.get('risk_flags')]
        possible=[(r.get('email'),'public_person_linked')] if r.get('email') else []
        possible += [(c['email'],c.get('kind','candidate')) for c in choices]
        selected=None
        for email,origin in possible:
            a=store.assess(email)
            if a.get('assessment')=='provider_valid' and not a.get('risk_flags') and not r.get('affiliation_review'):
                selected={'lead_id':r['id'],'name':r['name'],'company':r.get('company',''),'email':email,'origin':origin,'assessment':'provider_valid','valid_provider_count':a.get('valid_provider_count',0),'verification_observed_at':max((v.get('created_at',0) for v in store.attempts() if v['operation']=='verify' and v['payload'].get('email')==email and (v.get('outcome') or {}).get('assessment')=='provider_valid'),default=0),'identity_confirmed':False,'delivery_proven':False};break
        if selected: options.append(selected)
        if r.get('email'): published.append({'lead_id':r['id'],'name':r['name'],'email':r['email'],'evidence_id':r.get('email_evidence_id',''),'affiliation_review':r.get('affiliation_review',False)})
    def write(name,rows):
        keys=list(dict.fromkeys(k for r in rows for k in r)) or ['lead_id']
        with (out/name).open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.writer(f);w.writerow(keys)
            for row in rows:w.writerow([csv_cell(row.get(k,'')) for k in keys])
    write('contacts.csv',active);write('published-emails.csv',published);write('candidates.csv',candidates);write('provider-accepted-options.csv',options)
    write('provider-results.csv',[{'attempt_id':a['id'],'provider':a['provider'],'operation':a['operation'],'state':a['state'],'observed_at':a.get('created_at'),'email':(a.get('outcome') or {}).get('email') or a['payload'].get('email',''),'assessment':(a.get('outcome') or {}).get('assessment',''),'risk_flags':(a.get('outcome') or {}).get('risk_flags',[]),'fields':(a.get('outcome') or {}).get('fields',{}),'error':(a.get('outcome') or {}).get('error','')} for a in store.attempts()])
    summary={'active_contacts':len(active),'held_contacts':len(contacts)-len(active),'qualified_linkedin':sum(r.get('source_channel')=='linkedin' and r.get('qualified') is True for r in active),'other_source_contacts':sum(r.get('source_channel')!='linkedin' for r in active),'contacts_with_published_email':len(published),'unique_published_emails':len({r['email'] for r in published}),'provider_accepted_contact_options':len(options),'provider_candidates_are_public_emails':False,'delivery_guaranteed':False}
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    return summary
