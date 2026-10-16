"""Conservative input, exact accounting, and persistence boundaries."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl, urlsplit

from .models import PolicyError

PAYLOAD_KEYS = frozenset({'name', 'domain', 'domain_evidence_url', 'email', 'lead_id', 'provenance'})
SECRET_KEY = re.compile(r'(password|passwd|secret|token|authorization|cookie|api.?key|credential|header|request|environment)', re.I)


def parse_units(value, *, positive=False):
    """Decimal strings become integer millionths, never binary floats."""
    if isinstance(value, (float, bool)):
        raise PolicyError('amount_must_be_decimal_string')
    try:
        amount = Decimal(str(value))
        scaled = amount * 1_000_000
        if not amount.is_finite() or scaled != scaled.to_integral_value() or amount < 0:
            raise ValueError
        units = int(scaled)
    except (InvalidOperation, ValueError, OverflowError):
        raise PolicyError('invalid_amount') from None
    if positive and units <= 0:
        raise PolicyError('positive_amount_required')
    return units


def format_units(units):
    return format(Decimal(units) / Decimal(1_000_000), 'f')


def normalize_email(email):
    if not isinstance(email, str):
        raise PolicyError('invalid_email')
    email = email.strip().lower()
    if len(email) > 254 or not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,63}", email):
        raise PolicyError('invalid_email')
    local, domain = email.rsplit('@', 1)
    if local.startswith('.') or local.endswith('.') or '..' in local:
        raise PolicyError('invalid_email')
    return local + '@' + normalize_domain(domain)


def normalize_domain(domain):
    if not isinstance(domain, str):
        raise PolicyError('invalid_domain')
    domain = domain.strip().lower().rstrip('.')
    try:
        domain = domain.encode('idna').decode('ascii')
        ipaddress.ip_address(domain)
    except ValueError:
        pass
    else:
        raise PolicyError('public_domain_required')
    if len(domain) > 253 or '.' not in domain or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in domain.split('.')):
        raise PolicyError('invalid_domain')
    if domain.endswith(('.local', '.localhost', '.internal', '.test', '.invalid')):
        raise PolicyError('public_domain_required')
    return domain


def validate_evidence_url(url):
    if not isinstance(url, str) or any(ord(c) < 33 for c in url):
        raise PolicyError('invalid_evidence_url')
    try:
        parts = urlsplit(url)
        if parts.scheme != 'https' or parts.username is not None or parts.password is not None or parts.port not in (None, 443):
            raise ValueError
        normalize_domain(parts.hostname or '')
        if parts.fragment or any(SECRET_KEY.search(k) or k.lower() in {'key', 'auth', 'signature', 'sig', 'code', 'state', 'session', 'sessionid', 'sid', 'jwt'} for k, _ in parse_qsl(parts.query, keep_blank_values=True)):
            raise ValueError
    except (ValueError, PolicyError):
        raise PolicyError('unsafe_evidence_url') from None
    return url


def redact(value):
    """Drop credential-shaped keys and redact URLs/free text containing secrets."""
    if isinstance(value, dict):
        return {str(k): redact(v) for k, v in value.items() if not SECRET_KEY.search(str(k))}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        if re.search(r'(?i)(bearer\s+|(?:api[_-]?key|token|password|secret|authorization)\s*[:=])', value):
            return '[redacted]'
        if '://' in value:
            try:
                validate_evidence_url(value)
            except PolicyError:
                return '[redacted]'
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def sanitize_payload(payload):
    clean = {k: v for k, v in dict(payload).items() if k in PAYLOAD_KEYS}
    if 'email' in clean:
        clean['email'] = normalize_email(clean['email'])
    if 'domain' in clean:
        clean['domain'] = normalize_domain(clean['domain'])
    if 'domain_evidence_url' in clean:
        clean['domain_evidence_url'] = validate_evidence_url(clean['domain_evidence_url'])
    if 'lead_id' in clean:
        clean['lead_id'] = str(clean['lead_id'])
    return redact(clean)


def normalize_job(job):
    job = dict(job)
    operation = job.get('operation', 'verify')
    payload = sanitize_payload(job.get('payload', job))
    if operation in {'verify', 'signals'}:
        if not payload.get('email'):
            raise PolicyError('verification_requires_email')
    elif operation == 'find':
        if not isinstance(payload.get('name'), str) or not payload['name'].strip() or not payload.get('domain') or not payload.get('domain_evidence_url'):
            raise PolicyError('finding_requires_name_domain_evidence')
        payload['name'] = ' '.join(payload['name'].split())
        evidence_host = urlsplit(payload['domain_evidence_url']).hostname
        if evidence_host != payload['domain'] and not evidence_host.endswith('.' + payload['domain']):
            raise PolicyError('domain_evidence_mismatch')
    else:
        raise PolicyError('unsupported_operation')
    return {'provider': str(job.get('provider', '')), 'operation': operation,
            'payload': payload, 'crosscheck': job.get('crosscheck') is True}


def input_key(operation, payload):
    payload = sanitize_payload(payload)
    if operation in {'verify', 'signals'}:
        identity = {'email': payload.get('email')}
    else:
        identity = {'name': ' '.join(payload.get('name', '').casefold().split()), 'domain': payload.get('domain')}
    return hashlib.sha256(json.dumps({'operation': operation, **identity}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
