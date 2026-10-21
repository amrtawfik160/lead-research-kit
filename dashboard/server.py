#!/usr/bin/env python3
"""Local HTTP API for the dashboard. Binds loopback only. Never prints secrets."""
from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
KIT = ROOT.parent
sys.path.insert(0, str(KIT / 'src'))
sys.path.insert(0, str(ROOT))

from lead_research.models import BudgetError, BusyError, PolicyError, ProviderError  # noqa: E402

from api.sample import seed_sample  # noqa: E402
from api.snapshot import (  # noqa: E402
    DATA_DIR,
    Dashboard,
    SAMPLE_DIR,
    clean_rows,
    env_workspace_locked,
    load_settings,
    parse_tabular,
    save_settings,
    settings_view,
    update_settings,
)


def _dashboard():
    return Dashboard()


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        sys.stderr.write('%s - %s\n' % (self.address_string(), fmt % args))

    def do_OPTIONS(self):
        self._send(204, b'')

    def do_GET(self):
        try:
            self._dispatch('GET')
        except Exception as error:
            self._fail(error)

    def do_POST(self):
        try:
            self._dispatch('POST')
        except Exception as error:
            self._fail(error)

    def _dispatch(self, method):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/') or '/'
        query = {k: v[0] if v else '' for k, v in parse_qs(parsed.query).items()}
        body = self._body() if method == 'POST' else {}
        if method == 'GET' and path == '/api/settings':
            return self._json(settings_view())
        if method == 'POST' and path == '/api/settings':
            return self._json(update_settings(body))
        if method == 'POST' and path == '/api/sample/reset':
            seed_sample(SAMPLE_DIR, force=True)
            settings = load_settings()
            settings['sample'] = True
            save_settings(settings)
            if env_workspace_locked():
                return self._json(settings_view())
            return self._json(Dashboard().overview())
        dash = _dashboard()
        if method == 'GET' and path == '/api/health':
            return self._json({'ok': True, **dash.meta()})
        if method == 'GET' and path == '/api/overview':
            return self._json(dash.overview())
        if method == 'GET' and path == '/api/status':
            return self._json(dash.status())
        if method == 'GET' and path == '/api/contacts':
            return self._json(dash.contacts())
        if method == 'GET' and path.startswith('/api/contacts/'):
            lead_id = unquote(path.split('/', 3)[-1])
            return self._json(dash.contact(lead_id))
        if method == 'POST' and path.startswith('/api/contacts/') and path.endswith('/hold'):
            lead_id = unquote(path[len('/api/contacts/'):-len('/hold')])
            return self._json(dash.hold(lead_id, body.get('reason') or 'operator_hold', body.get('source') or 'dashboard'))
        if method == 'GET' and path == '/api/research':
            return self._json(dash.research())
        if method == 'GET' and path == '/api/daily':
            if query.get('date'):
                return self._json(dash.daily(query['date']))
            return self._json({'batches': dash.daily_list(), 'today': dash.daily()})
        if method == 'GET' and path.startswith('/api/daily/'):
            return self._json(dash.daily(unquote(path.split('/', 3)[-1])))
        if method == 'GET' and path == '/api/leads':
            return self._json(dash.leads(query.get('date')))
        if method == 'GET' and path == '/api/companies':
            return self._json(dash.companies())
        if method == 'GET' and path == '/api/providers':
            return self._json(dash.providers())
        if method == 'POST' and path.startswith('/api/providers/') and path.endswith('/enabled'):
            name = unquote(path[len('/api/providers/'):-len('/enabled')])
            return self._json(dash.set_enabled(name, bool(body.get('enabled'))))
        if method == 'GET' and path == '/api/jobs':
            return self._json(dash.jobs())
        if method == 'GET' and path == '/api/history':
            return self._json(dash.history_view())
        if method == 'POST' and path == '/api/clean':
            rows = body.get('rows')
            if rows is None and body.get('csv'):
                rows = parse_tabular(body['csv'])
            if not isinstance(rows, list):
                raise PolicyError('expected_record_array')
            return self._json(clean_rows(
                rows,
                min_tier=body.get('min_tier') or 'B',
                allow_personal_email=bool(body.get('allow_personal_email')),
                keep_non_decision_makers=bool(body.get('keep_non_decision_makers')),
            ))
        if method == 'POST' and path == '/api/daily/preview':
            sources = body.get('sources')
            if isinstance(sources, str):
                sources = [s.strip() for s in sources.split(',') if s.strip()]
            return self._json(dash.preview_daily(body.get('target') or 50, sources))
        if method == 'POST' and path == '/api/daily/run':
            sources = body.get('sources')
            if isinstance(sources, str):
                sources = [s.strip() for s in sources.split(',') if s.strip()]
            return self._json(dash.daily_run(
                body.get('target') or 20,
                execute=bool(body.get('execute')),
                sources=sources,
            ))
        if method == 'GET' and path == '/api/ops':
            return self._json(dash.ops())
        if method == 'POST' and path == '/api/ops':
            return self._json(dash.update_ops(body))
        if method == 'GET' and path == '/api/budget':
            return self._json(dash.budget_view())
        raise PolicyError('unknown_route')

    def _body(self):
        length = int(self.headers.get('Content-Length') or 0)
        if length <= 0:
            return {}
        if length > 8 * 1024 * 1024:
            raise PolicyError('input_too_large')
        raw = self.rfile.read(length)
        if not raw:
            return {}
        data = json.loads(raw.decode('utf-8'))
        if not isinstance(data, dict):
            raise PolicyError('expected_object')
        return data

    def _json(self, payload, status=200):
        text = json.dumps(payload, ensure_ascii=False, default=str).encode('utf-8')
        self._send(status, text, 'application/json; charset=utf-8')

    def _send(self, status, body, content_type='text/plain; charset=utf-8'):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', 'http://127.0.0.1:3000')
        self.send_header('Access-Control-Allow-Headers', 'content-type')
        self.send_header('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _fail(self, error):
        if isinstance(error, (PolicyError, ProviderError, BudgetError, BusyError)):
            return self._json({'error': str(error)}, 400)
        if isinstance(error, (json.JSONDecodeError, ValueError, KeyError, TypeError, OSError)):
            return self._json({'error': 'invalid_input_or_local_io'}, 400)
        return self._json({'error': 'unexpected_local_error_no_details_logged'}, 500)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Veinore dashboard API (loopback only)')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8787)
    args = parser.parse_args(argv)
    if args.host not in ('127.0.0.1', 'localhost', '::1'):
        raise SystemExit('dashboard API binds loopback only')
    DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    sys.stderr.write('dashboard api on http://%s:%s\n' % (args.host, args.port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
