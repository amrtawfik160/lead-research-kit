"""Seed a synthetic workspace using the kit's own Store, History and quality APIs."""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from lead_research.configuration import default_config, save_config
from lead_research.daily import History, render_report, write_daily
from lead_research.intake import add_evidence, generate_candidates, import_records
from lead_research.models import Response
from lead_research.quality import Target, assess
from lead_research.store import Store
from lead_research.workflow import route_run

SAMPLE_MARKER = '.sample-ready'
KIT_ROOT = Path(__file__).resolve().parents[2]


def seed_sample(root, force=False):
    """Create an offline synthetic workspace. Returns the workspace path."""
    root = Path(root)
    marker = root / SAMPLE_MARKER
    if marker.exists() and not force and (root / 'providers.json').exists():
        return root
    if root.exists() and force:
        _wipe(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    config = default_config()
    for name in ('hunter', 'serper', 'reoon', 'overpass', 'zerobounce', 'jina', 'hackernews', 'firecrawl'):
        config['providers'][name]['enabled'] = True
    save_config(root / 'providers.json', config)
    store = Store(root / 'research.sqlite3')
    history = History(root / 'lead-history.sqlite3')
    try:
        _seed_ledger(store, config)
        _seed_history(history, store)
        _seed_daily(root, history)
        _seed_ops(root)
    finally:
        history.close()
        store.close()
    marker.write_text('synthetic dashboard workspace; not live account state\n', encoding='utf-8')
    os.chmod(marker, 0o600)
    return root


def _wipe(root):
    for path in sorted(root.rglob('*'), reverse=True):
        if path.is_file() or path.is_symlink():
            path.unlink()
        elif path.is_dir():
            path.rmdir()


def _seed_ledger(store, config):
    examples = KIT_ROOT / 'examples' / 'contacts.json'
    import_records(store, examples)
    extra = [
        {
            'id': 'example-casey',
            'name': 'Casey Kim',
            'company': 'Pine Ridge Properties',
            'title': 'President',
            'profile_url': 'https://www.linkedin.com/in/example-synthetic-casey',
            'source_channel': 'linkedin',
            'source_url': 'https://example.org/captured-search',
            'filter_evidence': {'accepted': True, 'country': 'US', 'industry': 'Real Estate'},
            'synthetic': True,
        },
        {
            'id': 'example-jordan',
            'name': 'Jordan Lee',
            'company': 'Summit Holdings Brand',
            'title': 'LLC',
            'source_channel': 'public_web',
            'source_url': 'https://summit.example/about',
            'qualified': False,
            'record_kind': 'company',
            'synthetic': True,
        },
        {
            'id': 'example-riley',
            'name': 'Riley Stone',
            'company': 'Bay Realty',
            'title': 'Managing Broker',
            'profile_url': 'https://www.linkedin.com/in/example-synthetic-riley',
            'source_channel': 'public_web',
            'source_url': 'https://bay.example/team',
            'qualified': True,
            'synthetic': True,
        },
        {
            'id': 'example-sam',
            'name': 'Sam Park',
            'company': 'Harbor Capital',
            'title': 'Owner',
            'source_channel': 'manual',
            'source_url': 'https://harbor.example/team',
            'qualified': True,
            'research_complete': True,
            'synthetic': True,
        },
    ]
    for row in extra:
        store.put_contact(row)
    add_evidence(
        store, 'example-alex', 'alex@example.org', 'public_person_linked',
        'https://example.org/team', 'Alex Morgan, Founder, alex@example.org', person_matched=True,
    )
    add_evidence(
        store, 'example-riley', 'riley.stone@bay.example', 'public_person_linked',
        'https://bay.example/team', 'Riley Stone, Managing Broker, riley.stone@bay.example', person_matched=True,
    )
    store.hold_contact('example-jordan', 'brand_or_person_identity_review', 'https://summit.example/about')
    generate_candidates(store, 'example-alex', 'example.org', 'https://example.org/team')
    store.put_candidate({
        'id': 'candidate_casey_import',
        'lead_id': 'example-casey',
        'email': 'casey@pineridge.example',
        'kind': 'user_supplied_unattributed',
        'identity_confirmed': False,
    })
    store.confirm_budget('hunter', 'credits', '12', 'synthetic_dashboard_sample', attested_free=True)
    store.confirm_budget('hunter', 'find_ops', '8', 'synthetic_dashboard_sample', attested_free=True)
    store.confirm_budget('hunter', 'verify_ops', '10', 'synthetic_dashboard_sample', attested_free=True)
    store.confirm_budget('hunter', 'discover_requests', '40', 'synthetic_dashboard_sample', attested_free=True)
    store.confirm_budget('reoon', 'credits', '18', 'synthetic_dashboard_sample', attested_free=True)
    store.confirm_budget('serper', 'queries', '80', 'synthetic_dashboard_sample', attested_free=True)
    store.confirm_budget('zerobounce', 'validation_credits', '4', 'synthetic_dashboard_sample', attested_free=True)
    store.confirm_budget('jina', 'reads', '12', 'synthetic_dashboard_sample', attested_free=True)
    store.confirm_budget('hackernews', 'signal_requests', '5', 'synthetic_dashboard_sample', attested_free=True)
    store.confirm_budget('firecrawl', 'scrape_credits', '6', 'synthetic_dashboard_sample', attested_free=True)
    store.pause('apollo', 'sample_access_review_no_live_key')
    store.pause('tavily', 'sample_pause_probe')

    class Offline:
        def send(self, request):
            email = parse_qs(urlsplit(request.url).query)['email'][0]
            return Response(200, {'address': email, 'status': 'valid', 'sub_status': '', 'catchall_domain': False})

    route_run(
        store, config,
        [{'provider': 'zerobounce', 'operation': 'verify', 'payload': {'email': 'alex@example.org', 'lead_id': 'example-alex'}}],
        execute=True, limit=1, env={'ZEROBOUNCE_API_KEY': 'SYNTHETIC_DEMO_ONLY'},
        transport=Offline(), sleep=lambda seconds: None,
    )


def _seed_history(history, store):
    history.add_domains([
        {'domain': 'harborrealty.example', 'company': 'Harbor Realty Group'},
        {'domain': 'pineridge.example', 'company': 'Pine Ridge Properties'},
        {'domain': 'bay.example', 'company': 'Bay Realty'},
        {'domain': 'summit.example', 'company': 'Summit Holdings'},
        {'domain': 'example.org', 'company': 'Example Company'},
        {'domain': 'oakandpine.example', 'company': 'Oak and Pine Management'},
        {'domain': 'westfork.example', 'company': 'Westfork Development'},
        {'domain': 'lakeside.example', 'company': 'Lakeside Capital'},
        {'domain': 'cedar.example', 'company': 'Cedar Street Partners'},
        {'domain': 'ridgeview.example', 'company': 'Ridgeview Homes'},
    ], 'hunter-discover')
    history.add_domains([{'domain': 'austinbroker.example', 'company': 'Austin Brokerage'}], 'osm')
    history.save_sites([
        {
            'domain': 'harborrealty.example', 'status': 'ok', 'homepage_url': 'https://harborrealty.example/',
            'pattern': {'template': '{first}@{domain}', 'confidence': 0.8},
            'people': [
                {'name': 'Jane Sample', 'title': 'Founder & CEO', 'email': 'jane@harborrealty.example'},
                {'name': 'Pat Quinn', 'title': 'Managing Broker', 'email': 'pat@harborrealty.example'},
            ],
        },
        {
            'domain': 'bay.example', 'status': 'ok', 'homepage_url': 'https://bay.example/',
            'people': [{'name': 'Riley Stone', 'title': 'Managing Broker', 'email': 'riley.stone@bay.example'}],
        },
        {'domain': 'summit.example', 'status': 'unreachable', 'homepage_url': 'https://summit.example/', 'people': []},
    ])
    history.add_firms(
        [
            {'organization': 'Metro License Realty', 'city': 'Dallas', 'state': 'TX'},
            {'organization': 'Coastal Broker Group', 'city': 'Miami', 'state': 'FL'},
        ],
        [
            {'full_name': 'Morgan Ellis', 'title': 'Designated Broker', 'company': 'Metro License Realty'},
            {'full_name': 'Avery Cole', 'title': 'Qualifying Broker', 'company': 'Coastal Broker Group'},
        ],
    )
    history.add_prospects([
        {
            'full_name': 'Pat Quinn', 'title': 'Managing Broker', 'company': 'Harbor Realty Group',
            'company_website': 'harborrealty.example', 'email': 'pat@harborrealty.example',
            'linkedin_url': 'https://www.linkedin.com/in/example-synthetic-pat/',
        },
        {
            'full_name': 'Drew Hale', 'title': 'Owner', 'company': 'Westfork Development',
            'company_website': 'westfork.example',
        },
    ])
    older = [
        _lead('Casey Morgan', 'Designated Broker', 'Harbor Realty Group', 'harborrealty.example',
              'casey@harborrealty.example', 'Austin', 'TX', email_source='website_published'),
        _lead('Alex Kim', 'Partner', 'Bay Realty', 'bay.example', 'alex.kim@bay.example', 'Denver', 'CO',
              email_source='pattern_verified', email_verification='valid'),
    ]
    history.mark_delivered(older, '2026-10-19')
    history.suppress([{'email': 'do-not-contact@example.net', 'company_website': 'blocked.example'}], 'do_not_contact')
    history.record_outcomes([
        {'email': 'casey@harborrealty.example', 'full_name': 'Casey Morgan',
         'company_website': 'harborrealty.example', 'Outcome': 'replied'},
        {'email': 'alex.kim@bay.example', 'full_name': 'Alex Kim',
         'company_website': 'bay.example', 'Outcome': 'bounced'},
    ])
    history.distrust_pattern('bay.example', 'guessed_pattern_hard_bounce')
    history.add_refill('reoon', 'credits', '20', 'daily', 'Reoon free plan (operator-attested sample)')
    history.mark_market('hunter_city', ('Austin', 'TX'), '2026-10-20', found=4)
    history.mark_market('osm', ('Miami', 'FL'), '2026-10-19', found=2)


def _seed_daily(root, history):
    today = [
        _lead('Jane Sample', 'Founder & CEO', 'Harbor Realty Group', 'harborrealty.example',
              'jane@harborrealty.example', 'Austin', 'TX', email_source='website_published',
              linkedin='https://www.linkedin.com/in/jane-sample/', email_verification='valid'),
        _lead('Pat Quinn', 'Managing Broker', 'Harbor Realty Group', 'harborrealty.example',
              'pat@harborrealty.example', 'Austin', 'TX', email_source='website_published'),
        _lead('Morgan Ellis', 'Designated Broker', 'Metro License Realty', 'metrolicense.example',
              'morgan@metrolicense.example', 'Dallas', 'TX', email_source='public_record', source='tx-trec'),
        _lead('Avery Cole', 'Qualifying Broker', 'Coastal Broker Group', 'coastalbroker.example',
              'avery@coastalbroker.example', 'Miami', 'FL', email_source='website_published'),
        _lead('Drew Hale', 'Owner', 'Westfork Development', 'westfork.example',
              'drew@westfork.example', 'Denver', 'CO', email_source='hunter_finder', email_verification='unknown'),
        _lead('Quinn Walsh', 'Principal', 'Oak and Pine Management', 'oakandpine.example',
              'quinn@oakandpine.example', 'Portland', 'OR', email_source='website_published'),
        _lead('Reese Dalton', 'President', 'Lakeside Capital', 'lakeside.example',
              'reese@lakeside.example', 'Chicago', 'IL', email_source='pattern_verified', email_verification='valid'),
        _lead('Jamie Frost', 'CEO', 'Cedar Street Partners', 'cedar.example',
              'jamie@cedar.example', 'Boston', 'MA', email_source='website_published'),
        _lead('Taylor Brooks', 'Broker Owner', 'Ridgeview Homes', 'ridgeview.example',
              'taylor@ridgeview.example', 'Phoenix', 'AZ', email_source='website_published'),
        _lead('Cameron Shaw', 'Founder', 'Example Holdings', 'example.net',
              'cameron@example.net', 'Seattle', 'WA', email_source='website_published',
              linkedin='https://www.linkedin.com/in/example-synthetic-cameron/'),
        _lead('Hayden Cole', 'Managing Director', 'Austin Brokerage', 'austinbroker.example',
              'hayden@austinbroker.example', 'Austin', 'TX', email_source='osm'),
        _lead('Skyler Ames', 'Owner', 'Pine Ridge Properties', 'pineridge.example',
              'skyler@pineridge.example', 'Raleigh', 'NC', email_source='website_published'),
    ]
    yesterday = [
        _lead('Casey Morgan', 'Designated Broker', 'Harbor Realty Group', 'harborrealty.example',
              'casey@harborrealty.example', 'Austin', 'TX', email_source='website_published',
              linkedin='https://www.linkedin.com/in/example-synthetic-casey-morgan/'),
        _lead('Alex Kim', 'Partner', 'Bay Realty', 'bay.example', 'alex.kim@bay.example', 'Denver', 'CO',
              email_source='pattern_verified', email_verification='valid'),
        _lead('Jordan Lee', 'Principal', 'Summit Holdings', 'summit.example',
              'jordan@summit.example', 'Tampa', 'FL', email_source='website_published'),
        _lead('Riley Stone', 'President', 'Bay Realty', 'bay.example',
              'riley.stone@bay.example', 'Denver', 'CO', email_source='website_published'),
        _lead('Sam Park', 'Owner', 'Gone Group', 'gone.example', 'sam@gone.example', 'Atlanta', 'GA',
              email_source='website_published'),
        _lead('Blake Ivers', 'CEO', 'Ivers Realty', 'ivers.example', 'blake@ivers.example', 'Nashville', 'TN',
              email_source='hunter_finder'),
        _lead('Eden Marlow', 'Founder', 'Marlow Land', 'marlow.example', 'eden@marlow.example', 'Boise', 'ID',
              email_source='website_published'),
        _lead('Noah Greer', 'Owner', 'Greer Properties', 'greer.example', 'noah@greer.example', 'Columbus', 'OH',
              email_source='website_published'),
    ]
    _write_batch(root, '2026-10-20', today, target=12, execute=True, history=history, audited=True,
                 this_run=5, prior_delivered=7)
    _write_batch(root, '2026-10-19', yesterday, target=8, execute=True, history=history, audited=True)
    history.mark_delivered(today, '2026-10-20')


def _lead(name, title, company, website, email, city, state, *, email_source='website_published',
          email_verification='', source='', linkedin=''):
    raw = {
        'Full Name': name, 'Title': title, 'Company': company, 'Company Website': website,
        'LinkedIn URL': linkedin, 'Email': email, 'city': city, 'state': state,
        'email_source': email_source, 'email_verification': email_verification,
        'email_evidence_url': f'https://{website}/team', 'source': source or email_source,
    }
    record = assess(raw, Target(min_tier='B'))
    risk, reason = __import__('lead_research.quality', fromlist=['email_risk']).email_risk(record)
    record['email_risk'] = risk
    record['email_risk_reason'] = reason
    return record


def _write_batch(root, day, records, *, target, execute, history, audited=False,
                 this_run=None, prior_delivered=0):
    day_dir = Path(root) / 'daily' / day
    files = write_daily(records, day_dir)
    by_source, by_risk, by_type = {}, {}, {}
    for record in records:
        src = record.get('email_source') or 'unknown'
        by_source[src] = by_source.get(src, 0) + 1
        risk = record.get('email_risk') or ''
        by_risk[risk] = by_risk.get(risk, 0) + 1
        kind = record.get('company_type') or 'other'
        by_type[kind] = by_type.get(kind, 0) + 1
    shortfall = max(0, target - len(records))
    this_run = len(records) if this_run is None else this_run
    report = {
        'date': day, 'execute': execute, 'target': target, 'delivered': len(records),
        'this_run': this_run, 'prior_delivered': prior_delivered,
        'fresh_available': len(records) + 6, 'shortfall': shortfall,
        'delivered_by_source': by_source, 'delivered_by_risk': by_risk,
        'delivered_by_company_type': by_type,
        'files': files,
        'budgets_left': {
            'reoon.credits': '18', 'hunter.discover_requests': '40', 'serper.queries': '80',
            'jina.reads': '12', 'hackernews.signal_requests': '5',
        },
        'advice': history and (__import__('lead_research.daily', fromlist=['shortfall_advice']).shortfall_advice(shortfall, history) if shortfall else []),
        'steps': {
            'crawl': {'domains_planned': 18, 'sites_ok': 14, 'people_with_email': 22},
            'jina-read': {'sites': 4, 'reads': {'sent': 4}, 'team_pages': {'sent': 2}, 'people': 3},
            'verify': {'sent': 6, 'skipped_reasons': {}},
            'hunter-discover': {'sent': 2, 'domains_added': 6},
            'resolve': {'resolved': 5, 'by_method': {'guess': 3, 'serper': 2}},
            'email-safety': {'dropped': {'role_inbox': 1}},
        },
    }
    (day_dir / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    os.chmod(day_dir / 'report.json', 0o600)
    (day_dir / 'report.md').write_text(render_report(report), encoding='utf-8')
    os.chmod(day_dir / 'report.md', 0o600)
    if audited:
        sample_n = min(8, len(records))
        audit = {
            'date': day, 'sampled': sample_n, 'rows_in_file': len(records),
            'score': {'confirmed_pct': 75.0, 'partial_pct': 12.5, 'failed_pct': 12.5},
            'top_reasons': {'published email no longer on the evidence page': 1},
            'leads': [
                {'result': 'confirmed', 'full_name': records[0].get('full_name') or 'Jane Sample',
                 'company_website': records[0].get('company_website') or 'harborrealty.example',
                 'email_source': 'website_published', 'reasons': []},
                {'result': 'confirmed', 'full_name': records[1].get('full_name') or 'Pat Quinn',
                 'company_website': records[1].get('company_website') or 'harborrealty.example',
                 'email_source': 'website_published', 'reasons': []},
                {'result': 'partial', 'full_name': records[min(2, len(records)-1)].get('full_name') or 'Morgan Ellis',
                 'company_website': records[min(2, len(records)-1)].get('company_website') or 'metrolicense.example',
                 'email_source': 'public_record', 'reasons': ['published email no longer on the evidence page']},
            ],
        }
        (day_dir / 'audit.json').write_text(json.dumps(audit, indent=2) + '\n', encoding='utf-8')
        os.chmod(day_dir / 'audit.json', 0o600)
        from lead_research.audit import render_audit
        (day_dir / 'audit.md').write_text(render_audit(audit), encoding='utf-8')
        os.chmod(day_dir / 'audit.md', 0o600)
        (day_dir / 'report.md').write_text(render_report(report), encoding='utf-8')


def _seed_ops(root):
    path = Path(root) / 'veinore-ops.json'
    path.write_text(json.dumps({
        'jane@harborrealty.example': {
            'stage': 'contacted',
            'owner': 'Operator',
            'notes': 'Intro sent after the 2026-10-20 delivery.',
            'updated_at': 1761000000,
        },
        'pat@harborrealty.example': {
            'stage': 'qualified',
            'owner': 'Operator',
            'notes': 'Managing broker with a published website email.',
            'updated_at': 1761000000,
        },
        'drew@westfork.example': {
            'stage': 'new',
            'owner': '',
            'notes': 'Finder result — verify before outreach.',
            'updated_at': 1761000000,
        },
    }, indent=2) + '\n', encoding='utf-8')
    os.chmod(path, 0o600)
