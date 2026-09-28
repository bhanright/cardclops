"""The Comprehensive Rules (Tools → Rules), and card rulings (a card's detail view).

The rules are Wizards of the Coast's plain-text file, linked from magic.wizards.com/rules; the app
downloads it on first use (about 1 MB) into the card cache and checks for a newer edition monthly.
It is parsed into chapters (1. Game Concepts), sections (100. General), rules (100.1, 100.1a) with
their examples, and the glossary.

Rulings come from Scryfall (/cards/<id>/rulings): Wizards of the Coast's rulings as published in
Gatherer (the set release notes' FAQ lands there) and Scryfall's own notes. They're cached per
card for a month.
"""
import json
import os
import re
import threading
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

from . import net, scryfall
from .paths import CACHE_DIR
from .runtime import IN_BROWSER, in_background

RULES_PAGE = "https://magic.wizards.com/en/rules"
RULES_DIR = CACHE_DIR / "rules"
CHECK_EVERY = timedelta(days=30)
USER_AGENT = "Cardclops (personal collection app)"

CHAPTER = re.compile(r"^(\d)\. (.+)$")
SECTION = re.compile(r"^(\d{3})\. (.+)$")
RULE = re.compile(r"^(\d{3}\.\d+[a-z]?)\.? (.+)$")
RULE_NUMBER = re.compile(r"\b(\d{3}\.\d+[a-z]?|\d{3})\b")


def _get(url, timeout=60):
    return net.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)


def _find_rules_file():
    """(url, file name) of the current rules file. Wizards' site doesn't answer web pages, so the
    browser edition takes the copy published beside the card pack (scripts/build_pack.py)."""
    if IN_BROWSER:
        base = os.environ["CARDCLOPS_DATA_URL"]
        published = net.get_json(base + "manifest.json")["rules"]
        return base + published["file"], published["name"]
    page = _get(RULES_PAGE).decode("utf-8", errors="replace")
    links = re.findall(r'https://media\.wizards\.com/[^"\'<>]*?MagicCompRules[^"\'<>]*?\.txt', page)
    if not links:
        raise RuntimeError("Couldn't find the rules file on Wizards' rules page")
    url = links[0].replace(" ", "%20")
    return url, urllib.parse.unquote(url.rsplit("/", 1)[-1]).replace(" ", "-")


def parse(text):
    """{effective, chapters: [{number, title, sections: [{number, title}]}], rules: [{number, section,
    text, examples}], glossary: [{term, text}]} from the rules file's text."""
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    effective = next((m.group(1) for line in lines[:10] if (m := re.search(r"effective as of (.+?)\.?$", line))), None)
    # The contents repeat every heading; the rules proper start at the second "1. Game Concepts".
    starts = [i for i, line in enumerate(lines) if CHAPTER.match(line.strip())]
    body_start = next((i for i in starts if i > starts[0] and lines[i].strip() == lines[starts[0]].strip()), starts[0])
    glossary_at = next((i for i in range(body_start, len(lines)) if lines[i].strip() == "Glossary"), len(lines))
    credits_at = next((i for i in range(glossary_at, len(lines)) if lines[i].strip() == "Credits"), len(lines))

    chapters, rules, section = [], [], None
    for line in lines[body_start:glossary_at]:
        line = line.strip()
        if not line:
            continue
        if m := CHAPTER.match(line):
            chapters.append({"number": m.group(1), "title": m.group(2), "sections": []})
        elif (m := SECTION.match(line)) and chapters:
            section = m.group(1)
            chapters[-1]["sections"].append({"number": section, "title": m.group(2)})
        elif m := RULE.match(line):
            rules.append({"number": m.group(1), "section": m.group(1)[:3], "text": m.group(2), "examples": []})
        elif line.startswith("Example:") and rules:
            rules[-1]["examples"].append(line[len("Example:"):].strip())
        elif rules:
            rules[-1]["text"] += "\n" + line                  # a rule running onto another line

    glossary, term = [], None
    for line in lines[glossary_at + 1:credits_at]:
        if not line.strip():
            term = None
            continue
        if term is None:
            term = {"term": line.strip(), "text": ""}
            glossary.append(term)
        else:
            term["text"] = (term["text"] + "\n" + line.strip()).strip()
    return {"effective": effective, "chapters": chapters, "rules": rules, "glossary": glossary}


class RuleBook:
    """The downloaded rules, parsed once and kept in memory."""

    def __init__(self, directory=RULES_DIR):
        self.directory = Path(directory)
        self.lock = threading.Lock()
        self.data = None
        self.checking = False
        self._load()

    def _files(self):
        return sorted(self.directory.glob("MagicCompRules*.txt")) if self.directory.exists() else []

    def _load(self):
        files = self._files()
        if not files:
            return
        path = files[-1]
        data = parse(path.read_text(encoding="utf-8", errors="replace"))
        meta = {}
        meta_path = self.directory / "source.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        data.update(file=path.name, source_url=meta.get("url"), checked_at=meta.get("checked_at"))
        data["by_number"] = {rule["number"]: i for i, rule in enumerate(data["rules"])}
        self.data = data

    def download(self):
        """Fetch the current rules file (from the link on Wizards' rules page) if it's newer."""
        with self.lock:
            url, name = _find_rules_file()
            self.directory.mkdir(parents=True, exist_ok=True)
            target = self.directory / name
            if not target.exists():
                text = _get(url).decode("utf-8-sig", errors="replace")
                parse(text)                                   # refuse a file that doesn't parse
                partial = target.with_suffix(".part")
                partial.write_text(text, encoding="utf-8")
                partial.replace(target)
                for old in self._files():
                    if old != target:
                        old.unlink(missing_ok=True)
            (self.directory / "source.json").write_text(json.dumps(
                {"url": url, "checked_at": datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
            self._load()
        return self.overview()

    def check_now_and_then(self):
        """In the background, look for a newer edition if the last look was a month ago."""
        if not self.data:
            return                                    # the first download is the page's, in view
        checked = self.data.get("checked_at")
        if self.checking or (checked and datetime.now() - datetime.fromisoformat(checked) < CHECK_EVERY):
            return
        self.checking = True

        def work():
            try:
                self.download()
            except Exception as error:
                print(f"Rules check failed: {error}")
            finally:
                self.checking = False
        in_background(work, "rules-check")

    # -- reading --

    def overview(self):
        if not self.data:
            return {"downloaded": False}
        return {"downloaded": True, "effective": self.data["effective"], "source_url": self.data.get("source_url"),
                "chapters": self.data["chapters"], "rule_count": len(self.data["rules"]),
                "glossary_count": len(self.data["glossary"])}

    def section(self, number):
        if not self.data:
            raise LookupError("The rules aren't downloaded yet")
        title = next((s["title"] for c in self.data["chapters"] for s in c["sections"] if s["number"] == number), None)
        if title is None:
            raise KeyError(f"no section {number}")
        return {"number": number, "title": title, "rules": [r for r in self.data["rules"] if r["section"] == number]}

    def search(self, query, limit=150):
        """Rules and glossary entries: a rule number ("702.19", "702.19b") finds that rule and its
        subrules; words find rules and glossary entries containing all of them."""
        if not self.data:
            raise LookupError("The rules aren't downloaded yet")
        query = (query or "").strip()
        if not query:
            return {"rules": [], "glossary": []}
        if re.fullmatch(r"\d{3}(\.\d+[a-z]?)?\.?", query):
            number = query.rstrip(".")
            rules = [r for r in self.data["rules"] if r["number"] == number or r["number"].startswith(number + ".")
                     or (len(number) > 3 and r["number"].startswith(number) and r["number"][len(number):].isalpha())]
            return {"rules": rules[:limit], "glossary": []}
        words = [w.lower() for w in re.findall(r"[\w'’-]+", query)]
        match = lambda text: all(w in text.lower() for w in words)
        return {"rules": [r for r in self.data["rules"] if match(r["text"] + " " + " ".join(r["examples"]))][:limit],
                "glossary": [g for g in self.data["glossary"] if match(g["term"] + " " + g["text"])][:40]}

    def glossary(self):
        if not self.data:
            raise LookupError("The rules aren't downloaded yet")
        return {"glossary": self.data["glossary"]}


# ---- card rulings --------------------------------------------------------------------------------

RULINGS_KEEP = timedelta(days=30)


def rulings(connection, scryfall_id, oracle_id):
    """A card's rulings, newest first: [{date, source: "wotc"|"scryfall", text}], cached a month."""
    row = connection.execute("SELECT fetched_at, data FROM rulings WHERE oracle_id = ?", (oracle_id,)).fetchone()
    if row and datetime.now() - datetime.fromisoformat(row["fetched_at"]) < RULINGS_KEEP:
        return json.loads(row["data"])
    try:
        data = scryfall.api_get(f"{scryfall.API}/cards/{scryfall_id}/rulings").get("data", [])
    except Exception:
        if row:
            return json.loads(row["data"])                    # stale beats nothing when offline
        raise
    found = sorted(({"date": r.get("published_at"), "source": r.get("source"), "text": r.get("comment")} for r in data),
                   key=lambda r: r["date"] or "", reverse=True)
    connection.execute("INSERT OR REPLACE INTO rulings VALUES (?, ?, ?)",
                       (oracle_id, datetime.now().isoformat(timespec="seconds"), json.dumps(found)))
    connection.commit()
    return found
