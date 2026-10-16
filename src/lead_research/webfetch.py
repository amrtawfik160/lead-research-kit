"""Polite, SSRF-safe HTML fetcher for public company websites.

`SafeFetcher.get(url)` never raises for network or policy problems. It returns a
`FetchResult` whose `error` is one of the stable codes in `ERROR_CODES`.

Safety model:
- Only http/https URLs on ports 80/443 (or default), no userinfo, no control
  characters. Every redirect hop is re-validated; redirects are followed
  manually (at most `max_redirects`) and cross-host redirects are allowed.
- The hostname is resolved before every request and rejected when ANY
  resolved address is not globally routable (private, loopback, link-local,
  multicast, reserved, unspecified, shared, documentation, IPv4-mapped/6to4/
  NAT64 embeddings of those).
- With the default opener the TCP connection is pinned to the validated
  address (TLS still verifies the real hostname), which closes the DNS
  rebinding window between check and connect. A custom `opener` (tests,
  proxies) bypasses pinning; then rebinding remains a documented limitation.
- Environment proxies are ignored by the default opener; no cookies are kept.

Politeness model:
- One request at a time per hostname, spaced by `per_host_delay` seconds (or a
  robots.txt Crawl-delay, capped at `CRAWL_DELAY_CAP`).
- robots.txt is fetched once per origin and cached. Per RFC 9309: 4xx (and a
  redirect loop) means "no rules, allow all"; 5xx, network errors, or an
  unsafe robots location mean "disallow all" for that origin.
"""
from __future__ import annotations

import codecs
import http.client
import ipaddress
import os
import re
import socket
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import zlib
from dataclasses import dataclass, replace
from typing import Any, Callable

DEFAULT_USER_AGENT = 'LeadResearchKit/0.2 (+https://github.com/amrtawfik160/lead-research-kit)'
HTML_TYPES = frozenset({'text/html', 'application/xhtml+xml'})
ROBOTS_MAX_BYTES = 512_000
CRAWL_DELAY_CAP = 10.0
ERROR_CODES = frozenset({
    'blocked_by_robots', 'unsafe_url', 'private_address', 'too_large', 'timeout',
    'http_error', 'redirect_limit', 'not_html', 'network_error',
})
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
# Results worth caching: deterministic answers, not transient failures.
_CACHEABLE_ERRORS = frozenset({None, 'blocked_by_robots', 'not_html', 'too_large', 'redirect_limit'})
_TRANSIENT_4XX = frozenset({408, 425, 429})
_NAT64 = ipaddress.ip_network('64:ff9b::/96')
_QUOTE_SAFE = "/%:@!$&'()*+,;=-._~"
_META_CHARSET = re.compile(rb'<meta[^>]{0,200}?charset\s*=\s*["\']?\s*([A-Za-z0-9._:-]{1,40})', re.I)


@dataclass(frozen=True)
class FetchResult:
    """Outcome of one `SafeFetcher.get` call (after redirects)."""

    url: str
    final_url: str
    status: int | None
    content_type: str
    text: str
    from_cache: bool = False
    fetched_at: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        """True for a successful 2xx HTML (or robots) response."""
        return self.error is None and self.status is not None and 200 <= self.status < 300


class _Stop(Exception):
    """Internal control flow: abort a fetch with a stable error code."""

    def __init__(self, code: str, status: int | None = None):
        super().__init__(code)
        self.code = code
        self.status = status


# --------------------------------------------------------------------------- URL / IP policy

def normalize_url(url: str) -> str:
    """Return a canonical http(s) URL or raise ValueError when it is unsafe.

    Lowercases scheme/host, IDNA-encodes the host, drops fragments and default
    ports, percent-encodes unsafe path/query characters. Rejects non-http
    schemes, userinfo, control characters, backslashes and ports other than
    80/443.
    """
    if not isinstance(url, str):
        raise ValueError('not a string')
    url = url.strip()
    if not url or len(url) > 2048 or '\\' in url or any(ord(c) < 32 or ord(c) == 127 for c in url):
        raise ValueError('bad characters')
    parts = urllib.parse.urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ('http', 'https'):
        raise ValueError('scheme')
    if '@' in parts.netloc or parts.username is not None or parts.password is not None:
        raise ValueError('userinfo')
    host = parts.hostname
    port = parts.port  # raises ValueError for garbage ports
    if not host or port not in (None, 80, 443):
        raise ValueError('host/port')
    try:
        host = host.encode('idna').decode('ascii').rstrip('.')
    except UnicodeError:
        raise ValueError('idna') from None
    if not host or any(c.isspace() for c in host):
        raise ValueError('host')
    netloc = f'[{host}]' if ':' in host else host
    if port is not None and not ((scheme, port) in (('http', 80), ('https', 443))):
        netloc += f':{port}'
    path = urllib.parse.quote(parts.path or '/', safe=_QUOTE_SAFE)
    query = urllib.parse.quote(parts.query, safe=_QUOTE_SAFE + '?')
    return urllib.parse.urlunsplit((scheme, netloc, path, query, ''))


def is_public_address(value: str) -> bool:
    """True only for globally routable unicast addresses (IPv4 or IPv6)."""
    try:
        ip = ipaddress.ip_address(str(value).split('%', 1)[0])
    except ValueError:
        return False
    if ip.version == 6:
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip.sixtofour is not None:
            ip = ip.sixtofour
        elif ip in _NAT64:
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        elif ip.teredo is not None:
            return False
    return bool(ip.is_global) and not (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified)


# --------------------------------------------------------------------------- default opener

class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection that connects to a pre-validated IP instead of re-resolving."""

    def __init__(self, *args: Any, pinned: str | None = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._pinned = pinned

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._pinned or self.host, self.port), self.timeout, self.source_address)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPSConnection pinned to a validated IP; TLS verifies the real hostname."""

    def __init__(self, *args: Any, pinned: str | None = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._pinned = pinned

    def connect(self) -> None:
        sock = socket.create_connection(
            (self._pinned or self.host, self.port), self.timeout, self.source_address)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):  # noqa: D401 - urllib hook
        return self.do_open(_PinnedHTTPConnection, req, pinned=getattr(req, 'pinned_address', None))


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):  # noqa: D401 - urllib hook
        return self.do_open(_PinnedHTTPSConnection, req, context=self._context,
                            pinned=getattr(req, 'pinned_address', None))


class _PassThroughErrors(urllib.request.HTTPErrorProcessor):
    """Return every response (3xx/4xx/5xx) as-is; redirects are handled by SafeFetcher."""

    def http_response(self, request, response):
        return response

    https_response = http_response


def build_default_opener() -> urllib.request.OpenerDirector:
    """Opener with IP pinning, no proxies, no cookies, no automatic redirects."""
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _PinnedHTTPHandler(), _PinnedHTTPSHandler(),
        _PassThroughErrors())


# --------------------------------------------------------------------------- body helpers

def _decompress(raw: bytes, encoding: str, limit: int) -> tuple[bytes, bool]:
    """Bounded gzip/deflate decoding. Returns (data, truncated)."""
    encoding = (encoding or 'identity').strip().lower()
    if encoding in ('', 'identity'):
        return raw[:limit], len(raw) > limit
    if encoding in ('gzip', 'x-gzip'):
        wbits_options = (16 + zlib.MAX_WBITS,)
    elif encoding == 'deflate':
        wbits_options = (zlib.MAX_WBITS, -zlib.MAX_WBITS)
    else:
        raise _Stop('network_error')
    for wbits in wbits_options:
        try:
            out = zlib.decompressobj(wbits).decompress(raw, limit + 1)
        except zlib.error:
            continue
        return out[:limit], len(out) > limit
    raise _Stop('network_error')


def decode_body(body: bytes, content_type: str = '') -> str:
    """Decode bytes via header charset, BOM, <meta charset>, then UTF-8 (with replacement)."""
    charset = None
    match = re.search(r'charset\s*=\s*["\']?([A-Za-z0-9._:-]+)', content_type or '', re.I)
    if match:
        charset = match.group(1)
    elif body.startswith(codecs.BOM_UTF8):
        charset = 'utf-8-sig'
    elif body.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        charset = 'utf-16'
    else:
        meta = _META_CHARSET.search(body[:4096])
        if meta:
            charset = meta.group(1).decode('ascii', 'ignore')
    try:
        name = codecs.lookup(charset).name if charset else 'utf-8'
    except LookupError:
        name = 'utf-8'
    if name in ('latin-1', 'iso8859-1', 'ascii'):
        name = 'cp1252'  # what browsers actually do
    return body.decode(name, 'replace')


def _looks_like_html(body: bytes) -> bool:
    head = body[:512].lstrip().lower()
    return head.startswith((b'<!doctype html', b'<html', b'<head', b'<body'))


# --------------------------------------------------------------------------- cache

class _Cache:
    """Thread-safe sqlite cache of fetch results keyed by normalized URL."""

    def __init__(self, path: str):
        path = os.path.abspath(os.path.expanduser(path))
        parent = os.path.dirname(path)
        if not os.path.isdir(parent):
            os.makedirs(parent, mode=0o700, exist_ok=True)
            os.chmod(parent, 0o700)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        os.close(fd)
        os.chmod(path, 0o600)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
        with self._lock, self._conn:
            self._conn.execute(
                'CREATE TABLE IF NOT EXISTS pages (url TEXT PRIMARY KEY, final_url TEXT NOT NULL, '
                'status INTEGER, content_type TEXT NOT NULL, body BLOB NOT NULL, error TEXT, '
                'fetched_at REAL NOT NULL)')

    def get(self, key: str, now: float, max_age: float) -> FetchResult | None:
        with self._lock:
            row = self._conn.execute(
                'SELECT final_url, status, content_type, body, error, fetched_at FROM pages WHERE url = ?',
                (key,)).fetchone()
        if row is None or now - row[5] > max_age:
            return None
        try:
            text = zlib.decompress(row[3]).decode('utf-8')
        except (zlib.error, UnicodeDecodeError):
            return None
        return FetchResult(key, row[0], row[1], row[2], text, True, row[5], row[4])

    def put(self, key: str, result: FetchResult) -> None:
        body = zlib.compress(result.text.encode('utf-8'), 6)
        with self._lock, self._conn:
            self._conn.execute(
                'INSERT OR REPLACE INTO pages VALUES (?, ?, ?, ?, ?, ?, ?)',
                (key, result.final_url, result.status, result.content_type, body,
                 result.error, result.fetched_at))

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# --------------------------------------------------------------------------- fetcher

class _HostState:
    __slots__ = ('lock', 'last', 'delay')

    def __init__(self, delay: float):
        self.lock = threading.Lock()
        self.last: float | None = None
        self.delay = delay


class _RobotsEntry:
    __slots__ = ('lock', 'parser')

    def __init__(self):
        self.lock = threading.Lock()
        self.parser: urllib.robotparser.RobotFileParser | None = None


class SafeFetcher:
    """Thread-safe, polite, SSRF-safe GET client for HTML pages.

    `opener` must provide `open(request, timeout)` returning a response with
    `.status`/`.code`, `.headers` and `.read(n)` (urllib-compatible); urllib
    `HTTPError` is also accepted. `resolver` must be getaddrinfo-compatible.
    `clock`/`sleep` drive politeness and cache age (injectable for tests).
    """

    def __init__(self, cache_path: str | None = None, user_agent: str = DEFAULT_USER_AGENT,
                 per_host_delay: float = 1.0, timeout: float = 15, max_bytes: int = 2_000_000,
                 max_redirects: int = 4, respect_robots: bool = True,
                 cache_max_age: float = 14 * 86400,
                 resolver: Callable[..., Any] = socket.getaddrinfo,
                 opener: Any = None, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep):
        if max_bytes < 1 or max_redirects < 0 or timeout <= 0 or per_host_delay < 0:
            raise ValueError('invalid_fetcher_settings')
        self.user_agent = user_agent
        self.per_host_delay = float(per_host_delay)
        self.timeout = timeout
        self.max_bytes = int(max_bytes)
        self.max_redirects = int(max_redirects)
        self.respect_robots = respect_robots
        self.cache_max_age = cache_max_age
        self._resolver = resolver
        self._opener = opener or build_default_opener()
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._hosts: dict[str, _HostState] = {}
        self._robots: dict[str, _RobotsEntry] = {}
        self._cache = _Cache(cache_path) if cache_path else None
        self._headers = {
            'User-Agent': user_agent,
            'Accept': 'text/html,application/xhtml+xml;q=0.9,text/plain;q=0.5,*/*;q=0.1',
            'Accept-Encoding': 'gzip, deflate',
            'Accept-Language': 'en-US,en;q=0.8',
        }

    # ---------------------------------------------------------------- public

    def get(self, url: str) -> FetchResult:
        """Fetch one HTML page (following safe redirects). Never raises for I/O problems."""
        now = self._clock()
        try:
            key = normalize_url(url)
        except ValueError:
            return FetchResult(str(url), str(url), None, '', '', False, now, 'unsafe_url')
        if self._cache is not None:
            cached = self._cache.get(key, now, self.cache_max_age)
            if cached is not None:
                return replace(cached, url=url)
        try:
            result = self._fetch(key, original=url, for_robots=False)
        except Exception:  # defensive: never let a bug in a hop kill a crawl batch
            result = FetchResult(url, key, None, '', '', False, self._clock(), 'network_error')
        if self._cache is not None and self._is_cacheable(result):
            self._cache.put(key, result)
        return result

    def close(self) -> None:
        """Close the cache connection (if any)."""
        if self._cache is not None:
            self._cache.close()

    def __enter__(self) -> 'SafeFetcher':
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def host_delay(self, host: str) -> float:
        """Effective minimum spacing for `host` (per_host_delay or robots Crawl-delay)."""
        return self._host_state(host.lower()).delay

    # ---------------------------------------------------------------- internals

    @staticmethod
    def _is_cacheable(result: FetchResult) -> bool:
        if result.error in _CACHEABLE_ERRORS:
            return True
        return (result.error == 'http_error' and result.status is not None
                and 400 <= result.status < 500 and result.status not in _TRANSIENT_4XX)

    def _host_state(self, host: str) -> _HostState:
        with self._lock:
            state = self._hosts.get(host)
            if state is None:
                state = self._hosts[host] = _HostState(self.per_host_delay)
            return state

    def _resolve(self, host: str, port: int) -> str:
        """Resolve and validate every address; return the one to connect to."""
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:  # IP literal: validate as-is, never trust a resolver to echo it
            if not is_public_address(host):
                raise _Stop('private_address')
            return host
        try:
            infos = self._resolver(host, port, 0, socket.SOCK_STREAM)
            addresses = [str(info[4][0]) for info in infos]
        except (OSError, UnicodeError, ValueError, TypeError, IndexError):
            raise _Stop('network_error') from None
        if not addresses:
            raise _Stop('network_error')
        if not all(is_public_address(a) for a in addresses):
            raise _Stop('private_address')
        return sorted(addresses, key=lambda a: ':' in a)[0]  # prefer IPv4

    def _fetch(self, url: str, *, original: str, for_robots: bool) -> FetchResult:
        current = url
        limit = min(self.max_bytes, ROBOTS_MAX_BYTES) if for_robots else self.max_bytes
        for hop in range(self.max_redirects + 1):
            try:
                if hop:
                    try:
                        current = normalize_url(current)
                    except ValueError:
                        raise _Stop('unsafe_url') from None
                parts = urllib.parse.urlsplit(current)
                host = parts.hostname or ''
                port = parts.port or (443 if parts.scheme == 'https' else 80)
                pinned = self._resolve(host, port)
                if not for_robots and self.respect_robots and not self._robots_allowed(current):
                    raise _Stop('blocked_by_robots')
                status, headers, body, truncated = self._polite_request(
                    host, current, pinned, limit, want_html=not for_robots)
            except _Stop as stop:
                return FetchResult(original, current, stop.status, '', '', False, self._clock(), stop.code)
            if status in REDIRECT_STATUSES:
                location = (headers.get('location') or '').strip()
                if not location:
                    return FetchResult(original, current, status, '', '', False, self._clock(), 'http_error')
                nxt = urllib.parse.urljoin(current, location)
                if urllib.parse.urlsplit(nxt).scheme.lower() not in ('http', 'https'):
                    return FetchResult(original, current, status, '', '', False, self._clock(), 'unsafe_url')
                current = nxt
                continue
            return self._finish(original, current, status, headers, body, truncated, for_robots)
        return FetchResult(original, current, None, '', '', False, self._clock(), 'redirect_limit')

    def _finish(self, original: str, final: str, status: int, headers: dict[str, str],
                body: bytes | None, truncated: bool, for_robots: bool) -> FetchResult:
        raw_type = headers.get('content-type', '')
        ctype = raw_type.split(';', 1)[0].strip().lower()
        now = self._clock()
        if for_robots:
            text = decode_body(body or b'', raw_type) if 200 <= status < 300 else ''
            return FetchResult(original, final, status, ctype, text, False, now, None)
        if not 200 <= status < 300:
            return FetchResult(original, final, status, ctype, '', False, now, 'http_error')
        if body is None or (ctype and ctype not in HTML_TYPES) or (not ctype and not _looks_like_html(body)):
            return FetchResult(original, final, status, ctype, '', False, now, 'not_html')
        if truncated:
            return FetchResult(original, final, status, ctype, '', False, now, 'too_large')
        return FetchResult(original, final, status, ctype or 'text/html', decode_body(body, raw_type),
                           False, now, None)

    def _polite_request(self, host: str, url: str, pinned: str, limit: int, *,
                        want_html: bool) -> tuple[int, dict[str, str], bytes | None, bool]:
        """Serialize requests per host and enforce the minimum spacing."""
        state = self._host_state(host)
        with state.lock:
            if state.last is not None:
                wait = state.last + state.delay - self._clock()
                if wait > 0:
                    self._sleep(wait)
            state.last = self._clock()
            return self._request(url, pinned, limit, want_html)

    def _request(self, url: str, pinned: str, limit: int,
                 want_html: bool) -> tuple[int, dict[str, str], bytes | None, bool]:
        req = urllib.request.Request(url, headers=dict(self._headers), method='GET')
        req.pinned_address = pinned  # consumed by the pinned handlers
        try:
            resp = self._opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            resp = exc
        except TimeoutError:
            raise _Stop('timeout') from None
        except urllib.error.URLError as exc:
            raise _Stop('timeout' if isinstance(exc.reason, TimeoutError) else 'network_error') from None
        except (OSError, http.client.HTTPException, ValueError):
            raise _Stop('network_error') from None
        try:
            with resp:
                status = int(getattr(resp, 'status', None) or resp.code)
                headers = {str(k).lower(): str(v) for k, v in resp.headers.items()}
                ctype = headers.get('content-type', '').split(';', 1)[0].strip().lower()
                if status in REDIRECT_STATUSES or not 200 <= status < 300:
                    return status, headers, None, False
                if want_html and ctype and ctype not in HTML_TYPES:
                    return status, headers, None, False
                declared = headers.get('content-length', '').strip()
                if want_html and declared.isdigit() and int(declared) > limit:
                    raise _Stop('too_large', status)
                raw = self._read_bounded(resp, limit)
        except _Stop:
            raise
        except TimeoutError:
            raise _Stop('timeout') from None
        except (OSError, http.client.HTTPException, ValueError, AttributeError, TypeError):
            raise _Stop('network_error') from None
        body, truncated = _decompress(raw, headers.get('content-encoding', ''), limit)
        truncated = truncated or len(raw) > limit
        return status, headers, body, truncated

    def _read_bounded(self, resp: Any, limit: int) -> bytes:
        """Read at most limit+1 bytes, with an overall deadline of 2x timeout."""
        deadline = time.monotonic() + 2 * self.timeout
        chunks: list[bytes] = []
        size = 0
        while size <= limit:
            chunk = resp.read(min(65536, limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if time.monotonic() > deadline:
                raise _Stop('timeout')
        return b''.join(chunks)

    # ---------------------------------------------------------------- robots

    def _robots_allowed(self, url: str) -> bool:
        parts = urllib.parse.urlsplit(url)
        origin = f'{parts.scheme}://{parts.netloc}'
        with self._lock:
            entry = self._robots.get(origin)
            if entry is None:
                entry = self._robots[origin] = _RobotsEntry()
        with entry.lock:
            if entry.parser is None:
                entry.parser = self._load_robots(origin, (parts.hostname or '').lower())
            parser = entry.parser
        return parser.can_fetch(self.user_agent, url)

    def _load_robots(self, origin: str, host: str) -> urllib.robotparser.RobotFileParser:
        robots_url = origin + '/robots.txt'
        now = self._clock()
        result = self._cache.get(robots_url, now, self.cache_max_age) if self._cache else None
        if result is None:
            try:
                result = self._fetch(robots_url, original=robots_url, for_robots=True)
            except Exception:
                result = FetchResult(robots_url, robots_url, None, '', '', False, now, 'network_error')
            if self._cache is not None and (result.error == 'redirect_limit' or (
                    result.error is None and result.status is not None and result.status < 500)):
                self._cache.put(robots_url, result)
        parser = urllib.robotparser.RobotFileParser(robots_url)
        if result.error == 'redirect_limit' or (
                result.error is None and result.status is not None and 400 <= result.status < 500):
            parser.allow_all = True
        elif result.error is not None or result.status is None or not 200 <= result.status < 300:
            parser.disallow_all = True
        else:
            parser.parse(result.text.splitlines())
            delay = parser.crawl_delay(self.user_agent)
            if delay:
                try:
                    wanted = min(float(delay), CRAWL_DELAY_CAP)
                except (TypeError, ValueError):
                    wanted = 0.0
                state = self._host_state(host)
                state.delay = max(state.delay, wanted)
        return parser
