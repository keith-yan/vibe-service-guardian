import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from vsg.config import AppConfig, validate_config
from vsg.trusted_nodes import _private_addresses, _probe


class HealthHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def do_GET(self):
        if self.path != "/healthz":
            self.send_error(404)
            return
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class TrustedNodeTests(unittest.TestCase):
    def test_public_resolution_is_rejected_before_connection(self):
        public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 43921))]
        with patch("vsg.trusted_nodes.socket.getaddrinfo", return_value=public):
            with self.assertRaisesRegex(ValueError, "非私网"):
                _private_addresses("node.local", 43921)

    def test_loopback_node_uses_explicit_health_path(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = _probe(f"http://localhost:{server.server_port}")
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["health_path"], "/healthz")
        self.assertTrue(all(value in {"127.0.0.1", "::1"} for value in result["addresses"]))

    def test_config_rejects_public_and_credentialed_nodes(self):
        base = AppConfig(project_roots=["C:\\projects"])
        with self.assertRaises(ValueError):
            validate_config({"trusted_nodes": ["http://8.8.8.8:43921"]}, base)
        credentialed = "http://" + "user" + ":" + "pass" + "@127.0.0.1:43921"
        with self.assertRaises(ValueError):
            validate_config({"trusted_nodes": [credentialed]}, base)


if __name__ == "__main__":
    unittest.main()
