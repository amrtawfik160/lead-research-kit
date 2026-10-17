"""Bounded stdlib HTTP transport. Never retries, follows redirects, or logs secrets."""
import json
import math
import threading
import urllib.error
import urllib.parse
import urllib.request

from . import __version__

from .models import PolicyError, ProviderError, Request, Response

OFFICIAL_ORIGINS = frozenset({
    'https://api.hunter.io', 'https://api-1.verifalia.com',
    'https://api.clearout.io', 'https://api.zerobounce.net', 'https://disify.com',
    'https://google.serper.dev', 'https://api.search.brave.com', 'https://overpass-api.de',
    'https://api.apollo.io', 'https://api.prospeo.io', 'https://places.googleapis.com', 'https://api.exa.ai',
    'https://api.tomba.io', 'https://app.icypeas.com', 'https://emailverifier.reoon.com',
    'https://api.millionverifier.com',
    'https://api.tavily.com', 'https://api.firecrawl.dev', 'https://emailreputation.abstractapi.com',
    'https://api.emailable.com', 'https://api.kickbox.com', 'https://api.usebouncer.com',
    'https://api.myemailverifier.com', 'https://api.peopledatalabs.com', 'https://serpapi.com',
    'https://code.deepline.com',
    'https://api.quickemailverification.com', 'https://api.emailvalidation.io', 'https://api.emaillistverify.com',
    'https://api.enrow.io', 'https://api.lusha.com', 'https://app.zenserp.com', 'https://api.linkup.so',
    'https://ydc-index.io',
})
# Hard ceiling for any per-request response cap (Overpass place lists can be large).
MAX_RESPONSE_CEILING = 5 * 1024 * 1024


def validate_url(url: str, origins=OFFICIAL_ORIGINS, *, evidence=False) -> str:
    """Accept only HTTPS official origins; evidence URLs cannot carry queries."""
    try:
        if not isinstance(url, str) or any(ord(c) < 33 or ord(c) == 127 for c in url) or '\\' in url:
            raise ValueError
        p = urllib.parse.urlsplit(url)
        if (p.scheme != 'https' or p.username is not None or p.password is not None
                or p.fragment or p.netloc != p.hostname or f'https://{p.hostname}' not in origins
                or (evidence and p.query)):
            raise ValueError
    except (ValueError, TypeError):
        raise PolicyError('unsafe_url') from None
    return url


USER_AGENT = f'lead-research-kit/{__version__} (+https://github.com/amrtawfik160/lead-research-kit)'

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PolicyError('redirect_blocked')


class UrllibTransport:
    """Send one Request and return Response, including HTTP errors for parsing.

    Invalid JSON is represented by json_valid=False. All raised errors are stable
    codes; neither remote text nor request URLs, credentials, or headers escape.
    The optional opener exists for offline unit tests, never automatic retries.
    """
    deadline_pad = 10.0

    def __init__(self, max_response_bytes=1_048_576, opener=None):
        if type(max_response_bytes) is not int or max_response_bytes < 1:
            raise ValueError('invalid_response_limit')
        self.max_response_bytes = max_response_bytes
        self._opener = opener or urllib.request.build_opener(_NoRedirect())

    def send(self, request: Request) -> Response:
        """Run the request with a hard overall deadline (2x timeout + 10s).

        Socket timeouts only bound each read; a stalled DNS lookup or a server that
        trickles bytes could otherwise hold the single active ledger attempt forever.
        On deadline the worker thread is abandoned (daemon) and a timeout error is raised,
        which the Runner records as uncertain without retrying.
        """
        outcome = {}

        def work():
            try:
                outcome['response'] = self._send_once(request)
            except BaseException as exc:  # re-raised in the caller thread
                outcome['error'] = exc

        timeout = request.timeout
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            return self._send_once(request)  # invalid timeouts are rejected there with a stable code
        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        worker.join(min(float(timeout) * 2 + self.deadline_pad, 250))
        if worker.is_alive():
            raise ProviderError('request_deadline_exceeded')
        if 'error' in outcome:
            raise outcome['error']
        return outcome['response']

    def _send_once(self, request: Request) -> Response:
        validate_url(request.url)
        if request.method not in ('GET', 'POST'):
            raise PolicyError('unsupported_http_method')
        if (not isinstance(request.timeout, (float, int)) or isinstance(request.timeout, bool)
                or not math.isfinite(request.timeout) or not 0 < request.timeout <= 120):
            raise PolicyError('invalid_timeout')
        headers = {}
        for k, v in request.headers.items():
            if (not isinstance(k, str) or not isinstance(v, str) or not k
                    or any(not (c.isascii() and (c.isalnum() or c in "!#$%&'*+-.^_`|~")) for c in k)
                    or any(ord(c) < 32 or ord(c) == 127 for c in v)):
                raise PolicyError('invalid_headers')
            headers[k] = v
        if request.body is not None and not isinstance(request.body, bytes):
            raise PolicyError('invalid_body')
        limit = getattr(request, 'max_response_bytes', None)
        if limit is None:
            limit = self.max_response_bytes
        elif type(limit) is not int or not 1 <= limit <= MAX_RESPONSE_CEILING:
            raise PolicyError('invalid_response_limit')
        try:
            if not any(k.lower() == 'user-agent' for k in headers):
                # Python's default "Python-urllib" agent is refused by some provider firewalls (Cloudflare 1010)
                headers['User-Agent'] = USER_AGENT
            req = urllib.request.Request(request.url, request.body, headers, method=request.method)
            try:
                result = self._opener.open(req, timeout=request.timeout)
            except urllib.error.HTTPError as exc:
                result = exc
            with result:
                status = result.code
                if 300 <= status < 400:
                    raise PolicyError('redirect_blocked')
                raw = result.read(limit + 1)
                if len(raw) > limit:
                    raise ProviderError('response_too_large')
                response_headers = dict(result.headers.items())
            try:
                data = json.loads(raw)
                return Response(status, data, response_headers, True)
            except (ValueError, UnicodeError):
                return Response(status, None, response_headers, False)
        except (PolicyError, ProviderError):
            raise
        except Exception:
            raise ProviderError('transport_error') from None
