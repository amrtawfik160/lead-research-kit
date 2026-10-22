"""Machine-readable CLI catalog for agents (`schema --json` / `commands --json`)."""
from __future__ import annotations

from . import __version__

EXIT_CODES = {
    '0': 'OK. JSON written to stdout.',
    '2': 'Usage, policy, provider, budget, busy, records, or audit failure. JSON error on stderr with optional hint.',
}

OUTPUT_SHAPES = {
    'init': {'providers_enabled': 'int', 'budget_pools': 'int'},
    'status': {'contacts': 'int', 'held': 'int', 'budgets': 'list', 'providers': 'object', 'audit_ok': 'bool'},
    'audit': {'audit_ok': 'bool'},
    'provider list': {'providers': 'list'},
    'provider enable': {'provider': 'str', 'enabled': 'bool', 'key_present': 'bool'},
    'budget list': 'list of {provider, pool, remaining}',
    'daily': {'date': 'str', 'execute': 'bool', 'target': 'int', 'delivered': 'int', 'files': 'object'},
    'team join': {'team_name': 'str', 'members': 'list', 'leads_count': 'int'},
    'team list': {'members': 'list', 'areas': 'list', 'leads': 'list', 'by_finder': 'object'},
    'filter save': {'saved': 'object', 'filters': 'list', 'pinned': 'list'},
    'filter apply': {'filter': 'object', 'matched': 'int', 'leads': 'list'},
    'ops set': {'key': 'str', 'ops': {'stage': 'str', 'owner': 'str', 'notes': 'str'}},
    'ops list': {'ops': 'list', 'count': 'int', 'stages': 'list'},
    'leads list': {'count': 'int', 'leads': 'list', 'date': 'str'},
    'leads export': {'output': 'str', 'count': 'int', 'headers': 'list'},
    'workspace show': {'workspace': 'str', 'initialized': 'bool', 'today': 'str'},
    'schema': {'version': 'str', 'exit_codes': 'object', 'commands': 'list'},
}


def _flag(action):
    names = list(action.option_strings or [])
    return {
        'names': names or [action.dest],
        'dest': action.dest,
        'help': action.help or '',
        'required': bool(getattr(action, 'required', False)),
        'choices': list(action.choices) if action.choices else None,
        'default': None if action.default in (None, '==SUPPRESS==') or action.option_strings else action.default,
    }


def _walk(parser, trail):
    rows = []
    sub = None
    for action in parser._actions:
        if action.__class__.__name__ == '_SubParsersAction':
            sub = action
            break
    if sub is None:
        name = ' '.join(trail)
        rows.append({
            'name': name or parser.prog,
            'help': parser.description or getattr(parser, 'help', '') or '',
            'args': [_flag(action) for action in parser._actions if action.option_strings or action.dest not in ('help', 'command') and action.__class__.__name__ != '_SubParsersAction'],
            'output': OUTPUT_SHAPES.get(name) or OUTPUT_SHAPES.get(trail[-1] if trail else '', 'object'),
        })
        return rows
    for command, child in sub.choices.items():
        rows.extend(_walk(child, trail + [command]))
    return rows


def command_catalog(parser):
    global_args = [_flag(action) for action in parser._actions if action.option_strings]
    return {
        'program': 'lead-research',
        'version': __version__,
        'json_default': True,
        'non_interactive': True,
        'help': 'Every command accepts --help and --json. --execute is required for live spend.',
        'exit_codes': EXIT_CODES,
        'workspace_flag': '--workspace DIR (required for work; default: ./workspace)',
        'global_args': global_args,
        'commands': _walk(parser, []),
    }
