"""Bounded stdlib HTTP transport. Never retries, follows redirects, or logs secrets."""
import json
import math
import urllib.error
import urllib.parse
import urllib.request

from .models import PolicyError, ProviderError, Request, Response

OFFICIAL_ORIGINS = frozenset({
    'https://api.hunter.io', 'https://api-1.verifalia.com',
    'https://api.clearout.io', 'https://api.zerobounce.net', 'https://disify.com',
})


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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PolicyError('redirect_blocked')


class UrllibTransport:
    """Send one Request and return Response, including HTTP errors for parsing.

    Invalid JSON is represented by json_valid=False. All raised errors are stable
    codes; neither remote text nor request URLs, credentials, or headers escape.
    The optional opener exists for offline unit tests, never automatic retries.
    """
    def __init__(self, max_response_bytes=1_048_576, opener=None):
        if type(max_response_bytes) is not int or max_response_bytes < 1:
            raise ValueError('invalid_response_limit')
        self.max_response_bytes = max_response_bytes
        self._opener = opener or urllib.request.build_opener(_NoRedirect())

    def send(self, request: Request) -> Response:
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
        try:
            req = urllib.request.Request(request.url, request.body, headers, method=request.method)
            try:
                result = self._opener.open(req, timeout=request.timeout)
            except urllib.error.HTTPError as exc:
                result = exc
            with result:
                status = result.code
                if 300 <= status < 400:
                    raise PolicyError('redirect_blocked')
                raw = result.read(self.max_response_bytes + 1)
                if len(raw) > self.max_response_bytes:
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
