"""Compare a pasted decklist with the collection.

Accepts the common export shapes: "4 Lightning Bolt", "4x Lightning Bolt",
"1 Sol Ring (C21) 263 *F*", bare names, and section headers such as
"Sideboard", "Commander", "// Maybeboard" or "SIDEBOARD:".
"""
import re

LINE = re.compile(r"^\s*(?:(\d+)\s*x?\s+)?(.+?)\s*$", re.IGNORECASE)
# Trailing printing details that exports append: "(SET) 123", "[SET]", "*F*", "#tag"
PRINTING_SUFFIX = re.compile(r"\s+(\([A-Za-z0-9]{2,6}\)(\s+[\w★-]+)?|\[[^\]]*\]|\*[A-Z]\*|#\S+)\s*$")
SECTION_WORDS = {"deck", "main", "mainboard", "maindeck", "sideboard", "side", "commander", "commanders",
                 "companion", "maybeboard", "maybe", "considering", "tokens", "about"}


def _section_header(line):
    word = line.strip().strip("/:").strip().lower()
    return word.title() if word in SECTION_WORDS else None


class DeckChecker:
    def __init__(self, connection, collection):
        self.collection = collection
        self.by_name = {}        # lower-case full name or face name -> (oracle_id, name, cheapest usd)
        for row in connection.execute("SELECT oracle_id, name, cheapest_usd FROM oracle_cards"):
            record = (row["oracle_id"], row["name"], row["cheapest_usd"])
            self.by_name[row["name"].lower()] = record
            if " // " in row["name"]:
                for face in row["name"].split(" // "):
                    self.by_name.setdefault(face.lower(), record)
        # Cards held but missing from oracle_cards (tokens, promos) still match by name.
        for entry in collection.entries:
            self.by_name.setdefault(entry.name.lower(), (entry.oracle_id, entry.name, entry.price_usd))

    def parse(self, text):
        section = "Main"
        wanted = {}                      # (section, oracle_id or name) -> [section, name, count, record]
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            header = _section_header(line)
            if header:
                section = "Main" if header in ("Deck", "Mainboard", "Maindeck") else header
                continue
            if line.startswith("//") or line.startswith("#"):
                continue
            match = LINE.match(line)
            count = int(match.group(1) or 1)
            name = match.group(2)
            while (stripped := PRINTING_SUFFIX.sub("", name)) != name:
                name = stripped
            name = name.replace(" / ", " // ").strip()
            record = self.by_name.get(name.lower())
            key = (section, record[0] if record else name.lower())
            if key in wanted:
                wanted[key][2] += count
            else:
                wanted[key] = [section, record[1] if record else name, count, record]
        return list(wanted.values())

    def check(self, text, summarize):
        """`summarize(entry)` turns an Entry into the API's CardSummary."""
        lines = []
        owned_left = {}   # a card listed in two sections draws on one pool of copies
        totals = {"wanted": 0, "owned": 0, "missing": 0, "cost_to_complete_usd": 0.0, "unpriced_missing": 0}
        for section, name, count, record in self.parse(text):
            if record is None:
                lines.append({"section": section, "name": name, "wanted": count, "owned": 0, "missing": count,
                              "status": "unknown", "cheapest_usd": None, "printings": []})
                totals["wanted"] += count
                totals["missing"] += count
                continue
            oracle_id, canonical, cheapest = record
            entries = self.collection.by_oracle_id.get(oracle_id, [])
            available = owned_left.setdefault(oracle_id, sum(e.quantity for e in entries))
            owned = min(available, count)
            owned_left[oracle_id] = available - owned
            missing = count - owned
            status = "owned" if missing == 0 else ("partial" if owned else "missing")
            lines.append({
                "section": section, "name": canonical, "wanted": count, "owned": owned, "missing": missing,
                "status": status, "cheapest_usd": cheapest,
                "printings": [summarize(e) for e in sorted(entries, key=lambda e: -(e.price_usd or 0))][:6],
            })
            totals["wanted"] += count
            totals["owned"] += owned
            totals["missing"] += missing
            if missing:
                if cheapest is None:
                    totals["unpriced_missing"] += missing
                else:
                    totals["cost_to_complete_usd"] += missing * cheapest
        totals["cost_to_complete_usd"] = round(totals["cost_to_complete_usd"], 2)
        return {"lines": lines, "totals": totals}
