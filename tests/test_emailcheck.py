import sqlite3
import struct
import tempfile
import unittest
from pathlib import Path

from lead_research import emailcheck
from lead_research.emailcheck import (DnsError, DnsResolver, TYPE_A, TYPE_MX, build_query, check_domains, lookup_many,
                                      mx_lookup, mx_provider, parse_response, prioritize_verify, system_resolvers)


class FakeResolver:
    """name -> {qtype: (rcode, answers)} or an exception; counts queries."""
    def __init__(self, table):
        self.table = table
        self.calls = []

    def query(self, name, qtype):
        self.calls.append((name, qtype))
        entry = self.table.get(name, {})
        value = entry.get(qtype, (0, []))
        if isinstance(value, Exception):
            raise value
        return value


def mx_answer(*pairs):
    return (0, [(TYPE_MX, pair) for pair in pairs])


def response_packet(ident, domain, mx_pairs=(), rcode=0, truncated=False):
    """A real DNS response with name compression in the answers."""
    flags = 0x8180 | rcode | (0x0200 if truncated else 0)
    out = struct.pack('>HHHHHH', ident, flags, 1, len(mx_pairs), 0, 0)
    question = b''.join(bytes([len(p)]) + p.encode() for p in domain.split('.')) + b'\x00'
    out += question + struct.pack('>HH', TYPE_MX, 1)
    for pref, host in mx_pairs:
        if host == '':
            rdata = struct.pack('>H', pref) + b'\x00'
        else:
            label = host.split('.')[0]
            rdata = struct.pack('>H', pref) + bytes([len(label)]) + label.encode() + b'\xc0\x0c'  # label + pointer to qname
        out += b'\xc0\x0c' + struct.pack('>HHIH', TYPE_MX, 1, 300, len(rdata)) + rdata
    return out


class PacketTests(unittest.TestCase):
    def test_query_shape(self):
        packet = build_query('harbor.test', TYPE_MX, ident=0x1234)
        self.assertEqual(packet[:2], b'\x12\x34')
        self.assertTrue(packet.endswith(b'\x06harbor\x04test\x00\x00\x0f\x00\x01'))
        with self.assertRaises(ValueError):
            build_query('bad..name', TYPE_MX)

    def test_parse_mx_with_compression(self):
        data = response_packet(7, 'harbor.test', [(20, 'mx2.harbor.test'), (10, 'mx1.harbor.test')])
        rcode, truncated, answers = parse_response(data, 7)
        self.assertEqual((rcode, truncated), (0, False))
        self.assertEqual(sorted(v for _, v in answers), [(10, 'mx1.harbor.test'), (20, 'mx2.harbor.test')])

    def test_parse_null_mx_and_errors(self):
        rcode, _, answers = parse_response(response_packet(1, 'harbor.test', [(0, '')]), 1)
        self.assertEqual(answers, [(TYPE_MX, (0, ''))])
        with self.assertRaises(DnsError):
            parse_response(response_packet(1, 'harbor.test'), 2)  # id mismatch
        with self.assertRaises(DnsError):
            parse_response(b'\x00\x01', None)

    def test_resolver_falls_back_to_tcp_when_truncated(self):
        class Fake(DnsResolver):
            def __init__(self):
                super().__init__(servers=['192.0.2.1'], timeout=0.1, tries=1)
                self.tcp = 0

            def _udp(self, server, packet):
                ident = struct.unpack('>H', packet[:2])[0]
                return response_packet(ident, 'harbor.test', truncated=True)

            def _tcp(self, server, packet):
                self.tcp += 1
                ident = struct.unpack('>H', packet[:2])[0]
                return response_packet(ident, 'harbor.test', [(10, 'mx.harbor.test')])
        resolver = Fake()
        rcode, answers = resolver.query('harbor.test', TYPE_MX)
        self.assertEqual((rcode, resolver.tcp), (0, 1))
        self.assertEqual(answers, [(TYPE_MX, (10, 'mx.harbor.test'))])

    def test_resolver_tries_next_server_then_raises(self):
        class Fake(DnsResolver):
            def __init__(self):
                super().__init__(servers=['192.0.2.1', '192.0.2.2'], timeout=0.1, tries=1)
                self.seen = []

            def _udp(self, server, packet):
                self.seen.append(server)
                if server == '192.0.2.1':
                    raise OSError('timeout')
                ident = struct.unpack('>H', packet[:2])[0]
                return response_packet(ident, 'harbor.test', rcode=2)  # SERVFAIL
        resolver = Fake()
        with self.assertRaises(DnsError):
            resolver.query('harbor.test', TYPE_MX)
        self.assertEqual(resolver.seen, ['192.0.2.1', '192.0.2.2'])

    def test_system_resolvers_from_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'resolv.conf'
            path.write_text('# comment\nnameserver 192.168.1.1\nnameserver fe80::1%en0\nsearch lan\n')
            self.assertEqual(system_resolvers(str(path)), ['192.168.1.1', 'fe80::1', '1.1.1.1', '8.8.8.8'])
            self.assertEqual(system_resolvers(str(Path(tmp) / 'missing')), ['1.1.1.1', '8.8.8.8'])


class LookupTests(unittest.TestCase):
    def setUp(self):
        emailcheck.clear_cache()

    def tearDown(self):
        emailcheck.clear_cache()

    def test_mx_present_and_provider(self):
        r = FakeResolver({'harbor.test': {TYPE_MX: mx_answer((5, 'alt1.aspmx.l.google.com'), (1, 'aspmx.l.google.com'))}})
        result = mx_lookup('Harbor.test.', r)
        self.assertEqual((result['has_mx'], result['provider'], result['error']), (True, 'google', None))
        self.assertEqual(result['mx'][0], 'aspmx.l.google.com')

    def test_nxdomain_null_mx_implicit_and_none(self):
        r = FakeResolver({
            'gone.test': {TYPE_MX: (3, [])},
            'nomail.test': {TYPE_MX: mx_answer((0, ''))},
            'implicit.test': {TYPE_A: (0, [(TYPE_A, '192.0.2.10')])},
            'empty.test': {},
        })
        self.assertEqual(mx_lookup('gone.test', r)['error'], 'nxdomain')
        nomail = mx_lookup('nomail.test', r)
        self.assertEqual((nomail['has_mx'], nomail['null_mx']), (False, True))
        implicit = mx_lookup('implicit.test', r)
        self.assertEqual((implicit['has_mx'], implicit['implicit'], implicit['mx']), (True, True, ['implicit.test']))
        self.assertEqual((mx_lookup('empty.test', r)['has_mx'], mx_lookup('empty.test', r)['error']), (False, 'no_mx'))
        self.assertEqual(mx_lookup('not a domain', r)['error'], 'invalid_domain')

    def test_dns_error_is_unknown_and_not_cached(self):
        r = FakeResolver({'flaky.test': {TYPE_MX: DnsError('timeout')}})
        first = mx_lookup('flaky.test', r)
        self.assertIsNone(first['has_mx'])
        self.assertEqual(first['error'], 'dns_error')
        mx_lookup('flaky.test', r)
        self.assertEqual(len(r.calls), 2)  # retried, not cached

    def test_in_process_cache(self):
        r = FakeResolver({'harbor.test': {TYPE_MX: mx_answer((10, 'harbor-com.mail.protection.outlook.com'))}})
        mx_lookup('harbor.test', r)
        again = mx_lookup('harbor.test', r)
        self.assertEqual(again['provider'], 'microsoft365')
        self.assertEqual(len(r.calls), 1)

    def test_lookup_many_dedupes(self):
        table = {f'd{i}.test': {TYPE_MX: mx_answer((10, f'mx.d{i}.test'))} for i in range(40)}
        r = FakeResolver(table)
        results = lookup_many([f'user@d{i}.test' for i in range(40)] + ['d1.test', ''], r, workers=8)
        self.assertEqual(len(results), 40)
        self.assertTrue(all(v['has_mx'] for v in results.values()))

    def test_mx_provider_classes(self):
        self.assertEqual(mx_provider(['mx-01-us-west-2.prod.hydra.sophos.com']), 'other')
        self.assertEqual(mx_provider([]), '')
        self.assertEqual(mx_provider(['smtp.google.com']), 'google')


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        emailcheck.clear_cache()
        self.db = sqlite3.connect(':memory:')

    def tearDown(self):
        self.db.close()
        emailcheck.clear_cache()

    def test_cached_for_30_days_then_rechecked(self):
        r = FakeResolver({'harbor.test': {TYPE_MX: mx_answer((10, 'mx.harbor.test'))},
                          'flaky.test': {TYPE_MX: DnsError('timeout')}})
        now = [1_000_000.0]
        clock = lambda: now[0]
        first = check_domains(self.db, ['a@harbor.test', 'flaky.test'], resolver=r, clock=clock)
        self.assertTrue(first['harbor.test']['has_mx'])
        rows = self.db.execute('SELECT domain FROM domain_mx').fetchall()
        self.assertEqual(rows, [('harbor.test',)])  # DNS errors are not stored
        emailcheck.clear_cache()
        calls = len(r.calls)
        now[0] += 29 * 86400
        check_domains(self.db, ['harbor.test'], resolver=r, clock=clock)
        self.assertEqual(len(r.calls), calls)  # served from the table
        now[0] += 2 * 86400
        check_domains(self.db, ['harbor.test'], resolver=r, clock=clock)
        self.assertEqual(len(r.calls), calls + 1)  # stale after 30 days

    def test_summarize(self):
        counts = emailcheck.summarize({'a.test': {'has_mx': True, 'provider': 'google'},
                                       'b.test': {'has_mx': False, 'null_mx': True},
                                       'c.test': {'has_mx': None}})
        self.assertEqual((counts['has_mx'], counts['no_mail'], counts['null_mx'], counts['unknown']), (1, 1, 1, 1))
        self.assertEqual(counts['providers'], {'google': 1})


class PrioritizeVerifyTests(unittest.TestCase):
    def test_order_groups_and_skips(self):
        candidates = [
            {'email': 'pub.strong@known.test', 'kind': 'published', 'name_match': True, 'fresh': True, 'tier': 'A', 'score': 90},
            {'email': 'finder@finder.test', 'kind': 'finder', 'tier': 'B', 'score': 60},
            {'email': 'guess.old@harbor.test', 'kind': 'pattern', 'decision_maker': True, 'fresh': False, 'tier': 'A', 'score': 95},
            {'email': 'dre@weak.test', 'kind': 'published', 'name_match': False, 'fresh': True, 'tier': 'B', 'score': 70},
            {'email': 'guess.b@harbor.test', 'kind': 'pattern', 'decision_maker': True, 'fresh': True, 'tier': 'B', 'score': 70},
            {'email': 'guess.a@harbor.test', 'kind': 'pattern', 'decision_maker': True, 'fresh': True, 'tier': 'A', 'score': 85},
            {'email': 'x@catchall.test', 'kind': 'pattern', 'fresh': True, 'tier': 'A', 'score': 99},
            {'email': 'y@nomail.test', 'kind': 'pattern', 'fresh': True},
            {'email': 'done@harbor.test', 'kind': 'pattern', 'fresh': True},
            {'email': 'stale@pub.test', 'kind': 'published', 'fresh': False},
            {'email': 'GUESS.A@harbor.test', 'kind': 'finder'},  # duplicate keeps the first slot
        ]
        info = {'checked': {'done@harbor.test'}, 'catch_all_domains': {'catchall.test'}, 'checked_domains': {'known.test'},
                'no_mail_domains': {'nomail.test'}}
        out = prioritize_verify(candidates, info)
        self.assertEqual(out['emails'], ['guess.a@harbor.test', 'guess.b@harbor.test', 'dre@weak.test', 'finder@finder.test',
                                         'guess.old@harbor.test', 'pub.strong@known.test'])
        self.assertEqual(out['skipped'], {'catch_all_domain': 1, 'no_mail_server': 1, 'already_checked': 1, 'not_fresh': 1})
        self.assertEqual(out['groups']['pattern_fresh_dm'], 2)

    def test_published_cap(self):
        candidates = [{'email': f'p{i}@site{i}.test', 'kind': 'published', 'name_match': True, 'fresh': True, 'score': i}
                      for i in range(10)]
        out = prioritize_verify(candidates, {}, published_cap=3)
        self.assertEqual(out['emails'], ['p9@site9.test', 'p8@site8.test', 'p7@site7.test'])
        self.assertEqual(out['skipped']['published_over_cap'], 7)
        self.assertEqual(prioritize_verify(candidates, {}, published_cap=0)['emails'], [])

    def test_empty(self):
        self.assertEqual(prioritize_verify([], None), {'emails': [], 'groups': {}, 'skipped': {}})


if __name__ == '__main__':
    unittest.main()
