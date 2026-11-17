"""Tiny client for the GTA2 bridge on the host (../bridge/bridge.py). Standard library only."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

BRIDGE_URL = os.environ.get("GTA2_BRIDGE_URL", "http://host.docker.internal:8765")

ACTIONS = (
    "forward",
    "forward_left",
    "forward_right",
    "reverse",
    "reverse_left",
    "reverse_right",
    "handbrake_left",
    "handbrake_right",
)


def _post(path: str, body: dict, timeout: int) -> dict:
    req = urllib.request.Request(
        f"{BRIDGE_URL}{path}", json.dumps(body).encode(), {"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.load(res)
    except urllib.error.URLError as err:
        raise RuntimeError(
            f"GTA2 bridge not reachable at {BRIDGE_URL} ({err}). Is `make bridge` running on the host?"
        ) from err


def start(seed: int) -> dict:
    """Boots GTA2, steals a car (well, spawns one) and picks the first drop-off."""
    return _post("/api/start", {"seed": seed}, timeout=420)


def drive(action: str, ms: int = 700) -> dict:
    """Runs the game for one time slice with the keys for `action` held, then pauses it again."""
    return _post("/api/act", {"action": action, "ms": ms}, timeout=60)
