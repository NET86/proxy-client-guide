"""Redirected HTTP data must never become authenticated official evidence."""
from email.message import Message
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import build_readme as d


class Response(io.BytesIO):
    def __init__(self, body, url):
        super().__init__(body)
        self.url = url
        self.headers = Message()
        self.headers['Content-Type'] = 'text/plain; charset=utf-8'
        self.reads = 0

    def read(self, *args):
        self.reads += 1
        return super().read(*args)


class HttpsEvidenceTests(unittest.TestCase):
    def test_final_http_body_is_rejected_before_reading(self):
        for method, body in ((d.request_json, b'{"ok":true}'), (d.request_text, b'official evidence')):
            response = Response(body, 'http://example.invalid/redirected')
            with self.subTest(method=method.__name__), patch.object(d.urllib.request, 'urlopen', return_value=response):
                with self.assertRaises(d.ObservationError):
                    method('https://example.invalid/start')
            self.assertEqual(response.reads, 0)
            self.assertTrue(response.closed)

    def test_initial_plain_http_never_reaches_the_network(self):
        for method in (d.request_json, d.request_text):
            with self.subTest(method=method.__name__), patch.object(d.urllib.request, 'urlopen',
                    return_value=Response(b'{}', 'http://example.invalid/start')) as opened:
                with self.assertRaises(d.ObservationError):
                    method('http://example.invalid/start')
                opened.assert_not_called()

    def test_downgraded_404_is_not_trusted_as_confirmed_official_absence(self):
        for method in (d.request_json, d.request_text):
            error = HTTPError('http://example.invalid/redirected', 404, 'missing', {}, None)
            with self.subTest(method=method.__name__), patch.object(d.urllib.request, 'urlopen', side_effect=error):
                with self.assertRaises(d.ObservationError):
                    method('https://example.invalid/start')

    def test_legitimate_https_redirect_remains_supported(self):
        for method, body, expected in ((d.request_json, b'{"ok":true}', {'ok': True}),
                                       (d.request_text, b'official evidence', 'official evidence')):
            response = Response(body, 'https://cdn.example.invalid/redirected')
            with self.subTest(method=method.__name__), patch.object(d.urllib.request, 'urlopen', return_value=response):
                self.assertEqual(method('https://example.invalid/start'), expected)

    def test_native_redirect_history_catches_downgrade_then_return_to_https(self):
        import types
        response = Response(b'{"ok":true}', 'https://example.invalid/final')
        def opener(request, timeout):
            request.timeout = timeout
            handler = d.urllib.request.HTTPRedirectHandler()
            handler.parent = types.SimpleNamespace(open=lambda *args, **kwargs: response)
            return handler.http_error_302(request, io.BytesIO(), 302, 'Found',
                                          {'location': 'http://example.invalid/intermediate'})
        with patch.object(d.urllib.request, 'urlopen', side_effect=opener):
            with self.assertRaises(d.ObservationError):
                d.request_json('https://example.invalid/start')
        self.assertEqual(response.reads, 0)

    def test_http_error_response_is_closed_on_both_safe_and_rejected_paths(self):
        for url in ('http://example.invalid/error', 'https://example.invalid/error'):
            body = io.BytesIO(b'error body')
            error = HTTPError(url, 404, 'missing', {}, body)
            with self.subTest(url=url), patch.object(d.urllib.request, 'urlopen', side_effect=error):
                if url.startswith('https:'):
                    self.assertIsNone(d.request_json('https://example.invalid/start'))
                else:
                    with self.assertRaises(d.ObservationError):
                        d.request_json('https://example.invalid/start')
            self.assertTrue(body.closed)

    def test_missing_or_malformed_final_url_is_not_silently_trusted(self):
        for url in (None, '', 'file:///etc/passwd', 'https:///missing-host'):
            response = Response(b'{"ok":true}', url)
            with self.subTest(url=url), patch.object(d.urllib.request, 'urlopen', return_value=response):
                with self.assertRaises(d.ObservationError):
                    d.request_json('https://example.invalid/start')


if __name__ == '__main__':
    unittest.main()
