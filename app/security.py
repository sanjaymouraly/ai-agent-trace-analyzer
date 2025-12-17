"""Authentication, workspace isolation and rate limiting.

Local mode (``APP_AUTH_TOKEN`` unset) is a single-user deployment: every
request maps to the default workspace. Authenticated mode accepts either the
deployment token (which maps to the default workspace) or a workspace-scoped
API key created through the admin API.
"""

from __future__ import annotations

import hmac
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from app.config import settings


@dataclass(frozen=True)
class Principal:
    """The authenticated caller and the workspace it may read and write."""

    workspace_id: str
    actor: str
    authenticated: bool
    scope: str  # "token" | "api_key" | "local"

    @property
    def is_admin(self) -> bool:
        return self.scope in {"token", "local"}


class RateLimiter:
    """Fixed-window-per-minute limiter keyed by client address."""

    def __init__(self, limit: int = 240):
        self.limit = max(1, limit)
        self._buckets: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> tuple[bool, int]:
        """Return ``(allowed, remaining)`` and record the call."""
        now = time.monotonic()
        bucket = self._buckets[key]
        while bucket and now - bucket[0] > 60:
            bucket.popleft()
        if len(bucket) >= self.limit:
            return False, 0
        bucket.append(now)
        return True, max(0, self.limit - len(bucket))

    def reset(self) -> None:
        self._buckets.clear()


def _constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def resolve_principal(authorization: str | None, api_key_lookup: Any = None) -> Principal | None:
    """Resolve a bearer token into a principal.

    ``api_key_lookup`` is a callable that receives the raw key and returns the
    workspace id of a matching, enabled API key, or ``None``.
    """
    token = ""
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value:
            token = value.strip()
    if not token:
        if settings.auth_token:
            return None
        return Principal(settings.default_workspace, "local-user", False, "local")
    if settings.auth_token and _constant_time_equal(token, settings.auth_token):
        return Principal(settings.default_workspace, "deployment-token", True, "token")
    if api_key_lookup is not None:
        workspace_id = api_key_lookup(token)
        if workspace_id:
            return Principal(workspace_id, "api-key", True, "api_key")
    return None
