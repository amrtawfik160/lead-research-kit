"""Command-line interface. Network commands plan unless --execute is supplied."""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from .configuration import default_config, load_config, save_config
from .intake import add_evidence, export_workspace, generate_candidates, import_records, load_records, research_queue
from .models import BudgetError, BusyError, PolicyError, ProviderError, Response
from .providers import PROVIDERS, get_provider
from .store import Store
from .runner import Runner
from .workflow import route_run, verification_jobs


def emit(value, output=None):
    text=json.dumps(value,indent=2,ensure_ascii=False)+'\n'
    if output:
        path=Path(output);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf-8');path.chmod(0o600)
    else: print(text,end='')


def parser():
    p=argparse.ArgumentParser(prog='lead-research',description='Evidence-first lead research with explicit budgets and dry-first API requests')
    p.add_argument('--workspace',default='workspace',help='Private workspace directory, never commit it')
    s=p.add_subparsers(dest='command',required=True)
    s.add_parser('init',help='Create an empty private workspace; all providers disabled, all budgets zero')
    i=s.add_parser('import',help='Import user-supplied CSV, JSON or JSONL')
    i.add_argument('file');i.add_argument('--channel',choices=['linkedin','public_web','manual']);i.add_argument('--filters-accepted',action='store_true');i.add_argument('--filter-country');i.add_argument('--filter-industry');i.add_argument('--trust-classifications',action='store_true',help='Accept supported prior research labels without fetching/reverifying sources')
    e=s.add_parser('evidence',help='Record a source excerpt and its attribution')
    e.add_argument('lead_id');e.add_argument('--email',required=True);e.add_argument('--classification',choices=['public_person_linked','company_inbox','candidate'],required=True);e.add_argument('--source',required=True);e.add_argument('--excerpt',required=True);e.add_argument('--person-matched',action='store_true');e.add_argument('--affiliation-mismatch',action='store_true')
    h=s.add_parser('hold',help='Keep a record but exclude it from the active target')
    h.add_argument('lead_id');h.add_argument('--reason',required=True);h.add_argument('--source',required=True)
    q=s.add_parser('research-queue',help='Prepare at most 40 contacts for one research worker')
    q.add_argument('--limit',type=int,default=40);q.add_argument('--output')
    g=s.add_parser('generate',help='Make up to three explicitly unverified guesses from evidenced work domains')
    g.add_argument('lead_id');g.add_argument('--domain',required=True);g.add_argument('--source',required=True)
    v=s.add_parser('verify-queue',help='Prepare unique-address verification jobs, not network requests')
    v.add_argument('--provider',default='auto',choices=['auto',*PROVIDERS]);v.add_argument('--crosscheck',action='store_true');v.add_argument('--output')
    f=s.add_parser('find-queue',help='Prepare finder jobs from an explicit domain-evidence mapping')
    f.add_argument('file');f.add_argument('--provider',default='auto',choices=['auto',*PROVIDERS]);f.add_argument('--output')
    r=s.add_parser('run',help='Plan jobs; add --execute to spend confirmed allowance')
    r.add_argument('file');r.add_argument('--execute',action='store_true');r.add_argument('--limit',type=int,default=10);r.add_argument('--output')
    r=s.add_parser('resume',help='Poll one saved pending job with GET, never submit it again')
    r.add_argument('attempt_id');r.add_argument('--execute',action='store_true')
    b=s.add_parser('budget',help='Inspect, explicitly confirm, or read actual balances')
    bs=b.add_subparsers(dest='budget_action',required=True);bs.add_parser('list')
    bc=bs.add_parser('confirm');bc.add_argument('provider',choices=list(PROVIDERS));bc.add_argument('--pools',required=True,help='JSON mapping of pool to remaining decimal-string amount');bc.add_argument('--source',required=True);bc.add_argument('--free',action='store_true',required=True,help='Explicitly attest these are authorized free allowances, not purchased funds')
    by=bs.add_parser('sync');by.add_argument('provider',choices=['hunter','verifalia','clearout','zerobounce']);by.add_argument('--execute',action='store_true')
    z=s.add_parser('provider',help='Enable configuration or manage durable access pauses')
    zs=z.add_subparsers(dest='provider_action',required=True)
    for action in ('enable','disable'):
        a=zs.add_parser(action);a.add_argument('provider',choices=list(PROVIDERS))
    a=zs.add_parser('pause');a.add_argument('provider',choices=list(PROVIDERS));a.add_argument('--reason',required=True)
    a=zs.add_parser('review');a.add_argument('provider',choices=list(PROVIDERS));a.add_argument('--reason',required=True);a.add_argument('--evidence',required=True)
    c=s.add_parser('cancel-unsent',help='Cancel only a reservation that was never marked sent')
    c.add_argument('attempt_id');c.add_argument('--reason',required=True)
    x=s.add_parser('export',help='Export private contact/evidence/provider categories separately');x.add_argument('directory')
    s.add_parser('status');s.add_parser('audit')
    d=s.add_parser('demo',help='Run a synthetic offline demonstration in an empty workspace')
    d.add_argument('--output',required=True)
    return p


def _balance(store,config,provider_id,execute):
    adapter=get_provider(provider_id)
    if not config['providers'][provider_id]['enabled'] or store.provider_state(provider_id).get('paused'):
        raise PolicyError('provider_disabled_or_paused')
    if not execute:return {'dry_run':True,'provider':provider_id,'request':'balance read only; no secrets displayed'}
    if any(a['state'] in ('reserved','sent','pending') for a in store.attempts()):raise BusyError('active_attempt_prevents_balance_sync')
    if any(r.get('synthetic') for r in store.contacts()):raise PolicyError('synthetic_workspace_no_live_requests')
    if not store.verify_audit():raise PolicyError('audit_integrity_failed')
    from .transport import UrllibTransport
    response=UrllibTransport().send(adapter.balance_request(os.environ))
    try: result=adapter.parse_balance(response)
    except Exception:
        store.pause(provider_id,'balance_readback_failed');raise ProviderError('balance_readback_failed') from None
    for pool,amount in result['free_pools'].items():
        store.confirm_budget(provider_id,pool,str(amount),'authenticated_explicit_free_field',attested_free=True)
    return {'provider':provider_id,**result,'automatic_refill_from_calendar':False}


def _demo(store,output):
    if store.contacts() or store.attempts():raise PolicyError('demo_requires_empty_workspace')
    store.put_contact({'id':'demo-alex','name':'Alex Morgan','company':'Example Company','title':'Founder','source_channel':'manual','source_url':'https://example.org/team','qualified':True,'synthetic':True})
    add_evidence(store,'demo-alex','alex@example.org','public_person_linked','https://example.org/team','Alex Morgan, Founder, alex@example.org',person_matched=True)
    config=default_config();config['providers']['zerobounce']['enabled']=True
    store.confirm_budget('zerobounce','validation_credits','2','synthetic_demo_no_real_credit',attested_free=True)
    class Offline:
        def send(self,request):
            email=parse_qs(urlsplit(request.url).query)['email'][0]
            return Response(200,{'address':email,'status':'valid','sub_status':'','catchall_domain':False})
    report=route_run(store,config,[{'provider':'zerobounce','operation':'verify','payload':{'email':'alex@example.org','lead_id':'demo-alex'}}],execute=True,limit=1,env={'ZEROBOUNCE_API_KEY':'SYNTHETIC_DEMO_ONLY'},transport=Offline(),sleep=lambda seconds:None)
    return {'synthetic_demo':True,'live_requests':0,'real_addresses_checked':0,'export':export_workspace(store,output),'audit_ok':store.verify_audit(),'report':report}


def main(argv=None):
    args=parser().parse_args(argv)
    work=Path(args.workspace).expanduser().resolve()
    work.mkdir(mode=0o700,parents=True,exist_ok=True);work.chmod(0o700)
    cfg=work/'providers.json'
    if args.command in ('init','demo') and not cfg.exists():save_config(cfg,default_config())
    if not cfg.exists():raise PolicyError('run_init_first')
    config=load_config(cfg)
    store=Store(work/'research.sqlite3')
    try:
        if args.command=='init': result={'workspace':str(work),'providers_enabled':sum(p['enabled'] for p in config['providers'].values()),'budget_pools':len(store.budgets()),'network_requests':0,'existing_state_reset':False}
        elif args.command=='import':
            filters={'accepted':True,'country':args.filter_country,'industry':args.filter_industry} if args.filters_accepted else None
            if filters and not (args.filter_country and args.filter_industry):raise PolicyError('country_and_industry_filter_evidence_required')
            result=import_records(store,args.file,args.channel,filters,args.trust_classifications)
        elif args.command=='evidence':result=add_evidence(store,args.lead_id,args.email,args.classification,args.source,args.excerpt,args.person_matched,False if args.affiliation_mismatch else None)
        elif args.command=='hold':result=store.hold_contact(args.lead_id,args.reason,args.source)
        elif args.command=='research-queue':result=research_queue(store,args.limit)
        elif args.command=='generate':result=generate_candidates(store,args.lead_id,args.domain,args.source)
        elif args.command=='verify-queue':result=verification_jobs(store,args.provider,args.crosscheck)
        elif args.command=='find-queue':
            contacts={r['id']:r for r in store.contacts()};result=[]
            for item in load_records(args.file):
                lead=contacts.get(str(item['lead_id']))
                if not lead or lead.get('held'):raise PolicyError('unknown_or_held_lead')
                result.append({'provider':args.provider,'operation':'find','payload':{'lead_id':lead['id'],'name':lead['name'],'domain':item['domain'],'domain_evidence_url':item['source_url']}})
        elif args.command=='run':result=route_run(store,config,load_records(args.file),execute=args.execute,limit=args.limit)
        elif args.command=='resume':result=Runner(store,config).resume(args.attempt_id,execute=args.execute)
        elif args.command=='budget':
            if args.budget_action=='list':result=store.budgets()
            elif args.budget_action=='sync':result=_balance(store,config,args.provider,args.execute)
            else:
                pools=json.loads(args.pools)
                if not isinstance(pools,dict) or not pools:raise PolicyError('pool_mapping_required')
                allowed={p for charges in config['providers'][args.provider]['charges'].values() for p in charges}
                if set(pools)-allowed:raise PolicyError('unknown_budget_pool')
                from .policy import parse_units
                for amount in pools.values():parse_units(amount)
                for pool,amount in pools.items():store.confirm_budget(args.provider,pool,amount,args.source,attested_free=args.free)
                result={'provider':args.provider,'confirmation':'operator_attested_not_remotely_verified','budgets':store.budgets()}
        elif args.command=='provider':
            if args.provider_action in ('enable','disable'):
                config['providers'][args.provider]['enabled']=args.provider_action=='enable';save_config(cfg,config)
                result={'provider':args.provider,'enabled':config['providers'][args.provider]['enabled'],'runtime_pause':store.provider_state(args.provider).get('paused',False)}
            elif args.provider_action=='pause':result=store.pause(args.provider,args.reason)
            else:result=store.review_provider(args.provider,args.reason,args.evidence)
        elif args.command=='cancel-unsent':result=store.cancel_reserved(args.attempt_id,args.reason)
        elif args.command=='export':result=export_workspace(store,args.directory)
        elif args.command=='audit':
            result={'audit_ok':store.verify_audit()}
            if not result['audit_ok']:emit(result);return 2
        elif args.command=='demo':result=_demo(store,args.output)
        else:result={'contacts':len(store.contacts()),'held':sum(bool(r.get('held')) for r in store.contacts()),'budgets':store.budgets(),'providers':{p:store.provider_state(p) for p in config['providers']},'unresolved_attempts':[{'id':a['id'],'provider':a['provider'],'state':a['state']} for a in store.attempts() if a['state'] in ('reserved','sent','pending','uncertain')],'audit_ok':store.verify_audit()}
        emit(result,getattr(args,'output',None) if args.command!='demo' else None)
        if isinstance(result,dict) and result.get('stopped'):return 2
        return 0
    finally:store.close()


def entrypoint():
    try:return main()
    except (PolicyError,ProviderError,BudgetError,BusyError) as error:
        # These exceptions carry stable public codes, not raw provider messages
        print(json.dumps({'error':str(error)}),file=sys.stderr);return 2
    except (OSError,ValueError,KeyError,TypeError):
        print(json.dumps({'error':'invalid_input_or_local_io'}),file=sys.stderr);return 2
    except Exception:
        print(json.dumps({'error':'unexpected_local_error_no_details_logged'}),file=sys.stderr);return 2
