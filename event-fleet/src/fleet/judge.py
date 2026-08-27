"""Mechanism A: the in-request judge that scores a ranking before it is returned.

This is a judge PROMPT step that runs inside the request. It is not the Runtype
Evals feature and it does not import or call Runtype.

Rubric, in priority order:
  (b) GROUNDING -- is every fact cited in a pick's reason present in that
      speaker's enrichment evidence? This is the main job. Ungrounded facts are
      the exact failure mode this judge exists to catch, so it is weighted
      hardest and it hard-caps confidence when it fails.
  (a) INTENT -- do the picks that actually get rendered match the stated intent?
  (c) COVERAGE -- are there >= 3 picks that are both grounded and on-intent?

Scoring only. The kick-back retry loop is deliberately NOT built here.

Spec ref: Conference Prep Fleet technical spec, section 6 (Mechanism A).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from fleet.models import EvalResult, RankedPick, UserProfile
from fleet.rank import MODEL, _client, _speaker_block

log = logging.getLogger("fleet.judge")

AGENT = "C"

# How many picks the server actually renders (decision D-007). Check (a) is
# scored on these; checks (b) and (c) still see the full ranking.
RENDERED_TOP_N = 3

# How many picks one verdict audits. A full roster is 19-25 picks; auditing every
# one overruns the completion budget and returns no rubric at all. This is a
# comfortable superset of what is ever rendered (D-007).
AUDIT_LIMIT = 8

# Any unsupported claim caps confidence here, however good the rest looks.
UNGROUNDED_CONFIDENCE_CAP = 0.4
LOW_CONFIDENCE_THRESHOLD = 0.7


def _max_tokens(n_picks: int) -> int:
    """Completion budget. Measured against real calls: gpt-oss-120b spends most of
    this on reasoning, using ~1095 tokens for 5 picks and ~2180 for 20. An audit
    that has to quote many unsupported claims costs more, so this keeps roughly
    2.5x headroom -- a truncated verdict is a demo-critical failure."""
    return 1500 + 250 * n_picks

CHECK_INTENT = "top_picks_match_intent"
CHECK_GROUNDED = "facts_grounded"
CHECK_COVERAGE = "enough_valid_picks"

_SYSTEM = (
    "You are a strict grounding auditor for a conference prep agent. Another "
    "model recommended speakers to an attendee. Your job is to catch invented "
    "facts. You are not here to be agreeable.\n\n"
    "For each pick you get EVIDENCE (the only admissible source of truth about "
    "that speaker) and the REASON the recommender wrote.\n\n"
    "GROUNDING -- the priority:\n"
    "1. Extract every factual claim the REASON makes ABOUT THE SPEAKER: employer, "
    "role, product or project names, papers, talks, awards, numbers, dates, prior "
    "companies, team size, and any claimed relationship to the attendee.\n"
    "2. For each claim, decide whether the EVIDENCE for that same speaker directly "
    "states it, or trivially restates it. That is the ONLY thing that makes a "
    "claim supported.\n"
    "3. A claim that is merely plausible, well known to you, or a reasonable "
    "inference is NOT supported. Do not give the benefit of the doubt. If the "
    "evidence does not say it, list it in unsupported_claims, quoting the claim.\n"
    "4. Restating two separate evidence items in one sentence is supported. But "
    "asserting a causal or quantitative link BETWEEN them that the evidence does "
    "not state is not: flag only the invented link, not the underlying items. The "
    "speaker's name, title, company and session title are all evidence.\n"
    "5. Ignore statements about the ATTENDEE's own goals or interests, and ignore "
    "generic connective language such as 'worth meeting' or 'could help you'. "
    "Those are not claims about the speaker.\n"
    "6. If the REASON says there is no information about the speaker and makes no "
    "other claim, that is honest and fully grounded: return no unsupported claims. "
    "Admitting an absence is correct behaviour, never a violation.\n"
    "7. grounded is true if and only if unsupported_claims is empty.\n\n"
    "RELEVANCE: grade this speaker against the attendee's stated intent, on the "
    "strength of the EVIDENCE alone:\n"
    "  'direct'    -- the evidence shows they work on the thing the attendee asked for.\n"
    "  'adjacent'  -- the evidence shows work the attendee would still benefit from, "
    "on a neighbouring problem or one of their listed interests.\n"
    "  'unrelated' -- the evidence shows nothing that serves this attendee, or shows "
    "nothing at all. A speaker with no facts is 'unrelated'.\n"
    "A weaker but honest recommendation is 'adjacent', not 'unrelated'. Reserve "
    "'unrelated' for speakers this attendee would gain nothing from meeting."
)


Relevance = Literal["direct", "adjacent", "unrelated"]

# Contribution of each relevance grade to the intent term of the confidence score.
_RELEVANCE_WEIGHT: dict[str, float] = {"direct": 1.0, "adjacent": 0.6, "unrelated": 0.0}


class _Verdict(BaseModel):
    pick_index: int
    relevance: Relevance
    unsupported_claims: list[str] = Field(default_factory=list)
    grounded: bool


class _JudgeOut(BaseModel):
    verdicts: list[_Verdict] = Field(default_factory=list)
    # The schema requires this, so a complete response always carries it. It is
    # optional here only so a salvaged verdict -- truncated after `verdicts` closes
    # but before `summary` is emitted -- still validates. Every scored signal lives
    # in the verdicts; the summary is narrative for the Evaluation section.
    summary: str = ""


_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "pick_index": {"type": "integer"},
                    "relevance": {"type": "string", "enum": ["direct", "adjacent", "unrelated"]},
                    "unsupported_claims": {"type": "array", "items": {"type": "string"}},
                    "grounded": {"type": "boolean"},
                },
                "required": ["pick_index", "relevance", "unsupported_claims", "grounded"],
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["verdicts", "summary"],
}


class JudgeError(RuntimeError):
    """The judge could not produce a verdict. Raised loudly -- never swallowed."""


def _pick_block(i: int, pick: RankedPick) -> str:
    """Evidence + the reason under audit. Reuses rank's speaker renderer so the
    judge sees exactly the evidence the ranker saw -- no second format to drift."""
    return (
        f"--- PICK {i} (ranked #{pick.rank}) ---\n"
        f"EVIDENCE:\n{_speaker_block(i, pick.speaker)}\n"
        f"REASON UNDER AUDIT: {pick.reason}"
    )


def _user_prompt(user: UserProfile, picks: list[RankedPick], intent: str) -> str:
    return (
        f"ATTENDEE\n{user.summary}\n"
        f"interests: {', '.join(user.interests) or '(none recorded)'}\n\n"
        f"STATED INTENT\n{intent}\n\n"
        f"PICKS TO AUDIT ({len(picks)})\n"
        + "\n\n".join(_pick_block(i, p) for i, p in enumerate(picks))
        + f"\n\nReturn exactly {len(picks)} verdicts, one per pick index above."
    )


def _salvage(raw: str) -> str | None:
    """Close a verdict that is complete but unterminated.

    The model emits the whole audit, then pads whitespace until the token cap, so
    the JSON arrives structurally valid except for its closing brackets. Balancing
    them recovers a real verdict; anything still unparseable returns None and is
    retried rather than guessed at.
    """
    text = raw.strip()
    if not text.startswith("{"):
        return None
    depth = {"{": 0, "[": 0}
    in_string = escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "{[":
            depth["{" if ch == "{" else "["] += 1
        elif ch in "}]":
            depth["{" if ch == "}" else "["] -= 1
    if in_string or depth["{"] < 0 or depth["["] < 0:
        return None  # truncated mid-token: not recoverable, and never guessed
    closed = text + "]" * depth["["] + "}" * depth["{"]
    try:
        json.loads(closed)
    except json.JSONDecodeError:
        return None
    return closed


def _validate(raw: str, n: int) -> _JudgeOut:
    """Parse + coverage check. The message is fed back verbatim on the retry."""
    parsed = _JudgeOut.model_validate_json(raw)
    seen = [v.pick_index for v in parsed.verdicts]
    if sorted(seen) != list(range(n)):
        raise ValueError(
            f"verdicts must cover every pick_index 0..{n - 1} exactly once; got {sorted(seen)}"
        )
    for v in parsed.verdicts:
        if v.grounded and v.unsupported_claims:
            raise ValueError(
                f"pick_index {v.pick_index} is marked grounded but lists "
                f"unsupported claims {v.unsupported_claims}; grounded must be false"
            )
    return parsed


def _score(picks: list[RankedPick], out: _JudgeOut, skipped: int = 0) -> EvalResult:
    """Aggregate the model's per-pick verdicts into the rubric. Deterministic on
    purpose: every judgement here came from the real call, only the arithmetic
    is local, so the same verdicts always produce the same confidence."""
    by_index = {v.pick_index: v for v in out.verdicts}
    n = len(picks)

    ungrounded = [(picks[i].speaker.speaker.name, v.unsupported_claims)
                  for i, v in sorted(by_index.items()) if not v.grounded]
    grounded_ok = not ungrounded

    # Check (a) is scored on the slice the server actually renders: a rendered
    # pick the attendee would gain nothing from is the failure. A weaker but
    # honest 'adjacent' pick at rank 3 is a legitimate recommendation, not a bug.
    top = sorted(range(n), key=lambda i: picks[i].rank)[:RENDERED_TOP_N]
    intent_ok = all(by_index[i].relevance != "unrelated" for i in top)

    valid = [i for i, v in by_index.items() if v.grounded and v.relevance != "unrelated"]
    coverage_ok = len(valid) >= 3

    grounded_frac = sum(1 for v in by_index.values() if v.grounded) / n
    intent_frac = (
        sum(_RELEVANCE_WEIGHT[by_index[i].relevance] for i in top) / len(top) if top else 0.0
    )
    coverage_frac = min(len(valid) / 3, 1.0)

    confidence = 0.60 * grounded_frac + 0.25 * intent_frac + 0.15 * coverage_frac
    if not grounded_ok:
        # Grounding is the whole point. A single invented fact caps the score.
        confidence = min(confidence, UNGROUNDED_CONFIDENCE_CAP)
    confidence = round(confidence, 3)

    notes = out.summary.strip()
    if skipped:
        notes = (notes + " " if notes else "") + (
            f"(Audited the top {len(picks)} picks; {skipped} lower-ranked entries were "
            "not audited and are not rendered.)"
        )
    if ungrounded:
        detail = "; ".join(
            f"{name}: " + " | ".join(claims) for name, claims in ungrounded
        )
        notes = f"UNGROUNDED CLAIMS DETECTED -- {detail}. {notes}".strip()

    checks = {
        CHECK_GROUNDED: grounded_ok,
        CHECK_INTENT: intent_ok,
        CHECK_COVERAGE: coverage_ok,
    }
    return EvalResult(
        confidence=confidence,
        checks=checks,
        # Scoring only: this judge never rewrites the picks, so it never corrects.
        corrected=False,
        # Any failed check means the briefing is not trustworthy as-is, even when
        # the weighted score still looks respectable.
        low_confidence=confidence < LOW_CONFIDENCE_THRESHOLD or not all(checks.values()),
        notes=notes or None,
    )


def judge(user: UserProfile, picks: list[RankedPick], intent: str) -> EvalResult:
    """Score `picks` against `intent` with one real Nebius call.

    Returns an EvalResult carrying per-check pass/fail and a confidence in 0-1.
    Raises JudgeError if no valid verdict survives one corrective retry.
    """
    if not picks:
        log.warning("agent=%s step=judge why=no picks to judge, nothing to score", AGENT)
        return EvalResult(
            confidence=0.0,
            checks={CHECK_GROUNDED: False, CHECK_INTENT: False, CHECK_COVERAGE: False},
            corrected=False,
            low_confidence=True,
            notes="No picks were produced, so there was nothing to judge.",
        )

    # A full roster is 19-25 picks, and auditing all of them overruns the completion
    # budget: the verdict truncates mid-audit and the briefing renders with no rubric
    # at all, which costs more than the unaudited tail is worth. The reader only ever
    # sees the top few (D-007), so audit a superset of those and say so plainly.
    audited = picks[:AUDIT_LIMIT]
    skipped = len(picks) - len(audited)
    if skipped:
        log.info(
            "agent=%s step=judge why=auditing top %d of %d picks; the rest rank below "
            "anything that will be rendered", AGENT, len(audited), len(picks),
        )

    messages: list[dict[str, str]] = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": _user_prompt(user, audited, intent)},
    ]

    picks = audited
    client = _client()
    last_err: str | None = None
    budget = _max_tokens(len(picks))
    started = time.perf_counter()

    for attempt in (1, 2):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "judge_verdict", "strict": True, "schema": _SCHEMA},
                },
                temperature=0.0,
                max_tokens=budget,
                # gpt-oss-120b is a reasoning model: uncapped, it emits a valid verdict
                # then floods trailing whitespace until finish_reason=length. "low"
                # answers too shallowly to catch ungrounded claims; "medium" is correct
                # and no slower.
                reasoning_effort="medium",
            )
        except Exception as exc:  # network / auth / rate limit -- never silent
            log.error(
                "agent=%s step=judge.nebius attempt=%d model=%s why=%s: %s",
                AGENT, attempt, MODEL, type(exc).__name__, exc,
            )
            raise JudgeError(f"agent=C step=judge.nebius why=Nebius call failed: {exc}") from exc

        raw = resp.choices[0].message.content or ""
        if resp.choices[0].finish_reason != "stop":
            # gpt-oss-120b finishes the verdict, then floods whitespace until the cap.
            # reasoning_effort="medium" makes this rare but not impossible (~1 in 9 real
            # calls, always on the smallest budget), so the verdict is salvaged rather
            # than thrown away: a complete audit is not a failure just because trailing
            # padding ran past the limit. Only a genuinely incomplete verdict retries.
            salvaged = _salvage(raw)
            if salvaged is not None:
                try:
                    parsed = _validate(salvaged, len(picks))
                except (ValidationError, ValueError, json.JSONDecodeError):
                    salvaged = None
            if salvaged is None:
                last_err = f"response was cut off (finish_reason={resp.choices[0].finish_reason})"
                budget *= 2  # genuinely truncated -- buy room and retry
                log.warning(
                    "agent=%s step=judge.truncated attempt=%d why=verdict incomplete, retrying "
                    "with budget=%d", AGENT, attempt, budget,
                )
                continue
            log.warning(
                "agent=%s step=judge.salvaged attempt=%d why=verdict complete but response hit "
                "the token cap on trailing padding; parsed it instead of retrying", AGENT, attempt,
            )
            raw = salvaged
        try:
            parsed = _validate(raw, len(picks))
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            last_err = str(exc)
        else:
            result = _score(picks, parsed, skipped)
            log.info(
                "agent=%s step=judge model=%s picks=%d attempts=%d latency_s=%.2f "
                "confidence=%.3f checks=%s",
                AGENT, MODEL, len(picks), attempt, time.perf_counter() - started,
                result.confidence, result.checks,
            )
            if not result.checks[CHECK_GROUNDED]:
                # A failed grounding check is a loud finding, not a detail.
                log.error(
                    "agent=%s step=judge.grounding why=ranking cited facts absent from "
                    "the enrichment output | %s", AGENT, result.notes,
                )
            return result

        log.error(
            "agent=%s step=judge.parse attempt=%d model=%s why=%s | raw=%.400r",
            AGENT, attempt, MODEL, last_err, raw,
        )
        if attempt == 1:
            messages += [
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": (
                        f"That response was rejected: {last_err}\n"
                        f"Re-emit the audit as valid JSON matching the schema, with exactly "
                        f"{len(picks)} verdicts covering pick_index 0..{len(picks) - 1} once each."
                    ),
                },
            ]

    raise JudgeError(
        f"agent=C step=judge why=model returned an invalid verdict twice ({MODEL}): {last_err}"
    )
