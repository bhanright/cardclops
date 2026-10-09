"""The local server refuses requests a hostile web page could send (gallery/server.py).

Run with: python -m unittest tests.test_server_security
"""
import http.client
import threading
import unittest
from unittest import mock
from http.server import ThreadingHTTPServer

from gallery import server


class StubGallery:
    """Just enough of Gallery for the routes these tests touch."""
    def __init__(self):
        self.writes = []

    def summary(self):
        return {"ok": True}

    def alert_request(self, method, parts, params, body):
        self.writes.append((method, parts, body))
        return {"unseen": 0}

    def deck_change(self, method, deck_id, action, body, rest=()):
        self.writes.append((method, deck_id, action))
        return {"deleted": deck_id}


class SecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gallery = StubGallery()
        server.Handler.gallery = cls.gallery
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.putrequest(method, path, skip_host=True)
        for name, value in ({"Host": f"localhost:{self.port}"} | (headers or {})).items():
            if value is not None:
                connection.putheader(name, value)
        data = body.encode() if body is not None else b""
        connection.putheader("Content-Length", str(len(data)))
        connection.endheaders(data)
        response = connection.getresponse()
        status = response.status
        response.read()
        connection.close()
        return status

    def setUp(self):
        self.gallery.writes.clear()

    def test_ordinary_page_requests_work(self):
        self.assertEqual(self.request("GET", "/api/summary"), 200)
        self.assertEqual(self.request("GET", "/api/summary", headers={"Host": f"127.0.0.1:{self.port}"}), 200)
        self.assertEqual(self.request("GET", "/api/summary", headers={"Host": f"[::1]:{self.port}"}), 200)
        self.assertEqual(self.request("POST", "/api/alerts/seen", '{"all": true}',
                                      {"Content-Type": "application/json", "Origin": f"http://localhost:{self.port}"}), 200)
        self.assertEqual(self.request("DELETE", "/api/decks/3", headers={"Origin": f"http://localhost:{self.port}"}), 200)
        self.assertEqual(len(self.gallery.writes), 2)

    def test_dns_rebinding_host_is_refused(self):
        self.assertEqual(self.request("GET", "/api/summary", headers={"Host": "evil.example:8765"}), 403)
        self.assertEqual(self.request("GET", "/index.html", headers={"Host": "evil.example"}), 403)
        self.assertEqual(self.request("HEAD", "/index.html", headers={"Host": "localhost.evil.example"}), 403)

    def test_simple_cross_site_posts_are_refused(self):
        # What a hostile page can send without a preflight: text/plain or a form.
        self.assertEqual(self.request("POST", "/api/alerts/seen", '{"all": true}', {"Content-Type": "text/plain"}), 403)
        self.assertEqual(self.request("POST", "/api/alerts/seen", "all=1",
                                      {"Content-Type": "application/x-www-form-urlencoded"}), 403)
        self.assertEqual(self.request("POST", "/api/alerts/seen", '{"all": true}', {}), 403)
        self.assertEqual(self.gallery.writes, [])

    def test_foreign_or_null_origins_are_refused(self):
        for origin in ("https://evil.example", "http://localhost.evil.example", "null"):
            self.assertEqual(self.request("POST", "/api/alerts/seen", '{"all": true}',
                                          {"Content-Type": "application/json", "Origin": origin}), 403, origin)
        self.assertEqual(self.request("DELETE", "/api/decks/3", headers={"Origin": "https://evil.example"}), 403)
        self.assertEqual(self.gallery.writes, [])

    def test_a_hosted_name_is_accepted_only_when_configured(self):
        self.assertEqual(self.request("GET", "/api/summary", headers={"Host": "cardclops.com"}), 403)
        with mock.patch.object(server, "HOSTED_NAMES", {"cardclops.com"}):
            self.assertEqual(self.request("GET", "/api/summary", headers={"Host": "cardclops.com"}), 200)
            self.assertEqual(self.request("POST", "/api/alerts/seen", '{"all": true}',
                                          {"Host": "cardclops.com", "Content-Type": "application/json",
                                           "Origin": "https://cardclops.com"}), 200)
            self.assertEqual(self.request("POST", "/api/alerts/seen", '{"all": true}',
                                          {"Host": "cardclops.com", "Content-Type": "application/json",
                                           "Origin": "http://cardclops.com"}), 403)      # only over https
            self.assertEqual(self.request("GET", "/api/summary", headers={"Host": "evil.cardclops.com"}), 403)

    def test_host_parsing(self):
        self.assertEqual(server._host_name("LOCALHOST:8765"), "localhost")
        self.assertEqual(server._host_name("[::1]:8765"), "[::1]")
        self.assertEqual(server._host_name("127.0.0.1"), "127.0.0.1")
        self.assertFalse(server._is_local_origin("https://localhost:8765"))     # this server speaks http only


if __name__ == "__main__":
    unittest.main()

    def test_the_page_runs_only_its_own_scripts(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request("GET", "/", headers={"Host": f"localhost:{self.port}"})
        response = connection.getresponse()
        policy = response.getheader("Content-Security-Policy") or ""
        response.read()
        connection.close()
        self.assertIn("script-src 'self' 'sha256-", policy)          # the theme script, by its hash
        self.assertNotIn("unsafe-inline", policy.split("script-src")[1].split(";")[0])   # so no javascript: links


class TokenTests(unittest.TestCase):
    """With an access token (the Android app), requests without it are refused."""

    @classmethod
    def setUpClass(cls):
        cls.gallery = StubGallery()
        server.Handler.gallery = cls.gallery
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def status(self, headers):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request("GET", "/api/summary", headers={"Host": f"127.0.0.1:{self.port}", **headers})
        response = connection.getresponse()
        response.read()
        connection.close()
        return response.status

    def test_token_required_when_set(self):
        with mock.patch.object(server, "ACCESS_TOKEN", "s3cret"):
            self.assertEqual(self.status({}), 403)
            self.assertEqual(self.status({"Cookie": "cardclops_token=wrong"}), 403)
            self.assertEqual(self.status({"Cookie": "other=1; cardclops_token=s3cret"}), 200)
            self.assertEqual(self.status({"X-Cardclops-Token": "s3cret"}), 200)
        self.assertEqual(self.status({}), 200)                  # no token configured: as before


class PortTests(unittest.TestCase):
    def test_the_saved_port_and_the_environment(self):
        import os
        import tempfile
        from pathlib import Path
        from gallery import paths
        settings = Path(tempfile.mkdtemp()) / "app.json"
        with mock.patch.object(paths, "APP_SETTINGS_PATH", settings), mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CARDCLOPS_PORT", None)
            self.assertEqual(paths.app_port(), 8765)
            paths.save_port(8790)
            self.assertEqual(paths.app_port(), 8790)
            os.environ["CARDCLOPS_PORT"] = "8800"
            self.assertEqual(paths.app_port(), 8800)
            os.environ.pop("CARDCLOPS_PORT")
            paths.save_port(None)
            self.assertEqual(paths.app_port(), 8765)
        self.assertIsNone(paths.valid_port("80"))           # below 1024 needs administrator rights
        self.assertIsNone(paths.valid_port("seventy"))

