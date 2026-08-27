"""Render a `ConferenceBriefing` as the briefing a human actually reads (spec section 5).

A pure function: model in, string out. No I/O, no logging, no `print` -- stdout is the
MCP JSON-RPC channel (D-005) and this module never writes anywhere. The caller decides
what to do with the text.

Two rules the format exists to enforce:

* **Only picks with resolved facts are shown (D-007).** `rank()` ranks the whole roster,
  and an entry nothing could be verified about is page furniture, not a person worth
  meeting -- a live run ranked "Tuesday", "PM" and "New Engineer Orientation" as people.
  Fact-less entries stay in the ranking the judge audited; they do not reach the reader.
  What was dropped is stated out loud rather than quietly disappearing.
* **An empty or partial result renders as an empty or partial result.** Every section
  says what it actually has, including the sections that have nothing. A briefing that
  reads complete when the roster is half-published is worse than no briefing.
"""

from __future__ import annotations

import textwrap

from fleet.models import ConferenceBriefing, EvalResult, RankedPick, UserProfile

MAX_PICKS = 5
"""Upper bound on rendered picks (D-007: top 3-5)."""

MAX_SHARED = 5
"""Conversation openers per speaker. More than a handful reads as a keyword dump."""

WIDTH = 88

_NOTHING_PUBLISHED = (
    "No confirmed speakers published yet for this event; the roster publishes in waves. "
    "Nobody has been invented to fill this section -- check back once the next wave lands."
)


def _para(text: str, indent: str = "  ", bullet: str = "") -> str:
    """One wrapped paragraph, so a long fact or summary stays readable in a terminal.

    A bulleted paragraph hangs its continuation lines under the text, not the marker,
    so a list of long facts still reads as a list.
    """
    return textwrap.fill(
        " ".join(text.split()), width=WIDTH,
        initial_indent=indent + bullet, subsequent_indent=indent + " " * len(bullet),
    )


def _label(speaker_line: RankedPick) -> str:
    """`Name -- Title, Company`, skipping whatever the roster did not publish."""
    s = speaker_line.speaker.speaker
    detail = ", ".join(p for p in (s.title, s.company) if p)
    return f"{s.name} -- {detail}" if detail else s.name


def _shared_ground(user: UserProfile, pick: RankedPick) -> list[str]:
    """The user's own stated interests that literally appear in this speaker's evidence.

    Matched against the resolved facts, not asserted: this is a lookup, not a claim,
    so it can be shown as a conversation opener without inventing common ground.
    """
    es = pick.speaker
    blob = " ".join(es.facts + [es.alignment_note or "", es.speaker.session_title or ""]).lower()
    return [i for i in user.interests if i.lower() in blob][:MAX_SHARED]


def _rendered_picks(briefing: ConferenceBriefing) -> list[RankedPick]:
    """The picks a reader is allowed to see: fact-bearing, best first, capped."""
    grounded = [p for p in briefing.picks if p.speaker.facts]
    return sorted(grounded, key=lambda p: p.rank)[:MAX_PICKS]


def _header(briefing: ConferenceBriefing) -> list[str]:
    lines = [briefing.event_name]
    if briefing.event_description:
        lines.append(_para(briefing.event_description, indent=""))
    else:
        lines.append("(no event description was resolved for this event)")
    if briefing.roster_partial:
        lines += [
            "",
            _para(
                "PARTIAL ROSTER -- the lineup for this event is still being published in "
                "waves. Everything below is drawn from the speakers confirmed so far, not "
                "from the full lineup.",
                indent="",
            ),
        ]
    return lines


def _about_you(user: UserProfile) -> list[str]:
    lines = ["About you:"]
    if not user.summary and not user.interests:
        lines.append(
            _para(
                "No attendee profile was resolved, so the picks below were not matched "
                "against who you are -- only against the goal you typed."
            )
        )
        return lines
    if user.summary:
        lines.append(_para(user.summary))
    if user.interests:
        lines.append(_para("Interests: " + ", ".join(user.interests)))
    lines.append(f"  (profile source: {user.source})")
    return lines


def _no_picks_reason(briefing: ConferenceBriefing) -> str:
    """Why this briefing has nobody to show -- stated in the reader's terms."""
    if not briefing.picks:
        return _para(_NOTHING_PUBLISHED)
    return _para(
        f"{len(briefing.picks)} roster entries were ranked, but none of them resolved to a "
        "real person with verifiable facts, so there is nobody to put in front of you. "
        "Unverified entries are held back rather than presented as people to meet."
    )


def _aligned(briefing: ConferenceBriefing, picks: list[RankedPick]) -> list[str]:
    lines = ["Aligned sessions / speakers:"]
    if not picks:
        lines.append(_no_picks_reason(briefing))
        return lines
    for n, pick in enumerate(picks, 1):
        lines.append(_para(_label(pick), bullet=f"{n}. "))
        session = pick.speaker.speaker.session_title
        if session:
            lines.append(_para(f"Session: {session}", indent="     "))
    dropped = len(briefing.picks) - len(picks)
    if dropped:
        lines += ["", _para(
            f"Showing {len(picks)} of {len(briefing.picks)} ranked entries. {dropped} were "
            "held back for having no verifiable facts behind them; they were still audited "
            "by the judge below."
        )]
    return lines


def _why(user: UserProfile, picks: list[RankedPick]) -> list[str]:
    lines = ["Why they align:"]
    if not picks:
        lines.append("  Nobody to align yet -- see above.")
        return lines
    for pick in picks:
        lines.append(f"  {pick.speaker.speaker.name}")
        if pick.speaker.alignment_note:
            lines.append(_para(pick.speaker.alignment_note, indent="    "))
        for fact in pick.speaker.facts:
            lines.append(_para(fact, indent="    ", bullet="- "))
        lines.append(_para(f"Why: {pick.reason}", indent="    "))
        shared = _shared_ground(user, pick)
        if shared:
            lines.append(_para("Talk about: " + ", ".join(shared), indent="    "))
        lines.append(f"    (facts resolved via: {pick.speaker.source})")
        lines.append("")
    return lines[:-1]


def _evaluation(evaluation: EvalResult, run_id: str, degradations: list[str]) -> list[str]:
    lines = ["Evaluation:", f"  Confidence: {evaluation.confidence:.2f} / 1.00"]
    if evaluation.checks:
        lines.append("  Rubric:")
        lines += [
            f"    [{'PASS' if ok else 'FAIL'}] {name}"
            for name, ok in evaluation.checks.items()
        ]
    else:
        lines.append("  Rubric: no checks were recorded -- the judge did not return a verdict.")
    # The notes sit directly under the rubric: they are what explains a FAIL.
    if evaluation.notes:
        lines.append(_para(f"Judge notes: {evaluation.notes}"))
    lines.append(
        "  Self-correction: a correction pass ran and the picks above are the corrected set."
        if evaluation.corrected
        else "  Self-correction: not triggered."
    )
    if evaluation.low_confidence:
        lines.append("  LOW CONFIDENCE -- treat this briefing as a lead, not an answer.")
    if degradations:
        lines.append(f"  What degraded during this run ({len(degradations)}):")
        lines += [_para(d, indent="    ", bullet="- ") for d in degradations]
    else:
        lines.append("  Nothing degraded during this run; every lane returned normally.")
    lines.append(f"  Run: {run_id}")
    return lines


def render(briefing: ConferenceBriefing) -> str:
    """The whole briefing as spec section 5 text. Pure: same model in, same string out."""
    picks = _rendered_picks(briefing)
    blocks = [
        _header(briefing),
        _about_you(briefing.user),
        _aligned(briefing, picks),
        _why(briefing.user, picks),
        _evaluation(briefing.evaluation, briefing.run_id, briefing.degradations),
    ]
    return "\n\n".join("\n".join(block) for block in blocks)
