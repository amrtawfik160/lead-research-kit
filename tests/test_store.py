import os
import tempfile
import unittest
from lead_research.store import Store
from lead_research.models import Outcome, BusyError, BudgetError, PolicyError

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = self.tmp.name + '/work/ledger.db'
        self.s = Store(self.path)
    def tearDown(self):
        self.s.close(); self.tmp.cleanup()
    def fund(self, provider='one', amount='2'):
        self.s.confirm_budget(provider, 'credits', amount, 'synthetic free attestation')
    def reserve(self, key='a', provider='one', charges=None, group=None):
        return self.s.reserve(provider,'verify',key,{'email':'a@example.org','lead_id':1}, charges or {'credits':'0.5'},group or provider)
    def test_atomic_and_fraction(self):
        self.fund()
        with self.assertRaises(BudgetError): self.reserve(charges={'credits':'0.5','ops':'1'})
        self.assertEqual(self.s.budgets()[0]['remaining'], '2')
        a = self.reserve()
        self.assertEqual(self.s.budgets()[0]['remaining'], '1.5')
        self.s.cancel_reserved(a['id'],'never sent locally')
        self.assertEqual(self.s.budgets()[0]['remaining'], '2')
        self.assertIsNone(self.s.get_cached('one','verify','a'))
    def test_crash_reopen_and_lease(self):
        self.fund(); a = self.reserve(); self.s.mark_sent(a['id'])
        self.s.close(); self.s = Store(self.path)
        with self.assertRaises(PolicyError): self.s.cancel_reserved(a['id'],'not safe')
        other = Store(self.path)
        try:
            with self.assertRaises(BusyError): other.reserve('two','verify','b',{'email':'b@example.org'},{'credits':'1'},'two')
        finally: other.close()
        self.assertTrue(self.s.verify_audit())
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
    def test_no_pause_clear_and_privacy(self):
        self.s.pause('one','auth'); self.fund()
        self.assertTrue(self.s.provider_state('one')['paused'])
        self.s.put_contact({'id':1,'email':'A@example.org','token':'must not persist'})
        self.assertNotIn('token', self.s.contacts()[0])
        self.assertEqual(self.s.contacts()[0]['id'],'1')
    def test_votes_conflicts_and_signals(self):
        self.fund(); self.fund('two')
        a=self.reserve(); self.s.mark_sent(a['id']); self.s.finish(a['id'], Outcome('provider_valid'))
        b=self.reserve('b','two'); self.s.mark_sent(b['id']); self.s.finish(b['id'], Outcome('provider_invalid',risk_flags=('role',)))
        result=self.s.assess('a@example.org')
        self.assertEqual(result['assessment'],'conflict')
        self.assertIn('role',result['risk_flags'])
        self.fund('three')
        with self.assertRaises(PolicyError): self.reserve('c','three')
    def test_audit_tamper(self):
        self.fund(); self.s.db.execute("UPDATE audit SET event='{}' WHERE seq=1")
        self.assertFalse(self.s.verify_audit())
    def test_unsettled_confirmation_and_review_guard(self):
        self.fund(); a=self.reserve()
        with self.assertRaises(BudgetError): self.fund(amount='2')
        with self.assertRaises(BusyError): self.s.review_provider('one','checked','synthetic evidence')
        self.s.mark_sent(a['id'])
        with self.assertRaises(BusyError): self.s.review_provider('one','checked','synthetic evidence')
    def test_poll_claim_is_single_active(self):
        self.fund(); a=self.reserve(); self.s.mark_sent(a['id'])
        self.s.finish(a['id'],Outcome('pending',job_url='https://example.org/jobs/1'),state='pending')
        self.s.note_poll(a['id'])
        self.assertEqual(self.s.get_attempt(a['id'])['state'],'sent')
        with self.assertRaises(PolicyError): self.s.note_poll(a['id'])
