"""Pick funded providers without hiding cross-checks or retrying failed requests."""
import hashlib
from .models import PolicyError
from .policy import parse_units, normalize_job
from .runner import Runner


def route_run(store, config, jobs, *, execute=False, limit=10, env=None, transport=None, **runner_options):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= 100:
        raise PolicyError('limit_must_be_0_to_100')
    engine=Runner(store,config,env=env,transport=transport,**runner_options)
    available={(b['provider'],b['pool']):b['remaining_units'] for b in store.budgets()}
    results=[]; calls=0; stopped=False
    for raw in jobs:
        requested=raw.get('provider','auto')
        ids=[requested] if requested!='auto' else list(config.get('providers',{}))
        quotes=engine.plan([{**raw,'provider':p} for p in ids])
        selected=None
        # Reuse observations before spending, including held/uncertain observations
        for q in quotes:
            if q.get('status')=='cached': selected=q;break
        if selected is None:
            for q in quotes:
                if q.get('status')!='ready':continue
                if not execute and any(available.get((q['provider'],p),0)<parse_units(v,positive=True) for p,v in q['charges'].items()):continue
                selected=q;break
        if selected is None:
            results.append({'status':'skipped','reason':'no_eligible_funded_provider','options':quotes});continue
        if selected['status']=='ready' and calls>=limit:
            results.append({**selected,'status':'skipped','reason':'execution_limit'});continue
        if not execute:
            if selected['status']=='ready':
                for p,v in selected['charges'].items():available[(selected['provider'],p)]-=parse_units(v,positive=True)
                calls+=1
            results.append(selected);continue
        before=len(store.attempts())
        report=engine.run([selected],execute=True,limit=1)
        calls+=len(store.attempts())-before
        for item in report['results']:
            if item.get('attempt_id'):
                store.capture_finder(store.get_attempt(item['attempt_id']))
            results.append(item)
        blocked_result=any(r.get('status') in ('error','uncertain','pending') or r.get('state') in ('pending','sent','reserved','uncertain') or (r.get('outcome') or {}).get('error') for r in report['results'])
        if report.get('stopped') or blocked_result or (selected['status']=='cached' and ((selected.get('outcome') or {}).get('error') or selected.get('state') in ('pending','sent','reserved','uncertain'))):
            stopped=True;break
    return {'dry_run':not execute,'new_attempts':calls if execute else 0,'planned_requests':calls if not execute else None,'stopped':stopped,'results':results}


def verification_jobs(store, provider='auto', crosscheck=False):
    jobs=[];seen=set()
    held={r['id'] for r in store.contacts() if r.get('held')}
    for row in store.contacts()+store.candidates():
        lead_id=row.get('lead_id') or row.get('id')
        email=row.get('email')
        if not email or lead_id in held or row.get('risk_flags'):continue
        key=email.lower()
        if key in seen:continue
        seen.add(key)
        jobs.append({'provider':provider,'operation':'verify','crosscheck':crosscheck,'payload':{'email':key,'lead_id':str(lead_id)}})
    return jobs
