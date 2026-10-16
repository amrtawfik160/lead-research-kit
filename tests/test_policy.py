import unittest
from lead_research.models import PolicyError
from lead_research.policy import input_key, normalize_email, normalize_job, parse_units, sanitize_payload, validate_evidence_url

class PolicyTests(unittest.TestCase):
    def test_exact(self):
        self.assertEqual(parse_units('0.5'), 500000)
        for value in (0.5, '0.0000001', '-1', 'NaN'):
            with self.assertRaises(PolicyError): parse_units(value)
    def test_urls(self):
        self.assertEqual(validate_evidence_url('https://example.org/team'), 'https://example.org/team')
        for url in ('http://example.org', 'https://x:y@example.org', 'https://127.0.0.1/', 'https://example.org/?token=x', 'https://example.org:8443/'):
            with self.assertRaises(PolicyError): validate_evidence_url(url)
    def test_identity_and_secrets(self):
        self.assertEqual(normalize_email(' A@EXAMPLE.ORG '), 'a@example.org')
        self.assertEqual(input_key('verify', {'email':'A@example.org','lead_id':1}), input_key('verify', {'email':'a@example.org','lead_id':'2'}))
        self.assertNotIn('api_key', sanitize_payload({'email':'a@example.org','api_key':'secret'}))
    def test_find_requires_evidence(self):
        with self.assertRaises(PolicyError): normalize_job({'operation':'find','name':'A Person','domain':'example.org'})
        self.assertEqual(normalize_job({'operation':'find','name':'A Person','domain':'example.org','domain_evidence_url':'https://example.org/team'})['payload']['name'], 'A Person')
