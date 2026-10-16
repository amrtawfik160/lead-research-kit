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

from .discovery import (FIRECRAWL_MAX_HTML, canonical_linkedin, clean_text, encode_html, linkedin_from_handle,
                        parse_linkedin_result, safe_link)
from .models import Outcome, PolicyError, ProviderError, Request
from .policy import (APOLLO_SENIORITIES, HUNTER_DEPARTMENTS, HUNTER_SENIORITIES, OSM_DEFAULT_TAGS, OSM_TAG_ALLOWLIST,
                     US_STATES, apollo_criteria, domain_search_options, enrich_identity, linkedin_profile_url,
                     batch_queries, normalize_discover_filters, normalize_domain, normalize_email, overpass_area,
                     prospeo_criteria, search_query, strict_payload, us_state_code, validate_evidence_url,
                     COMMON_PAYLOAD_KEYS, person_identity, scrape_url, search_domains)
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


def _account_counter(entry, kind):
    """Remaining units of one /v2/account requests entry: 'remaining', else available - used."""
    if not isinstance(entry, dict):
        return None
    convert = _int if kind == 'int' else _decimal
    remaining = convert(entry.get('remaining'))
    if remaining is not None:
        return Decimal(remaining)
    used, available = convert(entry.get('used')), convert(entry.get('available'))
    if used is None or available is None:
        return None
    return max(Decimal(available) - Decimal(used), Decimal(0))


def hunter_free_pools_from_account(data):
    """Opt-in: Hunter Free-plan monthly allowances as ledger pools {'find_ops', 'verify_ops', 'credits'}.

    Only for an operator who explicitly accepts the Free plan's monthly searches/verifications as
    free budget (CLI: budget sync hunter --execute --accept-free-plan). ``data`` is the /v2/account
    JSON (with or without the outer 'data' key). Source: https://hunter.io/api-documentation/v2#account
    - each requests entry has used/available/remaining; 'remaining' is authoritative and
    available - used (floored at 0) is the fallback. With a single credits pool, searches and
    verifications are views of that pool; every Hunter charge also debits 'credits', so the credits
    pool caps the total. Without one, 'credits' is derived as searches + 0.5 x verifications (the
    documented per-request costs), which never binds tighter than the two separate pools.
    Raises ProviderError('not_free_plan') unless plan_name is exactly 'Free'.
    Returned amounts are decimal strings for Store.confirm_budget.
    """
    j = data.get('data') if isinstance(data, dict) and isinstance(data.get('data'), dict) else data
    if not isinstance(j, dict):
        raise ProviderError('invalid_response')
    if j.get('plan_name') != 'Free':
        raise ProviderError('not_free_plan')
    requests = j.get('requests')
    if not isinstance(requests, dict):
        raise ProviderError('invalid_response')
    pools = {}
    searches = _account_counter(requests.get('searches'), 'int')
    verifications = _account_counter(requests.get('verifications'), 'int')
    credits = _account_counter(requests.get('credits'), 'decimal')
    if searches is not None:
        pools['find_ops'] = searches
    if verifications is not None:
        pools['verify_ops'] = verifications
    if credits is None and searches is not None and verifications is not None:
        credits = searches + verifications / 2
    if credits is not None:
        pools['credits'] = credits
    if not pools:
        raise ProviderError('invalid_response')
    return {k: format(v.normalize(), 'f') if v else '0' for k, v in pools.items()}


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

MAX_BATCH_ITEMS = 100
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


def _serper_item(j):
    """One Serper /search response object -> (results, prospects) or None when it is an error."""
    if not isinstance(j, dict) or 'message' in j or 'statusCode' in j or j.get('error'):
        return None
    organic = j.get('organic')
    if organic is None and isinstance(j.get('searchParameters'), dict):
        organic = []
    if not isinstance(organic, list):
        return None
    return _serp_rows(organic, 'title', 'link', 'snippet')


def _same_query(a, b):
    return ' '.join(str(a).casefold().split()) == ' '.join(str(b).casefold().split())


class Serper(Provider):
    """Google results via serper.dev. num is fixed at 10 because num > 10 bills 2 credits.

    'search_batch' uses Serper's mini-batch mode: the POST body is a JSON array of up to 100
    query objects and the response is an array of normal search responses in the same order,
    each billed as one query (serper.dev Playground 'Mini-batch (up to 100 queries)'). Failed
    items come back inside an HTTP 200 array as error objects, so each item is checked.
    """
    id = 'serper'
    independence_group = 'serper'
    operations = ('search', 'search_batch')
    auth_env = ('SERPER_API_KEY',)
    # Serper plans allow 50+ queries/second; one request per 0.1s stays far below that.
    min_spacing = {'search': 0.1, 'search_batch': 0.1}
    # The Runner multiplies the fixed per-query charge by len(payload['queries']).
    per_item_charge = {'search_batch': 'queries'}
    origin = 'https://google.serper.dev'

    def _headers(self, env):
        return {'X-API-KEY': _secret(env, 'SERPER_API_KEY'), 'Content-Type': 'application/json'}

    def request(self, operation, payload, env):
        self._operation(operation)
        if operation == 'search_batch':
            payload = strict_payload('search_batch', payload)
            queries = payload.get('queries')
            if not isinstance(queries, list):
                raise PolicyError('invalid_queries')
            if len(queries) != len(set(queries)) or queries != batch_queries(queries):
                raise PolicyError('invalid_queries')
            body = [{'q': q, 'num': 10, 'gl': 'us', 'hl': 'en'} for q in queries]
            return self._request('POST', '/search', self._headers(env), body, timeout=90,
                                 max_response_bytes=MAX_RESPONSE_CEILING)
        body = {'q': search_query(payload.get('query')), 'num': 10, 'gl': 'us', 'hl': 'en'}
        if payload.get('num', 10) != 10 or any(k in payload for k in ('count', 'offset')):
            raise PolicyError('invalid_payload')
        page = payload.get('page', 1)
        if type(page) is not int or not 1 <= page <= 10:
            raise PolicyError('invalid_payload')
        if page != 1:
            body['page'] = page
        return self._request('POST', '/search', self._headers(env), body)

    def parse(self, operation, response):
        self._operation(operation)
        if operation == 'search_batch':
            return self._parse_batch(response)
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

    def _parse_batch(self, response):
        if response.json_valid and isinstance(response.data, dict) and 200 <= response.status < 300:
            return _error('provider_application_error')  # a whole-request error object, not an array
        if isinstance(response.data, list) or not response.json_valid:
            status = response.status
            code = {401: 'auth', 403: 'auth', 402: 'quota', 429: 'rate_limit'}.get(status)
            if code:
                return _error(code)
            if status >= 500:
                return _error('provider_error')
            if status < 200 or status >= 300:
                return _error('http_error')
            if not response.json_valid:
                return _error('invalid_response')
        else:
            failure = http_failure(response)
            return failure or _error('invalid_response')
        items = response.data
        if not items or len(items) > MAX_BATCH_ITEMS:
            return _error('invalid_response')
        batches = []
        for item in items:
            parsed = _serper_item(item)
            params = item.get('searchParameters') if isinstance(item, dict) else None
            echo = clean_text(params.get('q'), 400) if isinstance(params, dict) else None
            if parsed is None:
                batches.append({'query': echo, 'results': [], 'prospects': [], 'error': 'provider_application_error'})
            else:
                batches.append({'query': echo, 'results': parsed[0], 'prospects': parsed[1]})
        if all(b.get('error') for b in batches):
            return _error('provider_application_error')
        return Outcome('results' if any(b['results'] for b in batches) else 'not_found', fields={'batches': batches})

    def scope_outcome(self, operation, outcome, payload):
        """Key each batch entry by the query that was sent (response order = request order)."""
        if operation != 'search_batch':
            return outcome
        queries = payload.get('queries') or []
        batches = outcome.fields.get('batches') or []
        if len(batches) != len(queries):
            return _error('invalid_response')
        scoped = []
        for query, entry in zip(queries, batches):
            entry = dict(entry)
            echo = entry.get('query')
            if echo and not _same_query(echo, query) and not entry.get('error'):
                entry.update(results=[], prospects=[], error='query_mismatch')
            entry['query'] = query
            scoped.append(entry)
        assessment = 'results' if any(b['results'] for b in scoped) else 'not_found'
        return dataclasses.replace(outcome, assessment=assessment, fields={**outcome.fields, 'batches': scoped})

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
    operations = ('search', 'company_search')
    auth_env = ('EXA_API_KEY',)
    min_spacing = {'search': 0.25, 'company_search': 0.25}
    origin = 'https://api.exa.ai'

    def request(self, operation, payload, env):
        self._operation(operation)
        if operation == 'company_search':
            # category 'company' returns company pages (docs: 'people' and 'company' return
            # higher-quality profiles/pages). Fixed numResults 10 = one ledger query.
            clean = strict_payload('company_search', payload)
            body = {'query': search_query(clean.get('query')), 'type': 'auto', 'category': 'company',
                    'numResults': 10, 'userLocation': 'US'}
            headers = {'x-api-key': _secret(env, 'EXA_API_KEY'), 'Content-Type': 'application/json'}
            return self._request('POST', '/search', headers, body)
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
        if operation == 'company_search':
            # Generic {title, url, snippet, position} rows for resolve.pick_domain; no prospects.
            results = []
            for position, item in enumerate(items[:10], 1):
                if not isinstance(item, dict):
                    continue
                url = safe_link(item.get('url'))
                if not url:
                    continue
                highlights = item.get('highlights')
                snippet = item.get('text') if isinstance(item.get('text'), str) else (
                    ' · '.join(h for h in highlights if isinstance(h, str)) if isinstance(highlights, list) else None)
                results.append({'title': clean_text(item.get('title'), 300), 'url': url,
                                'snippet': clean_text(snippet, 500), 'position': position})
            return Outcome('results' if results else 'not_found', fields={'results': results})
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


_DEEPLINE_RUN_ID = re.compile(r'[A-Za-z0-9_-]{1,128}')
_DEEPLINE_QUEUED = ('queued', 'running', 'waiting')
_DEEPLINE_FINDER_STATUS = {'valid': 'valid', 'valid_catch_all': 'accept_all'}
_DEEPLINE_FINDER_STATUSES = ('valid', 'valid_catch_all', 'not_found')
_DEEPLINE_QUOTA_HINTS = ('credit', 'balance', 'insufficient', 'payment', 'limit_exceeded', 'spend')


def _deepline_finder_data(j):
    """The LeadMagic provider payload {email, status, ...} inside a Deepline execute envelope.

    Documented locations: SDK result.toolResponse.raw / toolResponse.rawV2 ({data:{email,status}});
    the tool contract's 'result.data'. Only a payload whose status is a documented finder status
    is accepted, so an unrelated nested object can never be mistaken for a result.
    """
    tool = j.get('toolResponse') if isinstance(j.get('toolResponse'), dict) else {}
    for container in (tool.get('rawV2'), tool.get('raw'), j.get('result'), j):
        if not isinstance(container, dict):
            continue
        inner = container.get('data') if isinstance(container.get('data'), dict) else {}
        for data in (inner.get('data'), inner, container):
            if isinstance(data, dict) and data.get('status') in _DEEPLINE_FINDER_STATUSES:
                return data
    return None


def _deepline_play_email(value, depth=0):
    """(email, facts) from a completed waterfall Play output; the output schema is live-described only.

    Accepts the email shapes the official Deepline skills read from this Play: a bare string, or
    {email} / {work_email} / {result: ...} objects, nested at most three levels.
    """
    if depth > 3:
        return None, {}
    if isinstance(value, str):
        email = _revealed_email(value.strip())
        return (email, {}) if email else (None, {})
    if not isinstance(value, dict):
        return None, {}
    facts = {}
    for key in ('email_validated', 'validated'):
        if type(value.get(key)) is bool:
            facts['email_validated'] = value[key]
    for key in ('email_status', 'validation_status', 'status'):
        if isinstance(value.get(key), str) and re.fullmatch(r'[a-z_]{2,32}', value[key].strip().lower()):
            facts.setdefault('email_status', value[key].strip().lower())
    for key in ('provider', 'source', 'email_provider', 'matched_provider', 'winning_provider'):
        if isinstance(value.get(key), str) and re.fullmatch(r'[A-Za-z0-9_. -]{2,40}', value[key]):
            facts.setdefault('waterfall_provider', value[key].strip().lower())
    for key in ('email', 'work_email', 'professional_email', 'result', 'output', 'em'):
        if key in value:
            email, inner = _deepline_play_email(value[key], depth + 1)
            if email:
                return email, {**inner, **facts}
    return None, facts


class Deepline(Provider):
    """Deepline managed-credit work-email finding (name + company domain -> email).

    Docs (verified 2026-10): OpenAPI https://deepline.com/openapi.json (server
    https://code.deepline.com, bearer workspace API key), Runtime API reference
    https://deepline.com/docs/sdk-v2/api-reference, tool card
    https://code.deepline.com/tools/leadmagic_email_finder, Prebuilt Plays
    https://deepline.com/docs/plays/tasks-overview.

    Default route (synchronous): POST /api/v2/integrations/leadmagic_email_finder/execute
    {payload: {first_name, last_name, domain}} -> envelope whose provider payload is
    {data: {email, status valid|valid_catch_all|not_found, ...}} (SDK: toolResponse.raw /
    toolResponse.rawV2; tool contract: result.data). Pricing model per_result, about 0.34
    Deepline credits per found email.

    Waterfall route (env DEEPLINE_FIND_ROUTE=waterfall): POST /api/v2/plays/run
    {name: 'prebuilt/name-and-domain-to-email-waterfall', input: {first_name, last_name, domain}}
    -> {workflowId, status} (OpenAPI PlayRunPackage: run.id / run.status) -> 'pending' with job
    URL exactly https://code.deepline.com/api/v2/runs/<id>; Runner.resume GETs it until
    run.status is completed/failed/cancelled (queued/running/waiting are non-terminal). Output is
    read from 'result' or package 'outputs'. Billing follows each executed waterfall stage
    (about 0.14-0.5 Deepline credits per find; misses can still bill when providers ran).

    Local charge is {'credits': '1'} per find request, a find counter; the workspace balance
    (GET /api/v2/billing/balance) is Deepline credits and is reported as metadata only.
    Errors are ErrorResponse {error: string | {code, message, resolution}}; 402 (and
    credit/balance error codes) map to 'quota'.
    """
    id = 'deepline'
    independence_group = 'deepline'
    operations = ('find',)
    auth_env = ('DEEPLINE_API_KEY',)
    min_spacing = {'find': 1.0, 'poll': 3.0, 'balance': 1.0}
    origin = 'https://code.deepline.com'
    supports_pending = True
    poll_methods = ('GET',)
    parses_client_errors = True
    FINDER_TOOL = 'leadmagic_email_finder'
    WATERFALL_PLAY = 'prebuilt/name-and-domain-to-email-waterfall'
    RUNS_PATH = '/api/v2/runs/'

    def _headers(self, env, body=False):
        headers = {'Authorization': 'Bearer ' + _secret(env, 'DEEPLINE_API_KEY'), 'Accept': 'application/json'}
        if body:
            headers['Content-Type'] = 'application/json'
        return headers

    @staticmethod
    def route(env):
        value = env.get('DEEPLINE_FIND_ROUTE')
        if value in (None, '', 'finder'):
            return 'finder'
        if value == 'waterfall':
            return 'waterfall'
        raise PolicyError('invalid_deepline_route')

    def request(self, operation, payload, env):
        self._operation(operation)
        first, last = _first_last(payload)
        person = {'first_name': first, 'last_name': last, 'domain': _finder_domain(payload)}
        headers = self._headers(env, body=True)
        if self.route(env) == 'waterfall':
            return self._request('POST', '/api/v2/plays/run', headers, {'name': self.WATERFALL_PLAY, 'input': person}, timeout=60)
        return self._request('POST', '/api/v2/integrations/' + self.FINDER_TOOL + '/execute', headers,
                             {'payload': person}, timeout=90)

    def _run_id(self, url):
        match = re.fullmatch(re.escape(self.origin + self.RUNS_PATH) + r'([A-Za-z0-9_-]{1,128})', url) if isinstance(url, str) else None
        if not match:
            raise PolicyError('unsafe_job_url')
        return match.group(1)

    def poll_request(self, job_url, env):
        """Read the run status; never starts or resubmits a run."""
        return self._request('GET', self.RUNS_PATH + self._run_id(job_url), self._headers(env), timeout=30)

    def balance_request(self, env):
        return self._request('GET', '/api/v2/billing/balance', self._headers(env))

    def parse_balance(self, response):
        j = _balance_base(response)
        total = _decimal(j.get('balance'))
        if total is None:
            raise ProviderError('invalid_response')
        metadata = {'deepline_credits': total, 'manual_free_attestation_required': True}
        usd = _decimal(j.get('rough_usd_balance'))
        if usd is not None:
            metadata['rough_usd_balance'] = usd
        # Deepline credits are fractional per find; they never become a local per-find pool.
        return {'free_pools': {}, 'metadata': metadata}

    def _error_body(self, response, default):
        """Map an ErrorResponse by HTTP status, then by its machine code (never kept as text)."""
        j = response.data if response.json_valid and isinstance(response.data, dict) else {}
        err = j.get('error')
        code = err.get('code') if isinstance(err, dict) else err if isinstance(err, str) else ''
        text = str(code or '').lower()
        if response.status == 402 or any(h in text for h in _DEEPLINE_QUOTA_HINTS):
            return _error('quota')
        if response.status in (401, 403) or 'unauthori' in text or 'api_key' in text or 'forbidden' in text:
            return _error('auth')
        if response.status == 429 or 'rate_limit' in text:
            return _error('rate_limit')
        return _error(default)

    def _run(self, j):
        run = j.get('run') if isinstance(j.get('run'), dict) else {}
        ident = j.get('workflowId', run.get('id'))
        status = run.get('status', j.get('status'))
        return ident, status

    def parse(self, operation, response):
        self._operation(operation)
        if 400 <= response.status < 500:
            return self._error_body(response, 'http_error')
        failure = http_failure(response)
        if failure:
            if failure.error == 'provider_application_error':
                return self._error_body(response, 'provider_application_error')
            return failure
        j = response.data
        if 'workflowId' in j or j.get('kind') == 'play_run' or isinstance(j.get('run'), dict):
            return self._parse_run(j)
        data = _deepline_finder_data(j)
        if data is None:
            return _error('invalid_response')
        status = data['status']
        fields = {'status': status, 'route': self.FINDER_TOOL, 'verification_is_fresh': False}
        if type(data.get('is_domain_catch_all')) is bool:
            fields['domain_catch_all'] = data['is_domain_catch_all']
        if status == 'not_found':
            return Outcome('not_found', fields=fields)
        email = _revealed_email(data.get('email'))
        if not email:
            return _error('invalid_response')
        flags = ['catch_all'] if status == 'valid_catch_all' or data.get('is_domain_catch_all') is True else []
        first, last = clean_text(data.get('first_name'), 60), clean_text(data.get('last_name'), 60)
        prospect = {'full_name': ' '.join(p for p in (first, last) if p) or None, 'first_name': first, 'last_name': last,
                    'name_complete': bool(first and last), 'email': email, 'source': 'deepline', 'source_url': None}
        fields.update(finder_verification_status=_DEEPLINE_FINDER_STATUS[status], provider='leadmagic', prospect=prospect)
        return Outcome('candidate', email, tuple(flags), fields)

    def _parse_run(self, j):
        ident, status = self._run(j)
        if status in _DEEPLINE_QUEUED:
            if ident is None and j.get('kind') == 'play_run':
                return Outcome('pending')  # a poll that is still running keeps its job pointer
            if not isinstance(ident, str) or not _DEEPLINE_RUN_ID.fullmatch(ident):
                return _error('invalid_response')
            return Outcome('pending', job_url=self.origin + self.RUNS_PATH + ident)
        if status in ('failed', 'cancelled'):
            return _error('provider_application_error')
        if status != 'completed':
            return _error('invalid_response')
        fields = {'status': 'completed', 'route': self.WATERFALL_PLAY, 'verification_is_fresh': False}
        email, facts = None, {}
        for key in ('result', 'outputs', 'output'):
            if key in j:
                email, facts = _deepline_play_email(j[key])
                if email:
                    break
        if facts.get('waterfall_provider'):
            fields['provider'] = facts['waterfall_provider']
        if 'email_validated' in facts:
            fields['email_validated'] = facts['email_validated']
        status_text = facts.get('email_status')
        if status_text:
            fields['email_status'] = status_text
        if not email:
            return Outcome('not_found', fields=fields)
        flags = []
        if status_text in ('catch_all', 'valid_catch_all', 'accept_all'):
            flags.append('catch_all')
            fields['finder_verification_status'] = 'accept_all'
        elif status_text == 'valid' or facts.get('email_validated') is True:
            fields['finder_verification_status'] = 'valid'
        prospect = {'email': email, 'source': 'deepline', 'source_url': None}
        return Outcome('candidate', email, tuple(flags), {**fields, 'prospect': prospect})


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


# ---------------------------------------------------------------------------
# v4 free-tier adapters: Tavily, SerpApi and Firecrawl (search / scrape), People Data
# Labs (enrichment), and the Abstract, Emailable, Kickbox, Bouncer and MyEmailVerifier
# single-address verifiers. All start disabled with zero budgets (configuration.py).
# ---------------------------------------------------------------------------



def _search_payload(payload, optional):
    """Adapter-side guard for one 'search' request: a query plus only the listed optional keys."""
    clean = strict_payload('search', payload)
    if 'query' not in clean or set(clean) - {'query'} - COMMON_PAYLOAD_KEYS - set(optional):
        raise PolicyError('invalid_payload')
    return clean


def _yes(value):
    """True / False for documented boolean-ish flags (true/false, 'true'/'false', 'yes'/'no'), else None."""
    if type(value) is bool:
        return value
    if isinstance(value, str) and value.strip().lower() in ('true', 'yes'):
        return True
    if isinstance(value, str) and value.strip().lower() in ('false', 'no'):
        return False
    return None


def _live_key(env, key):
    """Sandbox keys (test_...) return simulated verdicts, so they never reach the ledger."""
    value = _secret(env, key)
    if value.strip().lower().startswith('test_'):
        raise PolicyError('test_api_key_refused')
    return value


def _message_error(text):
    text = str(text or '').lower()
    if 'api key' in text or 'apikey' in text or 'unauthori' in text or 'blocked' in text:
        return _error('auth')
    if 'credit' in text or 'balance' in text or 'payment' in text:
        return _error('quota')
    if 'too many' in text or 'rate limit' in text:
        return _error('rate_limit')
    return _error('provider_application_error')


class Tavily(_NoBalance):
    """Tavily Search, basic depth only (1 API credit per request).

    Docs (verified 2026-10): https://docs.tavily.com/documentation/api-reference/endpoint/search -
    POST https://api.tavily.com/search, header 'Authorization: Bearer tvly-...', body {query,
    search_depth (basic|fast|ultra-fast = 1 credit, advanced = 2), max_results 0..20 (default 10),
    topic, country (topic general only), include_domains (<= 300), include_answer,
    include_raw_content, include_images, auto_parameters (may switch to advanced = 2 credits)}.
    Response {query, answer, images, results[{title, url, content, score, raw_content}],
    response_time, usage, request_id}. https://docs.tavily.com/documentation/api-credits - free
    Researcher plan: 1,000 credits/month; basic search = 1 credit whatever max_results is, so
    max_results defaults to 20. https://docs.tavily.com/documentation/rate-limits - development
    keys 100 RPM (spacing 0.7s); 429 errors carry {detail{error}}. HTTP 432 / 433 are the plan /
    pay-as-you-go credit limits and map to 'quota'.
    """
    id = 'tavily'
    independence_group = 'tavily'
    operations = ('search',)
    auth_env = ('TAVILY_API_KEY',)
    min_spacing = {'search': 0.7}
    origin = 'https://api.tavily.com'
    parses_client_errors = True

    def request(self, operation, payload, env):
        self._operation(operation)
        clean = _search_payload(payload, ('count', 'include_domains'))
        body = {'query': clean['query'], 'search_depth': 'basic', 'max_results': clean.get('count', 20),
                'topic': 'general', 'country': 'united states', 'auto_parameters': False, 'include_answer': False,
                'include_raw_content': False, 'include_images': False}
        if clean.get('include_domains'):
            body['include_domains'] = search_domains(clean['include_domains'])
        headers = {'Authorization': 'Bearer ' + _secret(env, 'TAVILY_API_KEY'), 'Content-Type': 'application/json'}
        return self._request('POST', '/search', headers, body, timeout=60)

    def parse(self, operation, response):
        self._operation(operation)
        if response.status in (432, 433):
            return _error('quota')
        failure = http_failure(response)
        if failure:
            return failure
        j = response.data
        if 'detail' in j:
            return _error('provider_application_error')
        items = j.get('results')
        if not isinstance(items, list):
            return _error('invalid_response')
        results, prospects = _serp_rows(items[:20], 'title', 'url', 'content')
        return Outcome('results' if results else 'not_found', fields={'prospects': prospects, 'results': results})


class SerpApi(_NoBalance):
    """SerpApi Google Search (engine=google), first organic page per request.

    Docs (verified 2026-10): https://serpapi.com/search-api - GET https://serpapi.com/search.json
    ?engine=google&q=&google_domain&gl&hl&start&api_key (api_key is a required query parameter;
    no header auth is documented). organic_results[{position, title, link, snippet}];
    search_metadata.status Processing -> Success | Error. 'num' is no longer documented, so the
    default first page (~10 results) is used and 'page' maps to start=(page-1)*10.
    https://serpapi.com/api-status-and-error-codes - errors are {error}; 401 bad key, 429 hourly
    throughput exceeded or searches exhausted; an empty result set is HTTP 200, status Success,
    organic_results_state 'Fully empty' with an 'error' message, which is 'not_found' here.
    https://serpapi.com/pricing - Free plan 250 searches/month, 50/hour throughput (spacing 72s);
    cached, errored and failed searches are not counted, but the ledger still debits 1.
    """
    id = 'serpapi'
    independence_group = 'serpapi'
    operations = ('search',)
    auth_env = ('SERPAPI_API_KEY',)
    min_spacing = {'search': 72.0}
    origin = 'https://serpapi.com'

    def request(self, operation, payload, env):
        self._operation(operation)
        clean = _search_payload(payload, ('page',))
        page = clean.get('page', 1)
        if type(page) is not int or not 1 <= page <= 10:
            raise PolicyError('invalid_payload')
        query = {'engine': 'google', 'q': clean['query'], 'google_domain': 'google.com', 'gl': 'us', 'hl': 'en'}
        if page != 1:
            query['start'] = (page - 1) * 10
        query['api_key'] = _secret(env, 'SERPAPI_API_KEY')
        return self._request('GET', '/search.json?' + urlencode(query), {'Accept': 'application/json'}, timeout=90)

    def parse(self, operation, response):
        self._operation(operation)
        j = response.data if response.status == 200 and response.json_valid and isinstance(response.data, dict) else None
        if j is not None:
            meta = j.get('search_metadata') if isinstance(j.get('search_metadata'), dict) else {}
            info = j.get('search_information') if isinstance(j.get('search_information'), dict) else {}
            state = info.get('organic_results_state')
            if meta.get('status') == 'Success' and isinstance(state, str) and state.strip().lower() == 'fully empty' \
                    and not j.get('organic_results'):
                return Outcome('not_found', fields={'prospects': [], 'results': []})
        failure = http_failure(response)
        if failure:
            return failure
        meta = j.get('search_metadata')
        if not isinstance(meta, dict):
            return _error('invalid_response')
        if meta.get('status') == 'Error':
            return _error('provider_error')
        if meta.get('status') != 'Success':
            return _error('invalid_response')  # Processing / Queued: never sent async here
        organic = j.get('organic_results', [])
        if not isinstance(organic, list):
            return _error('invalid_response')
        results, prospects = _serp_rows(organic, 'title', 'link', 'snippet')
        return Outcome('results' if results else 'not_found', fields={'prospects': prospects, 'results': results})


class Firecrawl(_NoBalance):
    """Firecrawl v2 web search and single-page scrape (rendered HTML for JS-heavy team pages).

    Docs (verified 2026-10): https://docs.firecrawl.dev/api-reference/endpoint/search - POST
    https://api.firecrawl.dev/v2/search {query (<= 500 chars), limit 1..100, sources ['web'],
    country, includeDomains, highlights, timeout}; header 'Authorization: Bearer fc-...';
    response {success, data{web[{url, title, description, position}]}, creditsUsed}.
    https://docs.firecrawl.dev/features/search - cost is 2 credits per 10 results (rounded up),
    so limit is fixed at 10: one ledger 'search_ops' unit = 2 Firecrawl credits.
    https://docs.firecrawl.dev/api-reference/endpoint/scrape and /features/scrape - POST /v2/scrape
    {url, formats ['html'], onlyMainContent false, parsers [], timeout} -> {success, data{html,
    metadata{sourceURL, url, statusCode, contentType}}}; 1 credit per scrape (parsers [] keeps a
    PDF at a flat 1 credit; JSON/question/PII options would add credits and are never sent).
    The API is HTTP 200 + success true even when the target page itself returned 4xx/5xx
    (metadata.statusCode), which becomes 'not_found' here. LinkedIn URLs are refused because
    Firecrawl routes them to paid third-party providers. https://docs.firecrawl.dev/api-reference/errors -
    errors are {success false, error}; 402 credits exhausted, 429 rate/concurrency limit.
    https://docs.firecrawl.dev/rate-limits - Free plan: 10 req/min for /scrape and /search
    (spacing 6.5s), 2 concurrent browsers; free keys get 1,000 credits.

    The scrape outcome keeps {url, requested_url, html_zb64, html_bytes, truncated, page_status}:
    HTML is capped at 500 KB and stored with encode_html (use discovery.scraped_html to read it).
    """
    id = 'firecrawl'
    independence_group = 'firecrawl'
    operations = ('search', 'scrape')
    auth_env = ('FIRECRAWL_API_KEY',)
    min_spacing = {'search': 6.5, 'scrape': 6.5}
    origin = 'https://api.firecrawl.dev'

    def request(self, operation, payload, env):
        self._operation(operation)
        headers = {'Authorization': 'Bearer ' + _secret(env, 'FIRECRAWL_API_KEY'), 'Content-Type': 'application/json'}
        if operation == 'scrape':
            clean = strict_payload('scrape', payload)
            body = {'url': scrape_url(clean.get('url')), 'formats': ['html'], 'onlyMainContent': False,
                    'parsers': [], 'timeout': 60000}
            return self._request('POST', '/v2/scrape', headers, body, timeout=90, max_response_bytes=MAX_RESPONSE_CEILING)
        clean = _search_payload(payload, ('include_domains',))
        body = {'query': clean['query'], 'limit': 10, 'sources': ['web'], 'country': 'US', 'highlights': False,
                'timeout': 60000}
        if clean.get('include_domains'):
            body['includeDomains'] = search_domains(clean['include_domains'])
        return self._request('POST', '/v2/search', headers, body, timeout=90)

    def parse(self, operation, response):
        self._operation(operation)
        failure = http_failure(response)
        if failure:
            return failure
        j = response.data
        if j.get('success') is not True:
            return _error('provider_application_error')
        data = j.get('data')
        if operation == 'search':
            web = data.get('web', []) if isinstance(data, dict) else data
            if not isinstance(web, list):
                return _error('invalid_response')
            results, prospects = _serp_rows(web[:10], 'title', 'url', 'description')
            return Outcome('results' if results else 'not_found', fields={'prospects': prospects, 'results': results})
        if not isinstance(data, dict) or not isinstance(data.get('html'), str):
            return _error('invalid_response')
        meta = data.get('metadata') if isinstance(data.get('metadata'), dict) else {}
        status = meta.get('statusCode')
        status = status if type(status) is int and 100 <= status <= 599 else None
        final = None
        for key in ('url', 'sourceURL'):
            final = safe_link(meta.get(key)) if isinstance(meta.get(key), str) else None
            if final:
                break
        encoded = data['html'].encode('utf-8', 'replace')
        truncated = len(encoded) > FIRECRAWL_MAX_HTML
        html = encoded[:FIRECRAWL_MAX_HTML].decode('utf-8', 'ignore') if truncated else data['html']
        fields = {'url': final, 'page_status': status, 'truncated': truncated}
        if (status is not None and status >= 400) or not html.strip():
            return Outcome('not_found', fields=fields)
        fields.update(html_zb64=encode_html(html), html_bytes=len(html.encode('utf-8')))
        return Outcome('results', fields=fields)

    def scope_outcome(self, operation, outcome, payload):
        """Tie a scraped page to the URL that was requested (the final URL may be a redirect)."""
        if operation != 'scrape':
            return outcome
        requested = payload.get('url')
        fields = {**outcome.fields, 'requested_url': requested}
        if not fields.get('url'):
            fields['url'] = requested
        return dataclasses.replace(outcome, fields=fields)


class PeopleDataLabs(_NoBalance):
    """People Data Labs Person Enrichment: one person, identity fields only.

    Docs (verified 2026-10): https://docs.peopledatalabs.com/docs/reference-person-enrichment-api -
    GET https://api.peopledatalabs.com/v5/person/enrich with profile | (name AND company/location),
    min_likelihood, titlecase, data_include; 200 {status, likelihood 1-10, data{...}} on a match,
    404 {status, error{type 'not_found'}} otherwise; billed per match only ("We charge per match"),
    so a ledger debit on a 404 is conservative. Free customers: 100 requests/minute (spacing
    0.7s). https://docs.peopledatalabs.com/docs/authentication - X-Api-Key header.
    https://docs.peopledatalabs.com/docs/errors - 400/405 invalid_request_error, 401
    authentication_error, 402 payment_required (credits used up), 429 rate_limit_error.
    https://docs.peopledatalabs.com/docs/fields and https://www.peopledatalabs.com/pricing/person -
    the Free plan (100 records/month) returns emails, phones and addresses only as true/false, so
    data_include asks for full_name, linkedin_url, job_title, job_company_name,
    job_company_website and location_name only; booleans are never kept as values.
    min_likelihood 6 makes weak matches a free 404 instead of a billed guess.
    """
    id = 'pdl'
    independence_group = 'pdl'
    operations = ('person_enrich',)
    auth_env = ('PDL_API_KEY',)
    min_spacing = {'person_enrich': 0.7}
    origin = 'https://api.peopledatalabs.com'
    parses_client_errors = True
    MIN_LIKELIHOOD = 6
    FIELDS = 'full_name,linkedin_url,job_title,job_company_name,job_company_website,location_name'

    def request(self, operation, payload, env):
        self._operation(operation)
        clean = person_identity(strict_payload(operation, payload))
        if clean.get('linkedin_url'):
            query = [('profile', linkedin_profile_url(clean['linkedin_url']))]
        else:
            query = [('name', clean['name']), ('company', clean.get('domain') or clean['company'])]
            if clean.get('location'):
                query.append(('location', clean['location']))
        query += [('min_likelihood', self.MIN_LIKELIHOOD), ('titlecase', 'true'), ('data_include', self.FIELDS)]
        headers = {'X-Api-Key': _secret(env, 'PDL_API_KEY'), 'Accept': 'application/json'}
        return self._request('GET', '/v5/person/enrich?' + urlencode(query), headers)

    def parse(self, operation, response):
        self._operation(operation)
        j = response.data if response.json_valid and isinstance(response.data, dict) else None
        if j is not None and response.status == 404:
            error = j.get('error')
            if isinstance(error, dict) and error.get('type') == 'not_found':
                return Outcome('not_found', fields={'likelihood': None})
        failure = http_failure(response)
        if failure:
            return failure
        data = j.get('data')
        likelihood = j.get('likelihood')
        if j.get('status') not in (200, None) or not isinstance(data, dict) \
                or type(likelihood) is not int or not 1 <= likelihood <= 10:
            return _error('invalid_response')
        full = clean_text(data.get('full_name'), 120)
        linkedin = canonical_linkedin(data['linkedin_url']) if isinstance(data.get('linkedin_url'), str) else None
        if not full and not linkedin:
            return _error('invalid_response')
        row = {'full_name': full, 'name_complete': bool(full and len(full.split()) >= 2),
               'title': clean_text(data.get('job_title'), 150), 'company': clean_text(data.get('job_company_name'), 200),
               'company_website': _company_domain(data.get('job_company_website')),
               'linkedin_url': linkedin, 'location': clean_text(data.get('location_name'), 120),
               'source': 'pdl', 'source_url': None}
        return Outcome('enriched', fields={'prospect': row, 'likelihood': likelihood, 'verification_is_fresh': False})

    def scope_outcome(self, operation, outcome, payload):
        """A profile lookup must return that profile; a different current employer is flagged."""
        if outcome.assessment != 'enriched':
            return outcome
        row = dict(outcome.fields.get('prospect') or {})
        asked = payload.get('linkedin_url')
        if asked and row.get('linkedin_url') and row['linkedin_url'] != linkedin_profile_url(asked):
            return Outcome('not_found', fields={'likelihood': outcome.fields.get('likelihood'), 'reason': 'profile_mismatch'})
        fields = dict(outcome.fields)
        domain = payload.get('domain')
        if domain and row.get('company_website') and not (
                row['company_website'] == domain or row['company_website'].endswith('.' + domain)
                or domain.endswith('.' + row['company_website'])):
            fields['company_mismatch'] = True
        return dataclasses.replace(outcome, fields=fields)


class AbstractEmail(_NoBalance):
    """Abstract Email Reputation API (the product a free Abstract email key is issued for).

    Docs (verified 2026-10): https://docs.abstractapi.com/api/email-reputation - GET
    https://emailreputation.abstractapi.com/v1/?email= with 'Authorization: Bearer KEY' (keeps the
    key out of URLs; api_key query is the alternative). Response {email_address,
    email_deliverability{status deliverable|undeliverable|unknown, status_detail, is_format_valid,
    is_smtp_valid, is_mx_valid}, email_quality{score, is_free_email, is_username_suspicious,
    is_disposable, is_catchall, is_role, ...}, email_risk{address_risk_status, domain_risk_status},
    ...}. status_detail: deliverable -> valid_email | high_traffic_email; undeliverable ->
    invalid_mailbox | full_mailbox | invalid_format | dns_record_not_found | unavailable_server.
    Errors: 401 bad key, 422/403 quota reached, 429 (free plans: 1 request/second, spacing 1.1s).
    Every submitted address costs one request credit. Free plan: 100 requests/month.
    """
    id = 'abstract'
    independence_group = 'abstract'
    operations = ('verify',)
    auth_env = ('ABSTRACT_EMAIL_API_KEY',)
    min_spacing = {'verify': 1.1}
    origin = 'https://emailreputation.abstractapi.com'
    parses_client_errors = True
    DETAILS = {'valid_email', 'high_traffic_email', 'invalid_mailbox', 'full_mailbox', 'invalid_format',
               'dns_record_not_found', 'unavailable_server', 'inbox_full'}

    def request(self, operation, payload, env):
        self._operation(operation)
        headers = {'Authorization': 'Bearer ' + _secret(env, 'ABSTRACT_EMAIL_API_KEY'), 'Accept': 'application/json'}
        return self._request('GET', '/v1/?' + urlencode({'email': _text(payload, 'email')}), headers, timeout=30)

    def parse(self, operation, response):
        self._operation(operation)
        if response.status == 422:
            return _error('quota')
        failure = http_failure(response)
        if failure:
            return failure
        j = response.data
        deliverability = j.get('email_deliverability')
        if not isinstance(deliverability, dict):
            return _error('invalid_response')
        status, detail = deliverability.get('status'), deliverability.get('status_detail')
        if status not in ('deliverable', 'undeliverable', 'unknown'):
            return _error('invalid_response')
        email = _email(j.get('email_address'))
        if not email:
            return _error('invalid_response')
        quality = j.get('email_quality') if isinstance(j.get('email_quality'), dict) else {}
        risk = j.get('email_risk') if isinstance(j.get('email_risk'), dict) else {}
        flags = [flag for key, flag in (('is_disposable', 'disposable'), ('is_role', 'role'), ('is_catchall', 'catch_all'),
                                        ('is_username_suspicious', 'suspicious_username')) if quality.get(key) is True]
        if risk.get('address_risk_status') == 'high':
            flags.append('high_risk')
        if risk.get('domain_risk_status') == 'high':
            flags.append('high_risk_domain')
        if status == 'deliverable':
            assessment = 'provider_valid' if detail in ('valid_email', 'high_traffic_email') else 'unknown'
            if deliverability.get('is_smtp_valid') is not True:
                assessment = 'unknown'
            if detail == 'high_traffic_email':
                flags.append('high_traffic_email')
            if quality.get('is_catchall') is True:
                assessment = 'catch_all'
        elif status == 'undeliverable':
            assessment = 'unknown' if detail in ('full_mailbox', 'inbox_full', 'unavailable_server') else 'provider_invalid'
            if detail in ('full_mailbox', 'inbox_full'):
                flags.append('inbox_full')
        else:
            assessment = 'unknown'
        fields = {'status': status, 'status_detail': detail if detail in self.DETAILS else None}
        for key in ('is_format_valid', 'is_smtp_valid', 'is_mx_valid'):
            if type(deliverability.get(key)) is bool:
                fields[key] = deliverability[key]
        score = quality.get('score')
        if isinstance(score, (int, float)) and not isinstance(score, bool) and 0 <= score <= 1:
            fields['score'] = score
        if type(quality.get('is_free_email')) is bool:
            fields['free'] = quality['is_free_email']
        return Outcome(assessment, email, tuple(dict.fromkeys(flags)), fields)


_EMAILABLE_REASONS = {'deliverable': ('accepted_email',),
                      'undeliverable': ('invalid_email', 'invalid_domain', 'rejected_email', 'invalid_smtp'),
                      'risky': ('low_quality', 'low_deliverability'),
                      'unknown': ('no_connect', 'timeout', 'unavailable_smtp', 'unexpected_error')}


class Emailable(_NoBalance):
    """Emailable single verification.

    Docs (verified 2026-10): https://emailable.com/docs/api/emails/ - GET
    https://api.emailable.com/v1/verify?email=&accept_all=&timeout=2..10 -> {email, state
    (deliverable|undeliverable|risky|unknown), reason, accept_all, disposable, role, free,
    mailbox_full, no_reply, score, ...}. Test keys return random sample responses, so test_
    keys are refused. https://emailable.com/docs/api/authentication/ - 'Authorization: Bearer KEY'
    header is accepted instead of api_key. https://emailable.com/docs/api/status-codes/ - 249
    "Try Again" (still verifying; a repeat within 5 minutes is free), 401/403 auth, 402 credits,
    429. States and reasons: /docs/email-verification/email-verification-results/results-overview/
    all-possible-states-and-reasons/. Rate limit 25/s on /v1/verify (/docs/api/rate-limits/).
    accept_all=true asks for the accept-all check so a catch-all domain is not reported as plain
    deliverable.
    """
    id = 'emailable'
    independence_group = 'emailable'
    operations = ('verify',)
    auth_env = ('EMAILABLE_API_KEY',)
    min_spacing = {'verify': 0.1}
    origin = 'https://api.emailable.com'
    MAPPING = {'deliverable': 'provider_valid', 'undeliverable': 'provider_invalid', 'risky': 'unknown', 'unknown': 'unknown'}

    def request(self, operation, payload, env):
        self._operation(operation)
        query = {'email': _text(payload, 'email'), 'accept_all': 'true', 'timeout': 10}
        headers = {'Authorization': 'Bearer ' + _live_key(env, 'EMAILABLE_API_KEY'), 'Accept': 'application/json'}
        return self._request('GET', '/v1/verify?' + urlencode(query), headers, timeout=30)

    def parse(self, operation, response):
        self._operation(operation)
        if response.status == 249:
            return _error('provider_retry_later')
        failure = http_failure(response)
        if failure:
            return failure
        j = response.data
        state = j.get('state')
        if not isinstance(state, str) or state not in self.MAPPING:
            return _error('invalid_response')
        email = _email(j.get('email'))
        if not email:
            return _error('invalid_response')
        reason = j.get('reason')
        assessment = self.MAPPING[state]
        if state == 'deliverable' and reason != 'accepted_email':
            assessment = 'unknown'
        flags = [flag for key, flag in (('disposable', 'disposable'), ('role', 'role'), ('accept_all', 'catch_all'),
                                        ('mailbox_full', 'inbox_full'), ('no_reply', 'no_reply')) if j.get(key) is True]
        if state == 'risky':
            flags.append('risky')
        if j.get('accept_all') is True and assessment == 'provider_valid':
            assessment = 'catch_all'
        if j.get('mailbox_full') is True and assessment == 'provider_valid':
            assessment = 'unknown'  # a full mailbox bounces today, as Reoon's inbox_full does
        fields = {'state': state, 'reason': reason if reason in _EMAILABLE_REASONS.get(state, ()) else None,
                  **({'free': j['free']} if type(j.get('free')) is bool else {})}
        score = j.get('score')
        if type(score) is int and 0 <= score <= 100:
            fields['score'] = score
        return Outcome(assessment, email, tuple(dict.fromkeys(flags)), fields)


_KICKBOX_REASONS = frozenset({'invalid_email', 'invalid_domain', 'rejected_email', 'accepted_email', 'low_quality',
                              'low_deliverability', 'no_connect', 'timeout', 'invalid_smtp', 'unavailable_smtp',
                              'unexpected_error'})


class Kickbox(_NoBalance):
    """Kickbox single verification.

    Docs (verified 2026-10): https://docs.kickbox.com/docs/single-verification-api - GET
    https://api.kickbox.com/v2/verify?email=&timeout=(ms, <= 30000) -> {result (deliverable|
    undeliverable|risky|unknown), reason, role, free, disposable, accept_all, did_you_mean,
    sendex 0..1, email (normalized), user, domain, success, message}.
    https://docs.kickbox.com/docs/using-the-api - auth by apikey query or 'Authorization: Bearer'
    header (used here); sandbox keys start test_ and are refused; errors {message, success
    false} with 400/403/429/500; 25 parallel requests per IP and 8,000/minute (spacing 0.1s).
    X-Kickbox-Balance reports remaining credits. Free accounts start with 100 verifications.
    """
    id = 'kickbox'
    independence_group = 'kickbox'
    operations = ('verify',)
    auth_env = ('KICKBOX_API_KEY',)
    min_spacing = {'verify': 0.1}
    origin = 'https://api.kickbox.com'
    MAPPING = {'deliverable': 'provider_valid', 'undeliverable': 'provider_invalid', 'risky': 'unknown', 'unknown': 'unknown'}

    def request(self, operation, payload, env):
        self._operation(operation)
        query = {'email': _text(payload, 'email'), 'timeout': 10000}
        headers = {'Authorization': 'Bearer ' + _live_key(env, 'KICKBOX_API_KEY'), 'Accept': 'application/json'}
        return self._request('GET', '/v2/verify?' + urlencode(query), headers, timeout=30)

    def parse(self, operation, response):
        self._operation(operation)
        j = response.data if response.json_valid and isinstance(response.data, dict) else None
        if j is not None and response.status == 200 and j.get('success') is False:
            return _message_error(j.get('message'))
        failure = http_failure(response)
        if failure:
            return failure
        if j.get('success') is not True:
            return _error('invalid_response')
        result = j.get('result')
        if not isinstance(result, str) or result not in self.MAPPING:
            return _error('invalid_response')
        email = _email(j.get('email'))
        if not email:
            return _error('invalid_response')
        reason = j.get('reason')
        assessment = self.MAPPING[result]
        if result == 'deliverable' and reason != 'accepted_email':
            assessment = 'unknown'
        flags = [flag for key, flag in (('disposable', 'disposable'), ('role', 'role'), ('accept_all', 'catch_all'))
                 if j.get(key) is True]
        if result == 'risky':
            flags.append('risky')
        if j.get('accept_all') is True and assessment == 'provider_valid':
            assessment = 'catch_all'
        fields = {'result': result, 'reason': reason if reason in _KICKBOX_REASONS else None, **_informational_fields(j)}
        sendex = j.get('sendex')
        if isinstance(sendex, (int, float)) and not isinstance(sendex, bool) and 0 <= sendex <= 1:
            fields['sendex'] = sendex
        return Outcome(assessment, email, tuple(dict.fromkeys(flags)), fields)


_BOUNCER_REASONS = frozenset({'accepted_email', 'low_deliverability', 'low_quality', 'invalid_email', 'invalid_domain',
                              'rejected_email', 'dns_error', 'unavailable_smtp', 'unsupported', 'timeout', 'unknown'})


class Bouncer(_NoBalance):
    """Bouncer real-time verification.

    Docs (verified 2026-10): https://docs.usebouncer.com/api-reference/real-time/verify-email -
    GET https://api.usebouncer.com/v1.1/email/verify?email=&timeout=(default 10, max 30), header
    x-api-key -> {email, status (deliverable|risky|undeliverable|unknown), reason, domain{name,
    acceptAll, disposable, free}, account{role, disabled, fullMailbox}, dns, provider, score
    0..100, toxic, toxicity 0..5, retryAfter (greylisted)}; yes/no/unknown strings. 1,000
    requests/minute by default (spacing 0.1s). https://docs.usebouncer.com/integration-guidelines -
    402 credits, 429 rate limited, 503 retry; '+' must be URL-encoded; risky + acceptAll 'yes' is
    a catch-all; toxicity 4-5 should be suppressed.
    """
    id = 'bouncer'
    independence_group = 'bouncer'
    operations = ('verify',)
    auth_env = ('BOUNCER_API_KEY',)
    min_spacing = {'verify': 0.1}
    origin = 'https://api.usebouncer.com'
    MAPPING = {'deliverable': 'provider_valid', 'undeliverable': 'provider_invalid', 'risky': 'unknown', 'unknown': 'unknown'}

    def request(self, operation, payload, env):
        self._operation(operation)
        query = urlencode({'email': _text(payload, 'email'), 'timeout': 10})  # encodes '+' as %2B
        return self._request('GET', '/v1.1/email/verify?' + query,
                             {'x-api-key': _secret(env, 'BOUNCER_API_KEY'), 'Accept': 'application/json'}, timeout=40)

    def parse(self, operation, response):
        self._operation(operation)
        failure = http_failure(response)
        if failure:
            return failure
        j = response.data
        status = j.get('status')
        if not isinstance(status, str) or status not in self.MAPPING:
            return _error('invalid_response')
        email = _email(j.get('email'))
        if not email:
            return _error('invalid_response')
        domain = j.get('domain') if isinstance(j.get('domain'), dict) else {}
        account = j.get('account') if isinstance(j.get('account'), dict) else {}
        reason = j.get('reason')
        assessment = self.MAPPING[status]
        if status == 'deliverable' and reason != 'accepted_email':
            assessment = 'unknown'
        accept_all = _yes(domain.get('acceptAll'))
        flags = [flag for value, flag in ((domain.get('disposable'), 'disposable'), (account.get('role'), 'role'),
                                          (account.get('disabled'), 'disabled'), (account.get('fullMailbox'), 'inbox_full'))
                 if _yes(value) is True]
        if accept_all:
            flags.append('catch_all')
            # Bouncer: a risky verdict on an accept-all domain is the catch-all case.
            if status == 'risky' or assessment == 'provider_valid':
                assessment = 'catch_all'
        if status == 'risky':
            flags.append('risky')
        if 'inbox_full' in flags and assessment == 'provider_valid':
            assessment = 'unknown'  # Bouncer guidance: suppress full mailboxes
        toxicity = j.get('toxicity')
        if type(toxicity) is int and toxicity >= 4:
            flags.append('toxic')
        if j.get('retryAfter') not in (None, ''):
            flags.append('greylisted')
        fields = {'status': status, 'reason': reason if reason in _BOUNCER_REASONS else None}
        if _yes(domain.get('free')) is not None:
            fields['free'] = _yes(domain.get('free'))
        score = j.get('score')
        if type(score) is int and 0 <= score <= 100:
            fields['score'] = score
        if type(toxicity) is int and 0 <= toxicity <= 5:
            fields['toxicity'] = toxicity
        return Outcome(assessment, email, tuple(dict.fromkeys(flags)), fields)


class MyEmailVerifier(_NoBalance):
    """MyEmailVerifier real-time single validation (API v2 'validate_single.php').

    Docs (account dashboard, verified 2026-10): GET
    https://api.myemailverifier.com/api/validate_single.php?apikey=KEY&email=EMAIL (the key is a
    query parameter; no header auth is documented) -> {Address, Status (Valid|Invalid|Catch-all|
    Unknown; 'Grey-listed' tolerated), catch_all, Disposable_Domain, Role_Based, Free_Domain,
    Greylisted, Diagnosis}. Flags arrive as "true"/"false" strings or 0/1 integers (both seen).
    Failures are {status false, message} (also on HTTP 200: unauthorized, blocked account, not
    enough credits); limit 30 requests/minute (spacing 2.1s). Diagnosis is free server text and
    is never kept. The Address echo must match the requested mailbox (case-insensitive).
    """
    id = 'myemailverifier'
    independence_group = 'myemailverifier'
    operations = ('verify',)
    auth_env = ('MYEMAILVERIFIER_API_KEY',)
    min_spacing = {'verify': 2.1}
    origin = 'https://api.myemailverifier.com'
    MAPPING = {'valid': 'provider_valid', 'invalid': 'provider_invalid', 'unknown': 'unknown',
               'catchall': 'catch_all', 'greylisted': 'unknown'}

    @staticmethod
    def _flag(value):
        if type(value) is int and value in (0, 1):
            return bool(value)
        if isinstance(value, str) and value.strip() in ('0', '1'):
            return value.strip() == '1'
        return _yes(value)

    def request(self, operation, payload, env):
        self._operation(operation)
        email = _text(payload, 'email')
        query = urlencode({'apikey': _secret(env, 'MYEMAILVERIFIER_API_KEY'), 'email': email})
        return self._request('GET', '/api/validate_single.php?' + query, {'Accept': 'application/json'}, timeout=60)

    def parse(self, operation, response):
        self._operation(operation)
        j = response.data if response.json_valid and isinstance(response.data, dict) else None
        if j is not None and 200 <= response.status < 300 and j.get('status') is False:
            return _message_error(j.get('message'))
        failure = http_failure(response)
        if failure:
            return failure
        raw = j.get('Status')
        status = re.sub(r'[\s_-]', '', raw).lower() if isinstance(raw, str) else None
        if status not in self.MAPPING:
            return _error('invalid_response')
        email = _email(j.get('Address').strip() if isinstance(j.get('Address'), str) else None)
        if not email:
            return _error('invalid_response')
        assessment = self.MAPPING[status]
        catch_all, grey = self._flag(j.get('catch_all')), self._flag(j.get('Greylisted'))
        flags = [flag for value, flag in ((j.get('Disposable_Domain'), 'disposable'), (j.get('Role_Based'), 'role'))
                 if self._flag(value) is True]
        if catch_all or status == 'catchall':
            flags.append('catch_all')
            if assessment == 'provider_valid':
                assessment = 'catch_all'
        if grey or status == 'greylisted':
            flags.append('greylisted')
            if assessment == 'provider_valid':
                assessment = 'unknown'
        fields = {'status': status}
        if self._flag(j.get('Free_Domain')) is not None:
            fields['free'] = self._flag(j.get('Free_Domain'))
        return Outcome(assessment, email.lower(), tuple(dict.fromkeys(flags)), fields)


PROVIDERS = {provider.id: provider for provider in (Hunter(), Verifalia(), Clearout(), ZeroBounce(), HunterInsight(), Disify(),
                                                    Serper(), Brave(), Overpass(), Apollo(), Prospeo(), GooglePlaces(),
                                                    Exa(), Tomba(), Icypeas(), Reoon(), MillionVerifier(),
                                                    AbstractEmail(), Emailable(), Kickbox(), Bouncer(), MyEmailVerifier(),
                                                    Tavily(), Firecrawl(), PeopleDataLabs(), SerpApi(), Deepline())}


def get_provider(provider_id):
    """Return a configured stateless adapter or a public-safe unsupported error."""
    try:
        return PROVIDERS[provider_id]
    except (KeyError, TypeError):
        raise PolicyError('unsupported_provider') from None
