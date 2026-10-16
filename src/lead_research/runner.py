"""Serial, dry-first execution. No network transaction holds a database lock."""
from __future__ import annotations

import os
import time

from .models import BudgetError, BusyError, Outcome, PolicyError
from .policy import input_key, normalize_job, parse_units, validate_evidence_url


def get_provider(id):
    from .providers import get_provider as lookup
    return lookup(id)


def _supports_pending(provider):
    # Verifalia (202 + Location) and adapters that declare supports_pending=True and
    # implement poll_request() for a same-origin, non-debiting result read.
    return provider.id == 'verifalia' or getattr(provider, 'supports_pending', False) is True


class Runner:
    def __init__(self, store, config, env=None, transport=None, sleep=time.sleep, clock=time.time):
        self.store, self.config = store, config
        self.env = os.environ if env is None else env
        self.transport = transport
        self.sleep, self.clock = sleep, clock

    def _auth(self, provider):
        if provider.id == 'verifalia':
            return bool(self.env.get('VERIFALIA_TOKEN') or (self.env.get('VERIFALIA_USERNAME') and self.env.get('VERIFALIA_PASSWORD')))
        keys = provider.auth_env
        if isinstance(keys, str):
            keys = (keys,)
        return all(self.env.get(key) for key in keys)

    def _quote(self, job):
        job = normalize_job(job)
        provider = get_provider(job['provider'])
        entry = self.config.get('providers', {}).get(provider.id, {})
        job['input_key'] = input_key(job['operation'], job['payload'])
        job['independence'] = provider.independence_group
        job['status'] = 'ready'
        job['charges'] = dict(entry.get('charges', {}).get(job['operation'], {}))
        reason = None
        if not entry.get('enabled', False):
            reason = 'provider_disabled'
        elif self.store.provider_state(provider.id).get('paused'):
            reason = 'provider_paused'
        elif job['operation'] not in provider.operations:
            reason = 'unsupported_operation'
        elif not self._auth(provider):
            reason = 'missing_auth'
        cached = self.store.get_cached(provider.id, job['operation'], job['input_key'])
        if cached:
            job.update(status='cached', attempt_id=cached['id'], outcome=cached.get('outcome'), state=cached['state'])
            return job
        if job['operation'] == 'verify':
            previous = [a for a in self.store.attempts() if a['operation'] == 'verify' and a['payload'].get('email') == job['payload']['email'] and a['state'] != 'cancelled']
            if previous:
                if any(a['state'] in {'sent', 'pending', 'uncertain', 'reserved'} or (a.get('outcome') or {}).get('error') for a in previous):
                    reason = 'unresolved_provider_error_or_active_attempt'
                elif not job['crosscheck']:
                    reason = 'cached_mailbox_requires_explicit_crosscheck'
                elif any(a['independence'] == provider.independence_group for a in previous) or len({a['independence'] for a in previous}) >= 2:
                    reason = 'independence_limit'
        try:
            if not job['charges']:
                raise PolicyError('charges_required')
            units = {p: parse_units(v, positive=True) for p, v in job['charges'].items()}
            if any(v not in {500000, 1000000} for v in units.values()):
                raise PolicyError('unsupported_charge')
            budgets = {(b['provider'], b['pool']): b['remaining_units'] for b in self.store.budgets()}
            if any(budgets.get((provider.id, p), 0) < value for p, value in units.items()):
                reason = reason or 'insufficient_confirmed_free_budget'
        except PolicyError as exc:
            reason = reason or str(exc)
        if reason:
            job.update(status='skipped', reason=reason)
        return job

    def plan(self, jobs):
        result, seen = [], set()
        for raw in jobs:
            try:
                item = self._quote(raw)
                key = (item['provider'], item['operation'], item['input_key'])
                if key in seen:
                    item.update(status='skipped', reason='duplicate_input')
                seen.add(key)
            except Exception:
                item = {'status': 'skipped', 'reason': 'invalid_job'}
            result.append(item)
        return result

    def _pace(self, provider, operation):
        state = self.store.provider_state(provider.id)
        spacing = provider.min_spacing.get(operation, 0)
        last = state.get('last_request_at')
        if last is not None:
            wait = float(spacing) - (self.clock() - last)
            if wait > 0:
                self.sleep(wait)

    def _send(self, request):
        if self.transport is None:
            from .transport import UrllibTransport
            self.transport = UrllibTransport()
        return self.transport.send(request)

    def _outcome(self, provider, operation, response, payload):
        # Never let any adapter accidentally treat failed HTTP as a mailbox vote.
        if response.status in (401, 403):
            return Outcome('error', error='auth')
        if response.status == 429:
            return Outcome('error', error='rate_limit')
        if response.status >= 500:
            return Outcome('error', error='provider_error')
        # Adapters that document business outcomes on 4xx (e.g. Prospeo NO_MATCH,
        # Tomba unknown_record) opt in to classify them; everything else stays http_error.
        client_errors = getattr(provider, 'parses_client_errors', False) is True and 400 <= response.status < 500
        if (response.status < 200 or response.status >= 300) and not client_errors:
            return Outcome('error', error='http_error')
        outcome = provider.parse(operation, response)
        # List operations ('results'/'not_found') complete normally; adapters may
        # additionally scope them to the requested input (e.g. queried domain).
        scope = getattr(provider, 'scope_outcome', None)
        if scope is not None and not outcome.error:
            outcome = scope(operation, outcome, payload)
        if outcome.job_url:
            validate_evidence_url(outcome.job_url)
        if operation in {'verify', 'signals'} and outcome.email and outcome.email.lower() != payload['email']:
            return Outcome('error', error='recipient_mismatch')
        return outcome

    def _finish_response(self, attempt, provider, response):
        outcome = self._outcome(provider, attempt['operation'], response, attempt['payload'])
        previous_job = (attempt.get('outcome') or {}).get('job_url')
        if (outcome.assessment == 'pending' and not outcome.job_url and previous_job
                and getattr(provider, 'supports_pending', False) is True):
            # A poll that is still running keeps the job pointer it was issued with.
            outcome = Outcome('pending', job_url=previous_job)
        if outcome.assessment == 'pending' and (not _supports_pending(provider) or not outcome.job_url):
            outcome = Outcome('error', error='invalid_pending')
        state = 'pending' if outcome.assessment == 'pending' else 'completed'
        saved = self.store.finish(attempt['id'], outcome, state=state, http_status=response.status)
        if outcome.error or outcome.assessment == 'error':
            self.store.pause(provider.id, outcome.error or 'provider_error')
        return saved, bool(outcome.error or outcome.assessment == 'error')

    def _report(self, results, execute, stopped=False):
        return {'dry_run': not execute, 'execute': execute, 'results': results, 'jobs': results,
                'stopped': stopped, 'completed': sum(r.get('status') == 'completed' for r in results),
                'pending': sum(r.get('status') == 'pending' for r in results),
                'errors': sum(r.get('status') in {'error', 'uncertain'} for r in results)}

    def run(self, jobs, execute=False, limit=10):
        if execute and not self.store.verify_audit():
            raise PolicyError('audit_integrity_failed')
        if execute and self.transport is None and any(r.get('synthetic') for r in self.store.contacts()):
            raise PolicyError('synthetic_workspace_no_live_requests')
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise PolicyError('invalid_limit')
        results, stopped, calls = [], False, 0
        for item in self.plan(jobs):
            if not execute or item['status'] != 'ready':
                results.append(item)
                continue
            if calls >= limit:
                results.append({**item, 'status': 'skipped', 'reason': 'execution_limit'})
                continue
            # Re-quote against results and spending earlier in this same batch.
            item = self._quote(item)
            if item['status'] != 'ready':
                results.append(item)
                continue
            provider = get_provider(item['provider'])
            attempt = None
            try:
                request = provider.request(item['operation'], item['payload'], self.env)
                attempt = self.store.reserve(provider.id, item['operation'], item['input_key'], item['payload'], item['charges'], provider.independence_group)
                self._pace(provider, item['operation'])
                self.store.mark_sent(attempt['id'], at=self.clock())
                calls += 1
                response = self._send(request)
                saved, failed = self._finish_response(attempt, provider, response)
                results.append({**item, 'status': 'error' if failed else saved['state'], 'attempt_id': attempt['id'], 'outcome': saved['outcome']})
                if failed:
                    stopped = True
                    break
            except (BudgetError, BusyError) as exc:
                results.append({**item, 'status': 'skipped', 'reason': str(exc)})
                if isinstance(exc, BusyError):
                    stopped = True
                    break
            except Exception:
                if attempt:
                    current = self.store.get_attempt(attempt['id'])
                    if current['state'] == 'reserved':
                        self.store.cancel_reserved(attempt['id'], 'local_failure_before_send')
                    elif current['state'] in {'sent', 'pending'}:
                        self.store.finish(attempt['id'], Outcome('unknown', error='network_or_processing_uncertain'), state='uncertain')
                self.store.pause(provider.id, 'network_or_processing_uncertain' if attempt else 'request_build_error')
                results.append({**item, 'status': 'uncertain' if attempt else 'error', 'reason': 'network_or_processing_uncertain' if attempt else 'request_build_error', 'attempt_id': attempt['id'] if attempt else None})
                stopped = True
                break
        return self._report(results, execute, stopped)

    def resume(self, attempt_id, execute=False):
        if execute and not self.store.verify_audit():
            raise PolicyError('audit_integrity_failed')
        if execute and self.transport is None and any(r.get('synthetic') for r in self.store.contacts()):
            raise PolicyError('synthetic_workspace_no_live_requests')
        attempt = self.store.get_attempt(attempt_id)
        item = {'attempt_id': attempt['id'], 'status': attempt['state'], 'poll_count': attempt['poll_count']}
        if not execute:
            return self._report([item], False)
        provider = get_provider(attempt['provider'])
        if attempt['state'] != 'pending' or not _supports_pending(provider):
            raise PolicyError('only_pending_verifalia_can_resume')
        entry = self.config.get('providers', {}).get(provider.id, {})
        if not entry.get('enabled') or self.store.provider_state(provider.id).get('paused') or not self._auth(provider):
            return self._report([{**item, 'status': 'skipped', 'reason': 'provider_unavailable'}], True)
        if attempt['poll_count'] >= 12:
            return self._report([{**item, 'status': 'skipped', 'reason': 'poll_limit'}], True)
        poll_sent = False
        try:
            request = provider.poll_request(attempt['outcome']['job_url'], self.env)
            # Polls are GET unless the adapter documents a read-only POST result route.
            if request.method not in getattr(provider, 'poll_methods', ('GET',)):
                raise PolicyError('poll_must_be_get')
            self._pace(provider, 'poll')
            self.store.note_poll(attempt_id, at=self.clock())
            poll_sent = True
            response = self._send(request)
            saved, failed = self._finish_response(attempt, provider, response)
            return self._report([{**item, 'status': 'error' if failed else saved['state'], 'outcome': saved['outcome'], 'poll_count': saved['poll_count']}], True, failed)
        except Exception:
            if not poll_sent:
                return self._report([{**item, 'status': 'skipped', 'reason': 'poll_not_sent'}], True)
            self.store.finish(attempt_id, Outcome('unknown', error='poll_uncertain'), state='uncertain')
            self.store.pause(provider.id, 'poll_uncertain')
            return self._report([{**item, 'status': 'uncertain', 'reason': 'poll_uncertain'}], True, True)
