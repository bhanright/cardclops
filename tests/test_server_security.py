"""The local server refuses requests a hostile web page could send (gallery/server.py).

Run with: python -m unittest tests.test_server_security
"""
import http.client
import json
import threading
import unittest
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

    def test_host_parsing(self):
        self.assertEqual(server._host_name("LOCALHOST:8765"), "localhost")
        self.assertEqual(server._host_name("[::1]:8765"), "[::1]")
        self.assertEqual(server._host_name("127.0.0.1"), "127.0.0.1")
        self.assertFalse(server._is_local_origin("https://localhost:8765"))     # this server speaks http only


if __name__ == "__main__":
    unittest.main()
