"""Hand-written test INPUT for rank(). Fixtures only -- never a canned ranking.

These stand in for Agent B's roster + Agent D's enrichment until the real roster
is swapped in by the integration task. The ranking they feed is always produced
by a real Nebius call.
"""

from fleet.models import EnrichedSpeaker, Speaker, UserProfile

USER = UserProfile(
    summary=(
        "Nikhil Kulkarni, founding engineer at a seed-stage startup shipping an "
        "LLM agent product. Spends most of his time on retrieval quality and on "
        "getting evals to catch regressions before customers do."
    ),
    interests=["RAG evaluation", "agent orchestration", "inference cost", "developer tooling"],
    source="iridium",
)

INTENT = "Find people who can help me make my agent evals actually catch regressions."

SPEAKERS: list[EnrichedSpeaker] = [
    EnrichedSpeaker(
        speaker=Speaker(
            name="Ann Delgado",
            title="Head of Evaluation",
            company="Vector Foundry",
            session_title="Why your RAG eval suite is lying to you",
            profile_url="https://example.invalid/ann",
        ),
        facts=[
            "Built the open-source harness `graderail` for scoring retrieval pipelines.",
            "Published a study of 40 production RAG systems and their eval drift.",
        ],
        alignment_note="Works on exactly the retrieval-eval problem the attendee named.",
        source="tavily",
    ),
    EnrichedSpeaker(
        speaker=Speaker(
            name="Bo Ramirez",
            title="Principal Silicon Architect",
            company="Northgate Semiconductor",
            session_title="Memory bandwidth is the new context window",
            profile_url="https://example.invalid/bo",
        ),
        facts=["Leads the accelerator memory subsystem team.", "Twelve years in GPU microarchitecture."],
        alignment_note=None,
        source="tavily",
    ),
    EnrichedSpeaker(
        speaker=Speaker(
            name="Priya Nandakumar",
            title="Staff Engineer, Agents",
            company="Loomstate",
            session_title="Orchestrating 40 agents without losing the trace",
            profile_url="https://example.invalid/priya",
        ),
        facts=[
            "Maintains Loomstate's multi-agent tracing layer.",
            "Wrote the postmortem on their 2025 agent-cascade outage.",
        ],
        alignment_note="Overlaps on agent orchestration and observability.",
        source="iridium",
    ),
    EnrichedSpeaker(
        speaker=Speaker(name="Marcus Feld", title=None, company=None, session_title=None, profile_url=None),
        facts=[],
        alignment_note=None,
        source="none",
    ),
    EnrichedSpeaker(
        speaker=Speaker(
            name="Sofia Iversen",
            title="CTO",
            company="Halyard AI",
            session_title="Cutting inference spend 8x without a smaller model",
            profile_url="https://example.invalid/sofia",
        ),
        facts=["Ran Halyard's migration to serverless inference.", "Open-sourced their router benchmark."],
        alignment_note="Speaks to the attendee's inference-cost interest.",
        source="tavily",
    ),
]
