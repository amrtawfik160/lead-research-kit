"""Dashboard write-route to CLI command map. The parity test reads this."""
from __future__ import annotations

# Every POST handler in dashboard/server.py must appear here.
# cli is the argv after `lead-research --workspace DIR`.
API_WRITE_PARITY = (
    {
        'method': 'POST',
        'path': '/api/ops',
        'action': 'Lead stage, owner, and notes',
        'cli': ['ops', 'set'],
        'also': [['ops', 'get'], ['ops', 'list']],
    },
    {
        'method': 'POST',
        'path': '/api/contacts/{id}/hold',
        'action': 'Hold a contact out of the target',
        'cli': ['hold'],
    },
    {
        'method': 'POST',
        'path': '/api/providers/{id}/enabled',
        'action': 'Enable or disable a provider',
        'cli': ['provider', 'enable'],
        'also': [['provider', 'disable'], ['provider', 'pause'], ['provider', 'review']],
    },
    {
        'method': 'POST',
        'path': '/api/clean',
        'action': 'Clean and score a lead list',
        'cli': ['clean'],
    },
    {
        'method': 'POST',
        'path': '/api/daily/preview',
        'action': 'Dry daily run (Run locally preview)',
        'cli': ['daily'],
    },
    {
        'method': 'POST',
        'path': '/api/daily/run',
        'action': 'Executed daily run (Run locally)',
        'cli': ['daily'],
        'flags': ['--execute'],
    },
    {
        'method': 'POST',
        'path': '/api/settings',
        'action': 'Switch workspace / sample mode',
        'cli': ['workspace', 'show'],
        'notes': 'CLI uses --workspace on every command. No process-wide settings file.',
    },
    {
        'method': 'POST',
        'path': '/api/sample/reset',
        'action': 'Rebuild the synthetic sample workspace',
        'cli': ['demo'],
        'notes': 'demo writes a synthetic workspace. Dashboard reset reseeds sample-workspace.',
    },
)

# Reads the dashboard offers; CLI equivalents (not enforced as writes).
API_READ_PARITY = (
    ('GET', '/api/health', ['workspace', 'show']),
    ('GET', '/api/overview', ['workspace', 'show']),
    ('GET', '/api/status', ['status']),
    ('GET', '/api/contacts', ['research-queue']),
    ('GET', '/api/research', ['research-queue']),
    ('GET', '/api/daily', ['daily']),
    ('GET', '/api/leads', ['leads', 'list']),
    ('GET', '/api/companies', ['collect']),
    ('GET', '/api/providers', ['provider', 'list']),
    ('GET', '/api/jobs', ['jobs', 'list']),
    ('GET', '/api/history', ['history', 'stats']),
    ('GET', '/api/ops', ['ops', 'list']),
    ('GET', '/api/budget', ['budget', 'list']),
    ('GET', '/api/team', ['team', 'list']),
    ('GET', '/api/filters', ['filter', 'list']),
    ('GET', '/api/settings', ['workspace', 'show']),
)

# Client-only dashboard actions (no POST). Still have a CLI.
CLIENT_ACTIONS = (
    {
        'action': 'Export leads CSV',
        'cli': ['leads', 'export'],
        'notes': 'Dashboard downloads in the browser. CLI writes a file.',
    },
    {
        'action': 'Save / pin / apply filters (including ICP)',
        'cli': ['filter', 'save'],
        'also': [['filter', 'apply'], ['filter', 'pin']],
    },
    {
        'action': 'Join a team / claim an area',
        'cli': ['team', 'join'],
    },
    {
        'action': 'Merge the shared team list',
        'cli': ['team', 'export'],
        'also': [['team', 'import'], ['team', 'list']],
    },
)


def write_paths():
    return {(item['method'], item['path']) for item in API_WRITE_PARITY}
