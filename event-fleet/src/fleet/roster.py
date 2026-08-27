"""Roster fetch for the Conference Prep Fleet. Agent B / task R1.

Pulls the real speaker roster for an event via a live Tavily REST call
(no SDK) and parses the rendered page text. AI Engineer conference pages
(ai.engineer/<city>/<year>) reuse the same template: a "Past speakers &
attendees from" carousel of prior-event alumni shown as social proof, and
-- once the wave actually publishes -- a separate confirmed-lineup section
for the event itself. This module extracts every named speaker card on the
page, then drops any that fall inside the "past speakers" block so a
forward-dated, not-yet-published event never gets its roster confused with
speakers from an unrelated past AI Engineer conference (e.g. World's Fair).
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime

import httpx

from fleet.models import Speaker

logger = logging.getLogger("fleet.roster")

TAVILY_API_URL = "https://api.tavily.com"
# Pre-verified entry point (coord/BOARD.md PRE-VERIFIED FACTS): the AI
# Engineer NY 2026 page. Speakers are wave-published; a short/empty list
# here is a normal outcome, not a bug.
AI_ENGINEER_NY_URL = "https://ai.engineer/nyc/2026"

# Matches the site's repeated speaker-card text: a name line (1-4
# Title-Case words) followed by a "Title, Company" line. Tavily's extract
# sometimes includes the card's headshot markdown and sometimes strips
# images entirely, so this matches on the name/title-line pair alone --
# present either way. Works for whichever section this pattern occurs in:
# past-speaker carousel today, a real confirmed lineup once one ships.
_SPEAKER_CARD_RE = re.compile(
    r"\n\n([A-Z][\w.'’-]*(?: [A-Z][\w.'’-]*){0,3})\n\n([^\n]*,[^\n]*)"
)
_PAST_SPEAKERS_HEADING_RE = re.compile(r"past speakers?\s*&?\s*attendees?", re.IGNORECASE)
_TITLE_RE = re.compile(r"^# (.+)$", re.MULTILINE)
_DATES_VENUE_RE = re.compile(
    r"([A-Z][a-z]+ \d{1,2}[–-]\d{1,2},\s*\d{4}) at the ([^.\n]+)\."
)
_CFP_CLOSES_RE = re.compile(r"Call for Speakers closes\s*([A-Za-z]+\s*\d{1,2})", re.IGNORECASE)


def _tavily_extract(url: str, api_key: str) -> str:
    """One real POST to Tavily /extract. Raises on transport/API failure."""
    resp = httpx.post(
        f"{TAVILY_API_URL}/extract",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"urls": [url]},
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results") or []
    if not results or not results[0].get("raw_content"):
        raise RuntimeError(f"Tavily extract returned no raw_content for {url}: {data}")
    return results[0]["raw_content"]


def _tavily_search_official_url(event_name: str, api_key: str) -> str:
    """Fallback for an event_name we don't have a pre-verified URL for."""
    resp = httpx.post(
        f"{TAVILY_API_URL}/search",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"query": f"{event_name} official conference website", "max_results": 3},
        timeout=30.0,
    )
    resp.raise_for_status()
    results = resp.json().get("results") or []
    if not results:
        raise RuntimeError(f"Tavily search found no candidate URL for event {event_name!r}")
    return results[0]["url"]


def _resolve_entry_url(event_name: str, api_key: str) -> str:
    normalized = event_name.lower()
    if "ai engineer" in normalized and ("ny" in normalized or "new york" in normalized):
        return AI_ENGINEER_NY_URL
    logger.warning(
        "roster: agent=B step=resolve_entry_url why=no pre-verified entry point for "
        "event_name=%r, falling back to Tavily search",
        event_name,
    )
    return _tavily_search_official_url(event_name, api_key)


def _extract_event_description(raw_content: str) -> str:
    title_match = _TITLE_RE.search(raw_content)
    title = title_match.group(1).strip() if title_match else "AI Engineer New York 2026"
    dv_match = _DATES_VENUE_RE.search(raw_content)
    if dv_match:
        return f"{title} -- {dv_match.group(1)} at {dv_match.group(2)}"
    return title


def _extract_current_speakers(raw_content: str) -> tuple[list[Speaker], int]:
    """Returns (confirmed speakers, total raw card matches found).

    Cards that fall inside a "past speakers & attendees from" block are
    alumni of *other* AI Engineer events used as social proof -- they are
    never returned as this event's roster.
    """
    heading_match = _PAST_SPEAKERS_HEADING_RE.search(raw_content)
    past_start = heading_match.start() if heading_match else None
    past_end = None
    if past_start is not None:
        next_heading = raw_content.find("## ", past_start)
        past_end = next_heading if next_heading != -1 else len(raw_content)

    seen: dict[str, Speaker] = {}
    total_cards = 0
    for match in _SPEAKER_CARD_RE.finditer(raw_content):
        total_cards += 1
        if past_start is not None and past_start <= match.start() < past_end:
            continue  # past-event alumni, not this event's roster
        name, title_company = match.group(1).strip(), match.group(2).strip()
        if name in seen:
            continue
        company = None
        title = title_company
        if "," in title_company:
            title, company = (p.strip() for p in title_company.rsplit(",", 1))
        seen[name] = Speaker(name=name, title=title or None, company=company)
    return list(seen.values()), total_cards


def _cfp_still_open(raw_content: str, today: date) -> bool | None:
    match = _CFP_CLOSES_RE.search(raw_content)
    if not match:
        return None
    try:
        closes_dt = datetime.strptime(f"{match.group(1)} {today.year}", "%b %d %Y")
    except ValueError:
        logger.warning(
            "roster: agent=B step=parse_cfp_close why=unparseable date string %r",
            match.group(1),
        )
        return None
    return closes_dt.date() >= today


def fetch_roster(event_name: str) -> tuple[list[Speaker], str, bool]:
    """Real Tavily extract of the public event page. Returns
    (speakers, event_description, is_partial). Never invents a speaker:
    an empty/short list for a forward-dated, wave-published event is a
    correct result, not a failure -- is_partial is set True to reflect it.
    """
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        logger.error("roster: agent=B step=fetch_roster why=TAVILY_API_KEY not set in env")
        raise RuntimeError("TAVILY_API_KEY is not set; cannot fetch a real roster")

    url = _resolve_entry_url(event_name, api_key)

    try:
        raw_content = _tavily_extract(url, api_key)
    except (httpx.HTTPError, RuntimeError) as first_err:
        logger.error(
            "roster: agent=B step=tavily_extract why=%s url=%s (retrying once)",
            first_err,
            url,
        )
        try:
            raw_content = _tavily_extract(url, api_key)
        except (httpx.HTTPError, RuntimeError) as second_err:
            logger.error(
                "roster: agent=B step=tavily_extract why=%s url=%s (retry also failed, raising)",
                second_err,
                url,
            )
            raise RuntimeError(f"Tavily extract failed twice for {url}: {second_err}") from second_err

    event_description = _extract_event_description(raw_content)
    speakers, total_cards = _extract_current_speakers(raw_content)

    cfp_open = _cfp_still_open(raw_content, date.today())
    is_partial = not speakers or cfp_open is not False

    if not speakers:
        logger.warning(
            "roster: agent=B step=fetch_roster why=no confirmed speakers published yet for "
            "event_name=%r url=%s (found %d named card(s) on page, all attributed to past "
            "AI Engineer events, not this one)",
            event_name,
            url,
            total_cards,
        )
    else:
        logger.info(
            "roster: agent=B step=fetch_roster event_name=%r url=%s confirmed_speakers=%d "
            "is_partial=%s",
            event_name,
            url,
            len(speakers),
            is_partial,
        )

    return speakers, event_description, is_partial
