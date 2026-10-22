"""HeyReach CSV export (leads export --format heyreach and the daily leads-heyreach.csv)."""
import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from lead_research.cli import main
from lead_research.daily import write_daily
from lead_research.leads import HEYREACH_HEADERS, heyreach_row, write_heyreach


def record(name, email, linkedin=''):
    return {'full_name': name, 'title': 'Managing Partner', 'company': 'Harbor Development Group',
            'company_website': 'harbor.example', 'linkedin_url': linkedin, 'email': email, 'city': 'Irvine',
            'state': 'CA', 'email_source': 'website_published', 'source': 'website', 'tier': 'A', 'score': 90,
            'issues': [], 'email_risk': 'low', 'email_verification': ''}


class HeyReachTests(unittest.TestCase):
    def test_row_layout(self):
        row = heyreach_row(record('Joseph R. Held', 'jheld@harbor.example', 'https://www.linkedin.com/in/jheld/'))
        self.assertEqual(list(row)[:10], ['Profile URL', 'First Name', 'Last Name', 'Full Name', 'Headline', 'Email Address',
                                          'Job Title', 'Location', 'Company', 'Company URL'])
        self.assertEqual((row['First Name'], row['Last Name']), ('Joseph', 'Held'))
        self.assertEqual(row['Headline'], 'Managing Partner at Harbor Development Group')
        self.assertEqual(row['Location'], 'Irvine, CA')
        self.assertEqual(row['Company URL'], 'https://harbor.example')
        self.assertEqual(row['Email Address'], 'jheld@harbor.example')

    def test_rows_without_linkedin_go_to_second_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = write_heyreach([record('Ada Lane', 'ada@harbor.example', 'https://www.linkedin.com/in/ada/'),
                                  record('Bo Chen', 'bo@harbor.example')], Path(tmp) / 'h.csv')
            self.assertEqual((out['with_linkedin'], out['without_linkedin']), (1, 1))
            with open(out['files']['no_linkedin'], newline='') as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(rows[0]['Full Name'], 'Bo Chen')
            self.assertEqual(list(rows[0]), list(HEYREACH_HEADERS))

    def test_daily_and_cli_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'ws'
            def call(*args):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = main(['--workspace', str(work), '--json', *args])
                return code, json.loads(out.getvalue())
            call('init')
            files = write_daily([record('Ada Lane', 'ada@harbor.example', 'https://www.linkedin.com/in/ada/')],
                                work / 'daily' / '2026-10-22-cre')
            self.assertTrue(Path(files['heyreach']).exists())
            code, out = call('leads', 'export', '--format', 'heyreach', '--date', '2026-10-22-cre', '--output', str(Path(tmp) / 'x.csv'))
            self.assertEqual(code, 0)
            self.assertEqual(out['with_linkedin'], 1)


if __name__ == '__main__':
    unittest.main()
