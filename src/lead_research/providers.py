"""Explicit provider adapters: finding, verifying, and signals are not interchangeable.

Only request objects contain credentials. Parsers retain selected structured facts,
never raw API errors, arbitrary server text, URLs with credentials, or account data.
Balances report proven free pools separately from unclassified total balances.
"""
import base64
import dataclasses
import json
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, quote_plus, urlencode, urlsplit

from .discovery import canonical_linkedin, clean_text, linkedin_from_handle, parse_linkedin_result, safe_link
from .models import Outcome, PolicyError, ProviderError, Request
from .policy import (APOLLO_SENIORITIES, HUNTER_DEPARTMENTS, HUNTER_SENIORITIES, OSM_DEFAULT_TAGS, OSM_TAG_ALLOWLIST,
                     US_STATES, apollo_criteria, domain_search_options, enrich_identity, linkedin_profile_url,
                     normalize_discover_filters, normalize_domain, normalize_email, overpass_area, prospeo_criteria,
                     search_query, strict_payload, us_state_code, validate_evidence_url)
from .transport import MAX_RESPONSE_CEILING, validate_url


def _error(code):
    return Outcome(assessment='error', error=code)


def http_failure(response):
    """Map service failures to errors, never an invalid-recipient assessment."""
    status = response.status
    code = {401: 'auth', 403: 'auth', 402: 'quota', 429: 'rate_limit'}.get(status)
    if code:
        return _error(code)
    if status >= 500:
        return _error('provider_error')
    if status < 200 or status >= 300:
        return _error('http_error')
    if not response.json_valid or not isinstance(response.data, dict):
        return _error('invalid_response')
    if response.data.get('error') or response.data.get('errors'):
        return _error('provider_application_error')
    return None


def _secret(env, key):
    value = env.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProviderError('missing_auth')
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise PolicyError('invalid_auth')
    return value


def _text(payload, key):
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise PolicyError('invalid_payload')
    return value.strip()


def _email(value):
    """Accept a conservative mailbox address, not URL-like provider text."""
    if not isinstance(value, str) or len(value) > 254 or value.count('@') != 1:
        return None
    local, domain = value.rsplit('@', 1)
    if not local or len(local) > 64 or local.startswith('.') or local.endswith('.') or '..' in local:
        return None
    if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+", local):
        return None
    label = r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?'
    if not re.fullmatch(label + r'(?:\.' + label + r')+', domain):
        return None
    return value


def _int(value):
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r'[0-9]+', value):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _decimal(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        d = Decimal(str(value))
        if d.is_finite() and d >= 0:
            return format(d, 'f')
    except InvalidOperation:
        pass
    return None


def _risks(data):
    flags = []
    for key in ('disposable', 'role', 'role_account', 'toxic', 'spamtrap', 'abuse', 'catchall_domain', 'catch_all', 'accept_all', 'block', 'gibberish', 'role_based'):
        if data.get(key) is True or data.get(key) in ('true', 'yes'):
            flags.append(key)
    return flags


def _informational_fields(data):
    # Public/free-mail hosts are context, not evidence of mailbox delivery risk.
    return {key: data[key] for key in ('free', 'webmail')
            if type(data.get(key)) is bool or data.get(key) in ('yes', 'no', 'true', 'false')}


def _balance_base(response):
    failure = http_failure(response)
    if failure:
        raise ProviderError(failure.error)
    return response.data


class Provider:
    """Adapters build requests without sending them; parse is side-effect free.

    Supported operations are declared in operations. Balance parsing returns
    {'free_pools': {...}, 'metadata': {...}} or raises a public-safe ProviderError.
    No adapter implements bulk verification or implicitly retries.
    """
    id = ''
    independence_group = ''
    operations = ()
    auth_env = ()
    min_spacing = {}
    origin = ''

    def _operation(self, operation):
        if operation not in self.operations:
            raise PolicyError('unsupported_operation')

    def _request(self, method, path, headers=None, body=None, timeout=30, raw_body=None, max_response_bytes=None):
        url = self.origin + path
        validate_url(url, {self.origin})
        data = raw_body if raw_body is not None else (
            json.dumps(body, separators=(',', ':')).encode() if body is not None else None)
        return Request(method, url, headers or {}, data, timeout, max_response_bytes)

    def request(self, operation, payload, env):
        """Build one official-origin Request; unsupported operations fail closed."""
        raise NotImplementedError

    def parse(self, operation, response):
        """Convert a bounded Response to an Outcome without server text or secrets."""
        raise NotImplementedError

    def balance_request(self, env):
        """Build a non-mutating account balance request, where supported."""
        raise PolicyError('unsupported_operation')

    def parse_balance(self, response):
        """Return known free pools and separate totals; never infer a refill."""
        raise PolicyError('unsupported_operation')


class Hunter(Provider):
    id = 'hunter'
    independence_group = 'hunter'
    operations = ('find', 'verify', 'discover', 'domain_search')
    auth_env = ('HUNTER_API_KEY',)
    # Discover: 5 req/s and 50/min -> 1.2s. Domain Search: 15 req/s, 500/min -> 0.2s.
    min_spacing = {'find': 10.0, 'verify': 10.0, 'balance': 10.0, 'discover': 1.2, 'domain_search': 0.2}
    origin = 'https://api.hunter.io'

    def _headers(self, env):
        return {'X-API-KEY': _secret(env, 'HUNTER_API_KEY')}

    def request(self, operation, payload, env):
        """Finder produces candidates; only verifier supplies mailbox assessment."""
        self._operation(operation)
        if operation == 'discover':
            # Free call; the body is the filters object itself (no offset on free plans).
            filters = normalize_discover_filters(payload.get('filters'))
            return self._request('POST', '/v2/discover', {**self._headers(env), 'Content-Type': 'application/json'}, filters)
        if operation == 'domain_search':
            domain = normalize_domain(_text(payload, 'domain'))
            options = domain_search_options(payload)
            query = {'domain': domain[4:] if domain.startswith('www.') else domain, 'type': 'personal', 'limit': options['limit']}
            if options['decision_maker']:
                query['decision_maker'] = 'true'
            for key in ('required_field', 'seniority', 'department'):
                if options[key]:
                    query[key] = ','.join(options[key])
            return self._request('GET', '/v2/domain-search?' + urlencode(query), self._headers(env))
        query = ({'domain': _text(payload, 'domain'), 'full_name': _text(payload, 'full_name' if 'full_name' in payload else 'name')}
                 if operation == 'find' else {'email': _text(payload, 'email')})
        route = 'email-finder' if operation == 'find' else 'email-verifier'
        return self._request('GET', '/v2/' + route + '?' + urlencode(query), self._headers(env))

    def parse(self, operation, response):
        self._operation(operation)
        failure = http_failure(response)
        if failure:
            return failure
        if operation == 'discover':
            return _parse_hunter_discover(response.data)
        if operation == 'domain_search':
            return _parse_hunter_domain_search(response.data)
        data = response.data.get('data')
        if not isinstance(data, dict):
            return _error('invalid_response')
        flags = tuple(_risks(data))
        if operation == 'find':
            email = _email(data.get('email'))
            report = data.get('verification')
            fields = {'verification_is_fresh': False, **_informational_fields(data)}
            if isinstance(report, dict) and report.get('status') in ('valid', 'invalid', 'accept_all', 'unknown'):
                fields['finder_verification_status'] = report['status']
            if email:
                return Outcome('candidate', email, flags, fields)
            if 'email' in data and data['email'] in (None, ''):
                return Outcome('not_found', fields=fields)
            return _error('invalid_response')
        status = data.get('status')
        mapping = {'valid': 'provider_valid', 'invalid': 'provider_invalid', 'accept_all': 'catch_all', 'unknown': 'unknown'}
        if not isinstance(status, str) or status not in mapping:
            return _error('invalid_response')
        if status == 'accept_all':
            flags += ('catch_all',)
        fields = {'status': status, **_informational_fields(data)}
        for key in ('regexp', 'gibberish', 'disposable', 'webmail', 'mx_records', 'smtp_server', 'smtp_check', 'accept_all', 'block'):
            if type(data.get(key)) is bool:
                fields[key] = data[key]
        email = _email(data.get('email'))
        if not email:
            return _error('invalid_response')
        return Outcome(mapping[status], email, tuple(dict.fromkeys(flags)), fields)

    def scope_outcome(self, operation, outcome, payload):
        """Keep only Domain Search prospects whose mailbox is at the queried domain."""
        if operation != 'domain_search' or outcome.assessment != 'results':
            return outcome
        domain = payload.get('domain') or ''
        prospects = [p for p in outcome.fields.get('prospects', []) if _at_domain(p.get('email'), domain)]
        fields = {**outcome.fields, 'prospects': prospects}
        return dataclasses.replace(outcome, assessment='results' if prospects else 'not_found', fields=fields)

    def balance_request(self, env):
        return self._request('GET', '/v2/account', self._headers(env))

    def parse_balance(self, response):
        j = _balance_base(response).get('data')
        if not isinstance(j, dict):
            raise ProviderError('invalid_response')
        # A Free subscription can still contain paid add-on credits. Account
        # counters describe availability, not the funding source of those units.
        plan = j.get('plan_name')
        metadata = {
            'plan': 'Free' if plan == 'Free' else 'non_free' if isinstance(plan, str) else 'unknown',
            'manual_free_attestation_required': True,
        }
        requests = j.get('requests')
        if isinstance(requests, dict):
            credit_entry = requests.get('credits')
            credits = _decimal(credit_entry.get('remaining')) if isinstance(credit_entry, dict) else None
            if credits is not None:
                metadata['reported_credits'] = credits
            for key, counter in (('searches', 'find_ops'), ('verifications', 'verify_ops')):
                entry = requests.get(key)
                value = _int(entry.get('remaining')) if isinstance(entry, dict) else None
                if value is not None:
                    metadata[counter] = value
        return {'free_pools': {}, 'metadata': metadata}


class Verifalia(Provider):
    id = 'verifalia'
    independence_group = 'verifalia'
    operations = ('verify',)
    auth_env = ('VERIFALIA_TOKEN', 'VERIFALIA_USERNAME', 'VERIFALIA_PASSWORD')
    min_spacing = {'verify': 10.0, 'poll': 10.0, 'balance': 10.0}
    origin = 'https://api-1.verifalia.com'

    def _headers(self, env):
        if env.get('VERIFALIA_TOKEN'):
            auth = 'Bearer ' + _secret(env, 'VERIFALIA_TOKEN')
        else:
            pair = _secret(env, 'VERIFALIA_USERNAME') + ':' + _secret(env, 'VERIFALIA_PASSWORD')
            auth = 'Basic ' + base64.b64encode(pair.encode()).decode('ascii')
        return {'Authorization': auth, 'Content-Type': 'application/json'}

    def request(self, operation, payload, env):
        self._operation(operation)
        return self._request('POST', '/v2.7/email-validations', self._headers(env),
                             {'entries': [{'inputData': _text(payload, 'email')}], 'quality': 'Standard'})

    def _job(self, url):
        validate_url(url, {self.origin}, evidence=True)
        if not re.fullmatch(re.escape(self.origin) + r'/v2\.7/email-validations/[A-Za-z0-9_-]{1,128}', url):
            raise PolicyError('unsafe_job_url')
        return url

    def poll_request(self, job_url, env):
        """GET a previously returned same-origin job URL; never resubmit the POST."""
        return Request('GET', self._job(job_url), self._headers(env))

    def parse(self, operation, response):
        self._operation(operation)
        if response.status == 202:
            location = next((v for k, v in response.headers.items() if k.lower() == 'location'), None)
            try:
                return Outcome('pending', job_url=self._job(location))
            except (PolicyError, TypeError):
                return _error('unsafe_job_url')
        failure = http_failure(response)
        if failure:
            return failure
        entries = response.data.get('entries')
        rows = entries.get('data') if isinstance(entries, dict) else None
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            return _error('invalid_response')
        row = rows[0]
        classification = row.get('classification')
        mapping = {'Deliverable': 'provider_valid', 'Undeliverable': 'provider_invalid', 'Risky': 'unknown', 'Unknown': 'unknown'}
        if not isinstance(classification, str) or classification not in mapping:
            return _error('invalid_response')
        status = row.get('status')
        known = {'Success', 'MailboxDoesNotExist', 'MailboxTemporarilyUnavailable', 'CatchAllConnectionFailure',
                 'ServerIsCatchAll', 'DomainDoesNotExist', 'DomainHasNullMx', 'InvalidEmailAddress',
                 'SyntaxFailure', 'Timeout', 'SmtpConnectionFailure', 'DnsQueryTimeout', 'MailboxIsFull'}
        # A novel status can never promote a recipient to valid.
        assessment = mapping[classification]
        if classification == 'Deliverable' and status != 'Success':
            assessment = 'unknown'
        flags = _risks(row)
        if classification == 'Risky':
            flags.append('risky')
        if status in ('ServerIsCatchAll', 'CatchAllConnectionFailure'):
            flags.append('catch_all')
        fields = {'classification': classification, **_informational_fields(row)}
        if isinstance(status, str) and status in known:
            fields['status'] = status
        email = _email(row.get('inputData')) or _email(row.get('emailAddress'))
        if not email:
            return _error('invalid_response')
        return Outcome(assessment, email, tuple(flags), fields)

    def balance_request(self, env):
        return self._request('GET', '/v2.7/credits/balance', self._headers(env))

    def parse_balance(self, response):
        j = _balance_base(response)
        value = _decimal(j.get('freeCredits'))
        if value is None:
            raise ProviderError('invalid_response')
        return {'free_pools': {'free_credits': value}, 'metadata': {}}


class Clearout(Provider):
    id = 'clearout'
    independence_group = 'clearout'
    operations = ('verify',)
    auth_env = ('CLEAROUT_API_KEY',)
    min_spacing = {'verify': 10.0, 'balance': 10.0}
    origin = 'https://api.clearout.io'

    def _headers(self, env):
        return {'Authorization': 'Bearer ' + _secret(env, 'CLEAROUT_API_KEY'), 'Content-Type': 'application/json'}

    def request(self, operation, payload, env):
        self._operation(operation)
        return self._request('POST', '/v2/email_verify/instant', self._headers(env), {'email': _text(payload, 'email')})

    def parse(self, operation, response):
        self._operation(operation)
        failure = http_failure(response)
        if failure:
            return failure
        j = response.data
        if j.get('status') != 'success':
            return _error('provider_application_error')
        data = j.get('data')
        mapping = {'valid': 'provider_valid', 'invalid': 'provider_invalid', 'catch_all': 'catch_all', 'unknown': 'unknown'}
        if not isinstance(data, dict) or not isinstance(data.get('status'), str) or data.get('status') not in mapping:
            return _error('invalid_response')
        flags = _risks(data)
        fields = {'status': data['status'], **_informational_fields(data)}
        safe = data.get('safe_to_send')
        if safe in ('yes', 'no', 'risky'):
            fields['safe_to_send'] = safe
            if safe != 'yes':
                flags.append('safe_to_send_' + safe)
        reason = data.get('reason')
        if reason in ('disposable', 'role', 'catch_all', 'spamtrap', 'mailbox_not_found', 'mailbox_full', 'risky'):
            fields['reason'] = reason
            flags.append(reason)
        if data['status'] == 'catch_all':
            flags.append('catch_all')
        email = _email(data.get('email_address')) or _email(data.get('email'))
        if not email:
            return _error('invalid_response')
        return Outcome(mapping[data['status']], email, tuple(dict.fromkeys(flags)), fields)

    def balance_request(self, env):
        return self._request('GET', '/v2/email_verify/getcredits', self._headers(env))

    def parse_balance(self, response):
        j = _balance_base(response)
        if j.get('status') != 'success' or not isinstance(j.get('data'), dict):
            raise ProviderError('invalid_response')
        total = _decimal(j['data'].get('available_credits'))
        if total is None:
            raise ProviderError('invalid_response')
        return {'free_pools': {}, 'metadata': {'total_credits': total, 'manual_free_attestation_required': True}}


class ZeroBounce(Provider):
    id = 'zerobounce'
    independence_group = 'zerobounce'
    operations = ('find', 'verify')
    auth_env = ('ZEROBOUNCE_API_KEY',)
    min_spacing = {'find': 10.0, 'verify': 1.0, 'balance': 10.0}
    origin = 'https://api.zerobounce.net'

    def request(self, operation, payload, env):
        self._operation(operation)
        query = {'api_key': _secret(env, 'ZEROBOUNCE_API_KEY')}
        if operation == 'verify':
            query.update(email=_text(payload, 'email'), ip_address='', timeout=10, activity_data='false', verify_plus='false')
            route = '/v2/validate'
        else:
            if 'first_name' in payload and 'last_name' in payload:
                first, last = _text(payload, 'first_name'), _text(payload, 'last_name')
            else:
                parts = _text(payload, 'name').split(maxsplit=1)
                if len(parts) != 2:
                    raise PolicyError('invalid_payload')
                first, last = parts
            query.update(domain=_text(payload, 'domain'), first_name=first, last_name=last)
            route = '/v2/guessformat'
        return self._request('GET', route + '?' + urlencode(query), timeout=10 if operation == 'verify' else 30)

    def parse(self, operation, response):
        self._operation(operation)
        failure = http_failure(response)
        if failure:
            return failure
        j = response.data
        if operation == 'find':
            confidence = j.get('email_confidence', j.get('confidence'))
            if confidence not in ('high', 'medium', 'low', 'undetermined'):
                return _error('invalid_response')
            email = _email(j.get('email'))
            if email:
                return Outcome('candidate', email, fields={'confidence': confidence, 'verification_is_fresh': False, **_informational_fields(j)})
            if (('email' in j and j['email'] in (None, ''))
                    or ('email' not in j and confidence == 'undetermined')):
                return Outcome('not_found', fields={'confidence': confidence})
            return _error('invalid_response')
        status = j.get('status')
        mapping = {'valid': 'provider_valid', 'invalid': 'provider_invalid', 'catch-all': 'catch_all', 'unknown': 'unknown',
                   'spamtrap': 'unknown', 'abuse': 'unknown', 'do_not_mail': 'unknown'}
        if not isinstance(status, str) or status not in mapping:
            return _error('invalid_response')
        flags = _risks(j)
        if status in ('spamtrap', 'abuse', 'do_not_mail'):
            flags.append(status)
        if status == 'catch-all':
            flags.append('catch_all')
        substatus = j.get('sub_status')
        known = {'role_based', 'role_based_catch_all', 'disposable', 'toxic', 'possible_trap', 'global_suppression',
                 'mailbox_not_found', 'no_dns_entries', 'failed_smtp_connection', 'mailbox_quota_exceeded',
                 'greylisted', 'antispam_system', 'exception_occurred', 'timeout_exceeded', 'forcible_disconnect',
                 'mail_server_temporary_error', 'mail_server_did_not_respond', 'does_not_accept_mail', 'leading_period_removed',
                 'alias_address', 'alternate', 'accept_all', 'gold', 'possible_typo', ''}
        fields = {'status': status, **_informational_fields(j)}
        if isinstance(substatus, str) and substatus in known:
            fields['sub_status'] = substatus
            if substatus not in ('', 'alias_address', 'alternate', 'gold', 'leading_period_removed'):
                flags.append(substatus)
        email = _email(j.get('address'))
        if not email:
            return _error('invalid_response')
        return Outcome(mapping[status], email, tuple(dict.fromkeys(flags)), fields)

    def balance_request(self, env):
        return self._request('GET', '/v2/getcredits?' + urlencode({'api_key': _secret(env, 'ZEROBOUNCE_API_KEY')}))

    def parse_balance(self, response):
        j = _balance_base(response)
        value = j.get('Credits')
        if value == '-1' or (type(value) is int and value == -1):
            raise ProviderError('auth')
        if not (type(value) is int and value >= 0) and not (
                isinstance(value, str) and re.fullmatch(r'(?:0|[1-9][0-9]*)', value)):
            raise ProviderError('invalid_response')
        total = _int(value)
        if total is None:
            raise ProviderError('invalid_response')
        return {'free_pools': {}, 'metadata': {'total_credits': total, 'manual_free_attestation_required': True}}


class HunterInsight(Provider):
    id = 'hunter-insight'
    independence_group = 'hunter'
    operations = ('signals',)
    auth_env = ()
    min_spacing = {'signals': 10.0}
    origin = 'https://api.hunter.io'

    def request(self, operation, payload, env):
        self._operation(operation)
        return self._request('GET', '/v2/email-insight?' + urlencode({'email': _text(payload, 'email')}))

    def parse(self, operation, response):
        self._operation(operation)
        failure = http_failure(response)
        if failure:
            return failure
        data = response.data.get('data')
        if not isinstance(data, dict):
            return _error('invalid_response')
        fields = {k: data[k] for k in ('regexp', 'syntax', 'disposable', 'webmail', 'mx_records', 'role', 'gibberish') if type(data.get(k)) is bool}
        if not fields:
            return _error('invalid_response')
        return Outcome('signals', risk_flags=tuple(_risks(data)), fields=fields)


class Disify(Provider):
    id = 'disify'
    independence_group = 'disify'
    operations = ('signals',)
    auth_env = ()
    min_spacing = {'signals': 10.0}
    origin = 'https://disify.com'

    def request(self, operation, payload, env):
        self._operation(operation)
        return self._request('GET', '/api/email/' + quote(_text(payload, 'email'), safe=''))

    def parse(self, operation, response):
        self._operation(operation)
        failure = http_failure(response)
        if failure:
            return failure
        j = response.data
        fields = {k: j[k] for k in ('format', 'dns', 'disposable') if type(j.get(k)) is bool}
        if not fields:
            return _error('invalid_response')
        return Outcome('signals', risk_flags=tuple(_risks(j)), fields=fields)


# ---------------------------------------------------------------------------
# Lead-source parsing (list outcomes: 'results' or 'not_found').
# ---------------------------------------------------------------------------

_HUNTER_PATTERN = re.compile(r'\{(?:first|last|f|l)\}(?:[._-]?\{(?:first|last|f|l)\}){0,2}')
_SOCIAL_HOSTS = ('facebook.com', 'instagram.com', 'linkedin.com', 'twitter.com', 'x.com', 'youtube.com', 'tiktok.com',
                 'yelp.com', 'zillow.com', 'realtor.com', 'google.com', 'goo.gl', 'linktr.ee', 'pinterest.com',
                 'trulia.com', 'redfin.com', 'homes.com', 'nextdoor.com', 'business.site', 'wa.me', 'bit.ly')
OSM_ATTRIBUTION = '\u00a9 OpenStreetMap contributors, ODbL'


def _at_domain(email, domain):
    if not isinstance(email, str) or '@' not in email or not domain:
        return False
    host = email.rsplit('@', 1)[1].lower()
    return host == domain or host.endswith('.' + domain)


def _safe_tree(value, depth=0):
    """Keep only dicts/lists/strings from provider metadata; bounded and URL-free."""
    if depth > 5:
        return None
    if isinstance(value, dict):
        out = {}
        for key, item in list(value.items())[:50]:
            if isinstance(key, str) and re.fullmatch(r'[a-z_]{1,40}', key):
                clean = _safe_tree(item, depth + 1)
                if clean not in (None, {}, []):
                    out[key] = clean
        return out
    if isinstance(value, list):
        return [c for c in (_safe_tree(v, depth + 1) for v in value[:100]) if c not in (None, {}, [])]
    if isinstance(value, str):
        return clean_text(value, 500)
    return None


def _company_domain(value, blocked=()):
    """Normalize a website-ish value to a registrable company host, or None for socials/invalid."""
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        return None
    text = value.strip()
    if '://' not in text:
        text = 'https://' + text.lstrip('/')
    try:
        host = normalize_domain(urlsplit(text).hostname or '')
    except (PolicyError, ValueError):
        return None
    if host.startswith('www.'):
        host = host[4:]
    if any(host == s or host.endswith('.' + s) for s in _SOCIAL_HOSTS + tuple(blocked)):
        return None
    return host


def _parse_hunter_discover(j):
    data = j.get('data')
    if not isinstance(data, list):
        return _error('invalid_response')
    companies = []
    for item in data[:100]:
        if not isinstance(item, dict):
            continue
        try:
            domain = normalize_domain(item.get('domain'))
        except PolicyError:
            continue
        counts = item.get('emails_count') if isinstance(item.get('emails_count'), dict) else {}
        companies.append({'domain': domain, 'organization': clean_text(item.get('organization'), 200),
                          'emails_count_personal': _int(counts.get('personal')),
                          'emails_count_total': _int(counts.get('total')), 'source': 'hunter_discover'})
    if data and not companies:
        return _error('invalid_response')
    meta = j.get('meta') if isinstance(j.get('meta'), dict) else {}
    fields = {'companies': companies, 'applied_filters': _safe_tree(meta.get('filters')) or {},
              'total_results': _int(meta.get('results'))}
    return Outcome('results' if companies else 'not_found', fields=fields)


def _parse_hunter_domain_search(j):
    data = j.get('data')
    if not isinstance(data, dict) or not isinstance(data.get('emails'), list):
        return _error('invalid_response')
    try:
        anchor = normalize_domain(data['domain']) if data.get('domain') is not None else None
    except PolicyError:
        return _error('invalid_response')
    if anchor and anchor.startswith('www.'):
        anchor = anchor[4:]
    pattern = data.get('pattern')
    organization = clean_text(data.get('organization'), 200)
    fields = {'domain': anchor, 'organization': organization,
              'pattern': pattern if isinstance(pattern, str) and _HUNTER_PATTERN.fullmatch(pattern) else None,
              'accept_all': data.get('accept_all') if type(data.get('accept_all')) is bool else None}
    if data['emails'] and not anchor:
        return _error('invalid_response')
    prospects = []
    for item in data['emails'][:100]:
        if not isinstance(item, dict) or item.get('type') != 'personal':
            continue
        email = _email(item.get('value'))
        if not email:
            continue
        try:
            email = normalize_email(email)
        except PolicyError:
            continue
        if not _at_domain(email, anchor):
            continue
        first, last = clean_text(item.get('first_name'), 60), clean_text(item.get('last_name'), 60)
        confidence = item.get('confidence')
        verification = item.get('verification') if isinstance(item.get('verification'), dict) else {}
        status = verification.get('status')
        source_url = None
        sources = item.get('sources')
        if isinstance(sources, list) and sources and isinstance(sources[0], dict):
            uri = sources[0].get('uri')
            try:
                source_url = validate_evidence_url(uri) if isinstance(uri, str) and len(uri) <= 2048 else None
            except PolicyError:
                source_url = None
        prospects.append({
            'full_name': ' '.join(p for p in (first, last) if p) or None, 'first_name': first, 'last_name': last,
            'title': clean_text(item.get('position'), 150), 'email': email,
            'email_confidence': confidence if type(confidence) is int and 0 <= confidence <= 100 else None,
            'email_verification': status if status in ('valid', 'accept_all', 'unknown', 'invalid') else None,
            'linkedin_url': linkedin_from_handle(item.get('linkedin')),
            'seniority': item.get('seniority') if item.get('seniority') in HUNTER_SENIORITIES else None,
            'department': item.get('department') if item.get('department') in HUNTER_DEPARTMENTS else None,
            'decision_maker': item.get('decision_maker') is True, 'email_source': 'hunter_domain_search',
            'company': organization, 'company_website': anchor, 'source': 'hunter', 'source_url': source_url})
    meta = j.get('meta') if isinstance(j.get('meta'), dict) else {}
    fields.update(prospects=prospects, total_results=_int(meta.get('results')))
    return Outcome('results' if prospects else 'not_found', fields=fields)


def _serp_rows(items, title_key, link_key, snippet_key):
    results, prospects = [], []
    for position, item in enumerate(items[:100], 1):
        if not isinstance(item, dict):
            continue
        title, link, snippet = item.get(title_key), item.get(link_key), item.get(snippet_key)
        rank = item.get('position') if type(item.get('position')) is int and item.get('position') > 0 else position
        row = {'title': clean_text(title, 300), 'link': safe_link(link), 'snippet': clean_text(snippet, 500), 'position': rank}
        if row['title'] or row['link']:
            results.append(row)
        prospect = parse_linkedin_result(title, snippet, link) if isinstance(link, str) else None
        if prospect:
            prospects.append({**prospect, 'position': rank})
    return results, prospects


class Serper(Provider):
    """Google results via serper.dev. num is fixed at 10 because num > 10 bills 2 credits."""
    id = 'serper'
    independence_group = 'serper'
    operations = ('search',)
    auth_env = ('SERPER_API_KEY',)
    min_spacing = {'search': 0.3}
    origin = 'https://google.serper.dev'

    def request(self, operation, payload, env):
        self._operation(operation)
        body = {'q': search_query(payload.get('query')), 'num': 10, 'gl': 'us', 'hl': 'en'}
        if payload.get('num', 10) != 10 or any(k in payload for k in ('count', 'offset')):
            raise PolicyError('invalid_payload')
        page = payload.get('page', 1)
        if type(page) is not int or not 1 <= page <= 10:
            raise PolicyError('invalid_payload')
        if page != 1:
            body['page'] = page
        headers = {'X-API-KEY': _secret(env, 'SERPER_API_KEY'), 'Content-Type': 'application/json'}
        return self._request('POST', '/search', headers, body)

    def parse(self, operation, response):
        self._operation(operation)
        failure = http_failure(response)
        if failure:
            return failure
        j = response.data
        if 'message' in j or 'statusCode' in j:
            return _error('provider_application_error')
        organic = j.get('organic')
        if organic is None and isinstance(j.get('searchParameters'), dict):
            organic = []
        if not isinstance(organic, list):
            return _error('invalid_response')
        results, prospects = _serp_rows(organic, 'title', 'link', 'snippet')
        return Outcome('results' if results else 'not_found', fields={'prospects': prospects, 'results': results})

    def parse_balance(self, response):
        _balance_base(response)
        raise ProviderError('balance_unsupported')


class Brave(Provider):
    """Brave Search API web results; free credit plans are about 1 request/second."""
    id = 'brave'
    independence_group = 'brave'
    operations = ('search',)
    auth_env = ('BRAVE_API_KEY',)
    min_spacing = {'search': 1.1}
    origin = 'https://api.search.brave.com'

    def request(self, operation, payload, env):
        self._operation(operation)
        if any(k in payload for k in ('num', 'page')):
            raise PolicyError('invalid_payload')
        count, offset = payload.get('count', 20), payload.get('offset', 0)
        if type(count) is not int or not 1 <= count <= 20 or type(offset) is not int or not 0 <= offset <= 9:
            raise PolicyError('invalid_payload')
        query = {'q': search_query(payload.get('query')), 'count': count, 'country': 'us'}
        if offset:
            query['offset'] = offset
        headers = {'X-Subscription-Token': _secret(env, 'BRAVE_API_KEY'), 'Accept': 'application/json'}
        return self._request('GET', '/res/v1/web/search?' + urlencode(query), headers)

    def parse(self, operation, response):
        self._operation(operation)
        failure = http_failure(response)
        if failure:
            return failure
        j = response.data
        if j.get('type') == 'ErrorResponse' or 'message' in j:
            return _error('provider_application_error')
        web = j.get('web')
        if web is None and j.get('type') == 'search':
            web = {'results': []}
        if not isinstance(web, dict) or not isinstance(web.get('results'), list):
            return _error('invalid_response')
        results, prospects = _serp_rows(web['results'], 'title', 'url', 'description')
        return Outcome('results' if results else 'not_found', fields={'prospects': prospects, 'results': results})

    def parse_balance(self, response):
        _balance_base(response)
        raise ProviderError('balance_unsupported')


def _ql_string(value):
    if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise PolicyError('invalid_area')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def build_overpass_query(area, state=None, tags=OSM_DEFAULT_TAGS, limit=500):
    """Build Overpass QL from validated parts only; raw QL is never accepted.

    A state name alone resolves through its ISO3166-2 code. Other names resolve to
    administrative boundary relations inside the US (or the given state) via map_to_area.
    """
    area = overpass_area(area)
    tag_list = [t for t in OSM_TAG_ALLOWLIST if t in tags]
    if not tag_list or set(tags) - set(OSM_TAG_ALLOWLIST) or type(limit) is not int or not 1 <= limit <= 1000:
        raise PolicyError('invalid_payload')
    state = us_state_code(state) if state else None
    lines = ['[out:json][timeout:60];']
    as_state = None
    try:
        as_state = us_state_code(area)
    except PolicyError:
        pass
    if as_state and (state is None or state == as_state):
        lines.append('area["ISO3166-2"=' + _ql_string('US-' + as_state) + ']->.a;')
    else:
        if state:
            lines.append('area["ISO3166-2"=' + _ql_string('US-' + state) + ']->.s;')
        else:
            lines.append('area["ISO3166-1"="US"]["admin_level"="2"]->.s;')
        lines.append('rel["boundary"="administrative"]["name"=' + _ql_string(area) + '](area.s)->.r;')
        lines.append('.r map_to_area->.a;')
    selectors = ''.join('nwr[' + _ql_string(k) + '=' + _ql_string(v) + '](area.a);'
                        for k, v in (t.split('=', 1) for t in tag_list))
    lines.append('(' + selectors + ');')
    lines.append('out tags center ' + str(limit) + ';')
    return '\n'.join(lines)


class Overpass(Provider):
    """OpenStreetMap Overpass API (no auth). Data © OpenStreetMap contributors, ODbL."""
    id = 'overpass'
    independence_group = 'overpass'
    operations = ('places',)
    auth_env = ()
    min_spacing = {'places': 5.0}
    origin = 'https://overpass-api.de'

    def request(self, operation, payload, env):
        self._operation(operation)
        ql = build_overpass_query(payload.get('area'), payload.get('state'), payload.get('tags') or OSM_DEFAULT_TAGS)
        headers = {'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json',
                   'User-Agent': 'lead-research-kit (stdlib urllib)'}
        return self._request('POST', '/api/interpreter', headers, raw_body=('data=' + quote_plus(ql)).encode('ascii'),
                             timeout=90, max_response_bytes=MAX_RESPONSE_CEILING)

    def parse(self, operation, response):
        self._operation(operation)
        failure = http_failure(response)
        if failure:
            return failure
        j = response.data
        if isinstance(j.get('remark'), str) and re.search(r'(?i)error|timed out|out of memory', j['remark']):
            return _error('provider_application_error')
        elements = j.get('elements')
        if not isinstance(elements, list):
            return _error('invalid_response')
        companies = []
        for item in elements[:1000]:
            if not isinstance(item, dict) or not isinstance(item.get('tags'), dict):
                continue
            tags = item['tags']
            name = clean_text(tags.get('name'), 200)
            kind, ident = item.get('type'), item.get('id')
            if not name or kind not in ('node', 'way', 'relation') or type(ident) is not int:
                continue
            domain = None
            for key in ('website', 'contact:website', 'url'):
                domain = _company_domain(tags.get(key))
                if domain:
                    break
            phone = tags.get('phone') or tags.get('contact:phone')
            phone = phone.split(';')[0].strip() if isinstance(phone, str) else None
            state = tags.get('addr:state')
            companies.append({'organization': name, 'domain': domain or '',
                              'phone': phone if phone and re.fullmatch(r'[+0-9 ().-]{7,32}', phone) else None,
                              'city': clean_text(tags.get('addr:city'), 80),
                              'state': state if isinstance(state, str) and state.upper() in US_STATES else clean_text(state, 40),
                              'source': 'openstreetmap', 'osm_id': kind + '/' + str(ident)})
        fields = {'companies': companies, 'attribution': OSM_ATTRIBUTION}
        return Outcome('results' if companies else 'not_found', fields=fields)


# ---------------------------------------------------------------------------
# v3 adapters: Apollo, Prospeo, Google Places, Exa, Tomba, Icypeas, Reoon,
# MillionVerifier. All are disabled by default with zero budgets (configuration.py).
# ---------------------------------------------------------------------------

class _NoBalance(Provider):
    """v3 adapters expose no balance sync; an error body still maps to a safe error code."""

    def parse_balance(self, response):
        _balance_base(response)
        raise ProviderError('balance_unsupported')


_PROVIDER_ID = re.compile(r'[A-Za-z0-9]{8,64}')
_SEARCH_ID = re.compile(r'[A-Za-z0-9_-]{1,128}')
_PLACE_ID = re.compile(r'[A-Za-z0-9_-]{10,300}')
_CURSOR_TEXT = re.compile(r'[A-Za-z0-9_-]{1,2048}')
_PHONE = re.compile(r'[+0-9 ().-]{7,32}')
# Site-builder / franchise-hosting hosts are not a company's own domain.
_BUILDER_HOSTS = ('wixsite.com', 'godaddysites.com', 'squarespace.com', 'weebly.com', 'wordpress.com', 'blogspot.com',
                  'webflow.io', 'carrd.co', 'square.site', 'myshopify.com', 'jimdosite.com', 'strikingly.com',
                  'site123.me', 'mystrikingly.com', 'ueniweb.com', 'placester.net', 'kw.com', 'remax.com',
                  'coldwellbanker.com', 'century21.com', 'exprealty.com', 'compass.com', 'bhhs.com', 'sothebysrealty.com')


def _revealed_email(value):
    """A real mailbox, never a masked preview such as 'jane.*****@example.org'."""
    email = _email(value)
    if not email or '*' in email:
        return None
    try:
        return normalize_email(email)
    except PolicyError:
        return None


def _bool(data, key):
    value = data.get(key)
    return value if type(value) is bool else None


def _phone(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if _PHONE.fullmatch(value) else None


def _first_last(payload):
    """Finder name parts from first_name/last_name, full_name, or the legacy 'name'."""
    if 'first_name' in payload and 'last_name' in payload:
        return _text(payload, 'first_name'), _text(payload, 'last_name')
    parts = _text(payload, 'full_name' if 'full_name' in payload else 'name').split(maxsplit=1)
    if len(parts) != 2:
        raise PolicyError('invalid_payload')
    return parts[0], parts[1]


def _finder_domain(payload):
    domain = normalize_domain(_text(payload, 'domain'))
    return domain[4:] if domain.startswith('www.') else domain


def _paging_free(payload):
    if any(k in payload for k in ('num', 'page', 'count', 'offset')):
        raise PolicyError('invalid_payload')


def _verification_status(value):
    return value if value in ('valid', 'accept_all', 'unknown', 'invalid') else None


# Apollo documents these contact email statuses (contact_email_status[]).
_APOLLO_EMAIL_STATUSES = ('verified', 'unverified', 'likely to engage', 'unavailable')


def _apollo_person(item):
    if not isinstance(item, dict):
        return None
    apollo_id = item.get('id') if isinstance(item.get('id'), str) and _PROVIDER_ID.fullmatch(item['id']) else None
    first = clean_text(item.get('first_name'), 60)
    last = clean_text(item.get('last_name'), 60)
    hidden = clean_text(item.get('last_name_obfuscated'), 60)
    if last and '*' in last:
        hidden, last = last, None
    complete = bool(first and last)
    if not apollo_id and not complete:
        return None
    org = item.get('organization') if isinstance(item.get('organization'), dict) else {}
    website = _company_domain(org.get('primary_domain')) or _company_domain(org.get('website_url'))
    seniority = item.get('seniority')
    location = ', '.join(p for p in (clean_text(item.get('city'), 80), clean_text(item.get('state'), 80)) if p) or None
    return {'apollo_id': apollo_id, 'first_name': first, 'last_name': last,
            'last_name_obfuscated': None if complete else hidden,
            'full_name': (first + ' ' + last) if complete else None,
            'display_name': ' '.join(p for p in (first, last or hidden) if p) or None,
            'name_complete': complete, 'title': clean_text(item.get('title'), 150),
            'linkedin_url': canonical_linkedin(item.get('linkedin_url')) if isinstance(item.get('linkedin_url'), str) else None,
            'company': clean_text(org.get('name'), 200), 'company_website': website, 'location': location,
            'has_email': _bool(item, 'has_email'),
            'seniority': seniority if seniority in APOLLO_SENIORITIES else None,
            'source': 'apollo', 'source_url': None}


class Apollo(_NoBalance):
    """Apollo.io People API Search (0 credits) and single-person People Enrichment.

    Docs (verified 2026-10): https://docs.apollo.io/reference/people-api-search -
    POST https://api.apollo.io/api/v1/mixed_people/api_search with filters as query
    parameters (person_titles[], include_similar_titles, person_seniorities[],
    person_locations[], organization_locations[], q_organization_domains_list[],
    organization_num_employees_ranges[], q_keywords, page, per_page); 100 per page, 500 pages.
    The response is {total_entries, people[{id, first_name, last_name_obfuscated, title,
    has_email, organization{name, has_* flags}}]}: no emails, phones, LinkedIn or domains.
    https://docs.apollo.io/reference/people-enrichment - POST /api/v1/people/match (id,
    linkedin_url, or name + domain) -> {person{first_name, last_name, linkedin_url, title,
    email, email_status, match_confidence, organization{name, website_url, primary_domain}}}.
    reveal_personal_emails/reveal_phone_number stay false (no webhook, no 8-credit mobiles).
    Auth: x-api-key header. Search is free of credits, so 'search_requests' is a local counter.
    Spacing 1.5s fits the free plan's documented per-minute limits.
    """
    id = 'apollo'
    independence_group = 'apollo'
    operations = ('people_search', 'people_enrich')
    auth_env = ('APOLLO_API_KEY',)
    min_spacing = {'people_search': 1.5, 'people_enrich': 1.5}
    origin = 'https://api.apollo.io'

    def _headers(self, env):
        return {'x-api-key': _secret(env, 'APOLLO_API_KEY'), 'Content-Type': 'application/json',
                'Cache-Control': 'no-cache', 'Accept': 'application/json'}

    def request(self, operation, payload, env):
        self._operation(operation)
        if operation == 'people_search':
            criteria = apollo_criteria(strict_payload(operation, payload).get('criteria'))
            query = []
            for key, param in (('titles', 'person_titles[]'), ('seniorities', 'person_seniorities[]'),
                               ('person_locations', 'person_locations[]'),
                               ('organization_locations', 'organization_locations[]'),
                               ('domains', 'q_organization_domains_list[]'),
                               ('employee_ranges', 'organization_num_employees_ranges[]')):
                query.extend((param, value) for value in criteria.get(key, ()))
            if 'keywords' in criteria:
                query.append(('q_keywords', criteria['keywords']))
            if 'include_similar_titles' in criteria:
                query.append(('include_similar_titles', 'true' if criteria['include_similar_titles'] else 'false'))
            query += [('page', criteria['page']), ('per_page', criteria['per_page'])]
            return self._request('POST', '/api/v1/mixed_people/api_search?' + urlencode(query), self._headers(env))
        identity = enrich_identity(operation, payload)
        if identity.get('apollo_id'):
            query = [('id', identity['apollo_id'])]
        elif identity.get('linkedin_url'):
            query = [('linkedin_url', identity['linkedin_url'])]
        else:
            query = [('name', identity['name']), ('domain', identity['domain'])]
        query += [('reveal_personal_emails', 'false'), ('reveal_phone_number', 'false')]
        return self._request('POST', '/api/v1/people/match?' + urlencode(query), self._headers(env))

    def parse(self, operation, response):
        self._operation(operation)
        failure = http_failure(response)
        if failure:
            return failure
        j = response.data
        if 'error_code' in j or 'error_details' in j:
            return _error('provider_application_error')
        if operation == 'people_search':
            people = j.get('people')
            if not isinstance(people, list):
                return _error('invalid_response')
            prospects = [row for row in (_apollo_person(item) for item in people[:100]) if row]
            if people and not prospects:
                return _error('invalid_response')
            return Outcome('results' if prospects else 'not_found',
                           fields={'prospects': prospects, 'total_results': _int(j.get('total_entries'))})
        if 'person' not in j:
            return _error('invalid_response')
        person = j['person']
        if person is None:
            return Outcome('not_found', fields={'match_confidence': None})
        row = _apollo_person(person)
        if not row:
            return _error('invalid_response')
        confidence = person.get('match_confidence')
        confidence = confidence if confidence in ('high', 'medium', 'low', 'none') else None
        status = person.get('email_status') if person.get('email_status') in _APOLLO_EMAIL_STATUSES else None
        email = _revealed_email(person.get('email'))
        row.update(email=email, email_status=status,
                   email_verification='valid' if email and status == 'verified' else None)
        fields = {'match_confidence': confidence, 'verification_is_fresh': False, 'prospect': row}
        if status:
            fields['finder_verification_status'] = status
        if confidence == 'none':
            return Outcome('not_found', fields={'match_confidence': confidence})
        if email:
            return Outcome('candidate', email, fields=fields)
        return Outcome('not_found', fields=fields)


# Prospeo returns business outcomes (no results/no match) as HTTP 400 + error_code.
_PROSPEO_NOT_FOUND = ('NO_RESULTS', 'NO_MATCH')
_PROSPEO_ERRORS = {'INVALID_API_KEY': 'auth', 'INSUFFICIENT_CREDITS': 'quota'}


def _prospeo_person(person, company):
    if not isinstance(person, dict):
        return None
    company = company if isinstance(company, dict) else {}
    person_id = person.get('person_id') if isinstance(person.get('person_id'), str) and _PROVIDER_ID.fullmatch(person['person_id']) else None
    first, last = clean_text(person.get('first_name'), 60), clean_text(person.get('last_name'), 60)
    full = clean_text(person.get('full_name'), 120) or (' '.join(p for p in (first, last) if p) or None)
    if not person_id and not full:
        return None
    location = person.get('location') if isinstance(person.get('location'), dict) else {}
    place = ', '.join(p for p in (clean_text(location.get('city'), 80), clean_text(location.get('state'), 80)) if p) or None
    country = location.get('country_code') if isinstance(location.get('country_code'), str) and re.fullmatch(r'[A-Z]{2}', location['country_code']) else None
    return {'prospeo_id': person_id, 'full_name': full, 'first_name': first, 'last_name': last,
            'name_complete': bool(first and last) or bool(full and len(full.split()) >= 2),
            'title': clean_text(person.get('current_job_title'), 150),
            'linkedin_url': canonical_linkedin(person.get('linkedin_url')) if isinstance(person.get('linkedin_url'), str) else None,
            'location': place, 'snippet_country': country, 'company': clean_text(company.get('name'), 200),
            'company_website': _company_domain(company.get('domain')) or _company_domain(company.get('website')),
            'source': 'prospeo', 'source_url': None}


class Prospeo(_NoBalance):
    """Prospeo Search Person (no emails) and Enrich Person (one person per job).

    Docs (verified 2026-10): https://prospeo.io/api-docs/search-person - POST
    https://api.prospeo.io/search-person {page, filters}; 25 results/page, 1 credit per page
    that returns people (repeat pages within 30 days are free, "free": true). Filters used:
    company.websites.include (<= 500 root domains), person_job_title {include, match_mode},
    person_seniority.include, person_location_search.include, company_location_search.include,
    max_person_per_company (https://prospeo.io/api-docs/filters-documentation). Response
    {error, free, results[{person, company}], pagination{current_page, per_page, total_page,
    total_count}}. https://prospeo.io/api-docs/enrich-person - POST /enrich-person
    {only_verified_email, enrich_mobile, data{person_id | linkedin_url | full_name +
    company_website}} -> {error, free_enrichment, person{..., email{status, revealed, email}},
    company}; 1 credit per email found. Errors are HTTP 400 with error_code (NO_RESULTS,
    NO_MATCH, INVALID_API_KEY, INSUFFICIENT_CREDITS, ...). Auth: X-KEY header.
    Spacing: search endpoints are rate-limited below enrich (5/s on Starter), so 2s / 0.5s.
    """
    id = 'prospeo'
    independence_group = 'prospeo'
    operations = ('people_search', 'enrich')
    auth_env = ('PROSPEO_API_KEY',)
    min_spacing = {'people_search': 2.0, 'enrich': 0.5}
    origin = 'https://api.prospeo.io'
    parses_client_errors = True

    def _headers(self, env):
        return {'X-KEY': _secret(env, 'PROSPEO_API_KEY'), 'Content-Type': 'application/json'}

    def request(self, operation, payload, env):
        self._operation(operation)
        if operation == 'people_search':
            criteria = prospeo_criteria(strict_payload(operation, payload).get('criteria'))
            filters = {}
            if 'websites' in criteria:
                filters['company'] = {'websites': {'include': criteria['websites']}}
            if 'titles' in criteria:
                filters['person_job_title'] = {'include': criteria['titles'], 'match_mode': criteria['title_match_mode']}
            if 'seniorities' in criteria:
                filters['person_seniority'] = {'include': criteria['seniorities']}
            if 'person_locations' in criteria:
                filters['person_location_search'] = {'include': criteria['person_locations']}
            if 'company_locations' in criteria:
                filters['company_location_search'] = {'include': criteria['company_locations']}
            if 'max_person_per_company' in criteria:
                filters['max_person_per_company'] = criteria['max_person_per_company']
            return self._request('POST', '/search-person', self._headers(env), {'page': criteria['page'], 'filters': filters})
        identity = enrich_identity(operation, payload)
        if identity.get('person_id'):
            data = {'person_id': identity['person_id']}
        elif identity.get('linkedin_url'):
            data = {'linkedin_url': identity['linkedin_url']}
        else:
            data = {'full_name': identity['name'], 'company_website': identity['domain']}
        body = {'only_verified_email': False, 'enrich_mobile': False, 'data': data}
        return self._request('POST', '/enrich-person', self._headers(env), body)

    def parse(self, operation, response):
        self._operation(operation)
        j = response.data if response.json_valid and isinstance(response.data, dict) else None
        if j is not None and response.status in (200, 400) and j.get('error') is True:
            code = j.get('error_code')
            if code in _PROSPEO_NOT_FOUND:
                return Outcome('not_found', fields={'error_code': code})
            return _error(_PROSPEO_ERRORS.get(code, 'provider_application_error'))
        failure = http_failure(response)
        if failure:
            return failure
        if j.get('error') is not False:
            return _error('invalid_response')
        if operation == 'people_search':
            results = j.get('results')
            if not isinstance(results, list):
                return _error('invalid_response')
            prospects = [row for row in (_prospeo_person(r.get('person'), r.get('company'))
                                         for r in results[:25] if isinstance(r, dict)) if row]
            if results and not prospects:
                return _error('invalid_response')
            page = j.get('pagination') if isinstance(j.get('pagination'), dict) else {}
            return Outcome('results' if prospects else 'not_found', fields={
                'prospects': prospects, 'total_results': _int(page.get('total_count')),
                'total_pages': _int(page.get('total_page')), 'free': _bool(j, 'free')})
        row = _prospeo_person(j.get('person'), j.get('company'))
        if not row:
            return _error('invalid_response')
        mail = j['person'].get('email') if isinstance(j['person'].get('email'), dict) else {}
        status = mail.get('status') if mail.get('status') in ('VERIFIED', 'UNAVAILABLE') else None
        email = _revealed_email(mail.get('email')) if mail.get('revealed') is True else None
        row.update(email=email, email_status=status,
                   email_verification='valid' if email and status == 'VERIFIED' else None)
        fields = {'verification_is_fresh': False, 'prospect': row, 'free_enrichment': _bool(j, 'free_enrichment')}
        if status:
            fields['finder_verification_status'] = status.lower()
        return Outcome('candidate' if email else 'not_found', email, fields=fields)


class GooglePlaces(_NoBalance):
    """Google Places API (New) Text Search with a fixed Enterprise field mask.

    Docs (verified 2026-10): https://developers.google.com/maps/documentation/places/web-service/text-search
    POST https://places.googleapis.com/v1/places:searchText {textQuery, pageSize 1-20,
    pageToken, regionCode, includedType}; headers X-Goog-Api-Key and X-Goog-FieldMask.
    websiteUri / nationalPhoneNumber put the request in the Text Search Enterprise SKU.
    Response {places[{id, displayName{text}, websiteUri, nationalPhoneNumber,
    formattedAddress, addressComponents[{longText, shortText, types}], types}], nextPageToken};
    an empty match returns {}. A pageToken request must repeat the other parameters.
    """
    id = 'google_places'
    independence_group = 'google_places'
    operations = ('text_search',)
    auth_env = ('GOOGLE_MAPS_API_KEY',)
    min_spacing = {'text_search': 0.2}
    origin = 'https://places.googleapis.com'
    FIELD_MASK = ('places.id,places.displayName,places.websiteUri,places.nationalPhoneNumber,places.formattedAddress,'
                  'places.addressComponents,places.types,nextPageToken')

    def request(self, operation, payload, env):
        self._operation(operation)
        clean = strict_payload(operation, payload)
        if 'text_query' not in clean:
            raise PolicyError('invalid_payload')
        body = {'textQuery': clean['text_query'], 'pageSize': clean.get('page_size', 20), 'regionCode': 'US'}
        if clean.get('included_type'):
            body['includedType'] = clean['included_type']
        if clean.get('page_cursor'):
            body['pageToken'] = clean['page_cursor']
        headers = {'X-Goog-Api-Key': _secret(env, 'GOOGLE_MAPS_API_KEY'), 'X-Goog-FieldMask': self.FIELD_MASK,
                   'Content-Type': 'application/json'}
        return self._request('POST', '/v1/places:searchText', headers, body)

    def parse(self, operation, response):
        self._operation(operation)
        failure = http_failure(response)
        if failure:
            return failure
        j = response.data
        places = j.get('places', [])
        if not isinstance(places, list):
            return _error('invalid_response')
        companies = []
        for item in places[:20]:
            if not isinstance(item, dict):
                continue
            place_id = item.get('id') if isinstance(item.get('id'), str) and _PLACE_ID.fullmatch(item['id']) else None
            display = item.get('displayName') if isinstance(item.get('displayName'), dict) else {}
            name = clean_text(display.get('text'), 200)
            if not place_id or not name:
                continue
            parts = {}
            for comp in item.get('addressComponents') or []:
                if isinstance(comp, dict) and isinstance(comp.get('types'), list):
                    for kind in ('locality', 'administrative_area_level_1', 'postal_code', 'country'):
                        if kind in comp['types'] and kind not in parts:
                            parts[kind] = comp.get('shortText') if kind != 'locality' else comp.get('longText')
            state = parts.get('administrative_area_level_1')
            types = [v for v in item.get('types') or [] if isinstance(v, str) and re.fullmatch(r'[a-z_]{2,60}', v)][:20]
            companies.append({'organization': name, 'domain': _company_domain(item.get('websiteUri'), _BUILDER_HOSTS) or '',
                              'phone': _phone(item.get('nationalPhoneNumber')),
                              'address': clean_text(item.get('formattedAddress'), 300),
                              'city': clean_text(parts.get('locality'), 80),
                              'state': state if isinstance(state, str) and state in US_STATES else None,
                              'postal_code': parts['postal_code'] if isinstance(parts.get('postal_code'), str) and re.fullmatch(r'[0-9]{5}(?:-[0-9]{4})?', parts['postal_code']) else None,
                              'country': parts['country'] if isinstance(parts.get('country'), str) and re.fullmatch(r'[A-Z]{2}', parts['country']) else None,
                              'types': types, 'place_id': place_id, 'source': 'google_places'})
        if places and not companies:
            return _error('invalid_response')
        cursor = j.get('nextPageToken')
        fields = {'companies': companies,
                  'next_page_cursor': cursor if isinstance(cursor, str) and _CURSOR_TEXT.fullmatch(cursor) else None}
        return Outcome('results' if companies else 'not_found', fields=fields)


class Exa(_NoBalance):
    """Exa neural search restricted to the 'people' category (LinkedIn-style profiles).

    Docs (verified 2026-10): https://docs.exa.ai/reference/search - POST https://api.exa.ai/search
    {query, type 'auto', category 'people', numResults, userLocation}; auth x-api-key header;
    response {results[{title, url, id, author, text?, highlights?}], requestId}. The legacy
    'linkedin profile' category is no longer listed; 'people' replaces it. numResults is fixed
    at 10 so a ledger query is one search request; paging keys are rejected.
    """
    id = 'exa'
    independence_group = 'exa'
    operations = ('search',)
    auth_env = ('EXA_API_KEY',)
    min_spacing = {'search': 0.25}
    origin = 'https://api.exa.ai'

    def request(self, operation, payload, env):
        self._operation(operation)
        _paging_free(payload)
        body = {'query': search_query(payload.get('query')), 'type': 'auto', 'category': 'people',
                'numResults': 10, 'userLocation': 'US'}
        headers = {'x-api-key': _secret(env, 'EXA_API_KEY'), 'Content-Type': 'application/json'}
        return self._request('POST', '/search', headers, body)

    def parse(self, operation, response):
        self._operation(operation)
        failure = http_failure(response)
        if failure:
            return failure
        items = response.data.get('results')
        if not isinstance(items, list):
            return _error('invalid_response')
        rows = []
        for item in items[:100]:
            if not isinstance(item, dict):
                continue
            highlights = item.get('highlights')
            snippet = item.get('text') if isinstance(item.get('text'), str) else (
                ' · '.join(h for h in highlights if isinstance(h, str)) if isinstance(highlights, list) else None)
            rows.append({'title': item.get('title'), 'url': item.get('url'), 'snippet': snippet})
        results, prospects = _serp_rows(rows, 'title', 'url', 'snippet')
        prospects = [{**row, 'source': 'exa'} for row in prospects]
        return Outcome('results' if results else 'not_found', fields={'prospects': prospects, 'results': results})


_TOMBA_NOT_FOUND = ('unknown_record', 'not_found', 'claimed_email', 'claimed_linkedin')
_TOMBA_ERRORS = {'authentication_failed': 'auth', 'api_key_expired': 'auth', 'invalid_token': 'auth',
                 'insufficient_scope': 'auth', 'forbidden': 'auth', 'quota_exceeded': 'quota', 'rate_limit': 'rate_limit'}


class Tomba(_NoBalance):
    """Tomba Email Finder and LinkedIn Finder.

    Docs (verified 2026-10): https://docs.tomba.io/api/finder and https://docs.tomba.io/openapi.md -
    GET https://api.tomba.io/v1/email-finder?domain&first_name&last_name and
    GET /v1/linkedin?url= (param name per the official SDK, github.com/tomba-io/python).
    Headers X-Tomba-Key / X-Tomba-Secret. 200 {data{email|null, first_name, last_name,
    full_name, website_url, company, position, linkedin, score, accept_all, type,
    verification{status}}}; errors {errors{type, message, code}} with 400 unknown_record for
    webmail/disposable domains and 451 claimed_* for withheld people.
    Spacing: https://docs.tomba.io/rate-limits lists 1/s, 2/min, 5/day for finders on the
    Free plan, so 30s (Basic+ plans allow ~1/s; spacing only ever slows requests down).
    """
    id = 'tomba'
    independence_group = 'tomba'
    operations = ('find', 'linkedin_email')
    auth_env = ('TOMBA_API_KEY', 'TOMBA_SECRET')
    min_spacing = {'find': 30.0, 'linkedin_email': 30.0}
    origin = 'https://api.tomba.io'
    parses_client_errors = True

    def request(self, operation, payload, env):
        self._operation(operation)
        headers = {'X-Tomba-Key': _secret(env, 'TOMBA_API_KEY'), 'X-Tomba-Secret': _secret(env, 'TOMBA_SECRET'),
                   'Accept': 'application/json'}
        if operation == 'linkedin_email':
            url = linkedin_profile_url(strict_payload(operation, payload).get('linkedin_url'))
            return self._request('GET', '/v1/linkedin?' + urlencode({'url': url}), headers, timeout=120)
        first, last = _first_last(payload)
        query = {'domain': _finder_domain(payload), 'first_name': first, 'last_name': last}
        return self._request('GET', '/v1/email-finder?' + urlencode(query), headers, timeout=120)

    def parse(self, operation, response):
        self._operation(operation)
        j = response.data if response.json_valid and isinstance(response.data, dict) else None
        errors = j.get('errors') if j is not None else None
        if isinstance(errors, dict) and isinstance(errors.get('type'), str):
            if errors['type'] in _TOMBA_NOT_FOUND:
                return Outcome('not_found', fields={'reason': errors['type']})
            return _error(_TOMBA_ERRORS.get(errors['type'], 'provider_application_error'))
        failure = http_failure(response)
        if failure:
            return failure
        data = j.get('data')
        if not isinstance(data, dict) or 'email' not in data:
            return _error('invalid_response')
        verification = data.get('verification') if isinstance(data.get('verification'), dict) else {}
        status = _verification_status(verification.get('status'))
        score = data.get('score') if type(data.get('score')) is int and 0 <= data['score'] <= 100 else None
        first, last = clean_text(data.get('first_name'), 60), clean_text(data.get('last_name'), 60)
        flags = _risks(data)
        if data.get('accept_all') is True:
            flags.append('catch_all')
        if data.get('type') == 'generic':
            flags.append('role')
        email = _revealed_email(data.get('email'))
        prospect = {'full_name': clean_text(data.get('full_name'), 120) or (' '.join(p for p in (first, last) if p) or None),
                    'first_name': first, 'last_name': last, 'name_complete': bool(first and last),
                    'title': clean_text(data.get('position'), 150), 'company': clean_text(data.get('company'), 200),
                    'company_website': _company_domain(data.get('website_url')),
                    'linkedin_url': linkedin_from_handle(data.get('linkedin')), 'email': email,
                    'email_confidence': score, 'email_verification': status, 'source': 'tomba', 'source_url': None}
        fields = {'score': score, 'verification_is_fresh': False, 'prospect': prospect, **_informational_fields(data)}
        if status:
            fields['finder_verification_status'] = status
        if email:
            return Outcome('candidate', email, tuple(dict.fromkeys(flags)), fields)
        if data['email'] is None:
            return Outcome('not_found', fields=fields)
        return _error('invalid_response')


_ICYPEAS_QUEUED = ('NONE', 'SCHEDULED', 'IN_PROGRESS')
_ICYPEAS_CERTAINTY = ('ultra_sure', 'very_sure', 'probable')


class Icypeas(_NoBalance):
    """Icypeas single email discovery: asynchronous submit, then a read-only result POST.

    Docs (verified 2026-10): https://api-doc.icypeas.com/find-emails/email-discovery -
    POST https://app.icypeas.com/api/email-search {firstname, lastname, domainOrCompany}
    -> {success, item{_id, status 'NONE'}}; https://api-doc.icypeas.com/fetch-results/search-item -
    POST /api/bulk-single-searchs/read {id} -> {success, items[{status, results{firstname,
    lastname, fullname, emails[{email, certainty}]}}]}. Statuses NONE/SCHEDULED/IN_PROGRESS
    are still queued; DEBITED/FOUND, NOT_FOUND/DEBITED_NOT_FOUND, BAD_INPUT,
    INSUFFICIENT_FUNDS, ABORTED are terminal (/how-works/search_statuses). Certainties
    ultra_sure/very_sure/probable (/how-works/certainties). Auth: Authorization header holding
    only the API key (getting-started; changelog 1.4.0). Credits: 1 per found email
    (/how-works/credit-cost); the read route is free and limited to 30 calls/min.

    The submit returns 'pending' with a same-origin job pointer; finish it with the existing
    Runner.resume (supports_pending=True, poll_methods=('POST',) because the documented read
    route is a non-mutating POST). As for Verifalia, a pending attempt blocks new reservations
    until it is resumed to completion. A '/api/sync/email-search' route appears only in
    marketing copy, not in the API reference, so it is not used.
    """
    id = 'icypeas'
    independence_group = 'icypeas'
    operations = ('find',)
    auth_env = ('ICYPEAS_API_KEY',)
    min_spacing = {'find': 0.2, 'poll': 2.5}
    origin = 'https://app.icypeas.com'
    supports_pending = True
    poll_methods = ('POST',)
    READ_PATH = '/api/bulk-single-searchs/read'

    def _headers(self, env):
        return {'Authorization': _secret(env, 'ICYPEAS_API_KEY'), 'Content-Type': 'application/json'}

    def request(self, operation, payload, env):
        self._operation(operation)
        first, last = _first_last(payload)
        body = {'firstname': first, 'lastname': last, 'domainOrCompany': _finder_domain(payload)}
        return self._request('POST', '/api/email-search', self._headers(env), body)

    def _job_id(self, url):
        match = re.fullmatch(re.escape(self.origin + self.READ_PATH) + r'\?id=([A-Za-z0-9_-]{1,128})', url) if isinstance(url, str) else None
        if not match:
            raise PolicyError('unsafe_job_url')
        return match.group(1)

    def poll_request(self, job_url, env):
        """Read the stored result for the submitted search; never resubmits the search."""
        return self._request('POST', self.READ_PATH, self._headers(env), {'id': self._job_id(job_url)})

    def parse(self, operation, response):
        self._operation(operation)
        failure = http_failure(response)
        if failure:
            return failure
        j = response.data
        if j.get('success') is not True or 'validationErrors' in j:
            return _error('provider_application_error')
        if 'item' in j:
            item = j['item'] if isinstance(j['item'], dict) else {}
            ident = item.get('_id')
            if not isinstance(ident, str) or not _SEARCH_ID.fullmatch(ident) or item.get('status') not in _ICYPEAS_QUEUED:
                return _error('invalid_response')
            return Outcome('pending', job_url=self.origin + self.READ_PATH + '?id=' + ident)
        items = j.get('items')
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            return _error('invalid_response')
        item = items[0]
        status = item.get('status')
        if status in _ICYPEAS_QUEUED:
            return Outcome('pending')
        if status == 'INSUFFICIENT_FUNDS':
            return _error('quota')
        if status in ('NOT_FOUND', 'DEBITED_NOT_FOUND', 'BAD_INPUT'):
            return Outcome('not_found', fields={'status': status})
        if status not in ('FOUND', 'DEBITED'):
            return _error('provider_application_error' if status == 'ABORTED' else 'invalid_response')
        results = item.get('results') if isinstance(item.get('results'), dict) else {}
        best = None
        for entry in results.get('emails') or []:
            if not isinstance(entry, dict) or not isinstance(entry.get('certainty'), str):
                continue
            certainty = entry['certainty'].lower()
            email = _revealed_email(entry.get('email'))
            if email and certainty in _ICYPEAS_CERTAINTY and (
                    best is None or _ICYPEAS_CERTAINTY.index(certainty) < _ICYPEAS_CERTAINTY.index(best[1])):
                best = (email, certainty)
        first, last = clean_text(results.get('firstname'), 60), clean_text(results.get('lastname'), 60)
        prospect = {'full_name': clean_text(results.get('fullname'), 120) or (' '.join(p for p in (first, last) if p) or None),
                    'first_name': first, 'last_name': last, 'name_complete': bool(first and last),
                    'email': best[0] if best else None, 'email_certainty': best[1] if best else None,
                    'source': 'icypeas', 'source_url': None}
        fields = {'status': status, 'verification_is_fresh': False, 'prospect': prospect}
        if not best:
            return Outcome('not_found', fields=fields)
        return Outcome('candidate', best[0], fields={**fields, 'certainty': best[1]})


class Reoon(_NoBalance):
    """Reoon Email Verifier single verification in POWER mode.

    Docs (verified 2026-10): https://www.reoon.com/articles/api-documentation-of-reoon-email-verifier/ -
    GET https://emailverifier.reoon.com/api/v1/verify?email=&key=&mode=power -> {email, status
    (safe|invalid|disabled|disposable|inbox_full|catch_all|role_account|spamtrap|unknown),
    overall_score, is_safe_to_send, is_deliverable, is_catch_all, is_disposable,
    is_role_account, is_spamtrap, has_inbox_full, is_disabled, is_free_email, mx_accepts_mail,
    can_connect_smtp, verification_mode}. QUICK mode never checks inboxes, so only a power-mode
    'safe' counts as provider_valid. Power mode can take over a minute (timeout 90s); the
    docs ask for <= 5 parallel threads, and this runner is serial (1s spacing).
    """
    id = 'reoon'
    independence_group = 'reoon'
    operations = ('verify',)
    auth_env = ('REOON_API_KEY',)
    min_spacing = {'verify': 1.0}
    origin = 'https://emailverifier.reoon.com'
    MAPPING = {'safe': 'provider_valid', 'invalid': 'provider_invalid', 'disabled': 'provider_invalid',
               'catch_all': 'catch_all', 'unknown': 'unknown', 'inbox_full': 'unknown', 'spamtrap': 'unknown',
               'disposable': 'unknown', 'role_account': 'unknown', 'valid': 'unknown'}
    FLAGS = {'is_disposable': 'disposable', 'is_role_account': 'role_account', 'is_spamtrap': 'spamtrap',
             'is_catch_all': 'catch_all', 'has_inbox_full': 'inbox_full', 'is_disabled': 'disabled'}

    def request(self, operation, payload, env):
        self._operation(operation)
        query = {'email': _text(payload, 'email'), 'key': _secret(env, 'REOON_API_KEY'), 'mode': 'power'}
        return self._request('GET', '/api/v1/verify?' + urlencode(query), timeout=90)

    def parse(self, operation, response):
        self._operation(operation)
        failure = http_failure(response)
        if failure:
            return failure
        j = response.data
        status = j.get('status')
        if status == 'error' or 'reason' in j:
            return _error('provider_application_error')
        if not isinstance(status, str) or status not in self.MAPPING:
            return _error('invalid_response')
        email = _email(j.get('email'))
        if not email:
            return _error('invalid_response')
        assessment = self.MAPPING[status]
        flags = [flag for key, flag in self.FLAGS.items() if j.get(key) is True]
        if status in ('inbox_full', 'spamtrap', 'disposable', 'role_account', 'catch_all', 'disabled'):
            flags.append(status)
        power = j.get('verification_mode') == 'power'
        if not power:
            flags.append('quick_mode_only')
        if assessment == 'provider_valid' and (not power or j.get('is_safe_to_send') is False):
            assessment = 'unknown'
        fields = {'status': status, 'verification_mode': j.get('verification_mode') if j.get('verification_mode') in ('power', 'quick') else None}
        score = j.get('overall_score')
        if type(score) is int and 0 <= score <= 100:
            fields['overall_score'] = score
        for key in ('is_safe_to_send', 'is_deliverable', 'can_connect_smtp', 'mx_accepts_mail', 'is_valid_syntax'):
            if type(j.get(key)) is bool:
                fields[key] = j[key]
        if type(j.get('is_free_email')) is bool:
            fields['free'] = j['is_free_email']
        return Outcome(assessment, email, tuple(dict.fromkeys(flags)), fields)


_MV_RESULTS = {'ok': ('provider_valid', 1), 'catch_all': ('catch_all', 2), 'unknown': ('unknown', 3),
               'disposable': ('unknown', 5), 'invalid': ('provider_invalid', 6)}
_MV_SUBRESULTS = frozenset({'unknown', 'ok', 'internal_error', 'invalid_syntax', 'no_local_ip_available', 'dns_server_failed',
                            'dns_no_mx', 'dns_no_a', 'could_not_connect', 'no_code_in_banner', 'invalid_banner_code',
                            'no_code_in_ehlo_response', 'no_code_in_helo_response', 'no_code_in_mail_from_response',
                            'no_code_in_rcpt_to_response', 'ip_blocked', 'no_mailbox', 'mailbox_disabled', 'mailbox_full',
                            'greylisted', 'connection_lost', 'connection_timeout', 'connection_refused',
                            'connection_reset_by_peer', 'connection_no_route_to_host', 'host_not_accept_incoming_mail',
                            'mail_service_unavailable', 'bad_domain', 'dns_error', 'anti_spam_system', 'dns_no_domain',
                            'dns_refused', 'timeout_error'})


class MillionVerifier(_NoBalance):
    """MillionVerifier Single API (real-time verification).

    Docs (verified 2026-10): https://developer.millionverifier.com/ (OpenAPI 3.1.0) -
    GET https://api.millionverifier.com/api/v3/?api=KEY&email=&timeout=2..60 -> {email,
    quality ('', good, bad, risky), result (ok|catch_all|unknown|error|disposable|invalid),
    resultcode 1-6, subresult, free, role, didyoumean, credits, executiontime, error,
    livemode}. Bad keys / missing email come back as 200 with result 'error' and an error
    message; demo keys report livemode false and return random results, so they are refused.
    """
    id = 'millionverifier'
    independence_group = 'millionverifier'
    operations = ('verify',)
    auth_env = ('MILLIONVERIFIER_API_KEY',)
    min_spacing = {'verify': 1.0}
    origin = 'https://api.millionverifier.com'

    def request(self, operation, payload, env):
        self._operation(operation)
        query = {'api': _secret(env, 'MILLIONVERIFIER_API_KEY'), 'email': _text(payload, 'email'), 'timeout': 10}
        return self._request('GET', '/api/v3/?' + urlencode(query), timeout=30)

    def parse(self, operation, response):
        self._operation(operation)
        j = response.data if response.status == 200 and response.json_valid and isinstance(response.data, dict) else None
        if j is not None and (j.get('result') == 'error' or (isinstance(j.get('error'), str) and j['error'].strip())):
            text = str(j.get('error') or '').lower()
            return _error('auth' if 'apikey' in text or 'api key' in text else 'quota' if 'credit' in text
                          else 'provider_application_error')
        failure = http_failure(response)
        if failure:
            return failure
        if j.get('livemode') is False:
            return _error('provider_application_error')
        result = j.get('result')
        if not isinstance(result, str) or result not in _MV_RESULTS:
            return _error('invalid_response')
        assessment, code = _MV_RESULTS[result]
        if 'resultcode' in j and j['resultcode'] != code:
            return _error('invalid_response')
        email = _email(j.get('email'))
        if not email:
            return _error('invalid_response')
        flags = _risks(j)
        if result in ('disposable', 'catch_all'):
            flags.append(result)
        quality = j.get('quality') if j.get('quality') in ('', 'good', 'bad', 'risky') else None
        if quality in ('bad', 'risky'):
            flags.append('quality_' + quality)
        fields = {'result': result, 'quality': quality, **_informational_fields(j)}
        if j.get('subresult') in _MV_SUBRESULTS:
            fields['subresult'] = j['subresult']
        if type(j.get('role')) is bool:
            fields['role'] = j['role']
        return Outcome(assessment, email, tuple(dict.fromkeys(flags)), fields)


PROVIDERS = {provider.id: provider for provider in (Hunter(), Verifalia(), Clearout(), ZeroBounce(), HunterInsight(), Disify(),
                                                    Serper(), Brave(), Overpass(), Apollo(), Prospeo(), GooglePlaces(),
                                                    Exa(), Tomba(), Icypeas(), Reoon(), MillionVerifier())}


def get_provider(provider_id):
    """Return a configured stateless adapter or a public-safe unsupported error."""
    try:
        return PROVIDERS[provider_id]
    except (KeyError, TypeError):
        raise PolicyError('unsupported_provider') from None
