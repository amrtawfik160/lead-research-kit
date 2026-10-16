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

from .discovery import clean_text, linkedin_from_handle, parse_linkedin_result, safe_link
from .models import Outcome, PolicyError, ProviderError, Request
from .policy import (HUNTER_DEPARTMENTS, HUNTER_SENIORITIES, OSM_DEFAULT_TAGS, OSM_TAG_ALLOWLIST, US_STATES,
                     domain_search_options, normalize_discover_filters, normalize_domain, normalize_email,
                     overpass_area, search_query, us_state_code, validate_evidence_url)
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


def _company_domain(value):
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
    if any(host == s or host.endswith('.' + s) for s in _SOCIAL_HOSTS):
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


PROVIDERS = {provider.id: provider for provider in (Hunter(), Verifalia(), Clearout(), ZeroBounce(), HunterInsight(), Disify(),
                                                    Serper(), Brave(), Overpass())}


def get_provider(provider_id):
    """Return a configured stateless adapter or a public-safe unsupported error."""
    try:
        return PROVIDERS[provider_id]
    except (KeyError, TypeError):
        raise PolicyError('unsupported_provider') from None
