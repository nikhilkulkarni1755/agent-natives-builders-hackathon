"""Regression tests for Mechanism A (the judge): lock in that it discriminates.

Every test below makes a real Nebius call -- judge() is not mocked. A judge that
passes everything is worth nothing, so these assert on DISCRIMINATION (grounded
passes, ungrounded fails, honest absence passes) and on the deterministic
arithmetic in judge._score, never on an exact confidence float from the model.

Picks are hand-written RankedPick fixtures built on the EnrichedSpeaker roster
in fixtures_rank.py (the existing pattern) -- legitimate test INPUT. The judge's
verdicts and scores are always produced by the real model, never canned.
"""

from fixtures_rank import INTENT, SPEAKERS, USER

from fleet.judge import CHECK_COVERAGE, CHECK_GROUNDED, UNGROUNDED_CONFIDENCE_CAP, judge
from fleet.models import RankedPick

ANN, _BO, PRIYA, MARCUS, SOFIA = SPEAKERS


def test_grounded_ranking_passes_with_enough_coverage():
    """Three picks, every reason citing only facts from that speaker's own
    fixture, all on-intent -- should pass grounding, pass the >=3 coverage
    threshold, and land at high confidence."""
    picks = [
        RankedPick(
            speaker=ANN,
            rank=1,
            reason=(
                "Ann built graderail, an open-source harness for scoring retrieval "
                "pipelines, and published a study of eval drift across 40 production "
                "RAG systems -- exactly the eval-regression problem you're chasing."
            ),
        ),
        RankedPick(
            speaker=PRIYA,
            rank=2,
            reason=(
                "Priya maintains Loomstate's multi-agent tracing layer and wrote the "
                "postmortem on their 2025 agent-cascade outage, which speaks directly "
                "to your agent-orchestration interest."
            ),
        ),
        RankedPick(
            speaker=SOFIA,
            rank=3,
            reason=(
                "Sofia ran Halyard's migration to serverless inference and "
                "open-sourced their router benchmark, relevant to your interest in "
                "inference cost."
            ),
        ),
    ]

    result = judge(USER, picks, INTENT)

    assert result.checks[CHECK_GROUNDED] is True
    assert result.checks[CHECK_COVERAGE] is True
    assert result.confidence > UNGROUNDED_CONFIDENCE_CAP
    assert result.low_confidence is False


def test_ungrounded_ranking_fails_and_caps_confidence():
    """Two of three reasons invent a fact absent from that speaker's evidence
    (a fake NASA role, a fake CUDA kernel spec) -- the judge must catch both,
    fail grounding, and hard-cap confidence at UNGROUNDED_CONFIDENCE_CAP."""
    picks = [
        RankedPick(
            speaker=ANN,
            rank=1,
            reason="Ann led NASA's Mars rover terrain-recognition team before pivoting to evals.",
        ),
        RankedPick(
            speaker=PRIYA,
            rank=2,
            reason="Priya designed the custom CUDA kernel that cut Loomstate's inference latency by 40%.",
        ),
        RankedPick(
            speaker=SOFIA,
            rank=3,
            reason="Sofia ran Halyard's migration to serverless inference.",
        ),
    ]

    result = judge(USER, picks, INTENT)

    assert result.checks[CHECK_GROUNDED] is False
    assert result.confidence <= UNGROUNDED_CONFIDENCE_CAP
    assert result.low_confidence is True
    assert result.notes and "UNGROUNDED CLAIMS DETECTED" in result.notes


def test_factless_speaker_honest_reason_passes_grounding():
    """Marcus has no facts at all. A reason that honestly admits there is no
    information, and invents nothing, must PASS grounding -- declining to
    invent is correct behaviour, not a violation (judge.py _SYSTEM rule 6)."""
    picks = [
        RankedPick(
            speaker=MARCUS,
            rank=1,
            reason=(
                "There is no information available about Marcus in the enrichment "
                "data, so I can't tell you how he fits your interests -- check his "
                "session listing directly if you want to know more."
            ),
        ),
    ]

    result = judge(USER, picks, INTENT)

    assert result.checks[CHECK_GROUNDED] is True


def test_enough_valid_picks_fails_below_threshold():
    """Same grounded, on-intent picks as the passing test, minus one -- swapped
    for Marcus, who has zero facts. judge.py's RELEVANCE rubric makes a
    fact-less speaker 'unrelated' by definition, so only 2 valid (grounded +
    non-unrelated) picks remain, below the >=3 threshold: coverage must fail
    even though nothing here is ungrounded."""
    picks = [
        RankedPick(
            speaker=ANN,
            rank=1,
            reason=(
                "Ann built graderail, an open-source harness for scoring retrieval "
                "pipelines, and published a study of eval drift across 40 production "
                "RAG systems -- exactly the eval-regression problem you're chasing."
            ),
        ),
        RankedPick(
            speaker=PRIYA,
            rank=2,
            reason=(
                "Priya maintains Loomstate's multi-agent tracing layer and wrote the "
                "postmortem on their 2025 agent-cascade outage, which speaks directly "
                "to your agent-orchestration interest."
            ),
        ),
        RankedPick(
            speaker=MARCUS,
            rank=3,
            reason=(
                "There is no information available about Marcus in the enrichment "
                "data, so I can't tell you how he fits your interests -- check his "
                "session listing directly if you want to know more."
            ),
        ),
    ]

    result = judge(USER, picks, INTENT)

    assert result.checks[CHECK_GROUNDED] is True  # nothing invented here
    assert result.checks[CHECK_COVERAGE] is False  # Marcus has no facts -> unrelated -> only 2 valid
