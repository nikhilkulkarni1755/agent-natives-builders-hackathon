"""Mitosis: the cited EVIDENCE STORE the judge audits against (D-019, D-020, D-021).

The judge is NOT replaced. Its grounding check stays exactly as it is -- it has
caught real invented facts on live data. This module only changes where evidence
can come from: `cited_facts()` returns evidence strings that already carry their
citation, ready to be appended to `EnrichedSpeaker.facts`, which is what the
judge renders as `fact:` lines and audits every claim against. One line in the
enrichment lane adopts it; nothing here imports the live path and nothing here
runs unless a caller asks for it.

Transport, probed live -- not guessed:
  POST https://mitosislabs.ai/api/mcp   MCP Streamable HTTP, JSON-RPC 2.0.
  Headers: Content-Type: application/json
           Accept: application/json, text/event-stream
  Stateless: `initialize` returns no Mcp-Session-Id, so a tools/call needs no
  handshake. The server may answer as plain JSON or as one SSE frame; both are
  decoded here.

Auth, the honest state (D-020):
  PUBLIC tools -- get_platform_status, search_docs, get_pricing, list_skills --
  answer with NO credential at all. `reachable()` uses one, so reachability is
  always provable.
  Every `cortex_*` tool returns 401 with
      WWW-Authenticate: Bearer ... scope="memory:read memory:write"
  until the human completes browser OAuth. There is no API key for this surface:
  OAuth 2.1 with dynamic client registration is the only path. `claude mcp add`
  stores that token inside Claude Code's own config, which this process cannot
  read, so the token reaches us only if the human exports it (see _token). A 401
  is therefore an EXPECTED, well-understood state, surfaced as
  MitosisUnauthorized carrying the exact remediation -- never a crash, never a
  silently empty result.

Response shapes are from the Mitosis developer docs (/developers/memory/asking
and /developers/memory/writing), read for this module. They are parsed
defensively: a missing field degrades that one item, it does not fail the call.

SECURITY: the bearer token lives in this process's memory only. It is never
written to a file, a log line, or a coord status.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

import httpx

log = logging.getLogger("fleet.mitosis")

AGENT = "X2"
ENDPOINT = os.environ.get("MITOSIS_MCP_URL", "https://mitosislabs.ai/api/mcp")
TIMEOUT = 30.0
USER_AGENT = "conference-prep-fleet/0.1"

# Answer with no credential at all. `reachable()` stays on this path so the
# integration is demonstrable on stage whether or not OAuth has completed.
PUBLIC_TOOLS = frozenset({"get_pricing", "get_platform_status", "search_docs", "list_skills"})

REMEDIATION = (
    "complete browser OAuth for Mitosis: `claude mcp add --transport http mitosis "
    f"{ENDPOINT}` then approve in the browser; export that bearer token as "
    "MITOSIS_ACCESS_TOKEN for this process. There is no API key for this surface."
)

# How much of one evidence preview is carried into a fact line. Long enough to
# hold a real claim, short enough that N of them do not crowd the judge prompt.
PREVIEW_CHARS = 300


class MitosisError(RuntimeError):
    """Any Mitosis call that did not succeed. Never raised silently."""

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


class MitosisUnauthorized(MitosisError):
    """A `cortex_*` tool answered 401: browser OAuth is not complete.

    Expected, not exceptional. `remediation` is the exact human action.
    """

    def __init__(self, message: str, *, status: int = 401):
        super().__init__(message, status=status)
        self.remediation = REMEDIATION


@dataclass(frozen=True)
class Evidence:
    """One ranked, cited item from cortex_ask. `universal_id` IS the citation."""

    universal_id: str
    preview: str
    score: float | None = None
    title: str | None = None
    source_table: str | None = None
    source_url: str | None = None
    cited_graph_url: str | None = None

    def as_fact(self) -> str:
        """The judge-facing form: the claim with its receipt attached.

        `EnrichedSpeaker.facts` is a list[str] and the judge treats each entry as
        admissible evidence, so the citation rides inside the string. That is what
        makes adoption a one-liner and keeps models.py untouched.
        """
        text = " ".join((self.title or "", self.preview)).strip()
        text = " ".join(text.split())[:PREVIEW_CHARS]
        return f"{text} [mitosis:{self.universal_id}]"


def _token() -> str | None:
    """The OAuth bearer, if the human has exported one. Never logged, never stored.

    `claude mcp add` keeps its token in Claude Code's own config, which this
    process has no access to, so an authorized run needs the token exported here.
    """
    return os.environ.get("MITOSIS_ACCESS_TOKEN") or os.environ.get("MITOSIS_TOKEN") or None


def _payload(r: httpx.Response) -> dict:
    """Decode one JSON-RPC reply, plain JSON or a single SSE frame."""
    body = r.text
    if "text/event-stream" in r.headers.get("content-type", ""):
        body = "\n".join(
            line[5:].strip() for line in r.text.splitlines() if line.startswith("data:")
        )
    try:
        return json.loads(body)
    except ValueError as exc:
        raise MitosisError(
            f"agent={AGENT} step=decode why=non-JSON response ({r.status_code})",
            status=r.status_code,
        ) from exc


def call_tool(name: str, arguments: dict | None = None) -> dict:
    """Invoke one Mitosis MCP tool. Returns its structured result.

    Raises MitosisUnauthorized on the 401 OAuth gate and MitosisError on anything
    else -- transport failure, JSON-RPC error, or a tool-level isError. Nothing is
    swallowed and no empty result is invented in place of a failure.
    """
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": USER_AGENT,
    }
    token = _token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    log.info("agent=%s step=call_tool tool=%s args=%s authed=%s",
             AGENT, name, sorted(arguments or {}), bool(token))
    try:
        r = httpx.post(ENDPOINT, json=request, headers=headers, timeout=TIMEOUT)
    except httpx.HTTPError as exc:
        log.error("agent=%s step=call_tool tool=%s why=transport %s", AGENT, name, exc)
        raise MitosisError(f"agent={AGENT} step=call_tool tool={name} why=transport: {exc}") from exc

    if r.status_code == 401:
        log.error("agent=%s step=call_tool tool=%s why=401 oauth incomplete (scope=%s); fix=%s",
                  AGENT, name, "memory:read memory:write", REMEDIATION)
        raise MitosisUnauthorized(
            f"agent={AGENT} step=call_tool tool={name} why=401 Mitosis OAuth not "
            f"complete; fix={REMEDIATION}"
        )
    if r.status_code != 200:
        log.error("agent=%s step=call_tool tool=%s why=http %s", AGENT, name, r.status_code)
        raise MitosisError(
            f"agent={AGENT} step=call_tool tool={name} why=http {r.status_code}: {r.text[:300]}",
            status=r.status_code,
        )

    body = _payload(r)
    if "error" in body:
        detail = str(body["error"].get("message", body["error"]))[:300]
        log.error("agent=%s step=call_tool tool=%s why=jsonrpc error: %s", AGENT, name, detail)
        raise MitosisError(f"agent={AGENT} step=call_tool tool={name} why={detail}")

    result = body.get("result") or {}
    if result.get("isError"):
        detail = json.dumps(result.get("content", []))[:300]
        log.error("agent=%s step=call_tool tool=%s why=tool error: %s", AGENT, name, detail)
        raise MitosisError(f"agent={AGENT} step=call_tool tool={name} why=tool error: {detail}")

    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    # No structuredContent: the payload is JSON inside the first text block.
    for block in result.get("content", []):
        if block.get("type") == "text":
            try:
                parsed = json.loads(block.get("text", ""))
            except ValueError:
                continue
            if isinstance(parsed, dict):
                return parsed
    raise MitosisError(f"agent={AGENT} step=call_tool tool={name} why=no structured result")


def reachable() -> dict:
    """Prove the endpoint answers, using a PUBLIC tool that needs no credential.

    This path is independent of OAuth, so it works on stage in every state.
    """
    status = call_tool("get_platform_status", {"service": "mcp"})
    log.info("agent=%s step=reachable ok=true detail=%s", AGENT, json.dumps(status)[:200])
    return status


def ask(question: str, *, limit: int = 5, since: str | None = None,
        until: str | None = None) -> list[Evidence]:
    """Read cited evidence from the user's memory. Requires OAuth.

    cortex_ask ranks by RELEVANCE, not recency -- bound the window with
    `since`/`until` (RFC 3339) whenever the question is time-sensitive.
    Raises MitosisUnauthorized if browser OAuth is not complete.
    """
    args: dict = {"question": question, "limit": limit}
    if since:
        args["since"] = since
    if until:
        args["until"] = until
    body = call_tool("cortex_ask", args)

    cited_graph_url = body.get("cited_graph_url")
    items: list[Evidence] = []
    for row in body.get("results") or []:
        uid = row.get("universal_id")
        preview = (row.get("preview") or "").strip()
        if not uid or not preview:
            # No citation or no content is not evidence. Dropping it is a
            # degradation, so it is logged rather than quietly skipped.
            log.warning("agent=%s step=ask why=dropped result missing universal_id/preview",
                        AGENT)
            continue
        items.append(Evidence(
            universal_id=uid,
            preview=preview,
            score=row.get("score"),
            title=row.get("title"),
            source_table=row.get("source_table"),
            source_url=row.get("source_url"),
            cited_graph_url=cited_graph_url,
        ))
    log.info("agent=%s step=ask q=%r results=%d cited_graph_url=%s",
             AGENT, question[:80], len(items), bool(cited_graph_url))
    return items


def remember(text: str, *, kind: str = "observation", confidence: float | None = None,
             source_universal_ids: list[str] | None = None) -> dict:
    """Write one durable fact into the memory. Requires OAuth.

    Returns the server's ack -- status, universal_id, embedded, linked_sources,
    derived_from_edges, retrievable_at. That universal_id is the citation a later
    `ask()` will hand back.
    """
    args: dict = {"text": text, "kind": kind}
    if confidence is not None:
        args["confidence"] = confidence
    if source_universal_ids:
        args["source_universal_ids"] = source_universal_ids
    ack = call_tool("cortex_remember", args)
    log.info("agent=%s step=remember universal_id=%s status=%s",
             AGENT, ack.get("universal_id"), ack.get("status"))
    return ack


def cited_facts(question: str, *, limit: int = 3,
                since: str | None = None) -> tuple[list[str], str | None]:
    """THE ADOPTION SURFACE: cited evidence strings + an optional degradation line.

    Never raises, never silently empty: a failure comes back as a human-readable
    degradation string in the second slot, matching the fleet's degradation
    contract (it appends straight to `ConferenceBriefing.degradations`). The
    strings in the first slot append straight to `EnrichedSpeaker.facts`, which
    the judge then audits every claim against -- so each alignment claim gets a
    receipt AND an independent auditor.

        facts, degraded = mitosis.cited_facts(speaker.name)
    """
    try:
        items = ask(question, limit=limit, since=since)
    except MitosisUnauthorized as exc:
        return [], (f"Mitosis evidence unavailable for {question[:60]!r}: browser OAuth "
                    f"not complete (401, scope memory:read memory:write). Fix: {exc.remediation}")
    except MitosisError as exc:
        return [], f"Mitosis evidence unavailable for {question[:60]!r}: {exc}"
    if not items:
        log.warning("agent=%s step=cited_facts q=%r why=memory returned no evidence",
                    AGENT, question[:80])
        return [], f"Mitosis holds no evidence for {question[:60]!r}"
    return [e.as_fact() for e in items], None
