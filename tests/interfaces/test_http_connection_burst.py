"""Resource browsing must not exhaust the local server's pending connections."""

from concurrent.futures import ThreadPoolExecutor
import errno
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

from cutvoke.interfaces.httpapi import HttpApi, serve
from cutvoke.interfaces.security import BindingError
from cutvoke.rendering.render import RenderService
from cutvoke.editing.service import EditService


class ConnectionBurstTests(unittest.TestCase):
    def test_resource_sized_burst_can_connect_before_accept_resumes(self):
        ready, resume = threading.Event(), threading.Event()
        servers, clients = [], []

        class PausedServer(ThreadingHTTPServer):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                servers.append(self)
                ready.set()

            def serve_forever(self, *args, **kwargs):
                resume.wait(3)
                super().serve_forever(*args, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            api = HttpApi(EditService(), RenderService(), media_dir=directory)
            with patch("cutvoke.interfaces.httpapi.ThreadingHTTPServer", PausedServer):
                thread = threading.Thread(target=serve, args=(api, "127.0.0.1", 0), daemon=True)
                thread.start()
                try:
                    self.assertTrue(ready.wait(2))
                    address = servers[0].server_address

                    def connect(_index):
                        client = socket.create_connection(address, timeout=.5)
                        clients.append(client)

                    with ThreadPoolExecutor(32) as workers:
                        list(workers.map(connect, range(32)))
                    self.assertEqual(len(clients), 32)
                    resume.set()
                    for client in clients:
                        client.settimeout(2)
                        client.sendall(b"GET /api/v1/projects HTTP/1.0\r\n\r\n")
                    for client in clients:
                        self.assertIn(b"200", client.recv(128).split(b"\r\n", 1)[0])
                finally:
                    for client in clients:
                        client.close()
                    resume.set()
                    if servers:
                        servers[0].shutdown()
                        servers[0].server_close()
                    thread.join(3)
                    api.close()
                self.assertFalse(thread.is_alive())


    def test_literal_loopback_listens_and_serves_runtime_without_reverse_dns(self):
        self._assert_runtime_listener("127.0.0.1", "127.0.0.1", lookup_allowed=False)

    def test_localhost_keeps_normal_httpserver_binding(self):
        self._assert_runtime_listener("localhost", "resolved-hostname", lookup_allowed=True)

    def _assert_runtime_listener(self, host, expected_name, *, lookup_allowed):
        servers = []

        def serve_once(server):
            servers.append(server)
            address = server.server_address
            self.assertEqual((server.server_name, server.server_port), (expected_name, address[1]))
            self.assertEqual(server.socket.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN), 1)
            self.assertTrue(server.allow_reuse_address)
            self.assertEqual(server.request_queue_size, 128)

            def fetch_runtime():
                connection = HTTPConnection(*address, timeout=2)
                try:
                    connection.request("GET", "/api/v1/runtime")
                    response = connection.getresponse()
                    return response.status, json.load(response)
                finally:
                    connection.close()

            with ThreadPoolExecutor(1) as pool:
                response = pool.submit(fetch_runtime)
                server.timeout = 2
                server.handle_request()
                status, runtime = response.result(timeout=3)
            self.assertEqual(status, 200)
            self.assertEqual((runtime["ok"], runtime["application"]), (True, "cutvoke"))

        with tempfile.TemporaryDirectory() as directory:
            api = HttpApi(EditService(), RenderService(), media_dir=directory)
            try:
                with patch.object(ThreadingHTTPServer, "serve_forever", serve_once), patch.object(
                        socket, "getfqdn", return_value=expected_name,
                        side_effect=None if lookup_allowed else AssertionError("unexpected reverse DNS")) as lookup:
                    serve(api, host, 0)
            finally:
                api.close()
        if lookup_allowed:
            lookup.assert_called_once_with("127.0.0.1")
        else:
            lookup.assert_not_called()
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0].socket.fileno(), -1)

    def test_literal_loopback_occupied_port_closes_failed_server(self):
        closed = []

        class CapturingServer(ThreadingHTTPServer):
            def server_close(self):
                super().server_close()
                closed.append(self.socket.fileno())

        with socket.socket() as occupied:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                occupied.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            with patch("cutvoke.interfaces.httpapi.ThreadingHTTPServer", CapturingServer), patch.object(
                    socket, "getfqdn", side_effect=AssertionError("unexpected reverse DNS")):
                with self.assertRaises(OSError) as failure:
                    serve(None, "127.0.0.1", occupied.getsockname()[1])
            self.assertIn(failure.exception.errno, {
                errno.EADDRINUSE, errno.EACCES,
                getattr(errno, "WSAEADDRINUSE", errno.EADDRINUSE),
                getattr(errno, "WSAEACCES", errno.EACCES),
            })
            self.assertGreaterEqual(occupied.fileno(), 0)
        self.assertEqual(closed, [-1])

    def test_nonloopback_policy_and_inherited_binding_unchanged(self):
        with patch.object(ThreadingHTTPServer, "server_bind", side_effect=OSError("bind sentinel")) as original:
            with self.assertRaises(BindingError):
                serve(None, "0.0.0.0", 0)
            original.assert_not_called()
            with self.assertRaisesRegex(OSError, "bind sentinel"):
                serve(None, "0.0.0.0", 0, allow_non_loopback=True)
            original.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
