"""Provider fees are explicit, while allowances always start at zero."""
import copy
import json
from pathlib import Path
from .models import PolicyError

DEFAULT = {'version':1,'providers':{
 'hunter':{'enabled':False,'charges':{'find':{'credits':'1','find_ops':'1'},'verify':{'credits':'0.5','verify_ops':'1'},
                                     'discover':{'discover_requests':'1'},'domain_search':{'credits':'1','find_ops':'1'}}},
 'verifalia':{'enabled':False,'charges':{'verify':{'free_credits':'1'}}},
 'clearout':{'enabled':False,'charges':{'verify':{'credits':'1'}}},
 'zerobounce':{'enabled':False,'charges':{'find':{'finder_queries':'1'},'verify':{'validation_credits':'1'}}},
 'hunter-insight':{'enabled':False,'charges':{'signals':{'signal_requests':'1'}}},
 'disify':{'enabled':False,'charges':{'signals':{'signal_requests':'1'}}},
 # search_batch charges are per item: 1 query unit x len(payload['queries']) (see 'per_item').
 'serper':{'enabled':False,'charges':{'search':{'queries':'1'},'search_batch':{'queries':'1'}},'per_item':['search_batch']},
 'brave':{'enabled':False,'charges':{'search':{'queries':'1'}}},
 'overpass':{'enabled':False,'charges':{'places':{'requests':'1'}}},
 # v3 additions. Dict order is auto-route order: existing providers keep priority so a
 # saved config never silently changes which account a job spends; among the new
 # verifiers Reoon (daily free credits) precedes MillionVerifier (paid credits).
 # Apollo search is 0 credits, so its pool is a local request counter.
 'apollo':{'enabled':False,'charges':{'people_search':{'search_requests':'1'},'people_enrich':{'credits':'1'}}},
 'prospeo':{'enabled':False,'charges':{'people_search':{'credits':'1'},'enrich':{'credits':'1'}}},
 'google_places':{'enabled':False,'charges':{'text_search':{'enterprise_requests':'1'}}},
 'exa':{'enabled':False,'charges':{'search':{'queries':'1'},'company_search':{'queries':'1'}}},
 'tomba':{'enabled':False,'charges':{'find':{'credits':'1'},'linkedin_email':{'credits':'1'}}},
 'icypeas':{'enabled':False,'charges':{'find':{'credits':'1'}}},
 'reoon':{'enabled':False,'charges':{'verify':{'credits':'1'}}},
 # v4 free-tier verifiers sit between Reoon and MillionVerifier: free monthly/daily
 # allowances are spent before MillionVerifier's paid credits. Existing providers keep
 # their relative order, and every new entry starts disabled with no budget.
 'abstract':{'enabled':False,'charges':{'verify':{'requests':'1'}}},
 'emailable':{'enabled':False,'charges':{'verify':{'credits':'1'}}},
 'kickbox':{'enabled':False,'charges':{'verify':{'credits':'1'}}},
 'bouncer':{'enabled':False,'charges':{'verify':{'credits':'1'}}},
 'myemailverifier':{'enabled':False,'charges':{'verify':{'credits':'1'}}},
 'millionverifier':{'enabled':False,'charges':{'verify':{'credits':'1'}}},
 # v4 free-tier lead sources. Firecrawl pools count operations: one 'search' op (limit 10)
 # bills 2 Firecrawl credits, one 'scrape' op bills 1. PDL bills only on a match.
 'tavily':{'enabled':False,'charges':{'search':{'credits':'1'}}},
 'firecrawl':{'enabled':False,'charges':{'search':{'search_ops':'1'},'scrape':{'scrape_credits':'1'}}},
 'pdl':{'enabled':False,'charges':{'person_enrich':{'credits':'1'}}},
 'serpapi':{'enabled':False,'charges':{'search':{'searches':'1'}}},
 # v5: Deepline managed-credit finder, last in auto-route order. 'credits' counts finds locally;
 # Deepline bills about 0.14-0.5 Deepline credits per find (0.34 per found email on the default
 # leadmagic_email_finder route; per executed stage on the waterfall Play).
 'deepline':{'enabled':False,'charges':{'find':{'credits':'1'}}}
}}

# Additions after the first v1 release. A saved v1 file may lack exactly these;
# they are filled from DEFAULT (providers disabled). Nothing else may be missing.
MIGRATION_ADDED_PROVIDERS = frozenset({'serper', 'brave', 'overpass',
                                       'apollo', 'prospeo', 'google_places', 'exa', 'tomba', 'icypeas',
                                       'reoon', 'millionverifier',
                                       'abstract', 'emailable', 'kickbox', 'bouncer', 'myemailverifier',
                                       'tavily', 'firecrawl', 'pdl', 'serpapi', 'deepline'})
MIGRATION_ADDED_CHARGES = {'hunter': frozenset({'discover', 'domain_search'}), 'serper': frozenset({'search_batch'}),
                           'exa': frozenset({'company_search'})}
# Operations whose fixed charge is multiplied by the number of payload items. The
# provider adapter must declare the same operation in its per_item_charge map.
MAX_CHARGE_ITEMS = 100

def default_config():
    return copy.deepcopy(DEFAULT)

def load_config(file):
    data=json.loads(Path(file).read_text(encoding='utf-8'))
    if not isinstance(data,dict) or data.get('version')!=1 or set(data)-{'version','providers'} or not isinstance(data.get('providers'),dict):
        raise PolicyError('unsupported_configuration')
    names=set(data['providers']); known=set(DEFAULT['providers'])
    if names-known or (known-names)-MIGRATION_ADDED_PROVIDERS:
        raise PolicyError('unsupported_configuration')
    migrated={'version':1,'providers':{}}
    for name,default in DEFAULT['providers'].items():
        if name not in data['providers']:
            migrated['providers'][name]=copy.deepcopy(default)
            continue
        entry=data['providers'][name]
        if not isinstance(entry,dict) or type(entry.get('enabled')) is not bool or set(entry)-{'enabled','charges','per_item'} or not isinstance(entry.get('charges'),dict):
            raise PolicyError('provider_charge_or_endpoint_override_rejected')
        # The per-item marker is fixed by DEFAULT: it may be absent (older files) or identical.
        if 'per_item' in entry and ('per_item' not in default or entry['per_item']!=default['per_item']):
            raise PolicyError('provider_charge_or_endpoint_override_rejected')
        charges=entry['charges']
        missing=set(default['charges'])-set(charges)
        if set(charges)-set(default['charges']) or missing-MIGRATION_ADDED_CHARGES.get(name,frozenset()) or any(charges[op]!=default['charges'][op] for op in charges):
            raise PolicyError('provider_charge_or_endpoint_override_rejected')
        migrated['providers'][name]={'enabled':entry['enabled'],'charges':copy.deepcopy(default['charges'])}
        if 'per_item' in default:
            migrated['providers'][name]['per_item']=list(default['per_item'])
    return migrated

def save_config(file,data):
    p=Path(file)
    p.write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
    p.chmod(0o600)
