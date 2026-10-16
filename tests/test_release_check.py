import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

spec=importlib.util.spec_from_file_location('release_check',Path(__file__).resolve().parents[1]/'scripts'/'release_check.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class ReleaseTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def test_clean_source_and_empty_template_pass(self):
        (self.root/'a.py').write_text('print(1)')
        (self.root/'.env.example').write_text('HUNTER_API_KEY=\n')
        self.assertTrue(module.check(self.root)['passed'])
    def test_database_and_filled_env_fail(self):
        (self.root/'research.sqlite3').write_bytes(b'private')
        (self.root/'.env').write_text('PRIVATE=value')
        self.assertFalse(module.check(self.root)['passed'])
    def test_data_outside_examples_fails(self):
        (self.root/'contacts.csv').write_text('name,email\n')
        self.assertEqual(module.check(self.root)['findings'][0]['rule'],'data_outside_synthetic_examples')
    def test_unmarked_contact_example_fails(self):
        (self.root/'examples').mkdir();(self.root/'examples'/'contacts.json').write_text(json.dumps([{'name':'Example Person'}]))
        self.assertFalse(module.check(self.root)['passed'])
    def test_reserved_examples_pass(self):
        (self.root/'examples').mkdir();(self.root/'examples'/'contacts.json').write_text(json.dumps([{'name':'Example Person','email':'person@example.org','synthetic':True}]))
        self.assertTrue(module.check(self.root)['passed'])
    def test_nonreserved_email_detected_without_printing_it(self):
        address='private-person'+'@'+'business'+'.com'
        (self.root/'source.py').write_text('value='+repr(address))
        result=module.check(self.root)
        self.assertFalse(result['passed']);self.assertNotIn(address,json.dumps(result))
    def test_secret_literal_detected_without_echo(self):
        value='A'*40
        (self.root/'source.py').write_text("api_key = '"+value+"'\n")
        result=module.check(self.root)
        self.assertFalse(result['passed']);self.assertNotIn(value,json.dumps(result))
    def test_symlink_rejected(self):
        (self.root/'source.py').write_text('print(1)');(self.root/'linked').symlink_to('source.py')
        self.assertFalse(module.check(self.root)['passed'])

if __name__=='__main__':unittest.main()
