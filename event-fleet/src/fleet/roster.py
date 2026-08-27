"""Roster fetch for the Conference Prep Fleet. Agent B / tasks R1-R3.

Returns the real, named PEOPLE speaking at an event. Event-agnostic by
construction: there is no domain check, no site-specific URL pattern and
no per-event special case anywhere in this module.

Three stages, in order of how clean the data is:

1. DISCOVER. Tavily search for "<event> speakers" gives candidate pages.
   For each, probe the `llms.txt` convention (`llms.txt` / `llms.md`) up
   the URL's own path, nearest directory first -- a growing number of
   conference sites publish a machine-readable index there, and any
   speaker/`llms-full` document it links to is read first because it is
   far cleaner than rendered HTML. Nearest first matters: an organiser
   who runs several events off one domain indexes all of them at the
   root, so only the manifest inside the event's own path is followed to
   its data. Rendered pages via Tavily /extract are the fallback.
2. EXTRACT. One Nebius `gpt-oss-120b` call reads the document text and
   returns people under a strict JSON schema, plus whether the document
   is about the event that was actually asked for. This replaced a
   name+title regex that could not tell a person from a calendar row, a
   section heading or a sponsor list, and so returned "Tuesday",
   "Keynote" and "Supporting Partners" as speakers (BOARD.md D-012).
3. VALIDATE. Nothing the model says is trusted on its own. A document
   about a different event is discarded whole -- putting real people at
   an event they are not attending is the worst failure available here.
   Every surviving name must pass a shape check (no digits, no weekday
   or clock words, no page-furniture words, sane length) AND appear
   verbatim in the source document. The verbatim check is what makes
   inventing a speaker structurally impossible, not merely discouraged.

Conference sites routinely carry a "past speakers & attendees" block of
real people from *previous* events as social proof. Those are excluded by
cutting the block out of the text before extraction, keyed on the heading
text alone so it works on any site that uses that common wording.

An event with no published lineup correctly yields an empty list with
is_partial=True. That is a real answer about the world, not a failure.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from openai import OpenAI

from fleet.models import Speaker

logger = logging.getLogger("fleet.roster")

TAVILY_API_URL = "https://api.tavily.com"
NEBIUS_BASE_URL = "https://api.studio.nebius.com/v1/"
MODEL = os.environ.get("NEBIUS_MODEL", "openai/gpt-oss-120b")

MAX_PEOPLE = 25          # a briefing needs a shortlist, not 500 names
_MAX_DOC_CHARS = 30000   # ~8k tokens of document per extraction call
_HEAD_CHARS = 2000       # always keep the document header for event facts
_MAX_FIELD_CHARS = 160   # a "title" longer than this is a swallowed page block

# The llms.txt convention: a machine-readable index of a site, published
# at a well-known filename. Not specific to any host.
_LLMS_FILES = ("llms.txt", "llms.md")
_LINK_RE = re.compile(r"https?://[^\s)>\]\"'`]+|(?<=[(\s])/[^\s)>\]\"'`]+")
_MACHINE_READABLE_RE = re.compile(r"speaker|llms-full", re.IGNORECASE)

# "Past speakers & attendees from ..." blocks list people from *previous*
# events. Anchored to line start so the same words inside a sentence
# ("...alongside past speakers from Morgan Stanley...") do not trigger it.
_PAST_SPEAKERS_RE = re.compile(r"^[^\S\n]*#*\s*past\s+(?:speakers?|attendees?)", re.IGNORECASE | re.MULTILINE)
_SPEAKERS_HEADING_RE = re.compile(r"^#{1,6}[^\n]*\bspeakers?\b", re.IGNORECASE | re.MULTILINE)
_TITLE_RE = re.compile(r"^# (.+)$", re.MULTILINE)
_DATES_VENUE_RE = re.compile(r"([A-Z][a-z]+ \d{1,2}[–-]\d{1,2},\s*\d{4}) at the ([^.\n]+)\.")
_CFP_CLOSES_RE = re.compile(r"Call for Speakers closes\s*([A-Za-z]+\s*\d{1,2})", re.IGNORECASE)

# Shape rules for a human name. These are the safety net under the model,
# and they encode exactly the failures D-012 recorded: weekdays, months,
# clock times and page furniture presented as speakers.
_NOT_A_PERSON_RE = re.compile(
    # No month names here on purpose: April, June and May are real first
    # names, and every calendar row that broke run a73a8a9e ("June 30",
    # "July 1") carries a digit and is rejected by the digit rule anyway.
    r"\b(?:mon|tues|wednes|thurs|fri|satur|sun)day\b"
    r"|\b[ap]\.?m\.?\b"
    r"|\b(?:speakers?|keynotes?|sponsors?|partners?|tracks?|schedules?|agenda|sessions?"
    r"|workshops?|panels?|registration|orientation|expo|booth|lunch|break|reception"
    r"|party|stage|rooms?|days?|tickets?|venue|hotel|program|welcome)\b",
    re.IGNORECASE,
)
_HAS_LETTER_RE = re.compile(r"[^\W\d_]")

_SYSTEM = (
    "You read the text of a conference web page and list the PEOPLE who are "
    "speaking, presenting or hosting at that event.\n"
    "A person is a named human being. These are NOT people and must never be "
    "listed: day or weekday names, dates, clock times, room or track names, "
    "session or talk titles, section headings ('Keynote', 'Speakers', "
    "'Registration'), sponsor or partner names, company names on their own, "
    "navigation labels, and lists of logos.\n"
    "Rules:\n"
    "1. Copy each name EXACTLY as it appears in the document. Never reformat, "
    "translate, complete or correct it. If a person's name is not written in "
    "the document, they do not go in the list.\n"
    "2. title = that person's job role and company = their employer, only if "
    "the document states it for THAT person. Keep title to the role itself, "
    "not a sentence of their bio. Otherwise null. Never guess.\n"
    "3. session_title = a talk the document attributes to THAT person "
    "specifically. If a section lists several people without saying who gives "
    "which talk, their session_title is null.\n"
    "4. List someone only if the document presents them as part of THIS "
    "event's line-up -- a speaker listing, a speaker card, a session or "
    "schedule entry. A person named inside ordinary prose is a mention, not a "
    "line-up entry: skip past speakers, previous-year examples, people cited "
    "as illustrations, organisers, sponsors' staff and testimonial authors. A "
    "call-for-proposals page with no confirmed line-up yields no people.\n"
    "5. If the document contains no people at all, return an empty list. An "
    "empty list is a correct answer; inventing a person is not.\n"
    f"6. Return at most {MAX_PEOPLE} people, in the order they appear.\n"
    "Also return event_description: one short factual phrase naming the event "
    "and, if the document states them, its dates and city. Copy only what the "
    "document says; return an empty string if it says nothing.\n"
    "Finally return is_requested_event: true if this document is about the event "
    "the user names, false if it is about a DIFFERENT event -- another city, "
    "another edition or another year -- even when the same organiser runs both. "
    "Abbreviations and re-wordings of the same event are still true."
)

_PERSON_PROPS = {
    "name": {"type": "string"},
    "title": {"type": ["string", "null"]},
    "company": {"type": ["string", "null"]},
    "session_title": {"type": ["string", "null"]},
}
_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "is_requested_event": {"type": "boolean"},
        "event_description": {"type": "string"},
        "people": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": _PERSON_PROPS,
                "required": list(_PERSON_PROPS),
            },
        },
    },
    "required": ["is_requested_event", "event_description", "people"],
}


def _collapse(text: str) -> str:
    """Whitespace-normalised text. Pages use non-breaking spaces inside
    names, so a raw substring check would reject real speakers."""
    return " ".join(text.split())


def _tavily_post(path: str, payload: dict, api_key: str) -> dict:
    resp = httpx.post(
        f"{TAVILY_API_URL}/{path}",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


def _tavily_extract(url: str, api_key: str) -> str | None:
    """Real Tavily /extract, with one retry. Returns None (never raises) so a
    dud candidate URL degrades to the next source instead of failing the run."""
    for attempt in (1, 2):
        try:
            results = _tavily_post("extract", {"urls": [url]}, api_key).get("results") or []
            if results and results[0].get("raw_content"):
                return results[0]["raw_content"]
            raise RuntimeError("no raw_content in response")
        except (httpx.HTTPError, RuntimeError, ValueError) as err:
            logger.error(
                "roster: agent=B step=tavily_extract why=%s url=%s attempt=%d", err, url, attempt
            )
    return None


def _plain_get(url: str) -> str | None:
    """Fetch a plain-text/markdown document directly. Used only for the
    machine-readable llms.txt-convention files, which need no rendering.
    Returns None for anything that came back as HTML or failed."""
    try:
        resp = httpx.get(url, timeout=15.0, follow_redirects=True)
        resp.raise_for_status()
        body = resp.text
    except (httpx.HTTPError, UnicodeDecodeError) as err:
        logger.debug("roster: agent=B step=manifest_probe url=%s why=%s", url, err)
        return None
    if body.lstrip()[:1] == "<":
        return None  # a soft-404 HTML page, not a machine-readable document
    return body


def _manifest_urls(page_url: str) -> list[str]:
    """llms.txt-convention locations for a page, NEAREST FIRST: the page's own
    directory, then each parent, then the site root. Derived from the URL alone
    -- no host knowledge. Order matters: an organiser that runs several events
    off one domain describes all of them in the root manifest, so the manifest
    sitting closest to the page we were pointed at is the one about this event.
    """
    parts = urlsplit(page_url)
    directory = parts.path if parts.path.endswith("/") else parts.path.rsplit("/", 1)[0] + "/"
    segments = [s for s in directory.split("/") if s]
    return [
        urlunsplit((parts.scheme, parts.netloc, "/" + "".join(f"{s}/" for s in segments[:i]) + name, "", ""))
        for i in range(len(segments), -1, -1)
        for name in _LLMS_FILES
    ]


def _linked_documents(manifest: str, base_url: str, limit: int = 2) -> list[str]:
    """URLs a manifest points at whose path names them as speaker or
    full-detail data (`speakers.json`, `llms-full.md`, ...)."""
    found: list[str] = []
    for match in _LINK_RE.finditer(manifest):
        url = urljoin(base_url, match.group(0).rstrip(".,;:"))
        if _MACHINE_READABLE_RE.search(urlsplit(url).path) and url not in found:
            found.append(url)
    return found[:limit]


def _documents(event_name: str, api_key: str):
    """Yield (source_url, text) for this event, cleanest source first."""
    pages = _tavily_post("search", {"query": f"{event_name} speakers", "max_results": 3}, api_key)
    urls = [r["url"] for r in pages.get("results") or []]
    if not urls:
        raise RuntimeError(f"Tavily search found no candidate URL for event {event_name!r}")
    logger.info("roster: agent=B step=discover event_name=%r candidates=%s", event_name, urls)

    # Only the top-ranked results: a manifest is worth reading when it belongs to
    # the event's own site, not to some third party that merely mentions the event.
    probed: set[str] = set()
    for page_url in urls[:2]:
        candidates = _manifest_urls(page_url)
        nearest = set(candidates[: len(_LLMS_FILES)])
        for manifest_url in candidates:
            if manifest_url in probed:
                continue
            probed.add(manifest_url)
            manifest = _plain_get(manifest_url)
            if manifest is None:
                continue
            logger.info("roster: agent=B step=manifest found=%s url=%s", manifest_url, page_url)
            # Only a manifest sitting in the event page's own directory is trusted to
            # link to that event's data; one further up describes the whole site, and
            # its links lead to the organiser's other events.
            if manifest_url in nearest:
                for linked in _linked_documents(manifest, manifest_url):
                    body = _plain_get(linked)
                    if body:
                        yield linked, body
            yield manifest_url, manifest
            break  # nearest manifest wins; a parent one describes other events too

    for url in urls:
        text = _tavily_extract(url, api_key)
        if text:
            yield url, text


def _strip_past_speakers(text: str) -> str:
    """Remove every "past speakers/attendees" block (heading to the next
    top-level heading). Those people spoke at a different event."""
    for match in reversed(list(_PAST_SPEAKERS_RE.finditer(text))):
        end = text.find("\n## ", match.start())
        text = text[: match.start()] + (text[end:] if end != -1 else "")
    return text


def _window(text: str) -> tuple[str, bool]:
    """The slice of a long document most likely to hold the lineup: the
    header (for event facts) plus everything from the first speakers
    heading. Returns (slice, truncated)."""
    if len(text) <= _MAX_DOC_CHARS:
        return text, False
    heading = _SPEAKERS_HEADING_RE.search(text, _HEAD_CHARS)
    start = heading.start() if heading else _HEAD_CHARS
    return text[:_HEAD_CHARS] + "\n[...]\n" + text[start : start + _MAX_DOC_CHARS - _HEAD_CHARS], True


def _client() -> OpenAI:
    key = os.environ.get("NEBIUS_API_KEY")
    if not key:
        raise RuntimeError("agent=B step=roster.client why=NEBIUS_API_KEY not set in env")
    return OpenAI(base_url=NEBIUS_BASE_URL, api_key=key)


def _field(value: str | None) -> str | None:
    """A role/company/talk the page really states, or nothing. Over-long
    values are swallowed page blocks (a whole sponsor list arrived as one
    speaker's "title" in run a73a8a9e) and are dropped, not truncated."""
    value = _collapse(value or "")
    return value if 0 < len(value) <= _MAX_FIELD_CHARS else None


def _validate(people: list[dict], source: str) -> list[Speaker]:
    """Keep only entries that are shaped like a person AND appear verbatim
    in the source. Everything dropped is logged with why."""
    grounding = _collapse(source).lower()
    kept: dict[str, Speaker] = {}
    for person in people:
        name = _collapse(person.get("name") or "")
        why = None
        if not (2 <= len(name) <= 60 and _HAS_LETTER_RE.search(name)):
            why = "implausible length"
        elif any(c.isdigit() for c in name) or _NOT_A_PERSON_RE.search(name):
            why = "reads as a date, a time or page furniture, not a person"
        elif not 1 <= len(name.split()) <= 5:
            why = "too many words to be a name"
        elif name.lower() not in grounding:
            why = "not present verbatim in the source document"
        if why:
            logger.warning("roster: agent=B step=validate rejected=%r why=%s", name[:80], why)
            continue
        kept.setdefault(
            name,
            Speaker(
                name=name,
                title=_field(person.get("title")),
                company=_field(person.get("company")),
                session_title=_field(person.get("session_title")),
            ),
        )
    return list(kept.values())[:MAX_PEOPLE]


def _extract_people(text: str, source_url: str, event_name: str) -> tuple[bool, list[Speaker], str, bool]:
    """One real Nebius call over one document. Returns (is_requested_event,
    speakers, event_description, truncated). Raises only if the model call
    itself fails -- a document with no people returns an empty list."""
    document, truncated = _window(_strip_past_speakers(text))
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": f"EVENT THE USER ASKED ABOUT: {event_name}\n\nDOCUMENT\n{document}"},
    ]
    client = _client()
    budget = 8000

    for attempt in (1, 2):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "roster", "strict": True, "schema": _SCHEMA},
                },
                temperature=0.0,
                max_tokens=budget,
                # gpt-oss-120b is a reasoning model; uncapped it floods trailing
                # whitespace until finish_reason=length (coord/BOARD.md, judge.py).
                reasoning_effort="medium",
            )
        except Exception as exc:  # network / auth / rate limit -- never silent
            logger.error(
                "roster: agent=B step=extract.nebius url=%s model=%s why=%s: %s",
                source_url, MODEL, type(exc).__name__, exc,
            )
            raise RuntimeError(f"agent=B step=extract.nebius why=Nebius call failed: {exc}") from exc

        choice = resp.choices[0]
        if choice.finish_reason != "stop":
            logger.error(
                "roster: agent=B step=extract url=%s why=response cut off (%s), attempt=%d",
                source_url, choice.finish_reason, attempt,
            )
            budget *= 2
            continue
        try:
            data = json.loads(choice.message.content or "")
        except json.JSONDecodeError as exc:
            logger.error("roster: agent=B step=extract.parse url=%s why=%s", source_url, exc)
            break
        if not data.get("is_requested_event"):
            # Organisers run several events off one domain; a document about the
            # wrong one would put real people at an event they are not attending.
            logger.warning(
                "roster: agent=B step=extract url=%s skipped=describes %r, not the requested %r",
                source_url, _collapse(data.get("event_description") or "")[:80], event_name,
            )
            return False, [], "", truncated
        return (
            True,
            _validate(data.get("people") or [], document),
            _collapse(data.get("event_description") or ""),
            truncated,
        )

    return False, [], "", truncated


def _fallback_description(text: str, event_name: str) -> str:
    title = _TITLE_RE.search(text)
    described = title.group(1).strip() if title else event_name
    dates_venue = _DATES_VENUE_RE.search(text)
    return f"{described} -- {dates_venue.group(1)} at {dates_venue.group(2)}" if dates_venue else described


def _cfp_still_open(text: str, today: date) -> bool | None:
    match = _CFP_CLOSES_RE.search(text)
    if not match:
        return None
    try:
        closes = datetime.strptime(f"{match.group(1)} {today.year}", "%b %d %Y").date()
    except ValueError:
        logger.warning(
            "roster: agent=B step=parse_cfp_close why=unparseable date string %r", match.group(1)
        )
        return None
    return closes >= today


def fetch_roster(event_name: str) -> tuple[list[Speaker], str, bool]:
    """Real published roster for `event_name`: (speakers, event_description,
    is_partial). Never invents a speaker -- an empty list for an event whose
    lineup is not published yet is a correct answer, reported with
    is_partial=True.
    """
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        logger.error("roster: agent=B step=fetch_roster why=TAVILY_API_KEY not set in env")
        raise RuntimeError("TAVILY_API_KEY is not set; cannot fetch a real roster")

    description: str | None = None

    for source_url, text in _documents(event_name, api_key):
        matches, speakers, described, truncated = _extract_people(text, source_url, event_name)
        if not matches:
            continue
        if description is None:
            description = described or _fallback_description(text, event_name)
        if speakers:
            logger.info(
                "roster: agent=B step=fetch_roster event_name=%r source=%s speakers=%d truncated=%s",
                event_name, source_url, len(speakers), truncated,
            )
            cfp_open = _cfp_still_open(text, date.today())
            return speakers, described or description, truncated or cfp_open is True

    if description is None:
        raise RuntimeError(
            f"No readable document about {event_name!r} was found; every candidate source "
            "either failed to fetch or turned out to describe a different event"
        )

    logger.warning(
        "roster: agent=B step=fetch_roster event_name=%r why=no named people published on any "
        "candidate source; returning an empty roster rather than inventing one",
        event_name,
    )
    return [], description, True
