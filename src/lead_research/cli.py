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


ERROR_HINTS = {
    'run_init_first': 'Workspace is not initialized yet. Run `lead-research --workspace <dir> init` first.',
    'env_file_must_be_private_0600': 'Environment file must have private 0600 permissions (`chmod 600 <file>`).',
    'missing_auth': 'Required provider API key environment variable is not set.',
    'provider_disabled_or_paused': 'Provider is disabled or paused. Enable it (`provider enable <name>`) and check `status`.',
    'sec_user_agent_required': 'Set SEC_USER_AGENT="Your Name you@example.com" before fetching sec-form-d.',
    'synthetic_workspace_no_live_requests': 'Sample/demo workspaces contain synthetic records and cannot send live network requests.',
    'demo_requires_empty_workspace': 'The `demo` command requires a fresh empty workspace directory.',
    'team_join_required': 'Join first: `team join --name YOUR_NAME --area-kind industry|region|source|icp --area "your slice"`.',
    'team_bundle_not_found': 'Point at a team file or shared folder: `team import FILE` or `team import --shared /path/to/folder`.',
    'team_id_mismatch': 'That file belongs to a different team. Use the shared veinore-team.json your group already started.',
    'area_already_claimed': 'Someone on the team already owns that area. Pick a different industry, region, source, or ICP slice.',
    'unknown_area_kind': 'Area kind must be industry, region, source, or icp.',
    'filter_name_required': 'Give the filter a short name: `filter save austin-new --date 2026-10-20 --stage new`.',
    'unknown_filter': 'No saved filter with that name. Run `filter list --json`.',
    'ops_key_required': 'Pass --email or --key for the lead (work email, or name|company).',
    'ops_patch_required': 'Set at least one of --stage, --owner, or --notes.',
    'unknown_lead_stage': 'Stage must be new, contacted, replied, qualified, or disqualified.',
    'unknown_country': 'Country must be an ISO code or US/USA/United States.',
    'invalid_revenue_min': 'Revenue minimum looks like 5000000, 5M, or $5M.',
    'leads_output_required': 'Give --output FILE.csv for `leads export`.',
    'invalid_filter_name': 'Filter names are letters, numbers, spaces, _ . - and cannot include $ or punctuation. Example: US CRE decision makers 5M.',
    'filter_name_too_long': 'Keep filter names at 40 characters or fewer.',
    'unknown_filter_field': 'Unknown filter field. Listing: stage, owner, area, source, risk, date, verified, found_by. Targeting: country, revenue_min, industry, include_titles, exclude_titles, exclude_junior.',
    'too_many_titles': 'At most 40 include/exclude titles.',
    'title_too_long': 'Each title must be 80 characters or fewer.',
    'too_many_industry_keywords': 'At most 30 industry keywords.',
    'invalid_bool_flag': 'Use true/false (or yes/no) for boolean filter flags.',
    'ops_key_too_long': 'The lead key is too long (email, or name|company).',
    'notes_too_long': 'Keep lead notes under 4,000 characters.',
    'owner_too_long': 'Keep the owner name under 80 characters.',
}


def load_env_file(path):
    """Load KEY=VALUE lines into os.environ without overriding variables already set."""
    file=Path(path).expanduser()
    if file.stat().st_mode & 0o077:raise PolicyError('env_file_must_be_private_0600')
    for line in file.read_text(encoding='utf-8').splitlines():
        line=line.strip()
        if not line or line.startswith('#') or '=' not in line:continue
        key,value=line.split('=',1)
        key,value=key.strip(),value.strip()
        if len(value)>=2 and value[0]==value[-1] and value[0] in '\'"':value=value[1:-1]
        if key.isidentifier() and key.upper()==key and value:os.environ.setdefault(key,value)


def emit(value, output=None):
    text=json.dumps(value,indent=2,ensure_ascii=False)+'\n'
    print(text,end='')
    if output:
        path=Path(output);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf-8');path.chmod(0o600)


def _provider_rows(config, store, env=None):
    env = os.environ if env is None else env
    rows = []
    for name in config['providers']:
        entry = config['providers'][name]
        adapter = get_provider(name)
        required = tuple(getattr(adapter, 'auth_env', ()) or ())
        optional = tuple(getattr(adapter, 'optional_auth_env', ()) or ())
        if name == 'verifalia':
            key_present = bool(env.get('VERIFALIA_TOKEN') or (env.get('VERIFALIA_USERNAME') and env.get('VERIFALIA_PASSWORD')))
        else:
            key_present = True if not required else all(bool(env.get(k)) for k in required)
        state = store.provider_state(name)
        rows.append({
            'id': name,
            'enabled': bool(entry.get('enabled', False)),
            'operations': list(getattr(adapter, 'operations', ()) or ()),
            'required_env': list(required),
            'optional_env': list(optional),
            'key_present': key_present,
            'paused': bool(state.get('paused', False)),
            'pause_reason': state.get('pause_reason'),
            'charges': entry.get('charges', {}),
        })
    return rows


def parser():
    p=argparse.ArgumentParser(prog='lead-research',description='Evidence-first lead research with explicit budgets and dry-first API requests')
    p.add_argument('--workspace',default='workspace',help='Private workspace directory, never commit it')
    p.add_argument('--env-file',help='KEY=VALUE file with API keys (kept private, 0600); existing environment variables win')
    p.add_argument('--json',action='store_true',default=True,help='Emit machine-readable JSON output (default; accepted anywhere on argv)')
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
    z=s.add_parser('provider',help='Enable configuration, inspect env/key status, or manage durable access pauses')
    zs=z.add_subparsers(dest='provider_action',required=True)
    zs.add_parser('list',help='List all providers, required/optional env var names, key presence, and pause state')
    for action in ('enable','disable'):
        a=zs.add_parser(action);a.add_argument('provider',choices=list(PROVIDERS))
    a=zs.add_parser('pause');a.add_argument('provider',choices=list(PROVIDERS));a.add_argument('--reason',required=True)
    a=zs.add_parser('review');a.add_argument('provider',choices=list(PROVIDERS));a.add_argument('--reason',required=True);a.add_argument('--evidence',required=True)
    c=s.add_parser('cancel-unsent',help='Cancel only a reservation that was never marked sent')
    c.add_argument('attempt_id');c.add_argument('--reason',required=True)
    x=s.add_parser('export',help='Export private contact/evidence/provider categories separately');x.add_argument('directory')
    s.add_parser('status',help='Workspace contacts, budgets, provider pauses, and audit flag')
    s.add_parser('audit',help='Verify the hash-chained ledger; exit 2 if it is broken')
    s.add_parser('schema',help='List every command, flag, output shape, and exit code as JSON')
    s.add_parser('commands',help='Alias for schema --json')
    ws=s.add_parser('workspace',help='Show the workspace this CLI invocation is using')
    wss=ws.add_subparsers(dest='workspace_action',required=True)
    wss.add_parser('show',help='Path, init state, team, filters, and latest daily batch')
    op=s.add_parser('ops',help='Set or read lead stage, owner, and notes (same file the dashboard shows)')
    ops=op.add_subparsers(dest='ops_action',required=True)
    osg=ops.add_parser('set',help='Save stage, owner, and/or notes for one lead')
    osg.add_argument('--email',help='Work email (preferred key)')
    osg.add_argument('--key',help='ops key: email, or name|company')
    osg.add_argument('--stage',choices=['new','contacted','replied','qualified','disqualified'])
    osg.add_argument('--owner')
    osg.add_argument('--notes')
    ogg=ops.add_parser('get',help='Read one lead\'s outreach fields')
    ogg.add_argument('--email');ogg.add_argument('--key')
    ops.add_parser('list',help='All saved stage/owner/notes rows')
    ld=s.add_parser('leads',help='List or export delivered and team leads')
    lds=ld.add_subparsers(dest='leads_action',required=True)
    ll=lds.add_parser('list',help='JSON lead rows; optional saved filter or ICP flags')
    _lead_select_args(ll)
    le=lds.add_parser('export',help='Write a CSV (same columns as the dashboard Export CSV button)')
    le.add_argument('--output',required=True,help='Destination .csv')
    _lead_select_args(le)
    cl=s.add_parser('clean',help='Clean and score any lead CSV/JSON into the 6-column format (offline, no workspace needed)')
    cl.add_argument('input');cl.add_argument('--output',required=True,help='Output directory');_target_args(cl)
    cr=s.add_parser('crawl',help='Crawl company websites in parallel for published people, titles, emails and LinkedIn links')
    cr.add_argument('sources',nargs='+',help='CSV/JSON/TXT files with website, domain or work-email columns, or bare domains')
    cr.add_argument('--output',required=True,help='JSON file for crawl results');cr.add_argument('--execute',action='store_true',help='Actually fetch public websites (default only plans)')
    cr.add_argument('--workers',type=int,default=16);cr.add_argument('--max-pages',type=int,default=6);cr.add_argument('--delay',type=float,default=1.0,help='Minimum seconds between requests to the same site')
    cr.add_argument('--limit',type=int,default=0,help='Only crawl the first N domains');cr.add_argument('--no-cache',action='store_true')
    j=s.add_parser('jobs',help='Prepare lead-source jobs for `run` (nothing is sent)')
    js=j.add_subparsers(dest='jobs_action',required=True)
    js.add_parser('list',help='Show saved job attempts from the ledger')
    jx=js.add_parser('xray',help='Search-engine LinkedIn profile queries (Serper or Brave)')
    jx.add_argument('--provider',choices=['serper','brave'],default='serper');jx.add_argument('--titles',help='Comma-separated titles (default: decision-maker titles)')
    jx.add_argument('--locations',help='Semicolon-separated, e.g. "Austin, Texas;Miami, Florida"');jx.add_argument('--states',help='Comma-separated US state codes, or "all"')
    jx.add_argument('--industry',default='real estate',help='Comma-separated industry terms');jx.add_argument('--pages',type=int,default=1);jx.add_argument('--output')
    jh=js.add_parser('hunter-discover',help='Free Hunter Discover company searches, one per state x headcount')
    jh.add_argument('--states',required=True,help='Comma-separated US state codes, or "all"');jh.add_argument('--headcounts',default='1-10,11-50,51-200');jh.add_argument('--output')
    jd=js.add_parser('hunter-domains',help='Hunter Domain Search for decision makers at known company domains')
    jd.add_argument('sources',nargs='+');jd.add_argument('--limit',type=int,default=10);jd.add_argument('--all-people',action='store_true',help='Do not filter to decision makers');jd.add_argument('--output')
    jo=js.add_parser('osm',help='OpenStreetMap real-estate offices in US places (free, gives company websites)')
    jo.add_argument('--areas',required=True,help='Semicolon-separated "City:ST" or "County:ST" entries');jo.add_argument('--output')
    co=s.add_parser('collect',help='Gather prospects and companies from completed lead-source runs');co.add_argument('--output')
    bu=s.add_parser('build',help='Merge sources, crawl results and imports into a scored 6-column lead CSV')
    bu.add_argument('--output',required=True,help='Output directory');bu.add_argument('--import',dest='imports',action='append',default=[],help='Lead rows from any tool (repeatable)')
    bu.add_argument('--prospects',action='append',default=[],help='Output of `collect` (repeatable)');bu.add_argument('--crawl',action='append',default=[],help='Output of `crawl` (repeatable)')
    bu.add_argument('--from-workspace',action='store_true',help='Also use prospects/companies from this workspace and its verification results');_target_args(bu)
    dy=s.add_parser('daily',help='Produce today\'s fresh lead file: refill domains, crawl, score, skip anything delivered before')
    dy.add_argument('--target',type=int,default=500);dy.add_argument('--execute',action='store_true',help='Fetch, spend confirmed budgets and record delivery (default: preview only)')
    dy.add_argument('--output-root',help='Folder for dated outputs (default: <workspace>/daily)');dy.add_argument('--date',help='Override the batch date, YYYY-MM-DD')
    dy.add_argument('--sources',default='records,resolve,hunter-discover,google-places,osm,crawl,find-emails,verify',help='Comma list from: records, resolve, hunter-discover, google-places, osm, apollo, crawl, jina-read, render-js, hunter-domains, find-emails, verify, xray-linkedin, pdl')
    dy.add_argument('--record-sources',default='tx-trec,ny-dos,co-dora,sec-form-d',help='Public-record feeds to pull (empty to skip)')
    dy.add_argument('--search-provider',choices=['serper','brave'],default='serper',help='Used to find websites for public-record firms and LinkedIn URLs')
    dy.add_argument('--records',action='append',default=[],help='JSON with companies/prospects from `records` importers (repeatable)')
    dy.add_argument('--max-domains',type=int,help='Domains to crawl today (default: target / 0.3 x 1.2)');dy.add_argument('--workers',type=int,default=24)
    dy.add_argument('--allow-missing-email',action='store_true',help='Also deliver leads without a work email');dy.add_argument('--min-tier',choices=['A','B'],default='B')
    dy.add_argument('--cap',action='append',default=[],help='Per-source daily cap, e.g. hunter-domains=50 (repeatable)')
    dy.add_argument('--filter',dest='targeting_filter',help='Apply a saved filter (including ICP) when picking today\'s leads')
    _targeting_args(dy)
    da=s.add_parser('daily-audit',help='Spot-check a sample of a day\'s delivered leads against their evidence (also: `daily audit`)')
    da.add_argument('--date',help='YYYY-MM-DD (default: today)');da.add_argument('--sample',type=int,default=20);da.add_argument('--seed',type=int)
    da.add_argument('--output-root',help='Folder with dated outputs (default: <workspace>/daily)')
    rc=s.add_parser('records',help='Public license and SEC records: decision makers at real-estate firms (free)')
    rs=rc.add_subparsers(dest='records_action',required=True)
    rs.add_parser('list',help='Available sources and their reuse terms')
    rf=rs.add_parser('fetch',help='Download one source (network; read-only public data)');rf.add_argument('source');rf.add_argument('--since',help='YYYY-MM-DD');rf.add_argument('--max-rows',type=int,default=5000);rf.add_argument('--output',required=True)
    rl=rs.add_parser('load',help='Parse a file you downloaded yourself (fl-dbpr-file or generic-csv)');rl.add_argument('source');rl.add_argument('path');rl.add_argument('--output',required=True)
    hi=s.add_parser('history',help='Remember delivered leads so daily files stay fresh')
    hs=hi.add_subparsers(dest='history_action',required=True)
    hm=hs.add_parser('import',help='Mark leads in earlier files as already delivered');hm.add_argument('files',nargs='+');hm.add_argument('--batch',default='imported')
    hs.add_parser('stats')
    hb=hs.add_parser('suppress',help='Block people or whole companies forever (customers, do-not-contact, competitors)');hb.add_argument('files',nargs='+');hb.add_argument('--reason',required=True)
    hf=hs.add_parser('feedback',help='Import outreach results (Outcome column: replied, meeting, bounced, unsubscribed, ...); bounces/unsubscribes get blocked')
    hf.add_argument('files',nargs='+')
    hs.add_parser('sources',help='Bounce and reply rates by email source, from imported feedback')
    hx=hs.add_parser('import-results',help='Import an outreach tool export (Instantly, Smartlead, Lemlist, Apollo, HubSpot or generic email+status); bounces and unsubscribes get blocked, safe to re-import')
    hx.add_argument('files',nargs='+');hx.add_argument('--tool',choices=['auto','instantly','smartlead','lemlist','apollo','hubspot','generic'],default='auto')
    hp=hs.add_parser('patterns',help='Domains whose guessed-email pattern hard-bounced (no more guesses there)');hp.add_argument('--clear',metavar='DOMAIN',help='Trust this domain\'s pattern again')
    rf=hs.add_parser('refill',help='Recurring FREE allowances (e.g. Reoon 20/day) the daily run resets automatically')
    rfs=rf.add_subparsers(dest='refill_action',required=True)
    ra=rfs.add_parser('add');ra.add_argument('provider',choices=list(PROVIDERS));ra.add_argument('--pool',required=True);ra.add_argument('--amount',required=True);ra.add_argument('--period',choices=['daily','monthly'],required=True);ra.add_argument('--source',required=True,help='Where this free allowance is documented')
    rfs.add_parser('list')
    rr=rfs.add_parser('remove');rr.add_argument('provider');rr.add_argument('--pool',required=True)
    d=s.add_parser('demo',help='Run a synthetic offline demonstration in an empty workspace')
    d.add_argument('--output',required=True)
    tm=s.add_parser('team',help='Join a team, claim one area, and merge lead lists from a shared file (no server)')
    tms=tm.add_subparsers(dest='team_action',required=True)
    tj=tms.add_parser('join',help='Set your name and area; write yourself into a shared team file if --shared is set')
    tj.add_argument('--name',required=True,help='Your name, used as attribution on leads you find')
    tj.add_argument('--area-kind',choices=['industry','region','source','icp'],required=True,help='What kind of slice you own')
    tj.add_argument('--area',required=True,help='The slice itself, e.g. "commercial real estate" or "Texas"')
    tj.add_argument('--team-id',help='Join this existing team id (default: create or read from --shared)')
    tj.add_argument('--team-name',help='Human team label')
    tj.add_argument('--shared',help='Shared folder or veinore-team.json path (Drive, git repo, USB). No hosted server.')
    tj.add_argument('--member-id',help='Stable id if you move machines; default is name + this workspace')
    te=tms.add_parser('export',help='Write the merged team list (with who found each lead) to a file')
    te.add_argument('--output',help='Destination .json file (default: <workspace>/exports/veinore-team.json)')
    te.add_argument('--shared',help='Also the shared folder or file your teammates import')
    ti=tms.add_parser('import',help='Merge a teammate bundle into this workspace (dedupe + skip their leads)')
    ti.add_argument('file',nargs='?',help='veinore-team.json from a teammate')
    ti.add_argument('--shared',help='Shared folder or file')
    tg=tms.add_parser('merge',help='Same as import: merge and dedupe a team bundle')
    tg.add_argument('file',nargs='?');tg.add_argument('--shared')
    tms.add_parser('list',help='Show members, areas, and the conjoined lead list')
    fl=s.add_parser('filter',help='Save, pin, and apply named lead filters (CLI first; pins also show in the dashboard)')
    fls=fl.add_subparsers(dest='filter_action',required=True)
    fs=fls.add_parser('save',help='Create or update a named filter')
    fs.add_argument('name');fs.add_argument('--stage',choices=['new','contacted','replied','qualified','disqualified'])
    fs.add_argument('--owner');fs.add_argument('--area');fs.add_argument('--source')
    fs.add_argument('--risk',choices=['low','medium','high']);fs.add_argument('--date',help='YYYY-MM-DD or "team"')
    fs.add_argument('--verified',choices=['verified','unverified']);fs.add_argument('--found-by')
    _targeting_args(fs)
    fs.add_argument('--pin',action='store_true',help='Pin so it stays at the top of filter list and the dashboard')
    fls.add_parser('list',help='List saved filters; pinned first')
    fp=fls.add_parser('pin',help='Pin a saved filter');fp.add_argument('name')
    fu=fls.add_parser('unpin',help='Unpin a saved filter');fu.add_argument('name')
    fa=fls.add_parser('apply',help='Return matching leads as JSON');fa.add_argument('name')
    return p


def _target_args(p):
    p.add_argument('--min-tier',choices=['A','B','C'],default='B',help='Lowest tier written to leads-clean.csv')
    p.add_argument('--allow-personal-email',action='store_true',help='Keep gmail/ISP addresses in the Email column')
    p.add_argument('--keep-non-decision-makers',action='store_true',help='Do not reject agents, VPs, investors and other non-owner titles')


def _targeting_args(p):
    p.add_argument('--country',help='ISO country or US/USA/United States')
    p.add_argument('--revenue-min',help='Minimum known revenue/ARR, e.g. 5000000 or 5M')
    p.add_argument('--industry',help='Comma-separated industry keywords')
    p.add_argument('--include-titles',help='Comma-separated titles to keep (CEO, COO, Partner, ...)')
    p.add_argument('--exclude-titles',help='Comma-separated titles to drop')
    p.add_argument('--exclude-junior',action='store_true',help='Drop intern/assistant/associate/agent titles')


def _lead_select_args(p):
    p.add_argument('--date',help='YYYY-MM-DD or team')
    p.add_argument('--filter',dest='filter_name',help='Apply a saved filter by name')
    p.add_argument('--stage',choices=['new','contacted','replied','qualified','disqualified'])
    p.add_argument('--owner');p.add_argument('--area');p.add_argument('--source')
    p.add_argument('--risk',choices=['low','medium','high'])
    p.add_argument('--verified',choices=['verified','unverified']);p.add_argument('--found-by')
    _targeting_args(p)


def _targeting_from_args(args):
    query = {
        'country': getattr(args, 'country', None),
        'revenue_min': getattr(args, 'revenue_min', None),
        'industry': getattr(args, 'industry', None),
        'include_titles': getattr(args, 'include_titles', None),
        'exclude_titles': getattr(args, 'exclude_titles', None),
    }
    if getattr(args, 'exclude_junior', False):
        query['exclude_junior'] = True
    return {key: value for key, value in query.items() if value not in (None, '', False)}


def _listing_from_args(args):
    query = {
        'stage': getattr(args, 'stage', None),
        'owner': getattr(args, 'owner', None),
        'area': getattr(args, 'area', None),
        'source': getattr(args, 'source', None),
        'risk': getattr(args, 'risk', None),
        'date': getattr(args, 'date', None),
        'verified': getattr(args, 'verified', None),
        'found_by': getattr(args, 'found_by', None),
    }
    query.update(_targeting_from_args(args))
    return {key: value for key, value in query.items() if value not in (None, '')}


def _workspace_show(work):
    from .daily import today_iso
    from .filters import list_filters
    from .team import load_state
    path = Path(work)
    daily = path / 'daily'
    batches = []
    if daily.is_dir():
        for folder in sorted(daily.iterdir()):
            if folder.is_dir() and (folder / 'report.json').exists() and not folder.name.endswith('-preview'):
                batches.append(folder.name)
    state = load_state(path) if (path / 'team.json').exists() else {}
    filters = list_filters(path) if (path / 'saved-filters.json').exists() else {'filters': [], 'pinned': [], 'count': 0}
    return {
        'workspace': str(path),
        'initialized': (path / 'providers.json').exists(),
        'today': today_iso(),
        'latest_daily': batches[-1] if batches else None,
        'daily_batches': batches,
        'team_joined': bool(state.get('member')),
        'team_name': state.get('team_name') or '',
        'filters': filters['count'],
        'pinned_filters': [item['name'] for item in filters.get('pinned') or []],
    }


def _target(args):
    from .quality import Target
    return Target(min_tier=args.min_tier,allow_personal_email=args.allow_personal_email,require_decision_maker=not args.keep_non_decision_makers)


def _source_domains(sources):
    from .pipeline import domains_from, read_any
    rows=[]
    for item in sources:
        path=Path(item)
        rows.extend(read_any(path) if path.is_file() else [{'domain':item}])
    return domains_from(rows)


def _states(value):
    from .policy import US_STATES
    codes=list(US_STATES) if value.strip().lower()=='all' else [v.strip().upper() for v in value.split(',') if v.strip()]
    bad=[c for c in codes if c not in US_STATES]
    if bad:raise PolicyError('unknown_us_state')
    return codes


def _offline_command(args,work):
    """Commands that never touch provider accounts or the ledger."""
    if args.command=='clean':
        from .quality import clean_csv
        return clean_csv(args.input,args.output,_target(args))
    if args.command=='crawl':
        domains=_source_domains(args.sources)
        if args.limit>0:domains=domains[:args.limit]
        if not 1<=args.workers<=64 or not 1<=args.max_pages<=20 or not 0.2<=args.delay<=30:raise PolicyError('crawl_limits_out_of_range')
        plan={'domains':len(domains),'max_requests':len(domains)*(args.max_pages+2),'workers':args.workers,'per_site_delay_seconds':args.delay,'respects_robots_txt':True}
        if not args.execute:return {'dry_run':True,**plan,'sample_domains':domains[:10],'next':'add --execute to crawl'}
        from .extract import crawl_domains
        from .webfetch import SafeFetcher
        def progress(done,total,domain):
            if done==total or done%10==0:print(json.dumps({'crawled':done,'of':total}),file=sys.stderr)
        with SafeFetcher(cache_path=None if args.no_cache else work/'web-cache.sqlite3',per_host_delay=args.delay) as fetcher:
            sites=crawl_domains(domains,fetcher,max_pages=args.max_pages,workers=args.workers,progress=progress)
        out=Path(args.output);out.parent.mkdir(parents=True,exist_ok=True)
        out.write_text(json.dumps({'sites':sites},indent=2,ensure_ascii=False)+'\n',encoding='utf-8');out.chmod(0o600)
        people=sum(len(x['people']) for x in sites)
        emails=sum(1 for x in sites for p in x['people'] if p.get('email'))
        return {'dry_run':False,**plan,'output':str(out),'sites_ok':sum(x['status']=='ok' for x in sites),'sites_unreachable':sum(x['status']=='unreachable' for x in sites),'sites_blocked':sum(x['status']=='blocked' for x in sites),'people_found':people,'people_with_email':emails,'sites_with_email_pattern':sum(bool(x.get('pattern')) for x in sites)}
    if args.command=='schema' or args.command=='commands':
        from .schema import command_catalog
        return command_catalog(parser())
    if args.command=='workspace':
        return _workspace_show(work)
    if args.command=='ops':
        from .ops import get_ops, list_ops, set_ops
        if args.ops_action=='set':
            if not (args.email or args.key):
                raise PolicyError('ops_key_required')
            if args.stage is None and args.owner is None and args.notes is None:
                raise PolicyError('ops_patch_required')
            return set_ops(work,key=args.key,email=args.email,stage=args.stage,owner=args.owner,notes=args.notes)
        if args.ops_action=='get':
            return get_ops(work,key=args.key,email=args.email)
        return list_ops(work)
    if args.command=='leads':
        from .leads import export_leads, list_leads
        query=_listing_from_args(args)
        if args.leads_action=='export':
            return export_leads(work,args.output,date=args.date,filter_name=args.filter_name,query=query)
        return list_leads(work,date=args.date,filter_name=args.filter_name,query=query)
    if args.command=='jobs':
        from . import discovery
        if args.jobs_action=='list':
            return None
        if args.jobs_action=='xray':
            from .policy import US_STATES
            titles=[t.strip() for t in args.titles.split(',')] if args.titles else list(discovery.DEFAULT_DM_TITLES)
            locations=[l.strip() for l in (args.locations or '').split(';') if l.strip()]
            if args.states:locations+=[US_STATES[c] for c in _states(args.states)]
            if not locations:raise PolicyError('locations_or_states_required')
            queries=discovery.build_xray_queries(titles,locations,industry_terms=tuple(t.strip() for t in args.industry.split(',') if t.strip()))
            return discovery.xray_jobs(args.provider,queries,pages=args.pages)
        if args.jobs_action=='hunter-discover':
            return discovery.hunter_discover_jobs(_states(args.states),headcounts=tuple(h.strip() for h in args.headcounts.split(',') if h.strip()))
        if args.jobs_action=='hunter-domains':
            return discovery.hunter_domain_jobs(_source_domains(args.sources),limit=args.limit,decision_maker=not args.all_people)
        areas=[]
        for item in args.areas.split(';'):
            if item.strip():
                name,_,state=item.partition(':');areas.append((name.strip(),state.strip()) if state.strip() else name.strip())
        return discovery.osm_jobs(areas)
    return None


def _companies(file):
    """Company rows saved by `collect` (its JSON has 'prospects' and 'companies')."""
    if not str(file).endswith('.json'):return []
    data=json.loads(Path(file).read_text(encoding='utf-8'))
    return [r for r in data.get('companies',[]) if isinstance(r,dict)] if isinstance(data,dict) else []


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


def _daily_audit_alias(argv):
    """`daily audit ...` -> `daily-audit ...` and accept `--json` anywhere on argv."""
    raw=list(sys.argv[1:] if argv is None else argv)
    argv=[tok for tok in raw if tok!='--json']
    for i,token in enumerate(argv[:-1]):
        if token=='daily' and argv[i+1]=='audit':return argv[:i]+['daily-audit']+argv[i+2:]
    return argv


def main(argv=None):
    args=parser().parse_args(_daily_audit_alias(argv))
    if args.env_file:load_env_file(args.env_file)
    work=Path(args.workspace).expanduser().resolve()
    if args.command=='records':
        from .records import SOURCES, EXCLUDED_SOURCES, fetch_source, load_local_file
        if args.records_action=='list':
            emit({'sources':{k:{'description':v.get('description'),'terms':v.get('terms_note')} for k,v in SOURCES.items()},'excluded':EXCLUDED_SOURCES});return 0
        data=fetch_source(args.source,since=args.since,max_rows=args.max_rows) if args.records_action=='fetch' else load_local_file(args.source,args.path)
        out=Path(args.output);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n',encoding='utf-8');out.chmod(0o600)
        emit({'source':data['source'],'companies':len(data['companies']),'prospects':len(data['prospects']),'output':str(out),'terms':data.get('terms_note')});return 0
    if args.command in ('schema','commands','workspace','ops','leads'):
        emit(_offline_command(args,work));return 0
    if args.command=='clean':
        emit(_offline_command(args,work));return 0
    if args.command=='jobs' and args.jobs_action!='list':
        emit(_offline_command(args,work),getattr(args,'output',None));return 0
    work.mkdir(mode=0o700,parents=True,exist_ok=True);work.chmod(0o700)
    if args.command=='crawl':
        emit(_offline_command(args,work));return 0
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
            if args.provider_action=='list':
                result={'providers':_provider_rows(config,store)}
            elif args.provider_action in ('enable','disable'):
                config['providers'][args.provider]['enabled']=args.provider_action=='enable';save_config(cfg,config)
                row=next(r for r in _provider_rows(config,store) if r['id']==args.provider)
                result={'provider':args.provider,'enabled':row['enabled'],'runtime_pause':row['paused'],'required_env':row['required_env'],'optional_env':row['optional_env'],'key_present':row['key_present']}
            elif args.provider_action=='pause':result=store.pause(args.provider,args.reason)
            else:result=store.review_provider(args.provider,args.reason,args.evidence)
        elif args.command=='cancel-unsent':result=store.cancel_reserved(args.attempt_id,args.reason)
        elif args.command=='export':result=export_workspace(store,args.directory)
        elif args.command=='audit':
            result={'audit_ok':store.verify_audit()}
            if not result['audit_ok']:emit(result);return 2
        elif args.command=='demo':result=_demo(store,args.output)
        elif args.command=='daily':
            from .daily import run_daily, DEFAULT_SOURCES, OPTIONAL_SOURCES
            sources=tuple(x.strip() for x in args.sources.split(',') if x.strip())
            if set(sources)-set(DEFAULT_SOURCES)-set(OPTIONAL_SOURCES):raise PolicyError('unknown_daily_source')
            if not 1<=args.target<=5000:raise PolicyError('target_out_of_range')
            caps={}
            for item in args.cap:
                name,_,value=item.partition('=')
                if not value.isdigit():raise PolicyError('invalid_cap')
                caps[name.strip()]=int(value)
            records=[json.loads(Path(f).read_text(encoding='utf-8')) for f in args.records]
            names=tuple(x.strip() for x in args.record_sources.split(',') if x.strip())
            targeting=_targeting_from_args(args)
            if args.targeting_filter:
                from .filters import list_filters
                from .targeting import TARGET_FIELDS, normalize_targeting
                item=next((row for row in list_filters(work)['filters'] if row['name'].casefold()==args.targeting_filter.casefold()),None)
                if not item:
                    raise PolicyError('unknown_filter')
                saved={field: item['query'].get(field) for field in TARGET_FIELDS if field in item['query']}
                saved.update(targeting)
                targeting=normalize_targeting(saved)
            result=run_daily(work,store,config,target=args.target,execute=args.execute,day=args.date,sources=sources+('build',),output_root=args.output_root,
                             max_domains=args.max_domains,workers=args.workers,require_email=not args.allow_missing_email,min_tier=args.min_tier,caps=caps,record_sources=records,
                             record_source_names=names,search_provider=args.search_provider,targeting=targeting or None)
        elif args.command=='daily-audit':
            from .audit import run_audit
            from .daily import today_iso, render_report
            from .webfetch import SafeFetcher
            if not 1<=args.sample<=500:raise PolicyError('sample_out_of_range')
            day_dir=Path(args.output_root or work/'daily')/(args.date or today_iso())
            with SafeFetcher(cache_path=str(work/'web-cache.sqlite3'),cache_max_age=86400,per_host_delay=1.0) as fetcher:
                report=run_audit(day_dir,fetcher,store=store,sample=args.sample,seed=args.seed)
            daily_json=day_dir/'report.json'
            if daily_json.exists():
                (day_dir/'report.md').write_text(render_report(json.loads(daily_json.read_text(encoding='utf-8'))),encoding='utf-8');os.chmod(day_dir/'report.md',0o600)
            result={k:report[k] for k in ('date','rows_in_file','sampled','counts','score','top_reasons')}
            result['files']={'json':str(day_dir/'audit.json'),'markdown':str(day_dir/'audit.md')}
        elif args.command=='history':
            from .daily import History, import_history
            if args.history_action=='import':result=import_history(work,args.files,args.batch)
            else:
                from .quality import read_rows
                from .policy import parse_units
                h=History(work/'lead-history.sqlite3')
                try:
                    if args.history_action=='suppress':result={'blocked_keys':h.suppress([r for f in args.files for r in read_rows(f)],args.reason),**h.stats()}
                    elif args.history_action=='feedback':result={'outcomes':h.record_outcomes([r for f in args.files for r in read_rows(f)]),'by_source':h.source_quality()}
                    elif args.history_action=='sources':result=h.source_quality()
                    elif args.history_action=='import-results':
                        from .results import import_results
                        result={'files':len(args.files),'results':[import_results(h,read_rows(f),args.tool) for f in args.files],'by_source':h.source_quality()}
                    elif args.history_action=='patterns':
                        if args.clear:h.clear_pattern(args.clear)
                        result={'distrusted_patterns':h.distrusted_patterns()}
                    elif args.history_action=='refill':
                        if args.refill_action=='add':
                            allowed={p for charges in config['providers'][args.provider]['charges'].values() for p in charges}
                            if args.pool not in allowed:raise PolicyError('unknown_budget_pool')
                            parse_units(args.amount,positive=True)
                            h.add_refill(args.provider,args.pool,args.amount,args.period,args.source)
                        elif args.refill_action=='remove':h.remove_refill(args.provider,args.pool)
                        result={'refills':h.refills(),'note':'operator-attested free allowances; the daily run resets each pool to this amount once per period, never adds on top'}
                    else:result=h.stats()
                finally:h.close()
        elif args.command=='jobs' and args.jobs_action=='list':
            result={'attempts':[{'id':a['id'],'provider':a.get('provider'),'operation':a.get('operation'),'state':a.get('state')} for a in store.attempts()]}
        elif args.command=='collect':
            from .discovery import collect
            result=collect(store)
        elif args.command=='build':
            from .pipeline import build, read_any, write_build
            imports=[r for f in args.imports for r in read_any(f)]
            prospects=[r for f in args.prospects for r in read_any(f)]
            companies=[r for f in args.prospects for r in _companies(f)]
            sites=[r for f in args.crawl for r in read_any(f)]
            if args.from_workspace:
                from .discovery import collect
                gathered=collect(store);prospects+=gathered['prospects'];companies+=gathered['companies']
            if not (imports or prospects or sites):raise PolicyError('no_lead_sources_given')
            written=write_build(build(imports=imports,prospects=prospects,site_results=sites,companies=companies,store=store,target=_target(args)),args.output)
            result={'output':written['paths'],'summary':written['summary']}
        elif args.command=='team':
            from .team import export_team, import_team, join, list_team
            if args.team_action=='join':
                result=join(work,name=args.name,area_kind=args.area_kind,area=args.area,team_id=args.team_id,
                            team_name=args.team_name,shared=args.shared,member_id=args.member_id)
            elif args.team_action=='export':
                result=export_team(work,output=args.output,shared=args.shared)
            elif args.team_action in ('import','merge'):
                result=import_team(work,args.file,shared=args.shared)
            else:
                result=list_team(work)
        elif args.command=='filter':
            from .filters import apply_filter, list_filters, pin_filter, save_filter, unpin_filter
            if args.filter_action=='save':
                result=save_filter(work,args.name,{
                    'stage':args.stage,'owner':args.owner,'area':args.area,'source':args.source,
                    'risk':args.risk,'date':args.date,'verified':args.verified,'found_by':args.found_by,
                    **_targeting_from_args(args),
                },pin=args.pin)
            elif args.filter_action=='list':
                result=list_filters(work)
            elif args.filter_action=='pin':
                result=pin_filter(work,args.name)
            elif args.filter_action=='unpin':
                result=unpin_filter(work,args.name)
            else:
                result=apply_filter(work,args.name)
        else:result={'contacts':len(store.contacts()),'held':sum(bool(r.get('held')) for r in store.contacts()),'budgets':store.budgets(),'providers':{p:store.provider_state(p) for p in config['providers']},'unresolved_attempts':[{'id':a['id'],'provider':a['provider'],'state':a['state']} for a in store.attempts() if a['state'] in ('reserved','sent','pending','uncertain')],'audit_ok':store.verify_audit()}
        emit(result,getattr(args,'output',None) if args.command not in ('demo','build','daily','daily-audit','history','team','filter','leads','ops','schema','commands','workspace') else None)
        if isinstance(result,dict) and result.get('stopped'):return 2
        return 0
    finally:store.close()


def entrypoint():
    from .records import RecordsError
    try:return main()
    except (PolicyError,ProviderError,BudgetError,BusyError,RecordsError) as error:
        # These exceptions carry stable public codes, not raw provider messages
        code=str(error)
        payload={'error':code}
        if code in ERROR_HINTS:payload['hint']=ERROR_HINTS[code]
        details=getattr(error,'details',None)
        if isinstance(details,dict):payload['details']=details
        print(json.dumps(payload),file=sys.stderr);return 2
    except (OSError,ValueError,KeyError,TypeError):
        print(json.dumps({'error':'invalid_input_or_local_io'}),file=sys.stderr);return 2
    except Exception:
        print(json.dumps({'error':'unexpected_local_error_no_details_logged'}),file=sys.stderr);return 2
