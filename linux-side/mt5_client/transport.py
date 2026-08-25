"""HTTP plumbing: retries and error translation. No trading concepts.

Retries cover only idempotent verbs and transient statuses. A POST to
/order/execute is never retried automatically - a duplicated market order is
far worse than a failed one.

Built on stdlib `urllib.request` rather than `requests`. This client is
invoked as a fresh subprocess per call (see `mt5_cli.py`, "built for an
LLM agent's shell/exec tool"), so connection pooling -- `requests`' main
advantage over the stdlib -- provides zero benefit here: there is never a
second request in the same process to reuse a connection for. Measured
cost of the old approach: `import requests` alone (pulling in `urllib3`
and `charset_normalizer`) took ~150ms on every single CLI invocation,
before the actual HTTP round-trip to a local/WSL-adjacent bridge server
even began -- almost certainly the dominant latency cost in the entire
"agent checks a price" path. `urllib.request` needs no such dependency
chain and imports in single-digit milliseconds.
"""

from __future__ import annotations

import json as json_lib
import logging
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .config import ClientConfig
from .exceptions import (
    BridgeHTTPError,
    BridgeUnauthorized,
    BridgeUnavailable,
    ConfirmTokenRejected,
    DailyLimitReached,
    OrderRejected,
)

log = logging.getLogger(__name__)

_ERROR_BY_NAME: dict[str, type[BridgeHTTPError]] = {
    "ConfirmTokenInvalid": ConfirmTokenRejected,
    "DailyLimitReached": DailyLimitReached,
    "OrderRejected": OrderRejected,
}

#: Matches the old `Retry(status_forcelist=(502, 503, 504))` exactly.
_RETRY_STATUSES = frozenset({502, 503, 504})


class Transport:
    """No connection pool by design - see the module docstring for why."""

    def __init__(self, config: ClientConfig) -> None:
        self._config = config
        self._headers = {"x-api-key": config.api_key, "content-type": "application/json"}

    def close(self) -> None:
        """No persistent connection to close (see module docstring). Kept
        so callers that treat Transport as a context-managed resource
        (e.g. `with BridgeClient(...) as c:`) need no changes."""

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self._config.base_url.rstrip('/')}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"

        body: bytes | None = None
        headers = dict(self._headers)
        if json is not None:
            body = json_lib.dumps(json).encode("utf-8")

        # Matches urllib3's Retry(total=N): N retries AFTER the first
        # attempt, so max_retries=3 means up to 4 total attempts here too
        # -- not 3, which would silently under-retry relative to the
        # behavior this replaces.
        max_attempts = self._config.max_retries + 1 if method == "GET" else 1

        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            request = urllib.request.Request(url, data=body, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=self._config.timeout) as response:
                    return json_lib.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code in _RETRY_STATUSES and attempt < max_attempts:
                    self._backoff(attempt)
                    continue
                raise self._to_exception(exc) from exc
            except (TimeoutError, socket.timeout) as exc:
                raise BridgeUnavailable(
                    f"bridge timed out after {self._config.timeout}s"
                ) from exc
            except urllib.error.URLError as exc:
                if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                    raise BridgeUnavailable(
                        f"bridge timed out after {self._config.timeout}s"
                    ) from exc
                last_error = exc
                if attempt < max_attempts:
                    self._backoff(attempt)
                    continue
                raise BridgeUnavailable(
                    f"cannot reach the bridge at {url}. Is the Windows-side server "
                    f"running, and is MT5_BRIDGE_URL correct for your WSL network mode?"
                ) from exc
            except OSError as exc:
                # Anything else at the socket/connection level (refused,
                # reset, DNS failure, ...) - callers should only ever see
                # bridge exceptions, matching the old
                # requests.exceptions.RequestException catch-all.
                last_error = exc
                if attempt < max_attempts:
                    self._backoff(attempt)
                    continue
                raise BridgeUnavailable(f"request to {url} failed: {exc}") from exc

        # Unreachable: the loop above always returns or raises. Kept only
        # so a type checker sees every path as terminal.
        raise BridgeUnavailable(  # pragma: no cover
            f"request to {url} failed after {max_attempts} attempts"
        ) from last_error

    def _backoff(self, attempt: int) -> None:
        """Same formula urllib3's Retry uses: backoff_factor * 2**(n-1),
        where n is which retry this is (1 = first retry, i.e. the attempt
        right after the initial one that just failed)."""
        time.sleep(self._config.backoff_factor * (2 ** (attempt - 1)))

    @staticmethod
    def _to_exception(exc: urllib.error.HTTPError) -> BridgeHTTPError:
        raw = exc.read()  # HTTPError's body can only be read once - do it here, only here.
        try:
            payload = json_lib.loads(raw) if raw else {}
        except json_lib.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):  # a JSON body is not necessarily an object
            payload = {}

        detail = payload.get("detail") or (
            raw[:200].decode("utf-8", errors="replace") if raw else ""
        )
        if exc.code == 401:
            return BridgeUnauthorized(401, detail, payload)

        error_cls = _ERROR_BY_NAME.get(str(payload.get("error")), BridgeHTTPError)
        return error_cls(exc.code, detail, payload)

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, json: dict[str, Any]) -> Any:
        return self.request("POST", path, json=json)
