"""Fail if a dashboard write route has no CLI equivalent."""
import ast
import re
import unittest
from pathlib import Path

from lead_research.cli import parser
from lead_research.parity import API_WRITE_PARITY, write_paths


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / 'dashboard' / 'server.py'


def posted_routes(source):
    tree = ast.parse(source)
    compile(tree, str(SERVER), 'exec')
    found = set()
    for match in re.finditer(r"method == 'POST' and path == '(/api/[^']+)'", source):
        found.add(('POST', match.group(1)))
    if "path.startswith('/api/contacts/')" in source and "path.endswith('/hold')" in source:
        found.add(('POST', '/api/contacts/{id}/hold'))
    if "path.startswith('/api/providers/')" in source and "path.endswith('/enabled')" in source:
        found.add(('POST', '/api/providers/{id}/enabled'))
    return found


class ParityTests(unittest.TestCase):
    def test_every_post_route_is_mapped(self):
        found = posted_routes(SERVER.read_text(encoding='utf-8'))
        mapped = write_paths()
        missing = found - mapped
        extra = mapped - found
        self.assertFalse(missing, f'API write routes with no CLI map: {sorted(missing)}')
        self.assertFalse(extra, f'Parity map lists routes not in server.py: {sorted(extra)}')

    def test_mapped_cli_commands_exist(self):
        catalog = parser()
        for item in API_WRITE_PARITY:
            argv = item['cli']
            sub = catalog
            for token in argv:
                action = next(
                    (a for a in sub._actions if a.__class__.__name__ == '_SubParsersAction'),
                    None,
                )
                self.assertIsNotNone(action, f'no subparser while resolving {argv}')
                self.assertIn(token, action.choices, f'{argv} not in CLI ({token} missing)')
                sub = action.choices[token]


if __name__ == '__main__':
    unittest.main()
