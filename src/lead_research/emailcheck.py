"""Free mail-domain checks: does an email's domain accept mail at all?

Standard library only. A tiny DNS client sends MX queries over UDP (TCP when the
answer is truncated) to the system resolvers from ``/etc/resolv.conf``, falling
back to 1.1.1.1 and 8.8.8.8. Per RFC 5321 a domain without MX records but with an
A/AAAA record still receives mail ("implicit MX"); per RFC 7505 a single MX of
"." (null MX) means the domain never accepts mail.

``mx_lookup`` returns one dict per domain::

    {'domain', 'has_mx': True | False | None, 'mx': [hosts by preference],
     'null_mx': bool, 'implicit': bool, 'provider': 'google' | 'microsoft365' | 'other' | '',
     'error': None | 'nxdomain' | 'no_mx' | 'null_mx' | 'dns_error' | 'invalid_domain'}

``has_mx`` is None when DNS could not answer (timeouts, SERVFAIL): unknown never
counts as "no mail server". Results are cached in-process and, through
``check_domains``, in a ``domain_mx`` table (re-checked after 30 days).
"""
from __future__ import annotations

import json
import os
import random
import re
import socket
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor

FALLBACK_RESOLVERS = ('1.1.1.1', '8.8.8.8')
TYPE_A, TYPE_MX, TYPE_AAAA = 1, 15, 28
RCODE_NXDOMAIN = 3
MAX_AGE_DAYS = 30
_DOMAIN_RE = re.compile(r'^(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$')

_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()


class DnsError(Exception):
    """No usable answer from any resolver (timeout, SERVFAIL, malformed)."""


def system_resolvers(path='/etc/resolv.conf'):
    """Nameservers from resolv.conf (IPv4/IPv6), then the public fallbacks."""
    servers = []
    try:
        with open(path, encoding='utf-8', errors='replace') as handle:
            for line in handle:
                parts = line.split()
                if len(parts) >= 2 and parts[0] == 'nameserver':
                    server = parts[1].split('%', 1)[0]
                    if server and server not in servers:
                        servers.append(server)
    except OSError:
        pass
    for server in FALLBACK_RESOLVERS:
        if server not in servers:
            servers.append(server)
    return servers


def _encode_name(name):
    out = b''
    for label in name.rstrip('.').split('.'):
        raw = label.encode('idna') if any(ord(c) > 127 for c in label) else label.encode('ascii')
        if not raw or len(raw) > 63:
            raise ValueError('invalid_domain')
        out += bytes([len(raw)]) + raw
    return out + b'\x00'


def build_query(name, qtype, ident=None):
    ident = random.randrange(0, 65536) if ident is None else ident
    header = struct.pack('>HHHHHH', ident, 0x0100, 1, 0, 0, 0)  # recursion desired
    return header + _encode_name(name) + struct.pack('>HH', qtype, 1)


def _read_name(data, offset, depth=0):
    labels = []
    jumped, end = False, offset
    while True:
        if offset >= len(data) or depth > 20:
            raise DnsError('malformed')
        length = data[offset]
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(data):
                raise DnsError('malformed')
            pointer = ((length & 0x3F) << 8) | data[offset + 1]
            if not jumped:
                end = offset + 2
            jumped, offset, depth = True, pointer, depth + 1
            continue
        if length == 0:
            if not jumped:
                end = offset + 1
            break
        offset += 1
        labels.append(data[offset:offset + length].decode('ascii', errors='replace'))
        offset += length
    return '.'.join(labels).lower(), end


def parse_response(data, ident=None):
    """(rcode, truncated, [(type, value)]) where MX value is (preference, host)."""
    if len(data) < 12:
        raise DnsError('malformed')
    rid, flags, qd, an, _ns, _ar = struct.unpack('>HHHHHH', data[:12])
    if ident is not None and rid != ident:
        raise DnsError('id_mismatch')
    if not flags & 0x8000:
        raise DnsError('not_a_response')
    rcode, truncated = flags & 0x000F, bool(flags & 0x0200)
    offset = 12
    for _ in range(qd):
        _, offset = _read_name(data, offset)
        offset += 4
    answers = []
    for _ in range(an):
        _, offset = _read_name(data, offset)
        if offset + 10 > len(data):
            raise DnsError('malformed')
        rtype, _cls, _ttl, rdlen = struct.unpack('>HHIH', data[offset:offset + 10])
        offset += 10
        rdata_at = offset
        offset += rdlen
        if offset > len(data):
            raise DnsError('malformed')
        if rtype == TYPE_MX and rdlen >= 3:
            preference = struct.unpack('>H', data[rdata_at:rdata_at + 2])[0]
            host, _ = _read_name(data, rdata_at + 2)
            answers.append((TYPE_MX, (preference, host)))
        elif rtype == TYPE_A and rdlen == 4:
            answers.append((TYPE_A, socket.inet_ntoa(data[rdata_at:offset])))
        elif rtype == TYPE_AAAA and rdlen == 16:
            answers.append((TYPE_AAAA, socket.inet_ntop(socket.AF_INET6, data[rdata_at:offset])))
    return rcode, truncated, answers


class DnsResolver:
    """Minimal recursive-resolver client. ``query`` returns (rcode, answers)."""

    def __init__(self, servers=None, timeout=2.0, tries=2):
        override = [x.strip() for x in os.environ.get('LEAD_RESEARCH_DNS_SERVERS', '').split(',') if x.strip()]
        self.servers = list(servers or override or system_resolvers())  # override e.g. "9.9.9.9,1.1.1.1"
        self.timeout = timeout
        self.tries = tries

    def _udp(self, server, packet):
        family = socket.AF_INET6 if ':' in server else socket.AF_INET
        with socket.socket(family, socket.SOCK_DGRAM) as sock:
            sock.settimeout(self.timeout)
            sock.sendto(packet, (server, 53))
            data, _ = sock.recvfrom(4096)
            return data

    def _tcp(self, server, packet):
        family = socket.AF_INET6 if ':' in server else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(self.timeout)
            sock.connect((server, 53))
            sock.sendall(struct.pack('>H', len(packet)) + packet)
            size = struct.unpack('>H', _recv_exact(sock, 2))[0]
            return _recv_exact(sock, size)

    def query(self, name, qtype):
        last = 'dns_error'
        for attempt in range(self.tries):
            for server in self.servers:
                ident = random.randrange(0, 65536)
                packet = build_query(name, qtype, ident)
                try:
                    rcode, truncated, answers = parse_response(self._udp(server, packet), ident)
                    if truncated:
                        rcode, _, answers = parse_response(self._tcp(server, packet), ident)
                except (OSError, DnsError, struct.error) as error:
                    last = getattr(error, 'args', ['dns_error'])[0] if isinstance(error, DnsError) else 'dns_error'
                    continue
                if rcode in (0, RCODE_NXDOMAIN):
                    return rcode, answers
                last = f'rcode_{rcode}'
        raise DnsError(last)


def _recv_exact(sock, size):
    data = b''
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise DnsError('short_read')
        data += chunk
    return data


def mx_provider(hosts):
    """Informational: which mail platform the MX hosts belong to."""
    joined = ' '.join(h.lower().rstrip('.') for h in hosts or ())
    if not joined:
        return ''
    if re.search(r'(^|\.)(google\.com|googlemail\.com)(\s|$)', joined):
        return 'google'
    if re.search(r'\.(mail\.protection\.outlook\.com|outlook\.com|office365\.us)(\s|$)', joined):
        return 'microsoft365'
    return 'other'


def _result(domain, **values):
    out = {'domain': domain, 'has_mx': None, 'mx': [], 'null_mx': False, 'implicit': False, 'provider': '', 'error': None}
    out.update(values)
    return out


def _lookup(domain, resolver):
    try:
        rcode, answers = resolver.query(domain, TYPE_MX)
    except DnsError:
        return _result(domain, error='dns_error')
    if rcode == RCODE_NXDOMAIN:
        return _result(domain, has_mx=False, error='nxdomain')
    mx = sorted((v for t, v in answers if t == TYPE_MX), key=lambda item: (item[0], item[1]))
    if mx:
        hosts = [host for _, host in mx if host]
        if not hosts:  # RFC 7505: "0 ." only
            return _result(domain, has_mx=False, null_mx=True, error='null_mx')
        return _result(domain, has_mx=True, mx=hosts, provider=mx_provider(hosts))
    # No MX: implicit MX when the domain itself has an address (RFC 5321 5.1)
    for qtype in (TYPE_A, TYPE_AAAA):
        try:
            rcode, answers = resolver.query(domain, qtype)
        except DnsError:
            return _result(domain, error='dns_error')
        if any(t == qtype for t, _ in answers):
            return _result(domain, has_mx=True, mx=[domain], implicit=True, provider='other')
    return _result(domain, has_mx=False, error='no_mx')


def normalize_mail_domain(value):
    domain = (value or '').strip().lower().rstrip('.')
    if '@' in domain:
        domain = domain.rsplit('@', 1)[1]
    return domain if _DOMAIN_RE.match(domain) else ''


def mx_lookup(domain, resolver=None, use_cache=True):
    """Mail-server facts for one domain (see module docstring)."""
    clean = normalize_mail_domain(domain)
    if not clean:
        return _result(domain or '', has_mx=False, error='invalid_domain')
    if use_cache:
        with _cache_lock:
            if clean in _cache:
                return dict(_cache[clean])
    result = _lookup(clean, resolver or DnsResolver())
    if use_cache and result['error'] != 'dns_error':
        with _cache_lock:
            _cache[clean] = dict(result)
    return result


def lookup_many(domains, resolver=None, workers=32):
    """Parallel ``mx_lookup`` for many domains; returns {domain: result}."""
    unique = list(dict.fromkeys(d for d in (normalize_mail_domain(x) for x in domains) if d))
    if not unique:
        return {}
    resolver = resolver or DnsResolver()
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(unique)))) as pool:
        results = list(pool.map(lambda d: mx_lookup(d, resolver), unique))
    return dict(zip(unique, results))


def clear_cache():
    with _cache_lock:
        _cache.clear()


# Persistent cache -----------------------------------------------------------

def ensure_table(db):
    db.execute('CREATE TABLE IF NOT EXISTS domain_mx(domain TEXT PRIMARY KEY, checked_at REAL NOT NULL, data TEXT NOT NULL)')


def load_cached(db, domains, max_age_days=MAX_AGE_DAYS, clock=time.time):
    ensure_table(db)
    cutoff = clock() - max_age_days * 86400
    found = {}
    domains = list(domains)
    for start in range(0, len(domains), 500):
        chunk = domains[start:start + 500]
        marks = ','.join('?' * len(chunk))
        for row in db.execute(f'SELECT domain, data FROM domain_mx WHERE checked_at >= ? AND domain IN ({marks})', (cutoff, *chunk)):
            found[row[0]] = json.loads(row[1])
    return found


def save(db, results, clock=time.time):
    """Persist definite answers only; DNS errors are retried next run."""
    ensure_table(db)
    now = clock()
    with db:
        for domain, result in results.items():
            if result.get('error') == 'dns_error':
                continue
            db.execute('INSERT OR REPLACE INTO domain_mx(domain, checked_at, data) VALUES (?, ?, ?)',
                       (domain, now, json.dumps(result, sort_keys=True)))


def check_domains(db, domains, resolver=None, workers=32, max_age_days=MAX_AGE_DAYS, clock=time.time):
    """Cached-then-live MX facts for many domains, stored in ``domain_mx``."""
    unique = list(dict.fromkeys(d for d in (normalize_mail_domain(x) for x in domains) if d))
    cached = load_cached(db, unique, max_age_days, clock)
    missing = [d for d in unique if d not in cached]
    fresh = lookup_many(missing, resolver, workers) if missing else {}
    save(db, fresh, clock)
    return {**cached, **fresh}


def summarize(results):
    counts = {'domains': len(results), 'has_mx': 0, 'no_mail': 0, 'null_mx': 0, 'unknown': 0, 'implicit': 0}
    for result in results.values():
        if result.get('has_mx') is True:
            counts['has_mx'] += 1
            counts['implicit'] += 1 if result.get('implicit') else 0
        elif result.get('has_mx') is False:
            counts['no_mail'] += 1
            counts['null_mx'] += 1 if result.get('null_mx') else 0
        else:
            counts['unknown'] += 1
    providers = {}
    for result in results.values():
        if result.get('provider'):
            providers[result['provider']] = providers.get(result['provider'], 0) + 1
    counts['providers'] = providers
    return counts



# Risk-first verification order ----------------------------------------------

VERIFY_GROUPS = ('pattern_fresh_dm', 'published_weak_unknown_domain', 'finder', 'pattern_other', 'published_unchecked')
_TIER_RANK = {'A': 0, 'B': 1, 'C': 2}


def prioritize_verify(candidates, history_info=None, published_cap=25):
    """Order verifier spending so each free check buys the most deliverable leads.

    ``candidates``: dicts with ``email``, ``kind`` ('pattern' | 'published' | 'finder'),
    and optionally ``decision_maker``, ``fresh``, ``name_match``, ``tier``, ``score``.
    ``history_info``: ``checked`` (emails already sent to a verifier), ``catch_all_domains``
    (a check there only says "accept-all"), ``checked_domains`` (domains whose catch-all
    status is known from earlier results) and ``no_mail_domains`` (no MX / null MX).

    Order: 1) pattern guesses for fresh decision makers (each valid one is a new lead),
    2) published emails that do not look like the person's name, on domains never checked,
    3) finder results, 4) other pattern guesses, 5) up to ``published_cap`` other published
    emails never checked. Returns {'emails', 'groups', 'skipped'}.
    """
    info = history_info or {}
    checked = {e.lower() for e in info.get('checked', ())}
    catch_all = {d.lower() for d in info.get('catch_all_domains', ())}
    known = {d.lower() for d in info.get('checked_domains', ())}
    no_mail = {d.lower() for d in info.get('no_mail_domains', ())}
    buckets = {name: [] for name in VERIFY_GROUPS}
    skipped = {}

    def skip(reason):
        skipped[reason] = skipped.get(reason, 0) + 1

    seen = set()
    for item in candidates or ():
        email = (item.get('email') or '').strip().lower()
        if '@' not in email:
            skip('invalid_email')
            continue
        domain = email.rsplit('@', 1)[1]
        kind = item.get('kind')
        fresh = item.get('fresh', True)
        if email in checked:
            skip('already_checked')
            continue
        if domain in no_mail:
            skip('no_mail_server')
            continue
        if domain in catch_all:
            skip('catch_all_domain')
            continue
        if kind == 'pattern':
            group = 'pattern_fresh_dm' if fresh and item.get('decision_maker', True) else 'pattern_other'
        elif kind == 'finder':
            group = 'finder'
        elif kind == 'published':
            if not fresh:
                skip('not_fresh')
                continue
            group = 'published_weak_unknown_domain' if not item.get('name_match') and domain not in known else 'published_unchecked'
        else:
            skip('unknown_kind')
            continue
        buckets[group].append(item)
    ordered, groups = [], {}
    for name in VERIFY_GROUPS:
        items = sorted(buckets[name], key=lambda i: (_TIER_RANK.get(i.get('tier'), 3), -(i.get('score') or 0), (i.get('email') or '').lower()))
        if name == 'published_unchecked':
            items = items[:max(0, published_cap)]
            if len(buckets[name]) > len(items):
                skipped['published_over_cap'] = skipped.get('published_over_cap', 0) + len(buckets[name]) - len(items)
        count = 0
        for item in items:
            email = item['email'].strip().lower()
            if email in seen:
                continue
            seen.add(email)
            ordered.append(email)
            count += 1
        if count:
            groups[name] = count
    return {'emails': ordered, 'groups': groups, 'skipped': skipped}
