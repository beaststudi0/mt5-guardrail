"""Client configuration, including the WSL networking quirk.

WSL2 in mirrored mode reaches the Windows host on `localhost`. In the older NAT
mode it does not, and the real host IP must be discovered another way.

Two sources are tried, in order:

1. `ip route show default` - the default gateway, which in WSL2 NAT mode *is*
   the Windows host's vEthernet adapter. This is the reliable source.
2. `/etc/resolv.conf` - historically doubled as the host IP, but on any WSL
   image with systemd-resolved enabled it instead contains the local stub
   resolver `127.0.0.53`, which is *not* the Windows host and must be
   filtered out rather than trusted blindly.
"""

from __future__ import annotations

import ipaddress
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PORT = 8787
_RESOLV_CONF = Path("/etc/resolv.conf")
_NAMESERVER = re.compile(r"^nameserver\s+(\S+)", re.MULTILINE)


def _is_useful_host_candidate(ip: str) -> bool:
    """Reject loopback/link-local addresses that are never the Windows host."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (addr.is_loopback or addr.is_link_local or addr.is_unspecified)


def _default_gateway() -> str | None:
    """The most reliable source in WSL2 NAT mode."""
    try:
        output = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None

    match = re.search(r"default via (\S+)", output)
    ip = match.group(1) if match else None
    return ip if ip and _is_useful_host_candidate(ip) else None


def _resolv_conf_nameserver() -> str | None:
    """Legacy fallback. Filtered because systemd-resolved sets 127.0.0.53 here,
    which is a local stub resolver, not the Windows host."""
    try:
        match = _NAMESERVER.search(_RESOLV_CONF.read_text(encoding="utf-8"))
    except OSError:
        return None
    ip = match.group(1) if match else None
    return ip if ip and _is_useful_host_candidate(ip) else None


def windows_host_ip() -> str | None:
    """Best-effort Windows host IP as seen from WSL2, or None if undetectable."""
    return _default_gateway() or _resolv_conf_nameserver()


def candidate_urls(port: int = DEFAULT_PORT) -> list[str]:
    """Ordered by likelihood, cheapest first."""
    urls = [f"http://localhost:{port}", f"http://127.0.0.1:{port}"]
    host_ip = windows_host_ip()
    if host_ip:
        urls.append(f"http://{host_ip}:{port}")
    return urls


@dataclass(frozen=True, slots=True)
class ClientConfig:
    base_url: str
    api_key: str
    timeout: float = 10.0
    max_retries: int = 3
    backoff_factor: float = 0.3

    @classmethod
    def from_env(cls) -> ClientConfig:
        api_key = os.environ.get("MT5_BRIDGE_API_KEY")
        if not api_key:
            raise RuntimeError(
                "MT5_BRIDGE_API_KEY is not set. It must match the key in the "
                "bridge server's .env on the Windows side."
            )
        raw_timeout = os.environ.get("MT5_BRIDGE_TIMEOUT", "10")
        try:
            timeout = float(raw_timeout)
        except ValueError as exc:
            raise RuntimeError(
                f"MT5_BRIDGE_TIMEOUT must be a number of seconds, got {raw_timeout!r}."
            ) from exc
        return cls(
            base_url=os.environ.get("MT5_BRIDGE_URL", f"http://localhost:{DEFAULT_PORT}"),
            api_key=api_key,
            timeout=timeout,
        )
