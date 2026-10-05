"""Resource browsing must not exhaust the local server's pending connections."""

from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

from cutvoke.core.httpapi import HttpApi, serve
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditService


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
            with patch("cutvoke.core.httpapi.ThreadingHTTPServer", PausedServer):
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


if __name__ == "__main__":
    unittest.main()
