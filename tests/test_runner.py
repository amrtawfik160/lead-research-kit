import tempfile
import unittest
from unittest.mock import patch
from lead_research.models import Outcome, Request, Response
from lead_research.runner import Runner
from lead_research.store import Store

class Adapter:
    id='one'; independence_group='one'; operations=('verify','signals'); auth_env=('KEY',); min_spacing={'verify':2,'poll':2}
    def request(self, operation, payload, env): return Request('POST','https://example.org/verify',{'Authorization':env['KEY']})
    def parse(self, operation, response): return response.data
    def poll_request(self, url, env): return Request('GET',url)
class Transport:
    def __init__(self, responses): self.responses=list(responses); self.requests=[]
    def send(self, request):
        self.requests.append(request)
        result=self.responses.pop(0)
        if isinstance(result,Exception): raise result
        return result
class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.s=Store(self.tmp.name+'/ledger.db')
        self.s.confirm_budget('one','credits','5','synthetic free')
        self.config={'providers':{'one':{'enabled':True,'charges':{'verify':{'credits':'0.5'}}}}}
        self.job={'provider':'one','email':'a@example.org'}
        self.adapter=Adapter(); self.patch=patch('lead_research.runner.get_provider',return_value=self.adapter); self.patch.start()
    def tearDown(self): self.patch.stop(); self.s.close(); self.tmp.cleanup()
    def runner(self, responses, **kwargs):
        self.t=Transport(responses)
        return Runner(self.s,self.config,env={'KEY':'synthetic'},transport=self.t,**kwargs)
    def test_dry_and_cache(self):
        r=self.runner([Response(200,Outcome('provider_valid'))])
        r.run([self.job]); self.assertEqual(len(self.t.requests),0); self.assertEqual(len(self.s.attempts()),0)
        r.run([self.job],execute=True); r.run([{**self.job,'lead_id':'2'}],execute=True)
        self.assertEqual(len(self.t.requests),1)
        self.assertEqual(self.s.budgets()[0]['remaining'],'4.5')
    def test_errors_stop_batch(self):
        for response in (Response(401),Response(200,Outcome('error',error='application_error'))):
            with self.subTest(response=response):
                if self.s.attempts(): break
                r=self.runner([response]); result=r.run([self.job,{**self.job,'email':'b@example.org'}],execute=True)
                self.assertTrue(result['stopped']); self.assertEqual(len(self.t.requests),1)
                self.assertTrue(self.s.provider_state('one')['paused'])
    def test_network_held_not_invalid(self):
        r=self.runner([OSError('private error text')]); result=r.run([self.job],execute=True)
        self.assertEqual(result['results'][0]['status'],'uncertain')
        self.assertEqual(self.s.budgets()[0]['remaining'],'4.5')
        self.assertNotEqual(self.s.assess('a@example.org')['assessment'],'provider_invalid')
        self.assertNotIn('private error text',str(self.s.attempts()))
    def test_pending_get_no_redebit(self):
        self.adapter.id='verifalia'; self.adapter.independence_group='verifalia'
        self.config['providers']['verifalia']=self.config['providers'].pop('one')
        self.s.confirm_budget('verifalia','credits','2','synthetic free')
        t=Transport([Response(202,Outcome('pending',job_url='https://example.org/jobs/1')),Response(200,Outcome('provider_valid'))])
        r=Runner(self.s,self.config,env={'VERIFALIA_TOKEN':'synthetic','KEY':'synthetic'},transport=t,sleep=lambda _:None)
        r.run([{**self.job,'provider':'verifalia'}],execute=True)
        a=self.s.attempts()[0]; before=self.s.budgets()
        r.resume(a['id']); self.assertEqual(len(t.requests),1)
        r.resume(a['id'],execute=True)
        self.assertEqual([q.method for q in t.requests],['POST','GET'])
        self.assertEqual(self.s.budgets(),before)
        self.assertEqual(self.s.get_attempt(a['id'])['poll_count'],1)
    def test_disabled_and_insufficient_are_no_send(self):
        r=self.runner([]); self.config['providers']['one']['enabled']=False
        self.assertEqual(r.run([self.job],execute=True)['results'][0]['reason'],'provider_disabled')
        self.assertEqual(len(self.t.requests),0)
    def test_batch_pacing_and_exact_debits(self):
        waits=[]
        r=self.runner([Response(200,Outcome('unknown')),Response(200,Outcome('provider_valid'))],clock=lambda:100,sleep=waits.append)
        r.run([self.job,{**self.job,'email':'b@example.org'}],execute=True)
        self.assertEqual(waits,[2])
        self.assertEqual(self.s.budgets()[0]['remaining'],'4')
    def test_explicit_independent_crosscheck_only(self):
        r=self.runner([Response(200,Outcome('unknown')),Response(200,Outcome('provider_valid'))])
        r.run([self.job],execute=True)
        other=Adapter(); other.id='two'; other.independence_group='two'
        self.config['providers']['two']=self.config['providers']['one']
        self.s.confirm_budget('two','credits','2','synthetic free')
        with patch('lead_research.runner.get_provider',return_value=other):
            second={**self.job,'provider':'two'}
            self.assertEqual(r.run([second],execute=True)['results'][0]['reason'],'cached_mailbox_requires_explicit_crosscheck')
            r.run([{**second,'crosscheck':True}],execute=True)
        self.assertEqual(len(self.t.requests),2)
