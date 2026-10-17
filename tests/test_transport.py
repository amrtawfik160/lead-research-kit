"""No socket is opened: a fake opener exercises bounded transport behavior."""
import io
import unittest
import urllib.error
import urllib.request

from lead_research.models import PolicyError, ProviderError, Request
from lead_research.transport import UrllibTransport, _NoRedirect


class FakeResponse(io.BytesIO):
    def __init__(self, body=b'{}', code=200, headers=None):
        super().__init__(body)
        self.code = code
        self.headers = headers or {}


class FakeOpener:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        if self.error:
            raise self.error
        return self.response


class TransportTests(unittest.TestCase):
    def test_success_and_timeout(self):
        fake = FakeOpener(FakeResponse(b'{"ok": true}', headers={'X-Test': 'test'}))
        out = UrllibTransport(opener=fake).send(Request('GET', 'https://api.hunter.io/v2/account', timeout=10))
        self.assertEqual(out.data, {'ok': True})
        self.assertEqual(out.headers, {'X-Test': 'test'})
        self.assertEqual(fake.calls[0][1], 10)
        self.assertEqual(len(fake.calls), 1)

    def test_invalid_json_and_empty(self):
        for body in (b'', b'html', b'\xff', b'{'):
            out = UrllibTransport(opener=FakeOpener(FakeResponse(body))).send(Request('GET', 'https://disify.com/api/email/alex%40example.org'))
            self.assertFalse(out.json_valid)
            self.assertIsNone(out.data)

    def test_http_errors_return_for_parser(self):
        for status in (401, 403, 402, 429, 500, 524):
            exc = urllib.error.HTTPError('https://api.hunter.io/?fixture-secret', status, 'fixture-secret', {}, io.BytesIO(b'{"error":"fake"}'))
            out = UrllibTransport(opener=FakeOpener(error=exc)).send(Request('GET', 'https://api.hunter.io/v2/account'))
            self.assertEqual(out.status, status)

    def test_redirect_is_never_followed(self):
        with self.assertRaisesRegex(PolicyError, '^redirect_blocked$'):
            _NoRedirect().redirect_request(None, None, 302, 'fixture-secret', {}, 'https://example.org/?fixture-secret')
        for code in (301, 302, 303, 307, 308):
            fake = FakeOpener(FakeResponse(b'', code, {'Location': 'https://example.org/'}))
            with self.assertRaisesRegex(PolicyError, '^redirect_blocked$'):
                UrllibTransport(opener=fake).send(Request('GET', 'https://api.hunter.io/v2/account'))
            self.assertEqual(len(fake.calls), 1)

    def test_body_bounds(self):
        for body, succeeds in ((b'{}', True), (b'{} ', False)):
            transport = UrllibTransport(max_response_bytes=2, opener=FakeOpener(FakeResponse(body)))
            if succeeds:
                self.assertEqual(transport.send(Request('GET', 'https://api.hunter.io/v2/account')).data, {})
            else:
                with self.assertRaisesRegex(ProviderError, '^response_too_large$'):
                    transport.send(Request('GET', 'https://api.hunter.io/v2/account'))

    def test_origin_policy(self):
        for url in ('http://api.hunter.io/v2/account', 'https://example.org/', 'https://api.hunter.io.evil.example.org/',
                    'https://fake:fixture-secret@api.hunter.io/', 'https://api.hunter.io/?x=1#fragment',
                    'https://api.hunter.io:443/', 'https://127.0.0.1/', 'https://api.hunter.io/\r\nX: fixture-secret',
                    'https://api.hunter.io\\@evil.example.org/'):
            fake = FakeOpener()
            with self.assertRaisesRegex(PolicyError, '^unsafe_url$'):
                UrllibTransport(opener=fake).send(Request('GET', url))
            self.assertEqual(fake.calls, [])

    def test_header_injection(self):
        for headers in ({'Authorization': 'abc\r\nfixture-secret'}, {'X\nBad': 'fixture-secret'}, {'Host:': 'fixture-secret'}):
            with self.assertRaisesRegex(PolicyError, '^invalid_headers$'):
                UrllibTransport(opener=FakeOpener()).send(Request('GET', 'https://api.hunter.io/v2/account', headers))

    def test_errors_are_safe_and_no_retry(self):
        fake = FakeOpener(error=RuntimeError('https://example.org/?key=fixture-secret headers fixture-secret'))
        with self.assertRaisesRegex(ProviderError, '^transport_error$') as caught:
            UrllibTransport(opener=fake).send(Request('GET', 'https://api.hunter.io/v2/account'))
        self.assertNotIn('fixture-secret', str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertEqual(len(fake.calls), 1)

    def test_invalid_request_policy(self):
        for timeout in (0, -1, 121, float('nan'), float('inf'), True):
            with self.assertRaisesRegex(PolicyError, '^invalid_timeout$'):
                UrllibTransport(opener=FakeOpener()).send(Request('GET', 'https://api.hunter.io/v2/account', timeout=timeout))
        with self.assertRaisesRegex(PolicyError, '^unsupported_http_method$'):
            UrllibTransport(opener=FakeOpener()).send(Request('DELETE', 'https://api.hunter.io/v2/account'))




class DeadlineTests(unittest.TestCase):
    def test_hung_request_raises_deadline(self):
        import threading, time
        from lead_research.transport import UrllibTransport
        from lead_research.models import Request, ProviderError
        release = threading.Event()

        class Hang:
            def open(self, req, timeout=None):
                release.wait(10)
                raise OSError('late')
        transport = UrllibTransport(opener=Hang())
        transport.deadline_pad = 0.1
        start = time.monotonic()
        with self.assertRaises(ProviderError) as ctx:
            transport.send(Request('GET', 'https://api.hunter.io/v2/account', {}, None, timeout=0.2))
        release.set()
        self.assertEqual(str(ctx.exception), 'request_deadline_exceeded')
        self.assertLess(time.monotonic() - start, 15)

if __name__ == '__main__':
    unittest.main()
