"""Explicit provider adapters: finding, verifying, and signals are not interchangeable.

Only request objects contain credentials. Parsers retain selected structured facts,
never raw API errors, arbitrary server text, URLs with credentials, or account data.
Balances report proven free pools separately from unclassified total balances.
"""
import base64
import json
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urlencode

from .models import Outcome, PolicyError, ProviderError, Request
from .transport import validate_url


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

    def _request(self, method, path, headers=None, body=None, timeout=30):
        url = self.origin + path
        validate_url(url, {self.origin})
        return Request(method, url, headers or {},
                       json.dumps(body, separators=(',', ':')).encode() if body is not None else None, timeout)

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
    operations = ('find', 'verify')
    auth_env = ('HUNTER_API_KEY',)
    min_spacing = {'find': 10.0, 'verify': 10.0, 'balance': 10.0}
    origin = 'https://api.hunter.io'

    def _headers(self, env):
        return {'X-API-KEY': _secret(env, 'HUNTER_API_KEY')}

    def request(self, operation, payload, env):
        """Finder produces candidates; only verifier supplies mailbox assessment."""
        self._operation(operation)
        query = ({'domain': _text(payload, 'domain'), 'full_name': _text(payload, 'full_name' if 'full_name' in payload else 'name')}
                 if operation == 'find' else {'email': _text(payload, 'email')})
        route = 'email-finder' if operation == 'find' else 'email-verifier'
        return self._request('GET', '/v2/' + route + '?' + urlencode(query), self._headers(env))

    def parse(self, operation, response):
        self._operation(operation)
        failure = http_failure(response)
        if failure:
            return failure
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


PROVIDERS = {provider.id: provider for provider in (Hunter(), Verifalia(), Clearout(), ZeroBounce(), HunterInsight(), Disify())}


def get_provider(provider_id):
    """Return a configured stateless adapter or a public-safe unsupported error."""
    try:
        return PROVIDERS[provider_id]
    except (KeyError, TypeError):
        raise PolicyError('unsupported_provider') from None
