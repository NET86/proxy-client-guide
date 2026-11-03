"""Review probes using real HTTPResponse buffering and a virtual trickle socket."""
import http.client
import io
import unittest
from unittest.mock import patch

import test_directory as fixtures

d = fixtures.d


class TrickleSocket:
    def __init__(self, wire, clock, tick):
        self.wire = wire
        self.position = 0
        self.clock = clock
        self.tick = tick
        self.active = False
        self.timeout = None
        self.timeouts = []

    def settimeout(self, value):
        self.timeout = value
        self.timeouts.append(value)

    def makefile(self, mode):
        sock = self

        class Raw(io.RawIOBase):
            _sock = sock

            def readable(self):
                return True

            def readinto(self, target):
                if sock.position == len(sock.wire):
                    return 0
                if sock.active:
                    # A socket timeout applies to each underlying receive.
                    # Every byte arrives within that timeout, even though a
                    # buffered read/line can require many such receives.
                    if sock.timeout is not None and sock.tick > sock.timeout:
                        sock.clock[0] += sock.timeout
                        raise TimeoutError("individual receive timed out")
                    sock.clock[0] += sock.tick
                target[0] = sock.wire[sock.position]
                sock.position += 1
                return 1

        return io.BufferedReader(Raw())


class TotalDeadlineReviewTests(unittest.TestCase):
    @staticmethod
    def body_response(clock, tick):
        body = b"x" * d.RESPONSE_CHUNK_BYTES
        wire = b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
        sock = TrickleSocket(wire, clock, tick)
        response = http.client.HTTPResponse(sock)
        response.begin()
        sock.active = True
        response.url = "https://official.example/source"
        return response, sock, body

    def test_fast_buffered_response_remains_valid(self):
        clock = [0.0]
        response, _, body = self.body_response(clock, tick=0.0)
        with patch.object(d.time, "monotonic", side_effect=lambda: clock[0]):
            budget = d.DiscoveryBudget(2, 15.0)
            try:
                self.assertEqual(d.read_response(response, budget=budget), body)
            finally:
                response.close()

    def test_buffered_body_read_cannot_outlive_total_deadline(self):
        clock = [0.0]
        response, sock, _ = self.body_response(clock, tick=0.01)
        with patch.object(d.time, "monotonic", side_effect=lambda: clock[0]):
            budget = d.DiscoveryBudget(2, 15.0)
            try:
                with self.assertRaises((d.DiscoveryBudgetExceeded, TimeoutError)):
                    d.read_response(response, budget=budget)
            finally:
                response.close()
        self.assertTrue(sock.timeouts, "exercise the production socket timeout adjustment")
        self.assertLessEqual(clock[0], 15.01,
                             "buffered read kept receiving after the total deadline")

    def test_header_parsing_cannot_outlive_total_request_deadline(self):
        clock = [0.0]
        wire = b"HTTP/1.1 200 OK\r\nX-Slow: " + b"x" * 8192 + b"\r\nContent-Length: 1\r\n\r\nx"
        sock = TrickleSocket(wire, clock, tick=0.01)

        def open_response(request, *, timeout):
            sock.settimeout(timeout)
            sock.active = True
            response = http.client.HTTPResponse(sock)
            response.begin()
            response.url = request.full_url
            return response

        with patch.object(d.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(d, "_open_request", side_effect=open_response):
            budget = d.DiscoveryBudget(2, 15.0)
            with self.assertRaises((d.DiscoveryBudgetExceeded, TimeoutError)):
                d.request_text("https://official.example/source", attempts=1, budget=budget)
        self.assertLessEqual(clock[0], 15.01,
                             "status/header buffering kept receiving after the total deadline")
