"""'Cards like this one that you already own', by rules text and function.

Each distinct card becomes a TF-IDF vector of its rules-text words and word
pairs, its types, and its Scryfall Tagger function tags (weighted up, because
two cards tagged `removal-destroy` are alike even when worded differently).
Similarity is cosine, scored through an inverted index so a lookup only
touches cards that share at least one term.
"""
import math
import re
from collections import Counter, defaultdict

WORD = re.compile(r"[a-z0-9+/{}\-']+")
REMINDER_TEXT = re.compile(r"\([^)]*\)")
STOPWORDS = frozenset("a an the of to and or it its is you your that this on for with as at be by from in into "
                      "each any if may can then are was have has".split())
TAG_WEIGHT = 3.0
TYPE_WEIGHT = 1.5


def _terms(entry):
    text = entry.oracle_text.lower()
    for face_name in entry.name.lower().split(" // "):
        text = text.replace(face_name, " cardname ")
    text = REMINDER_TEXT.sub(" ", text)
    words = [w for w in WORD.findall(text) if w not in STOPWORDS]
    counts = Counter(words)
    counts.update(f"{a}_{b}" for a, b in zip(words, words[1:]))
    for word in re.findall(r"[a-z]+", entry.type_line.lower()):
        counts[f"type:{word}"] += TYPE_WEIGHT
    for tag in entry.tags:
        counts[f"tag:{tag}"] += TAG_WEIGHT
    return counts


class SimilarityIndex:
    def __init__(self, collection):
        representatives = {}
        for entry in collection.entries:
            representatives.setdefault(entry.oracle_id, entry)
        term_counts = {oracle_id: _terms(entry) for oracle_id, entry in representatives.items()}
        document_frequency = Counter(term for counts in term_counts.values() for term in counts)
        total = len(term_counts)
        idf = {term: math.log(total / (1 + df)) + 1 for term, df in document_frequency.items()}

        self.vectors = {}
        self._cache = {}
        self.postings = defaultdict(list)
        for oracle_id, counts in term_counts.items():
            vector = {term: (1 + math.log(count)) * idf[term] for term, count in counts.items() if count > 0}
            norm = math.sqrt(sum(v * v for v in vector.values())) or 1.0
            vector = {term: v / norm for term, v in vector.items()}
            self.vectors[oracle_id] = vector
            for term, weight in vector.items():
                self.postings[term].append((oracle_id, weight))

    def similar(self, oracle_id, limit=12):
        """Up to `limit` (oracle_id, score) pairs, best first. Results are cached: deck
        suggestions ask about the same cards on every visit."""
        key = (oracle_id, limit)
        if key not in self._cache:
            self._cache[key] = self._similar(oracle_id, limit)
        return self._cache[key]

    def _similar(self, oracle_id, limit):
        vector = self.vectors.get(oracle_id)
        if not vector:
            return []
        scores = defaultdict(float)
        for term, weight in vector.items():
            posting = self.postings[term]
            if len(posting) > 4000:        # near-universal terms add cost, not signal
                continue
            for other, other_weight in posting:
                scores[other] += weight * other_weight
        scores.pop(oracle_id, None)
        return sorted(scores.items(), key=lambda item: -item[1])[:limit]
