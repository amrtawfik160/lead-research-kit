import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from lead_research.configuration import default_config
from lead_research.intake import import_records, add_evidence, export_workspace, generate_candidates, research_queue, csv_cell
from lead_research.models import PolicyError, Response
from lead_research.policy import validate_evidence_url
from lead_research.store import Store
from lead_research.workflow import route_run, verification_jobs

class IntakeWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=Store(self.root/'work'/'db.sqlite3')
    def tearDown(self):
        self.store.close();self.tmp.cleanup()
    def records(self,rows):
        p=self.root/'input.json';p.write_text(json.dumps(rows));return p
    def lead(self,**extra):
        return {'id':'1','name':'Alex Morgan','title':'Founder','source_channel':'linkedin','profile_url':'https://www.linkedin.com/in/example-alex','filter_evidence':{'accepted':True,'country':'US','industry':'Real Estate'},**extra}
    def imported(self,**extra):
        return import_records(self.store,self.records([self.lead(**extra)]))
    def configured(self):
        c=default_config();c['providers']['zerobounce']['enabled']=True
        self.store.confirm_budget('zerobounce','validation_credits','2','synthetic test',attested_free=True)
        return c
    def test_import_requires_channel(self):
        with self.assertRaises(PolicyError):import_records(self.store,self.records([{'name':'Alex Morgan'}]))
    def test_numeric_and_string_ids_stay_one(self):
        self.imported(id=1);self.imported(id='1')
        self.assertEqual(len(self.store.contacts()),1)
    def test_profile_url_preserves_id_across_new_ordinal(self):
        self.imported();self.imported(id='999')
        self.assertEqual(self.store.contacts()[0]['id'],'1')
    def test_plain_supplied_email_is_not_published(self):
        self.imported(email='alex@example.org')
        self.assertFalse(self.store.contacts()[0].get('email'))
        self.assertEqual(self.store.candidates()[0]['kind'],'user_supplied_unattributed')
    def test_evidence_requires_excerpt_and_person_attestation(self):
        self.imported()
        with self.assertRaises(PolicyError):add_evidence(self.store,'1','alex@example.org','public_person_linked','https://example.org/team','Alex Morgan',True)
        with self.assertRaises(PolicyError):add_evidence(self.store,'1','alex@example.org','public_person_linked','https://example.org/team','alex@example.org')
    def test_role_mailbox_kept_separate(self):
        self.imported()
        with self.assertRaises(PolicyError):add_evidence(self.store,'1','info@example.org','public_person_linked','https://example.org/team','Alex info@example.org',True)
        add_evidence(self.store,'1','info@example.org','company_inbox','https://example.org/team','info@example.org')
        self.assertFalse(self.store.contacts()[0].get('email'))
    def test_existing_hold_and_source_survive_refresh(self):
        self.imported();self.store.hold_contact('1','deceased review','https://example.org/memorial')
        self.imported(source_channel='public_web',source_url='https://example.org/team',held=False)
        row=self.store.contacts()[0]
        self.assertTrue(row['held']);self.assertEqual(row['source_channel'],'linkedin')
    def test_primary_email_survives_partial_import(self):
        self.imported();add_evidence(self.store,'1','alex@example.org','public_person_linked','https://example.org/team','Alex Morgan alex@example.org',True)
        self.imported(company='Example Company')
        self.assertEqual(self.store.contacts()[0]['email'],'alex@example.org')
    def test_brand_and_deceased_are_held(self):
        self.imported(name='Example Realty LLC')
        self.assertTrue(self.store.contacts()[0]['held'])
    def test_linkedin_qualification_requires_accepted_filters(self):
        self.imported(filter_evidence={})
        self.assertFalse(self.store.contacts()[0]['qualified'])
    def test_research_queue_bounded_and_excludes_hold(self):
        for i in range(45):self.store.put_contact({'id':str(i),'name':'Example Person','held':i==0})
        self.assertEqual(len(research_queue(self.store,100)),40)
    def test_guesses_are_not_published_or_company_patterns(self):
        self.imported();rows=generate_candidates(self.store,'1','example.org','https://example.org/team')
        self.assertEqual(len(rows),3);self.assertTrue(all(not r['company_pattern_proven'] for r in rows));self.assertFalse(self.store.contacts()[0].get('email'))
    def test_free_mail_and_unrelated_domain_evidence_rejected(self):
        self.imported()
        for domain,url in [('gmail.com','https://gmail.com/team'),('example.org','https://example.net/team')]:
            with self.assertRaises(PolicyError):generate_candidates(self.store,'1',domain,url)
    def test_spreadsheet_formula_escaping(self):
        for value in ['=SUM(1,1)',' +cmd','@cmd','\t=1','-1',' ＝1']:
            self.assertTrue(csv_cell(value).startswith("'"))
    def test_oauth_codes_not_valid_source_urls(self):
        for key in ['code','state','access_token','session']:
            with self.assertRaises(PolicyError):validate_evidence_url('https://example.org/?'+key+'=SYNTHETIC')
    def test_auto_route_uses_funded_provider_and_dry_plan_does_not_debit(self):
        c=self.configured();before=self.store.budgets()
        result=route_run(self.store,c,[{'operation':'verify','payload':{'email':'alex@example.org'}}],env={'ZEROBOUNCE_API_KEY':'SYNTHETIC_TEST'})
        self.assertTrue(result['dry_run']);self.assertEqual(result['results'][0]['provider'],'zerobounce');self.assertEqual(self.store.budgets(),before);self.assertEqual(self.store.attempts(),[])
    def test_dry_plan_simulates_budget_over_whole_batch(self):
        c=self.configured()
        jobs=[{'operation':'verify','payload':{'email':f'person{i}@example.org'}} for i in range(3)]
        r=route_run(self.store,c,jobs,env={'ZEROBOUNCE_API_KEY':'SYNTHETIC_TEST'})
        self.assertEqual(r['planned_requests'],2);self.assertEqual(r['results'][-1]['status'],'skipped')
    def test_actual_error_report_stops_even_if_flag_is_missing(self):
        c=self.configured();jobs=[{'operation':'verify','payload':{'email':f'p{i}@example.org'}} for i in range(2)]
        with patch('lead_research.workflow.Runner.run',return_value={'results':[{'status':'error','outcome':{'error':'auth'}}],'stopped':False}) as send:
            r=route_run(self.store,c,jobs,execute=True,env={'ZEROBOUNCE_API_KEY':'SYNTHETIC_TEST'})
        self.assertTrue(r['stopped']);self.assertEqual(send.call_count,1)
    def test_finder_never_promotes_primary_email(self):
        self.imported();c=default_config();c['providers']['zerobounce']['enabled']=True
        self.store.confirm_budget('zerobounce','finder_queries','1','synthetic test',attested_free=True)
        class Fake:
            def send(self,r):return Response(200,{'email':'alex@example.org','email_confidence':'high'})
        route_run(self.store,c,[{'operation':'find','payload':{'lead_id':'1','name':'Alex Morgan','domain':'example.org','domain_evidence_url':'https://example.org/team'}}],execute=True,env={'ZEROBOUNCE_API_KEY':'SYNTHETIC_TEST'},transport=Fake(),sleep=lambda n:None)
        self.assertFalse(self.store.contacts()[0].get('email'));self.assertEqual(self.store.candidates()[0]['kind'],'provider_finder')
        self.assertEqual(len(self.store.candidates()),1)
        attempt=self.store.attempts()[0]
        self.store.capture_finder(attempt)
        self.assertEqual(len(self.store.candidates()),1)
    def test_request_and_response_representations_hide_sensitive_data(self):
        from lead_research.models import Request
        secret='SYNTHETIC_TEST_SECRET'
        request=Request('POST','https://api.hunter.io/?api_key='+secret,{'Authorization':secret},secret.encode())
        response=Response(200,{'echo':secret},{'set-cookie':secret})
        self.assertNotIn(secret,repr(request));self.assertNotIn(secret,repr(response))
    def test_held_leads_never_enter_verification_queue(self):
        self.imported(email='alex@example.org');self.store.hold_contact('1','review','https://example.org/team')
        self.assertEqual(verification_jobs(self.store),[])
    def test_export_separates_candidates_and_escapes_names(self):
        self.imported(name='=DANGEROUS',email='alex@example.org')
        s=export_workspace(self.store,self.root/'exports')
        self.assertEqual(s['contacts_with_published_email'],0);self.assertEqual(s['provider_accepted_contact_options'],0)
        with (self.root/'exports'/'contacts.csv').open(encoding='utf-8-sig') as f:row=next(csv.DictReader(f))
        self.assertEqual(row['name'],"'=DANGEROUS")

if __name__=='__main__':unittest.main()
