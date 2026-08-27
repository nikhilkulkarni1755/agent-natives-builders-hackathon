"""Door 2: a thin HTTP client for the Iridium REST API.

Door 2 is a plain server-to-server HTTPS call we make ourselves with our own
API key. Cotal never carries an Iridium credential and there is no OAuth
handoff between the two -- they are separate auth planes, do not conflate them.

Shape of the real API (verified by live call, not guessed):
  POST /auth/login    {"api_key": "<raw key>"} -> access_token (~15 min) + refresh_token
  POST /auth/refresh  {"refresh_token": "..."} -> a NEW pair; the old refresh token
                      is rotated out and reuse outside its short grace window
                      revokes every session for the account.
  Each MCP tool is its own REST path, `/mcp/tools/<name>`. No-argument tools are
  GET (get_user_profile, list_*); tools that take arguments are POST with the
  arguments as the top-level JSON body -- there is no {"name","arguments"} envelope.

GOTCHA: the API key is sent RAW. An `irid_` prefix returns 401 "Invalid API key";
the prefix in the docs is illustrative notation, not part of the value.

SECURITY: an Iridium token is unscoped full access to the human's real LinkedIn
account. It is never written to a file, a log line or a coord status -- it lives
in this process's memory only. Rotated refresh tokens are therefore persisted
in-memory for the process lifetime; a restart simply logs in again with the API
key, which is cheap and avoids ever putting a token on disk.

Spec ref: Conference Prep Fleet technical spec, section 3 (Enrichment).
"""

from __future__ import annotations

import logging
import os
import threading

import httpx

log = logging.getLogger("fleet.iridium")

AGENT = "D"
BASE_URL = os.environ.get("IRIDIUM_BASE_URL", "https://api.iridiumhqmcp.com")
USER_AGENT = "conference-prep-fleet/0.1"
TIMEOUT = 90.0

# We only ever read. These prefixes act on the human's real production LinkedIn
# account -- sending a DM, approving a draft, deleting the account -- so the
# client refuses them outright rather than trusting every caller to remember.
_MUTATING_PREFIXES = (
    "approve_", "backfill_", "cancel_", "classify_", "connect_", "create_",
    "delete_", "disconnect_", "discover_", "draft_", "edit_", "enroll_",
    "report_", "reschedule_", "rewrite_", "run_", "schedule_", "send_",
    "skip_", "update_",
)


class IridiumError(RuntimeError):
    """Any Iridium call that did not succeed. Never raised silently."""

    def __init__(self, message: str, *, status: int | None = None, detail: str = ""):
        super().__init__(message)
        self.status = status
        self.detail = detail


class IridiumPreconditionError(IridiumError):
    """An account-state precondition the token cannot fix.

    These 4xx even with a perfectly valid token: LinkedIn not connected, terms
    not accepted (403), subscription invalid (402), account inactive. They are
    human-only fixes, so they are surfaced with `reason` set rather than being
    folded into a generic failure.
    """

    def __init__(self, message: str, *, status: int, detail: str, reason: str):
        super().__init__(message, status=status, detail=detail)
        self.reason = reason


def _precondition_reason(status: int, detail: str) -> str | None:
    """Classify a 4xx that a valid token cannot resolve. None => not a precondition."""
    text = detail.lower()
    if status == 402:
        return "subscription_invalid"
    if status == 403:
        if "linkedin" in text:
            return "linkedin_not_connected"
        if "term" in text or "tos" in text:
            return "tos_not_accepted"
        if "inactive" in text or "deactivat" in text:
            return "account_inactive"
        return "forbidden"
    return None


class IridiumClient:
    """One authenticated session against Door 2. Safe to share across threads."""

    def __init__(self, api_key: str | None = None, base_url: str = BASE_URL):
        key = api_key or os.environ.get("IRIDIUM_API_KEY", "")
        if not key:
            raise IridiumError(
                "agent=D step=init why=IRIDIUM_API_KEY is unset; "
                "source the repo .env before starting the server"
            )
        self._api_key = key
        self._access: str | None = None
        self._refresh: str | None = None
        self._lock = threading.Lock()
        self._http = httpx.Client(
            base_url=base_url,
            timeout=TIMEOUT,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )

    # -- auth ------------------------------------------------------------
    def _store(self, body: dict, step: str) -> None:
        """Take both tokens from an auth response. The refresh token is rotated
        on every use, so overwriting it here is what keeps us inside the grace
        window -- reusing a superseded one revokes every session for the user."""
        access = body.get("access_token")
        if not access:
            raise IridiumError(f"agent={AGENT} step={step} why=no access_token in response")
        self._access = access
        self._refresh = body.get("refresh_token") or self._refresh
        log.info("agent=%s step=%s ok=true rotated_refresh=%s",
                 AGENT, step, bool(body.get("refresh_token")))

    def login(self) -> str:
        """Exchange the raw API key for an access token. Returns it (never log it)."""
        r = self._http.post("/auth/login", json={"api_key": self._api_key})
        if r.status_code != 200:
            raise IridiumError(
                f"agent={AGENT} step=login why=login failed with {r.status_code}; "
                "the key is sent raw -- an irid_ prefix is rejected",
                status=r.status_code,
                detail=_detail(r),
            )
        self._store(r.json(), "login")
        return self._access  # type: ignore[return-value]

    def _reauth(self) -> None:
        """Refresh the access token, falling back to a full login.

        Held under the lock so two concurrent 401s cannot both spend the same
        rotated refresh token and trip the reuse revocation.
        """
        if self._refresh:
            r = self._http.post("/auth/refresh", json={"refresh_token": self._refresh})
            if r.status_code == 200:
                self._store(r.json(), "refresh")
                return
            log.warning(
                "agent=%s step=refresh why=refresh rejected with %s, falling back to login",
                AGENT, r.status_code,
            )
            self._refresh = None
        self.login()

    def _token(self) -> str:
        if self._access is None:
            self.login()
        return self._access  # type: ignore[return-value]

    # -- requests --------------------------------------------------------
    def request(self, method: str, path: str, json_body: dict | None = None) -> dict:
        """One authenticated call, retried exactly once after a 401 refresh."""
        with self._lock:
            token = self._token()
        for attempt in (1, 2):
            r = self._http.request(
                method, path, json=json_body,
                headers={"Authorization": f"Bearer {token}"},
            )
            if r.status_code == 401 and attempt == 1:
                log.info("agent=%s step=auth why=401 on %s, refreshing", AGENT, path)
                with self._lock:
                    self._reauth()
                    token = self._token()
                continue
            return _decode(r, path)
        raise IridiumError(f"agent={AGENT} step=request why=unreachable retry state for {path}")

    def call_tool(self, name: str, payload: dict | None = None) -> dict:
        """Invoke one Iridium MCP tool. `payload` None means a no-argument GET tool.

        Read-only tools only -- a mutating name is refused before it leaves the
        process, because these act on a real human's live LinkedIn account.
        """
        if name.startswith(_MUTATING_PREFIXES):
            raise IridiumError(
                f"agent={AGENT} step=call_tool why=refusing to call mutating tool "
                f"{name!r}; this fleet only reads from the user's LinkedIn account"
            )
        log.info("agent=%s step=call_tool tool=%s args=%s",
                 AGENT, name, sorted(payload) if payload else [])
        method = "GET" if payload is None else "POST"
        return self.request(method, f"/mcp/tools/{name}", payload)

    def account(self) -> dict:
        """The signed-in account: display_name, linkedin_member_id, and the
        precondition flags (tos_accepted, subscription_status, profile_complete).
        This is the seed for 'who is the current user' -- get_user_profile carries
        settings and capacity, not identity."""
        return self.request("GET", "/users/me")


def _detail(r: httpx.Response) -> str:
    """Best-effort error text. FastAPI puts it in `detail`; Cloudflare sends HTML."""
    try:
        body = r.json()
    except ValueError:
        return r.text[:300]
    if isinstance(body, dict):
        return str(body.get("detail") or body)[:300]
    return str(body)[:300]


def _decode(r: httpx.Response, path: str) -> dict:
    """Raise loudly on any non-200; never swallow, never return a half-result."""
    detail = "" if r.status_code == 200 else _detail(r)
    if r.status_code != 200:
        reason = _precondition_reason(r.status_code, detail)
        if reason:
            log.error("agent=%s step=%s why=precondition %s (%s): %s",
                      AGENT, path, reason, r.status_code, detail)
            raise IridiumPreconditionError(
                f"agent={AGENT} step={path} why={reason}: {detail}",
                status=r.status_code, detail=detail, reason=reason,
            )
        log.error("agent=%s step=%s why=http %s: %s", AGENT, path, r.status_code, detail)
        raise IridiumError(
            f"agent={AGENT} step={path} why=http {r.status_code}: {detail}",
            status=r.status_code, detail=detail,
        )
    try:
        body = r.json()
    except ValueError as exc:
        log.error("agent=%s step=%s why=non-JSON 200 response", AGENT, path)
        raise IridiumError(f"agent={AGENT} step={path} why=non-JSON response") from exc
    if not isinstance(body, dict):
        raise IridiumError(f"agent={AGENT} step={path} why=expected object, got {type(body).__name__}")
    return body


_client: IridiumClient | None = None
_client_lock = threading.Lock()


def client() -> IridiumClient:
    """Process-wide client, so one login is shared and one refresh token is tracked."""
    global _client
    with _client_lock:
        if _client is None:
            _client = IridiumClient()
        return _client
