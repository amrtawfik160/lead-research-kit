import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from lead_research.cli import main
from lead_research.models import PolicyError
from lead_research.store import Store
from lead_research.configuration import load_config

class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.work=self.root/'work'
    def tearDown(self):self.tmp.cleanup()
    def call(self,*args):
        s=io.StringIO()
        with contextlib.redirect_stdout(s),patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('live network forbidden')):
            code=main(['--workspace',str(self.work),*args])
        return code,json.loads(s.getvalue())
    def test_init_is_empty_disabled_private(self):
        code,r=self.call('init');self.assertEqual(code,0);self.assertEqual(r['providers_enabled'],0)
        self.assertEqual(self.work.stat().st_mode&0o777,0o700)
        self.assertEqual((self.work/'research.sqlite3').stat().st_mode&0o777,0o600)
    def test_demo_is_offline_and_marked_synthetic(self):
        code,r=self.call('demo','--output',str(self.root/'demo'))
        self.assertEqual(code,0);self.assertTrue(r['synthetic_demo']);self.assertEqual(r['live_requests'],0)
        self.assertEqual(r['export']['provider_accepted_contact_options'],1);self.assertTrue(r['audit_ok'])
    def test_demo_refuses_existing_contacts(self):
        self.call('demo','--output',str(self.root/'demo'))
        with self.assertRaises(PolicyError):self.call('demo','--output',str(self.root/'other'))
    def test_demo_cannot_turn_into_live_workspace(self):
        self.call('demo','--output',str(self.root/'demo'))
        self.call('provider','enable','zerobounce')
        p=self.root/'jobs.json';p.write_text(json.dumps([{'provider':'zerobounce','operation':'verify','payload':{'email':'alex@example.org'}}]))
        with patch.dict('os.environ',{'ZEROBOUNCE_API_KEY':'SYNTHETIC_TEST'}):
            with self.assertRaisesRegex(PolicyError,'synthetic_workspace'):
                self.call('run',str(p),'--execute')
    def test_second_init_does_not_reset_budgets_or_config(self):
        self.call('init');self.call('provider','enable','zerobounce')
        self.call('budget','confirm','zerobounce','--pools','{"validation_credits":"2"}','--source','synthetic test','--free')
        _,r=self.call('init');self.assertEqual(r['providers_enabled'],1);self.assertEqual(r['budget_pools'],1)
    def test_config_fee_tampering_rejected(self):
        self.call('init');p=self.work/'providers.json';c=json.loads(p.read_text());c['providers']['hunter']['charges']['find']['credits']='0.5';p.write_text(json.dumps(c))
        with self.assertRaises(PolicyError):load_config(p)
    def test_paused_provider_not_cleared_by_enable_or_budget(self):
        self.call('init');self.call('provider','pause','hunter','--reason','access denied')
        self.call('provider','enable','hunter')
        self.call('budget','confirm','hunter','--pools','{"credits":"1"}','--source','synthetic free attestation','--free')
        _,r=self.call('status');self.assertTrue(r['providers']['hunter']['paused'])
    def test_balance_is_dry_by_default(self):
        self.call('init');self.call('provider','enable','hunter')
        _,r=self.call('budget','sync','hunter');self.assertTrue(r['dry_run'])
    def test_audit_detects_tampering(self):
        self.call('init');self.call('provider','pause','hunter','--reason','test')
        s=Store(self.work/'research.sqlite3');s.db.execute("UPDATE audit SET event='{}'");s.db.commit();s.close()
        code,r=self.call('audit');self.assertEqual(code,2);self.assertFalse(r['audit_ok'])

if __name__=='__main__':unittest.main()
