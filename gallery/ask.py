"""Turn an English question into a search query, using the local `claude` CLI.

The model only writes the query; the gallery's own engine runs it, so every
number the user sees comes from their data, not from the model. A query that
fails to parse goes back to the model once with the error.
"""
import json
import re
import shutil
import subprocess

MODEL = "sonnet"
TIMEOUT_SECONDS = 90

SYSTEM_PROMPT = """You translate questions about a Magic: The Gathering collection into Scryfall search syntax,
which a local engine runs against the user's own cards. Reply with JSON only:
{"query": "<search>", "explanation": "<one sentence on how the query reads the question>"}

Syntax (Scryfall's, plus collection extensions):
- name words or "quoted"; !"Exact Name"; o:/oracle: rules text ("phrase" or /regex/; ~ = this card's name)
- t: type; c: colors (c:b has black, c=b exactly black, c<=ub within U/B, c>=2 color count); id: color identity (id:esper fits Esper)
- mv / cmc numeric (mv<3); m: mana symbols ({G}{G}); pow, tou, loy numeric; r: rarity (r>=rare)
- f:/legal: format legal (pauper, commander, modern, standard, pioneer, legacy, vintage, ...); banned:, restricted:
- k: keyword ability; otag: Scryfall Tagger function tag (include descendants, e.g. otag:removal)
- is: foil, nonfoil, etched, dfc, commander, reserved, gamechanger, permanent, spell, vanilla, ...
- s:/set: set code; year / date; a: artist; usd, eur, tix numeric price per copy
- collection fields: qty (copies held), cond (near_mint, played...), lang, added (date added), paid (purchase price), gain (usd minus paid)
- boolean: implicit AND, or, parentheses, - to negate; order:usd / order:name etc and direction:desc may be appended.

Prefer rules-text regexes that match Oracle wording exactly (e.g. "destroy target nonland permanent" and
"destroy all nonland permanents" are the Oracle phrasings). When a function tag fits the question better than
wording, use it; only use tags from the candidate list you are given. Answer in JSON only, no prose, no code fence."""


class AskError(RuntimeError):
    pass


def available():
    return shutil.which("claude") is not None


# Words that describe the search itself rather than what a card does; they
# would otherwise pull in every "mana-value-matters" style tag.
QUESTION_WORDS = frozenset("mana value cards card have many much that with less than more which what "
                           "legal color colour colors black white blue green colorless deck format "
                           "costs cost show find list does their them those these there".split())
# Everyday words for effects whose Tagger names use other words.
TAG_SYNONYMS = {"destroy": ["removal"], "kill": ["removal"], "exile": ["removal"], "remove": ["removal"],
                "counter": ["counterspell"], "search": ["tutor"], "fetch": ["tutor"],
                "ramp": ["ramp", "mana-rock", "mana-dork"], "sweeper": ["sweeper", "board-wipe"],
                "wrath": ["sweeper", "board-wipe"], "reanimate": ["reanimate"], "discard": ["discard"]}


def _candidate_tags(question, tag_index, limit=40):
    words = {w for w in re.findall(r"[a-z]{4,}", question.lower()) if w not in QUESTION_WORDS}
    for word in list(words):
        for stem, extra in TAG_SYNONYMS.items():
            if word.startswith(stem):
                words.update(extra)
    stems = {w[:6] for w in words}
    scored = []
    for slug, description in tag_index.labels.items():
        hits = sum(1 for stem in stems if stem in slug)
        if hits:
            scored.append((-hits, len(slug), slug, description))
    scored.sort()
    return [f"{slug}: {description}" if description else slug for _, _, slug, description in scored[:limit]]


def _run_claude(prompt):
    executable = shutil.which("claude")
    if not executable:
        raise AskError("The claude command line tool is not on PATH, so Ask is unavailable. Type a query instead.")
    try:
        completed = subprocess.run(
            [executable, "-p", "--model", MODEL, "--output-format", "json", "--tools", "",
             "--no-session-persistence", "--system-prompt", SYSTEM_PROMPT],
            input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise AskError("Claude took too long to answer; try again or type a query.") from error
    if completed.returncode != 0:
        raise AskError(f"claude exited with {completed.returncode}: {completed.stderr.strip()[:300]}")
    envelope = json.loads(completed.stdout)
    text = envelope.get("result", "") if isinstance(envelope, dict) else str(envelope)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise AskError(f"Claude's reply was not a query: {text[:200]}")
    answer = json.loads(match.group(0))
    return answer.get("query", "").strip(), answer.get("explanation", "").strip()


def translate(question, tag_index, compile_query):
    tags = _candidate_tags(question, tag_index)
    prompt = f"Question: {question}\n\nCandidate function tags:\n" + "\n".join(tags or ["(none matched)"])
    query, explanation = _run_claude(prompt)
    try:
        compile_query(query, tag_index)
    except ValueError as error:
        query, explanation = _run_claude(
            f"{prompt}\n\nYour previous query `{query}` failed to parse: {error}. Fix it.")
        compile_query(query, tag_index)
    return {"query": query, "explanation": explanation}
