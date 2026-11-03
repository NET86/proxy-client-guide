"""Real urllib/HTTP buffering over deterministic, virtual-time socket receives."""
import io
import unittest
from unittest.mock import patch

import test_directory as fixtures

d = fixtures.d


class TrickleSocket:
    def __init__(self, wire, clock, tick, slow_after=0):
        self.wire = wire
        self.position = 0
        self.clock = clock
        self.tick = tick
        self.slow_after = slow_after
        self.timeout = None
        self.timeouts = []
        self.files = []
        self.closed = False

    def settimeout(self, value):
        self.timeout = value
        self.timeouts.append(value)

    def sendall(self, data):
        pass

    def close(self):
        self.closed = True

    def makefile(self, mode):
        sock = self

        class Raw(io.RawIOBase):
            _sock = sock

            def readable(self):
                return True

            def readinto(self, target):
                if sock.position == len(sock.wire):
                    return 0
                if sock.position >= sock.slow_after:
                    # Each byte arrives within the original socket timeout;
                    # buffered read/readline may internally receive many bytes.
                    if sock.timeout is not None and sock.tick > sock.timeout:
                        sock.clock[0] += sock.timeout
                        raise TimeoutError("individual receive timed out")
                    sock.clock[0] += sock.tick
                target[0] = sock.wire[sock.position]
                sock.position += 1
                return 1

        raw = Raw()
        self.files.append(raw)
        return io.BufferedReader(raw)


class ReceiveDeadlineTests(unittest.TestCase):
    def exercise(self, wire, *, tick=0.0, slow_after=0, budgeted=True,
                 method=None, seconds=15.0, connect_elapsed=0.0):
        clock = [0.0]
        sock = TrickleSocket(wire, clock, tick, slow_after)

        def connect(connection):
            clock[0] += connect_elapsed
            sock.settimeout(connection.timeout)
            connection.sock = sock

        with patch.object(d.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(d.urllib.request, "getproxies", return_value={}), \
             patch.object(d.http.client.HTTPSConnection, "connect", connect):
            budget = d.DiscoveryBudget(2, seconds) if budgeted else None
            try:
                result = (method or d.request_text)(
                    "https://official.example/source", attempts=1, budget=budget)
                error = None
            except (d.DiscoveryBudgetExceeded, TimeoutError, OSError) as exc:
                result, error = None, exc
        self.assertTrue(sock.closed)
        self.assertTrue(sock.files)
        self.assertTrue(all(raw.closed for raw in sock.files))
        return result, error, clock[0], sock

    def test_fast_responses_keep_standard_framing(self):
        for headers, body in (
            (b"Content-Length: 5\r\n", b"hello"),
            (b"", b"hello"),
            (b"Transfer-Encoding: chunked\r\n", b"5\r\nhello\r\n0\r\n\r\n"),
        ):
            for budgeted in (True, False):
                with self.subTest(headers=headers, budgeted=budgeted):
                    result, error, _, _ = self.exercise(
                        b"HTTP/1.1 200 OK\r\n" + headers + b"\r\n" + body,
                        budgeted=budgeted)
                    self.assertIsNone(error)
                    self.assertEqual(result, "hello")

    def test_slow_body_cannot_outlive_deadline(self):
        for framing in (b"Content-Length: 65536\r\n", b"", b"Transfer-Encoding: chunked\r\n"):
            head = b"HTTP/1.1 200 OK\r\n" + framing + b"\r\n"
            body = b"x" * d.RESPONSE_CHUNK_BYTES
            if framing.startswith(b"Transfer"):
                body = b"10000\r\n" + body + b"\r\n0\r\n\r\n"
            with self.subTest(framing=framing):
                _, error, elapsed, sock = self.exercise(head + body, tick=0.01, slow_after=len(head))
                self.assertIsInstance(error, (d.DiscoveryBudgetExceeded, TimeoutError))
                self.assertLessEqual(elapsed, 15.01)
                self.assertLess(sock.timeouts[-1], 0.02)

    def test_status_headers_and_chunk_lines_share_deadline(self):
        for wire in (
            b"HTTP/1.1 200 " + b"x" * 8192 + b"\r\nContent-Length: 1\r\n\r\nx",
            b"HTTP/1.1 200 OK\r\nX-Slow: " + b"x" * 8192 + b"\r\nContent-Length: 1\r\n\r\nx",
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1;" + b"x" * 8192 + b"\r\nx\r\n0\r\n\r\n",
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1\r\nx\r\n0\r\nX-Trailer: " + b"x" * 8192 + b"\r\n\r\n",
            b"HTTP/1.1 302 Found\r\nX-Slow: " + b"x" * 8192 + b"\r\nLocation: /next\r\n\r\n",
            b"HTTP/1.1 404 Missing\r\nX-Slow: " + b"x" * 8192 + b"\r\n\r\n",
        ):
            with self.subTest(wire=wire[:80]):
                _, error, elapsed, _ = self.exercise(wire, tick=0.01)
                self.assertIsInstance(error, (d.DiscoveryBudgetExceeded, TimeoutError, d.urllib.error.URLError))
                self.assertLessEqual(elapsed, 15.01)

    def test_json_uses_same_transport_and_remaining_budget(self):
        wire = b'HTTP/1.1 200 OK\r\nContent-Length: 11\r\n\r\n{"ok":true}'
        result, error, _, sock = self.exercise(wire, method=d.request_json, connect_elapsed=14.0)
        self.assertEqual(result, {"ok": True})
        self.assertIsNone(error)
        self.assertLessEqual(sock.timeouts[-1], 1.0)
        _, error, elapsed, _ = self.exercise(wire, method=d.request_json, tick=0.1, connect_elapsed=14.0)
        self.assertIsNotNone(error)
        self.assertLessEqual(elapsed, 15.01)

    def test_truncation_is_still_rejected(self):
        _, error, _, _ = self.exercise(b"HTTP/1.1 200 OK\r\nContent-Length: 6\r\n\r\nhello")
        self.assertIsInstance(error, OSError)
        self.assertIn("Incomplete", str(error))


if __name__ == "__main__":
    unittest.main()
