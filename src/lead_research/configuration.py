"""Provider fees are explicit, while allowances always start at zero."""
import copy
import json
from pathlib import Path
from .models import PolicyError

DEFAULT = {'version':1,'providers':{
 'hunter':{'enabled':False,'charges':{'find':{'credits':'1','find_ops':'1'},'verify':{'credits':'0.5','verify_ops':'1'}}},
 'verifalia':{'enabled':False,'charges':{'verify':{'free_credits':'1'}}},
 'clearout':{'enabled':False,'charges':{'verify':{'credits':'1'}}},
 'zerobounce':{'enabled':False,'charges':{'find':{'finder_queries':'1'},'verify':{'validation_credits':'1'}}},
 'hunter-insight':{'enabled':False,'charges':{'signals':{'signal_requests':'1'}}},
 'disify':{'enabled':False,'charges':{'signals':{'signal_requests':'1'}}}
}}

def default_config():
    return copy.deepcopy(DEFAULT)

def load_config(file):
    data=json.loads(Path(file).read_text(encoding='utf-8'))
    if data.get('version')!=1 or set(data.get('providers',{}))!=set(DEFAULT['providers']):
        raise PolicyError('unsupported_configuration')
    for name,entry in data['providers'].items():
        if type(entry.get('enabled')) is not bool or entry.get('charges')!=DEFAULT['providers'][name]['charges'] or set(entry)-{'enabled','charges'}:
            raise PolicyError('provider_charge_or_endpoint_override_rejected')
    return data

def save_config(file,data):
    p=Path(file)
    p.write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
    p.chmod(0o600)
