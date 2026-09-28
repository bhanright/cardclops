"""The API without HTTP (gallery/api.py): the routing both the server and the browser edition use.

Run with: python -m unittest tests.test_api
"""
import threading
import unittest
from http import HTTPStatus

from gallery.api import Api


class StubDeckBook:
    decks = {3: {"name": "Rings"}}

    def export_all(self):
        return {"format": "cardclops-decks", "exported_at": "2026-09-28T10:00:00", "decks": []}


class StubSetBook:
    def missing_text(self, code, params):
        return f"1 Card from {code}\n"


class StubGallery:
    def __init__(self):
        self.lock = threading.Lock()
        self.deckbook = StubDeckBook()
        self.setbook = StubSetBook()
        self.calls = []

    def summary(self):
        return {"copies": 5}

    def deck_detail(self, deck_id):
        return {"deck": {"deck_id": deck_id}}

    def deck_change(self, method, deck_id, action, body, rest=()):
        self.calls.append((method, deck_id, action, rest))
        if action == "lines" and not body:
            raise ValueError("Choose a card")
        return {"ok": True}

    def binder_change(self, method, parts, body):
        raise KeyError("no binder 9")

    def stats(self):
        raise RuntimeError("boom")


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.gallery = StubGallery()
        self.api = Api(self.gallery)

    def test_reads(self):
        self.assertEqual(self.api.handle("GET", "/api/summary").body, {"copies": 5})
        self.assertEqual(self.api.handle("GET", "/api/decks/3").body, {"deck": {"deck_id": 3}})
        self.assertEqual(self.api.handle("GET", "/api/decks/4").status, HTTPStatus.NOT_FOUND)
        text = self.api.handle("GET", "/api/sets/ABC/missing.txt")
        self.assertEqual((text.kind, text.body), ("text", "1 Card from abc\n"))
        export = self.api.handle("GET", "/api/decks/export")
        self.assertEqual((export.kind, export.filename), ("download", "cardclops-decks-2026-09-28.json"))
        self.assertEqual(self.api.handle("GET", "/api/nothing").status, HTTPStatus.NOT_FOUND)

    def test_writes_and_their_errors(self):
        self.assertEqual(self.api.handle("POST", "/api/decks/3/lines", body={"text": "1 Sol Ring"}).body, {"ok": True})
        self.assertEqual(self.gallery.calls[-1], ("POST", 3, "lines", ()))
        self.api.handle("DELETE", "/api/decks/3/lines/7")
        self.assertEqual(self.gallery.calls[-1], ("DELETE", 3, "lines", ("7",)))
        self.assertEqual(self.api.handle("POST", "/api/decks/3/lines").status, HTTPStatus.BAD_REQUEST)
        self.assertEqual(self.api.handle("PATCH", "/api/binders/9", body={"name": "x"}).status, HTTPStatus.NOT_FOUND)
        crash = self.api.handle("GET", "/api/stats")
        self.assertEqual((crash.status, crash.body), (HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "RuntimeError: boom"}))

    def test_quit_is_left_to_the_host(self):
        self.assertEqual(self.api.handle("POST", "/api/quit").after, "shutdown")

        class Window:
            closed = threading.Event()

            def close(self):
                self.closed.set()
        window = Window()
        response = Api(self.gallery, app_window=window).handle("POST", "/api/quit")
        response.after()
        self.assertTrue(window.closed.wait(2))


if __name__ == "__main__":
    unittest.main()
