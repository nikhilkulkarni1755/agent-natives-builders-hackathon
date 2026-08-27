"""Single source of truth for every shape in the fleet.

No agent redefines any of these. No parallel dict version exists anywhere.
Spec ref: Conference Prep Fleet technical spec, section 7.
"""

from enum import Enum

from pydantic import BaseModel, Field


class PrepRequest(BaseModel):
    """Input to the prep_conference tool."""

    event_name: str
    intent: str


class Speaker(BaseModel):
    """One roster entry. Produced by Roster, consumed by Enrichment + Priorities."""

    name: str
    title: str | None = None
    company: str | None = None
    session_title: str | None = None
    profile_url: str | None = None


class EnrichedSpeaker(BaseModel):
    """Speaker plus resolved facts. Reuses Speaker; nothing re-declared."""

    speaker: Speaker
    facts: list[str] = Field(default_factory=list)
    alignment_note: str | None = None
    source: str = "tavily"  # which plane resolved it: iridium | tavily | none


class UserProfile(BaseModel):
    """Who the user is, from Iridium enrichment (or Tavily fallback)."""

    summary: str
    interests: list[str] = Field(default_factory=list)
    source: str = "iridium"


class RankedPick(BaseModel):
    speaker: EnrichedSpeaker
    rank: int
    reason: str


class EvalResult(BaseModel):
    """Mechanism A output: the in-request judge verdict."""

    confidence: float
    checks: dict[str, bool] = Field(default_factory=dict)
    corrected: bool = False
    low_confidence: bool = False
    notes: str | None = None


class ConferenceBriefing(BaseModel):
    """The return payload. Assembled from the above; nothing redefined."""

    run_id: str
    event_name: str
    event_description: str
    user: UserProfile
    picks: list[RankedPick] = Field(default_factory=list)
    evaluation: EvalResult
    roster_partial: bool = False
    degradations: list[str] = Field(default_factory=list)


class FeedbackVerdict(str, Enum):
    useful = "useful"
    partially_useful = "partially_useful"
    not_useful = "not_useful"


class FeedbackReason(str, Enum):
    """Closed set. Doubles as the eval-suite grader dimensions -- do not restate elsewhere."""

    wrong_people = "wrong_people"
    weak_alignment = "weak_alignment"
    hallucinated_fact = "hallucinated_fact"
    missed_someone = "missed_someone"
    stale_or_thin_data = "stale_or_thin_data"
    other = "other"


class RunFeedback(BaseModel):
    verdict: FeedbackVerdict
    reasons: list[FeedbackReason] = Field(default_factory=list)
    corrected_picks: list[str] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=1000)


class EvalAck(BaseModel):
    run_id: str
    recorded: bool
    promoted_to_eval_suite: bool
