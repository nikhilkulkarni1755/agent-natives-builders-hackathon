"""Enrichment plane: resolve real people through Iridium Door 2.

Two jobs, both fixed by coord/CONTRACTS.md:
  enrich_user()     -- who the attendee is, so ranking has something to rank against.
  enrich_speakers() -- resolve roster names to real facts, for grounded reasons.

Door 2 shape (verified live in E1, not guessed):
  GET  /users/me                        -> display_name + linkedin_member_id
  POST /mcp/tools/get_linkedin_profile  -> {"results": [...], "total": N, "query_used": str|None}

`get_user_profile` is deliberately NOT used to identify the attendee: it returns
account settings and capacity, and its identity fields are null until Iridium
onboarding is complete. `/users/me` is the identity seed.

DISAMBIGUATION IS THE POINT (board decision D-007). A name lookup is a live
LinkedIn search that returns up to 5 people: `{"name": "Shawn Wang", ...}` came
back with three different humans. Attaching the wrong person's facts to a speaker
is the worst failure this system can produce -- it is precisely what the judge's
grounding check exists to catch, and it would be indefensible on stage. So a
candidate is accepted only when it wins on independent evidence AND clears the
runner-up by a margin. Otherwise the speaker comes back with NO facts, which is
correct behavior: fact-less speakers still rank and are filtered from the render.

When Iridium cannot resolve a speaker -- it failed, it found nobody, or it found
several people and none of them could be trusted -- enrichment falls back to
public web data via Tavily rather than returning nothing. That fallback carries
the SAME evidence discipline: a page is admissible only if it corroborates this
speaker independently of their name, and only sentences that actually name them
become facts, so a 50-person agenda page can never lend one speaker another
speaker's line. Unconfirmed still means no facts.

Degradations are never silent: `take_degradations()` drains a human-readable
record of every fallback that fired, and each one is logged at error level as
loudly as an outright failure.

Spec ref: Conference Prep Fleet technical spec, section 3 (Enrichment).
"""

from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from fleet.iridium import IridiumError, client
from fleet.models import EnrichedSpeaker, Speaker, UserProfile

log = logging.getLogger("fleet.enrich")

AGENT = "E2/enrich"
TOOL = "get_linkedin_profile"

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
USER_CACHE = DATA_DIR / "iridium_user_profile.json"

# A resolved candidate must beat this to be trusted, and beat the runner-up by
# MIN_MARGIN. Company agreement alone (2.0) clears it; a name that merely matches
# does not, because every result matches the name we searched for.
MIN_SCORE = 2.0
MIN_MARGIN = 1.5

# Public-data fallback. Same MIN_SCORE bar as the LinkedIn path, deliberately:
# a public page is not a softer standard, just a different source.
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_RESULTS = 4
ANCHOR_EVIDENCE = 2.0
"""A page that names this speaker AND carries their event or their talk title in
full is direct evidence of this person at this event -- weighted like an employer
match. Never awarded on a one-token anchor, where the overlap would mean nothing."""
MAX_PUBLIC_FACTS = 3
MIN_FACT_WORDS = 8       # "View profile for <name>" is true, and it is not a fact
MAX_FACT_CHARS = 240

LOOKUP_WORKERS = 4
"""Concurrent LinkedIn lookups. Small on purpose: this is a real rate-limited
read on a real account, not a load test."""

PUBLIC_LIMIT = 12
"""Total speakers enriched. The first `limit` of them go through LinkedIn; the
rest are resolved from public web data only. Tavily is a different plane with a
different quota and no LinkedIn exposure, so widening coverage here costs zero
LinkedIn calls -- which is the whole point of keeping the two budgets apart."""

PUBLIC_WORKERS = 12
"""Concurrent public-web lookups. Larger than LOOKUP_WORKERS because nothing here
touches the rate-limited account, and sized to clear PUBLIC_LIMIT in one round:
measured cold, seven concurrent searches take 5.0s, so a second round would put
the whole enrichment phase on the demo's critical path for no benefit."""

_STOPWORDS = frozenset(
    "the a an of at in for and or to with on inc llc ltd co corp company "
    "senior staff principal lead head chief officer vp director manager".split()
)

_degradations: list[str] = []


def take_degradations() -> list[str]:
    """Drain the degradations recorded since the last call.

    The contract signatures return models, not tuples, so this is how a fallback
    reaches the briefing's `degradations` list. Draining keeps one run's
    degradations from leaking into the next.
    """
    drained = list(_degradations)
    _degradations.clear()
    return drained


def _degrade(why: str) -> None:
    """Record a fallback. Logged at error level -- a degradation is not a shrug."""
    log.error("agent=%s step=degradation why=%s", AGENT, why)
    _degradations.append(why)


def _tokens(*parts: str | None) -> set[str]:
    """Lowercase content words, for evidence comparison."""
    text = " ".join(p for p in parts if p)
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOPWORDS and len(t) > 2}


# -- shared profile helpers ---------------------------------------------------
def _role_line(profile: dict) -> str | None:
    role = profile.get("current_role") or {}
    title, company = role.get("title"), role.get("company")
    if title and company:
        return f"{title} at {company}"
    return title or company or None


def _facts(profile: dict) -> list[str]:
    """Ground facts from one resolved profile. Only fields the API really returned."""
    facts: list[str] = []
    role = _role_line(profile)
    if role:
        facts.append(f"Currently {role}.")
    if profile.get("headline"):
        facts.append(f"LinkedIn headline: {profile['headline']}.")
    if profile.get("location"):
        facts.append(f"Based in {profile['location']}.")

    past = [p for p in (profile.get("past_companies") or []) if p.get("company")][:3]
    if past:
        facts.append("Previously: " + "; ".join(
            f"{p.get('title') or 'role'} at {p['company']}" for p in past
        ) + ".")

    schools = [e["school"] for e in (profile.get("education") or []) if e.get("school")][:2]
    if schools:
        facts.append("Studied at " + " and ".join(schools) + ".")

    skills = [s for s in (profile.get("skills") or []) if isinstance(s, str)][:8]
    if skills:
        facts.append("Listed skills: " + ", ".join(skills) + ".")

    for post in (profile.get("recent_posts") or [])[:2]:
        text = (post.get("text") or "").strip().replace("\n", " ")
        if text:
            facts.append(f"Recently posted: \"{text[:180]}\"")
    return facts


def _interests(profile: dict) -> list[str]:
    """Interests come from declared skills -- real signal, never inferred."""
    return [s for s in (profile.get("skills") or []) if isinstance(s, str)][:12]


def _lookup(payload: dict) -> dict:
    """One real get_linkedin_profile call. Returns the raw response dict."""
    return client().call_tool(TOOL, payload)


# -- enrich_user --------------------------------------------------------------
def _looks_like_name(hint: str) -> bool:
    """Is this hint a person's name, or the user's stated intent?

    `server.prep_conference` passes the run's *intent* into this argument, so a
    hint is only used as a search name when it plausibly is one. Feeding a
    sentence like "I want to hire ML engineers" into a LinkedIn name search would
    return confident nonsense.
    """
    words = hint.split()
    return 1 < len(words) <= 4 and not re.search(r"[.!?,;:]", hint) and all(
        w[:1].isupper() for w in words if w[:1].isalpha()
    )


def _cache_user(payload: dict, source: str) -> None:
    """Pre-warm the demo fallback with a REAL captured response.

    This is a cache, not a stub: the payload is exactly what Iridium returned,
    stamped with when it was captured and which endpoint produced it.
    """
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        USER_CACHE.write_text(json.dumps({
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "endpoint": f"POST /mcp/tools/{TOOL}",
            "profile": payload,
        }, indent=2))
        log.info("agent=%s step=cache wrote=%s", AGENT, USER_CACHE)
    except OSError as exc:
        # A cache write failure must not fail a live run, but it is never silent.
        log.error("agent=%s step=cache path=%s exc=%r", AGENT, USER_CACHE, exc, exc_info=True)


def _cached_user(after_failure: bool = True) -> UserProfile | None:
    """The pre-warmed profile, if one was captured.

    Serving it is a degradation only when a live call actually failed. When it is
    served deliberately to stay off LinkedIn's rate limit, that is the configured
    policy, not a fallback, and must not be reported to the user as one.
    """
    if not USER_CACHE.is_file():
        return None
    try:
        blob = json.loads(USER_CACHE.read_text())
        profile = blob["profile"]
    except (OSError, ValueError, KeyError) as exc:
        log.error("agent=%s step=cache-read path=%s exc=%r", AGENT, USER_CACHE, exc, exc_info=True)
        return None
    if after_failure:
        _degrade(
            f"Iridium was unreachable, so the attendee profile came from the cached "
            f"response captured at {blob.get('captured_at')} rather than a live call."
        )
    return _to_user_profile(profile, source="iridium-cache")


def _to_user_profile(profile: dict, source: str) -> UserProfile:
    """Compose a UserProfile from one resolved LinkedIn profile."""
    bits = [b for b in (profile.get("name"), _role_line(profile), profile.get("location")) if b]
    summary = " -- ".join(bits)
    about = (profile.get("about") or "").strip().replace("\n", " ")
    if about:
        # Cut on a sentence, else a word -- a hard slice ends the About-you
        # paragraph mid-word ("...hard tradeoffs by hi"), which is visible to the reader.
        blurb = about[:400]
        if len(about) > 400:
            cut = max(blurb.rfind(". "), blurb.rfind("! "), blurb.rfind("? "))
            blurb = blurb[:cut + 1] if cut > 200 else blurb[:blurb.rfind(" ")].rstrip(",;:") + "..."
        summary = f"{summary}. {blurb}"
    return UserProfile(summary=summary, interests=_interests(profile), source=source)


def enrich_user(hint: str | None = None) -> UserProfile:
    """Resolve who the attendee is, via Iridium Door 2.

    Identity is seeded from `/users/me`, so the primary path needs no
    disambiguation at all: `linkedin_member_id` resolves to exactly one person.
    `hint` is only consulted when the account carries no LinkedIn id, and only if
    it actually looks like a name.

    Every lookup spends a real LinkedIn search behind Iridium, and LinkedIn throttles
    an account that searches too often. The attendee does not change between runs, so
    a cached profile is served unless FLEET_ENRICH_LIVE=1 explicitly asks for a fresh
    one. This keeps rehearsals and test runs off the live quota.
    """
    if os.environ.get("FLEET_ENRICH_LIVE") != "1":
        cached = _cached_user(after_failure=False)
        if cached is not None:
            log.info("agent=%s step=enrich_user served=cache (FLEET_ENRICH_LIVE!=1)", AGENT)
            return cached

    try:
        account = client().account()
        member_id = account.get("linkedin_member_id")
        name = account.get("display_name")

        if member_id:
            payload = {"linkedin_id": member_id}
        elif hint and _looks_like_name(hint):
            payload = {"name": hint}
        elif name:
            payload = {"name": name}
        else:
            raise IridiumError(
                f"agent={AGENT} step=enrich_user why=account has no linkedin_member_id "
                "and no usable name; cannot identify the attendee"
            )

        results = (_lookup(payload).get("results") or [])
        if not results:
            raise IridiumError(
                f"agent={AGENT} step=enrich_user why={TOOL} resolved no profile for "
                f"{sorted(payload)}"
            )

        profile = results[0]
        log.info(
            "agent=%s step=enrich_user resolved=%r by=%s facts=%d",
            AGENT, profile.get("name"), next(iter(payload)), len(_interests(profile)),
        )
        _cache_user(profile, source="GET /users/me -> linkedin_id" if member_id else "name lookup")
        return _to_user_profile(profile, source="iridium")

    except IridiumError as exc:
        log.error("agent=%s step=enrich_user exc=%r", AGENT, exc, exc_info=True)
        cached = _cached_user()
        if cached is not None:
            return cached
        _degrade(
            f"Iridium could not identify the attendee ({type(exc).__name__}: {exc}) and no "
            "cached profile was available; the briefing has no attendee profile to rank against."
        )
        return UserProfile(summary="", interests=[], source="none")


# -- enrich_speakers ----------------------------------------------------------
def _score(speaker: Speaker, candidate: dict) -> float:
    """Independent evidence that this candidate IS this speaker.

    The name is deliberately worth nothing: every result matches the name we
    searched for, so the name cannot separate three different Shawn Wangs. Only
    corroborating evidence counts.
    """
    score = 0.0
    cand_company = _tokens(
        (candidate.get("current_role") or {}).get("company"), candidate.get("headline")
    )
    if speaker.company:
        overlap = _tokens(speaker.company) & cand_company
        if overlap:
            score += 2.0                                   # same employer: strong
    if speaker.title:
        title_overlap = _tokens(speaker.title) & _tokens(
            (candidate.get("current_role") or {}).get("title"), candidate.get("headline")
        )
        score += min(len(title_overlap), 3) * 0.5          # same kind of role: weak
    if speaker.session_title:
        topic_overlap = _tokens(speaker.session_title) & _tokens(
            candidate.get("headline"), candidate.get("about"), " ".join(candidate.get("skills") or [])
        )
        score += min(len(topic_overlap), 3) * 0.25         # talks about the same thing: weakest
    return score


def _tavily_results(query: str) -> list[dict]:
    """One real Tavily search. Returns the raw result dicts (title/url/content).

    The roster lane's Tavily helper is not reused: it is /extract-shaped around
    one page's raw content, this path wants ranked search snippets, and that
    module belongs to another lane.
    """
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        raise RuntimeError(
            f"agent={AGENT} step=public_fallback why=TAVILY_API_KEY is unset; "
            "the public-data fallback cannot run"
        )
    resp = httpx.post(
        TAVILY_SEARCH_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"query": query, "max_results": TAVILY_RESULTS, "search_depth": "advanced"},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json().get("results") or []


def _mentions(name: str, *parts: str | None) -> bool:
    """Does this text actually name the speaker? Whitespace-normalised, case-blind."""
    text = " ".join(" ".join((p or "").split()) for p in parts).lower()
    return " ".join(name.split()).lower() in text


def _public_evidence(speaker: Speaker, event_name: str | None, result: dict) -> float:
    """How strongly one search result is evidence about THIS speaker.

    `_score` is reused verbatim over the page's title and snippet, so the public
    path inherits the LinkedIn path's rule that a matching name is worth nothing
    -- we searched for the name, and there were three Shawn Wangs. On top of it,
    an anchor the page must carry *in full*: the event, or the talk this speaker
    is giving. A roster often has no employer, and a page that names the speaker
    and their exact session is the strongest public evidence available that this
    is the right human.
    """
    page = _tokens(result.get("title"), result.get("content"))
    score = _score(speaker, {"headline": result.get("title"), "about": result.get("content")})
    for anchor in (event_name, speaker.session_title):
        tokens = _tokens(anchor)
        if len(tokens) > 1 and tokens <= page:
            score += ANCHOR_EVIDENCE
    return score


def _name_sentences(name: str, text: str | None) -> list[str]:
    """Sentences from a page that actually name the speaker.

    This is the misattribution guard for public data. A conference agenda names
    fifty people on one page; taking only the lines that carry THIS speaker's name
    means another speaker's history can never be attached to them.
    """
    needle = " ".join(name.split()).lower()
    out: list[str] = []
    for raw in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        sentence = " ".join(raw.split())
        at = sentence.lower().find(needle)
        if at < 0 or len(sentence.split()) < MIN_FACT_WORDS:
            continue
        if len(sentence) > MAX_FACT_CHARS:
            # Keep a window AROUND the name, never a prefix. A schedule row can
            # run past the cap, and cutting it from the front drops the name the
            # line was selected for -- which is how another speaker's session
            # ends up attributed to this one. Observed live, hence the recheck.
            start = max(0, at - MAX_FACT_CHARS // 3)
            window = sentence[start:start + MAX_FACT_CHARS].rstrip()
            sentence = f"{'...' if start else ''}{window}..."
        if _mentions(name, sentence):
            out.append(sentence)
    return out


def _public_facts(speaker: Speaker, event_name: str | None) -> tuple[list[str], list[str]]:
    """Public-web facts for one speaker: (facts, source domains).

    Empty when nothing can be confirmed to be this person, which is the correct
    answer -- a fact-less speaker still ranks (D-007), a misattributed fact is a
    disaster. Every fact is stamped with the domain it came from, so the judge's
    grounding check and the reader see the same provenance.
    """
    terms = [f'"{speaker.name}"']
    terms += [t for t in (speaker.company, event_name, speaker.session_title) if t]
    results = _tavily_results(" ".join(terms + ["speaker"]))

    facts: list[str] = []
    sources: list[str] = []
    seen: set[str] = set()
    for result in results:
        score = _public_evidence(speaker, event_name, result)
        named = _mentions(speaker.name, result.get("title"), result.get("content"))
        if not named or score < MIN_SCORE:
            log.info(
                "agent=%s step=public_fallback speaker=%r rejected=%s named=%s score=%.2f",
                AGENT, speaker.name, result.get("url"), named, score,
            )
            continue
        domain = urlsplit(result.get("url") or "").netloc.removeprefix("www.") or "the web"
        for sentence in _name_sentences(speaker.name, result.get("content")):
            key = sentence.lower()
            if key in seen:
                continue
            seen.add(key)
            facts.append(f"Per {domain}: {sentence}")
            if domain not in sources:
                sources.append(domain)
            if len(facts) >= MAX_PUBLIC_FACTS:
                return facts, sources
    return facts, sources


def _fallback(speaker: Speaker, event_name: str | None, why: str | None = None) -> EnrichedSpeaker:
    """Resolve one speaker from public web data.

    `why` set means Iridium tried and failed on this speaker: the fallback firing
    at all is a degradation and every branch below says so out loud, whether or
    not it finds anything (spec section 6).

    `why` None means this speaker was never looked up on LinkedIn -- they are
    outside the rate-limited budget by policy (D-015), so public data is their
    PRIMARY plane, not a fallback, and reporting it as a degradation would bury
    the real ones. Exactly the line `_cached_user(after_failure)` already draws.
    """
    def record(message: str) -> None:
        if why:
            _degrade(message)
        else:
            log.info("agent=%s step=public_only speaker=%r %s", AGENT, speaker.name, message)

    lead = (f"Iridium could not resolve {speaker.name!r} ({why})" if why
            else f"{speaker.name!r} was outside the LinkedIn budget, so public data only")
    try:
        facts, sources = _public_facts(speaker, event_name)
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        # A Tavily outage is loud on both paths: on the by-policy path it is not a
        # user-facing degradation, but it is never a silent one.
        log.error("agent=%s step=public_fallback speaker=%r exc=%r",
                  AGENT, speaker.name, exc, exc_info=True)
        record(f"{lead}, and the Tavily public-data lookup also failed "
               f"({type(exc).__name__}: {exc}); they are in the briefing with no facts.")
        return EnrichedSpeaker(speaker=speaker, source="none")

    if not facts:
        record(f"{lead}, and public web search found nothing that could be confirmed to be "
               "this person; they are in the briefing with no facts rather than risk "
               "attributing another person's history to them.")
        return EnrichedSpeaker(speaker=speaker, source="none")

    log.info(
        "agent=%s step=public_fallback speaker=%r after_failure=%s facts=%d sources=%s",
        AGENT, speaker.name, bool(why), len(facts), sources,
    )
    record(f"{lead}; their {len(facts)} fact(s) came from public web search via Tavily "
           f"({', '.join(sources)}), not from LinkedIn.")
    return EnrichedSpeaker(speaker=speaker, facts=facts, source="tavily")


def _resolve_speaker(speaker: Speaker, event_name: str | None = None) -> EnrichedSpeaker:
    """Resolve one speaker, or return them fact-less rather than risk a misattribution."""
    payload: dict = {"name": speaker.name}
    if speaker.company:
        payload["company"] = [speaker.company]
    hints = sorted(_tokens(speaker.title))[:4]
    if hints:
        payload["headline_keywords"] = hints

    try:
        response = _lookup(payload)
    except IridiumError as exc:
        log.error("agent=%s step=resolve speaker=%r exc=%r", AGENT, speaker.name, exc, exc_info=True)
        return _fallback(speaker, event_name, f"{type(exc).__name__}: {exc}")

    candidates = response.get("results") or []
    if not candidates:
        log.info("agent=%s step=resolve speaker=%r why=no match", AGENT, speaker.name)
        return _fallback(speaker, event_name, "LinkedIn returned no match for this name")

    ranked = sorted(((_score(speaker, c), c) for c in candidates), key=lambda p: p[0], reverse=True)
    best_score, best = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 else 0.0

    if best_score < MIN_SCORE or (best_score - runner_up) < MIN_MARGIN:
        log.warning(
            "agent=%s step=resolve speaker=%r why=ambiguous candidates=%d best=%.2f "
            "runner_up=%.2f -- returning no facts rather than risk a misattribution",
            AGENT, speaker.name, len(candidates), best_score, runner_up,
        )
        return _fallback(
            speaker, event_name,
            f"{len(candidates)} LinkedIn profiles matched with no clear winner, "
            f"best {best_score:.2f} vs runner-up {runner_up:.2f}",
        )

    log.info(
        "agent=%s step=resolve speaker=%r matched=%r score=%.2f margin=%.2f",
        AGENT, speaker.name, best.get("name"), best_score, best_score - runner_up,
    )
    return EnrichedSpeaker(
        speaker=speaker,
        facts=_facts(best),
        alignment_note=_role_line(best),
        source="iridium",
    )


def enrich_speakers(
    speakers: list[Speaker],
    limit: int = 3,
    public_limit: int = PUBLIC_LIMIT,
    event_name: str | None = None,
) -> list[EnrichedSpeaker]:
    """Enrich the head of the roster; everyone else passes through fact-less.

    The two planes have two budgets, because they have two costs. `limit` is the
    LinkedIn budget: a real rate-limited read on a real account, deliberately tiny
    (D-015), and `limit=0` means look nobody up on LinkedIn at all. `public_limit`
    is the TOTAL number of speakers enriched -- the ones past `limit` skip Iridium
    entirely and go straight to public web data, which costs no LinkedIn calls.
    That split is what stops a small LinkedIn budget from also throttling coverage:
    a speaker who reaches the reader with no facts is filtered out of the render
    (D-007), so thin enrichment is invisible thinness.

    Every speaker is returned either way, so ranking still sees the whole roster.
    `event_name` (optional, so the existing call site is unaffected) gives the
    public path a second identity anchor -- this person AT this event.
    """
    if not speakers:
        log.warning("agent=%s step=enrich_speakers why=empty roster, nothing to enrich", AGENT)
        return []

    head = speakers[:limit]                              # LinkedIn, then public on failure
    public = speakers[len(head):max(limit, public_limit)]  # public only, no LinkedIn call
    # Both planes run at once: one live LinkedIn read costs ~14s, and the public
    # lookups fit inside that window instead of extending the run. The Iridium
    # client locks around its token, so overlapping is safe; its pool stays small
    # to be polite to a rate-limited upstream, while the public pool is wider
    # because nothing in it touches the account. Order is preserved.
    with ThreadPoolExecutor(max_workers=min(LOOKUP_WORKERS, len(head)) or 1) as linkedin, \
            ThreadPoolExecutor(max_workers=min(PUBLIC_WORKERS, len(public)) or 1) as web:
        futures = [linkedin.submit(_resolve_speaker, s, event_name) for s in head]
        futures += [web.submit(_fallback, s, event_name) for s in public]
        out = [f.result() for f in futures]
    out += [EnrichedSpeaker(speaker=s, source="none") for s in speakers[len(out):]]

    resolved = sum(1 for e in out if e.source == "iridium")
    got_public = sum(1 for e in out if e.source == "tavily")
    log.info(
        "agent=%s step=enrich_speakers total=%d linkedin_budget=%d public_only=%d "
        "resolved=%d public=%d fact_less=%d",
        AGENT, len(out), len(head), len(public), resolved, got_public,
        len(out) - resolved - got_public,
    )
    return out
