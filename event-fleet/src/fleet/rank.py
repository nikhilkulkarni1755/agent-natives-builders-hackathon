"""Priorities plane: rank enriched speakers against the user's stated intent.

One real Nebius call (serverless inference only -- this never rents a GPU).
Structured output is forced with a strict JSON schema and validated into the
Pydantic shapes from `fleet.models`. Nothing here is stubbed, cached or faked.

Spec ref: Conference Prep Fleet technical spec, section 4 (Priorities).
"""

from __future__ import annotations

import json
import logging
import os
import time

from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

from fleet.models import EnrichedSpeaker, RankedPick, UserProfile

log = logging.getLogger("fleet.rank")

AGENT = "C"
NEBIUS_BASE_URL = "https://api.studio.nebius.com/v1/"
MODEL = os.environ.get("NEBIUS_MODEL", "openai/gpt-oss-120b")

_SYSTEM = (
    "You are the priorities planner for a conference prep agent. You rank the "
    "speakers a specific attendee should seek out, given that attendee's profile "
    "and their stated intent for the event.\n"
    "Rules:\n"
    "1. Rank EVERY speaker you are given. Return one entry per speaker index, no "
    "duplicates, no omissions, ranks 1..N with 1 = seek out first.\n"
    "2. Order by fit to the stated INTENT first, then the attendee's interests.\n"
    "3. The reason must be one or two sentences, addressed to the attendee, and "
    "must cite only facts present in that speaker's supplied block. Never invent "
    "a fact, a company, a talk title or a shared connection.\n"
    "4. If a speaker's block is thin, say so plainly in the reason rather than "
    "guessing."
)


class _PickOut(BaseModel):
    """Wire shape the model returns. Deliberately indices, not speaker copies:
    the EnrichedSpeaker is re-attached from the input so identity and facts can
    never be paraphrased or hallucinated by the model."""

    speaker_index: int
    rank: int
    reason: str


class _RankingOut(BaseModel):
    picks: list[_PickOut] = Field(default_factory=list)


_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "speaker_index": {"type": "integer"},
                    "rank": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["speaker_index", "rank", "reason"],
            },
        }
    },
    "required": ["picks"],
}


class RankError(RuntimeError):
    """Ranking could not be produced. Raised loudly -- never swallowed."""


def _client() -> OpenAI:
    key = os.environ.get("NEBIUS_API_KEY")
    if not key:
        raise RankError("agent=C step=rank.client why=NEBIUS_API_KEY not set in env")
    return OpenAI(base_url=NEBIUS_BASE_URL, api_key=key)


def _speaker_block(i: int, es: EnrichedSpeaker) -> str:
    s = es.speaker
    lines = [f"[{i}] {s.name}"]
    if s.title or s.company:
        lines.append(f"    role: {' @ '.join(p for p in (s.title, s.company) if p)}")
    if s.session_title:
        lines.append(f"    session: {s.session_title}")
    for f in es.facts:
        lines.append(f"    fact: {f}")
    if es.alignment_note:
        lines.append(f"    alignment: {es.alignment_note}")
    lines.append(f"    enriched_by: {es.source}")
    return "\n".join(lines)


def _user_prompt(user: UserProfile, speakers: list[EnrichedSpeaker], intent: str) -> str:
    return (
        f"ATTENDEE\n{user.summary}\n"
        f"interests: {', '.join(user.interests) or '(none recorded)'}\n"
        f"profile source: {user.source}\n\n"
        f"INTENT FOR THIS EVENT\n{intent}\n\n"
        f"SPEAKERS ({len(speakers)} of them, index in brackets)\n"
        + "\n".join(_speaker_block(i, es) for i, es in enumerate(speakers))
        + f"\n\nReturn exactly {len(speakers)} picks, one per index above."
    )


def _validate(raw: str, n: int) -> list[_PickOut]:
    """Parse + check coverage. Any problem raises ValueError with a message that
    is fed straight back to the model on the single retry."""
    parsed = _RankingOut.model_validate_json(raw)
    seen = [p.speaker_index for p in parsed.picks]
    if sorted(seen) != list(range(n)):
        raise ValueError(
            f"picks must cover every speaker_index 0..{n - 1} exactly once; "
            f"got {sorted(seen)}"
        )
    if sorted(p.rank for p in parsed.picks) != list(range(1, n + 1)):
        raise ValueError(f"rank must be 1..{n} with no ties or gaps; got {[p.rank for p in parsed.picks]}")
    for p in parsed.picks:
        if not p.reason.strip():
            raise ValueError(f"speaker_index {p.speaker_index} has an empty reason")
    return parsed.picks


def rank(user: UserProfile, speakers: list[EnrichedSpeaker], intent: str) -> list[RankedPick]:
    """Rank `speakers` for `user` against `intent` with one real Nebius call.

    Returns every supplied speaker as a RankedPick, best first. Raises RankError
    if the model cannot produce a valid ranking after one corrective retry.
    """
    if not speakers:
        log.warning("agent=%s step=rank why=empty speaker list, nothing to rank", AGENT)
        return []

    messages: list[dict[str, str]] = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": _user_prompt(user, speakers, intent)},
    ]

    client = _client()
    last_err: str | None = None
    started = time.perf_counter()

    for attempt in (1, 2):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "ranked_picks", "strict": True, "schema": _SCHEMA},
                },
                temperature=0.2,
                max_tokens=400 + 220 * len(speakers),
            )
        except Exception as exc:  # network / auth / rate limit -- never silent
            log.error(
                "agent=%s step=rank.nebius attempt=%d model=%s why=%s: %s",
                AGENT, attempt, MODEL, type(exc).__name__, exc,
            )
            raise RankError(f"agent=C step=rank.nebius why=Nebius call failed: {exc}") from exc

        raw = resp.choices[0].message.content or ""
        if resp.choices[0].finish_reason != "stop":
            last_err = f"response was cut off (finish_reason={resp.choices[0].finish_reason})"
        else:
            try:
                picks = _validate(raw, len(speakers))
            except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                last_err = str(exc)
            else:
                elapsed = time.perf_counter() - started
                log.info(
                    "agent=%s step=rank model=%s speakers=%d attempts=%d latency_s=%.2f tokens=%s",
                    AGENT, MODEL, len(speakers), attempt, elapsed,
                    getattr(resp.usage, "total_tokens", "?"),
                )
                by_rank = sorted(picks, key=lambda p: p.rank)
                return [
                    RankedPick(speaker=speakers[p.speaker_index], rank=p.rank, reason=p.reason.strip())
                    for p in by_rank
                ]

        log.error(
            "agent=%s step=rank.parse attempt=%d model=%s why=%s | raw=%.400r",
            AGENT, attempt, MODEL, last_err, raw,
        )
        if attempt == 1:
            # Feed the parse error back verbatim; one retry only, then fail loudly.
            messages += [
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": (
                        f"That response was rejected: {last_err}\n"
                        f"Re-emit the ranking as valid JSON matching the schema, with exactly "
                        f"{len(speakers)} picks covering speaker_index 0..{len(speakers) - 1} "
                        f"once each and ranks 1..{len(speakers)}."
                    ),
                },
            ]

    raise RankError(
        f"agent=C step=rank why=model returned an invalid ranking twice ({MODEL}): {last_err}"
    )
