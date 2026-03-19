from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

COMMAND_TIMEOUT = 15
API_BASE = "http://127.0.0.1:9993"
TOKEN_PATHS = [
    "/var/lib/zerotier-one/authtoken.secret",
]
POLKIT_HELPER = "/usr/lib/zerotier-gui/zerotier-gui-helper"


# --- Enums ---

class ServiceState(Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    UNKNOWN = "unknown"



class NetworkStatus(Enum):
    OK = "OK"
    ACCESS_DENIED = "ACCESS_DENIED"
    NOT_FOUND = "NOT_FOUND"
    PORT_ERROR = "PORT_ERROR"
    REQUESTING_CONFIGURATION = "REQUESTING_CONFIGURATION"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def from_str(cls, s: str) -> NetworkStatus:
        try:
            return cls(s)
        except ValueError:
            return cls.UNKNOWN


# --- Dataclasses ---

@dataclass
class ZtStatus:
    address: str
    version: str
    online: bool
    service_state: ServiceState


@dataclass
class ZtPeerPath:
    address: str       # "203.0.113.5/9993"
    active: bool
    expired: bool
    preferred: bool
    last_send: int     # epoch ms
    last_receive: int  # epoch ms


@dataclass
class ZtPeer:
    address: str       # 10-digit hex
    version: str       # "" if unknown ("-1.-1.-1" → "")
    latency: int       # ms, -1 if unknown
    role: str          # LEAF, UPSTREAM, ROOT, PLANET
    paths: List[ZtPeerPath] = field(default_factory=list)


@dataclass
class ZtNetwork:
    id: str
    name: str
    status: NetworkStatus
    assigned_addresses: List[str] = field(default_factory=list)
    type: str = ""
    bridge: bool = False
    mac: str = ""
    mtu: int = 0
    device: str = ""
    routes: List[dict] = field(default_factory=list)
    broadcast_enabled: bool = False
    netconf_revision: int = 0
    allow_managed: bool = True
    allow_global: bool = False
    allow_default: bool = False
    allow_dns: bool = False


# --- Exceptions ---

class ZtCommandError(Exception):
    """Generic CLI error."""


class ZtAuthDismissed(ZtCommandError):
    """User cancelled the pkexec dialog (exit code 126)."""


class ZtAuthFailed(ZtCommandError):
    """Polkit authorization failed (exit code 127)."""


class ZtServiceDown(ZtCommandError):
    """ZeroTier service is not running."""


# --- Client ---

class ZeroTierClient:

    def __init__(self):
        self._token: Optional[str] = None

    # --- Auth token (one-time pkexec) ---

    @staticmethod
    def _has_polkit_helper() -> bool:
        return os.path.isfile(POLKIT_HELPER) and os.access(POLKIT_HELPER, os.X_OK)

    def _ensure_token(self) -> None:
        if self._token:
            return

        # Try reading without root first (works if user is in zerotier group)
        for path in TOKEN_PATHS:
            try:
                with open(path) as f:
                    self._token = f.read().strip()
                    return
            except (PermissionError, FileNotFoundError):
                continue

        # Need pkexec — one-time password prompt
        if self._has_polkit_helper():
            # Installed mode: use dedicated helper (nice Polkit message)
            result = self._run_subprocess(
                ["pkexec", POLKIT_HELPER, "read-token"]
            )
            token = result.strip()
            if token:
                self._token = token
                return
        else:
            # Dev mode fallback: generic pkexec cat
            for path in TOKEN_PATHS:
                result = self._run_subprocess(["pkexec", "cat", path])
                token = result.strip()
                if token:
                    self._token = token
                    return

        raise ZtCommandError(
            "Could not find ZeroTier auth token in any known location"
        )

    def clear_token(self) -> None:
        """Clear cached token (e.g. after service restart)."""
        self._token = None

    # --- Subprocess helper (for pkexec commands only) ---

    @staticmethod
    def _run_subprocess(args: list[str]) -> str:
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            raise ZtCommandError(f"Command timed out: {' '.join(args)}") from exc
        except FileNotFoundError as exc:
            raise ZtCommandError(f"Command not found: {args[0]}") from exc

        if result.returncode == 126:
            raise ZtAuthDismissed("Authentication dialog dismissed by user")
        if result.returncode == 127:
            raise ZtAuthFailed("Polkit authorization failed")
        if result.returncode != 0:
            stderr = result.stderr.strip()
            raise ZtCommandError(
                f"Command failed (rc={result.returncode}): {stderr or result.stdout.strip()}"
            )
        return result.stdout

    # --- Local HTTP API (no root needed after token read) ---

    def _api(self, method: str, path: str, body: Optional[bytes] = None) -> dict | list:
        self._ensure_token()
        url = f"{API_BASE}{path}"
        req = urllib.request.Request(
            url,
            method=method,
            data=body,
            headers={"X-ZT1-Auth": self._token},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except urllib.error.URLError as exc:
            raise ZtCommandError(f"ZeroTier API unreachable: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ZtCommandError(f"Invalid API response: {exc}") from exc

    # --- Service state (no root needed) ---

    def get_service_state(self) -> ServiceState:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "zerotier-one"],
                capture_output=True, text=True, timeout=COMMAND_TIMEOUT,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return ServiceState.UNKNOWN
        state = result.stdout.strip().lower()
        if state == "active":
            return ServiceState.ACTIVE
        if state == "inactive":
            return ServiceState.INACTIVE
        return ServiceState.UNKNOWN

    # --- ZeroTier status & networks (via local API) ---

    def get_status(self) -> ZtStatus:
        data = self._api("GET", "/status")
        service_state = self.get_service_state()
        return ZtStatus(
            address=data.get("address", ""),
            version=data.get("version", ""),
            online=data.get("online", False),
            service_state=service_state,
        )

    def list_networks(self) -> list[ZtNetwork]:
        data = self._api("GET", "/network")
        networks = []
        for net in data:
            networks.append(ZtNetwork(
                id=net.get("id", net.get("nwid", "")),
                name=net.get("name", ""),
                status=NetworkStatus.from_str(net.get("status", "UNKNOWN")),
                assigned_addresses=net.get("assignedAddresses", []),
                type=net.get("type", ""),
                bridge=net.get("bridge", False),
                mac=net.get("mac", ""),
                mtu=net.get("mtu", 0),
                device=net.get("portDeviceName", ""),
                routes=net.get("routes", []),
                broadcast_enabled=net.get("broadcastEnabled", False),
                netconf_revision=net.get("netconfRevision", 0),
                allow_managed=net.get("allowManaged", True),
                allow_global=net.get("allowGlobal", False),
                allow_default=net.get("allowDefault", False),
                allow_dns=net.get("allowDNS", False),
            ))
        return networks

    def join_network(self, network_id: str) -> None:
        self._api("POST", f"/network/{network_id}", b"{}")

    def leave_network(self, network_id: str) -> None:
        self._api("DELETE", f"/network/{network_id}")

    def list_peers(self) -> list[ZtPeer]:
        data = self._api("GET", "/peer")
        peers = []
        for p in data:
            version = p.get("version", "")
            if version == "-1.-1.-1":
                version = ""
            paths = []
            for pp in p.get("paths", []):
                paths.append(ZtPeerPath(
                    address=pp.get("address", ""),
                    active=pp.get("active", False),
                    expired=pp.get("expired", False),
                    preferred=pp.get("preferred", False),
                    last_send=pp.get("lastSend", 0),
                    last_receive=pp.get("lastReceive", 0),
                ))
            peers.append(ZtPeer(
                address=p.get("address", ""),
                version=version,
                latency=p.get("latency", -1),
                role=p.get("role", ""),
                paths=paths,
            ))
        return peers

    def set_network_settings(
        self,
        network_id: str,
        allow_managed: bool,
        allow_global: bool,
        allow_default: bool,
        allow_dns: bool,
    ) -> None:
        body = json.dumps({
            "allowManaged": allow_managed,
            "allowGlobal": allow_global,
            "allowDefault": allow_default,
            "allowDNS": allow_dns,
        }).encode()
        self._api("POST", f"/network/{network_id}", body)

    # --- Service autostart ---

    def get_service_startup(self) -> bool:
        """Return True if ZeroTier service is enabled at boot."""
        try:
            result = subprocess.run(
                ["systemctl", "is-enabled", "zerotier-one"],
                capture_output=True, text=True, timeout=COMMAND_TIMEOUT,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False
        return result.stdout.strip().lower() == "enabled"

    def set_service_autostart(self, enabled: bool) -> None:
        """Enable or disable ZeroTier service at boot."""
        if enabled:
            self._run_service_command(
                ["systemctl", "enable", "zerotier-one"],
                helper_action=None,
            )
        else:
            self._run_service_command(
                ["systemctl", "disable", "zerotier-one"],
                helper_action=None,
            )

    # --- Service management (still needs pkexec) ---

    def start_service(self) -> None:
        if self._has_polkit_helper():
            self._run_subprocess(["pkexec", POLKIT_HELPER, "start-service"])
        else:
            self._run_subprocess(["pkexec", "systemctl", "start", "zerotier-one"])

    def stop_service(self) -> None:
        if self._has_polkit_helper():
            self._run_subprocess(["pkexec", POLKIT_HELPER, "stop-service"])
        else:
            self._run_subprocess(["pkexec", "systemctl", "stop", "zerotier-one"])

    def _run_service_command(
        self, args: list[str], helper_action: str | None = None
    ) -> None:
        """Try without pkexec first; escalate only if needed."""
        try:
            result = subprocess.run(
                args, capture_output=True, text=True, timeout=COMMAND_TIMEOUT,
            )
            if result.returncode == 0:
                return
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        if helper_action and self._has_polkit_helper():
            self._run_subprocess(["pkexec", POLKIT_HELPER, helper_action])
        else:
            self._run_subprocess(["pkexec", *args])
