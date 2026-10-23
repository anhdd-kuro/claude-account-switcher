"""Fetch Claude API usage stats via the OAuth usage endpoint."""

import json
import math
import urllib.request
import urllib.error
from datetime import datetime, timezone

from claude_switcher import keychain
from claude_switcher.config import AccountInfo
from claude_switcher.usage_state import UsageState, UsageWindow

USAGE_URL = "https://api.anthropic.com/oauth/usage"


def _extract_token(creds_json: str) -> str | None:
    """Extract the OAuth access token from a credentials JSON blob."""
    try:
        data = json.loads(creds_json)
        return data.get("claudeAiOauth", {}).get("accessToken")
    except (json.JSONDecodeError, AttributeError):
        return None


def fetch_usage(service: str) -> dict | None:
    """Fetch usage for a Keychain service. Returns parsed JSON or None on failure.

    Response format:
        {
            "five_hour": {"utilization": 42.5, "resets_at": "..."},
            "seven_day": {"utilization": 18.3, "resets_at": "..."}
        }
    """
    creds = keychain.read_credentials(service)
    if not creds:
        return None

    token = _extract_token(creds)
    if not token:
        return None

    req = urllib.request.Request(
        USAGE_URL,
        headers={
            "Accept": "application/json",
            "anthropic-beta": "oauth-2025-04-20",
            "Authorization": f"Bearer {token}",
            "User-Agent": "claude-code/2.1.11",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return None


def fetch_usage_for_account(account: AccountInfo | str) -> dict | None:
    """Fetch usage with a profile backup, accepting legacy email callers."""
    service = account.credential_service if isinstance(account, AccountInfo) else f"claude-switcher:{account}"
    return fetch_usage(service)


def fetch_active_usage() -> dict | None:
    """Fetch usage for the currently active Claude Code session."""
    return fetch_usage(keychain.CLAUDE_SERVICE)


def _format_reset_delta(resets_at: str) -> str:
    """Convert an ISO 8601 resets_at timestamp to a human-readable relative time."""
    try:
        # Strip fractional seconds for simpler parsing
        cleaned = resets_at.replace("Z", "+00:00")
        reset_dt = datetime.fromisoformat(cleaned)
        now = datetime.now(timezone.utc)
        diff = int((reset_dt - now).total_seconds())

        if diff <= 0:
            return "now"

        days = diff // 86400
        hours = (diff % 86400) // 3600
        minutes = (diff % 3600) // 60

        if days > 0:
            return f"{days}d {hours}h"
        elif hours > 0:
            return f"{hours}h {minutes}m"
        else:
            return f"{minutes}m"
    except (ValueError, TypeError):
        return "?"


def claude_usage_state(usage: dict | None) -> UsageState:
    """Convert Claude usage data into a normalized usage state."""
    if not usage:
        return UsageState(available=False, display="Usage indisponible")

    parts = []
    windows = []
    five_h = usage.get("five_hour", {})
    seven_d = usage.get("seven_day", {})

    limits = [("5h", five_h), ("7j", seven_d)]
    limits.extend(
        (key.removeprefix("seven_day_").replace("_", " ").title(), window)
        for key, window in usage.items()
        if key.startswith("seven_day_")
    )
    for label, window in limits:
        if not isinstance(window, dict) or "utilization" not in window:
            continue
        try:
            percent = float(window["utilization"])
        except (TypeError, ValueError):
            continue

        if not math.isfinite(percent):
            continue

        reset = _format_reset_delta(window["resets_at"]) if "resets_at" in window else None
        reset_suffix = f" ({reset})" if reset else ""
        parts.append(f"{label} {percent:.0f}%{reset_suffix}")
        windows.append(UsageWindow(label=label, percent=percent, resets_in=reset))

    if not parts:
        return UsageState(available=False, display="Usage indisponible")

    return UsageState(available=True, display=" | ".join(parts), windows=tuple(windows))


def format_usage(usage: dict | None) -> str:
    """Format usage data into a readable string."""
    return claude_usage_state(usage).display
