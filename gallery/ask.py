"""Turn an English question into a search query, using the local `claude` CLI, or in the browser
edition the visitor's own Anthropic API key.

The model only writes the query; the gallery's own engine runs it, so every
number the user sees comes from their data, not from the model. A query that
fails to parse goes back to the model once with the error.

The API key (browser edition) is the visitor's: the page keeps it in its own storage and sends it
with each question; it is used for this one call to Anthropic and never stored or logged here.
"""
import json
import re
import shutil
import subprocess

from . import net

MODEL = "sonnet"
TIMEOUT_SECONDS = 90
API_URL = "https://api.anthropic.com/v1/messages"
API_MODEL = "claude-sonnet-5"
API_VERSION = "2023-06-01"

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
    return _parse_answer(envelope.get("result", "") if isinstance(envelope, dict) else str(envelope))


API_ERRORS = {401: "Anthropic didn't accept that API key. Check it in Settings → Ask box.",
              403: "That API key isn't allowed to use the model. Check it in the Anthropic Console.",
              429: "Your Anthropic account is being rate limited or is out of credit; try again shortly.",
              529: "Anthropic's API is overloaded right now; try again in a minute."}


def _run_api(prompt, api_key):
    """One call to Anthropic's Messages API with the visitor's key. Browsers may call it directly
    once they say so (the anthropic-dangerous-direct-browser-access header)."""
    body = {"model": API_MODEL, "max_tokens": 400, "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}]}
    headers = {"x-api-key": api_key, "anthropic-version": API_VERSION, "content-type": "application/json",
               "anthropic-dangerous-direct-browser-access": "true"}
    try:
        reply = json.loads(net.get(API_URL, json.dumps(body).encode(), headers, timeout=TIMEOUT_SECONDS))
    except net.NetError as error:
        raise AskError(API_ERRORS.get(error.status, f"Couldn't reach Anthropic ({error})")) from None
    return _parse_answer("".join(part.get("text", "") for part in reply.get("content", [])))


def _parse_answer(text):
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise AskError(f"Claude's reply was not a query: {text[:200]}")
    answer = json.loads(match.group(0))
    return answer.get("query", "").strip(), answer.get("explanation", "").strip()


def translate(question, tag_index, compile_query, api_key=None):
    """{query, explanation} for `question`: through the API with `api_key`, else the local CLI."""
    run = (lambda prompt: _run_api(prompt, api_key)) if api_key else _run_claude
    tags = _candidate_tags(question, tag_index)
    prompt = f"Question: {question}\n\nCandidate function tags:\n" + "\n".join(tags or ["(none matched)"])
    query, explanation = run(prompt)
    try:
        compile_query(query, tag_index)
    except ValueError as error:
        query, explanation = run(f"{prompt}\n\nYour previous query `{query}` failed to parse: {error}. Fix it.")
        compile_query(query, tag_index)
    return {"query": query, "explanation": explanation}
