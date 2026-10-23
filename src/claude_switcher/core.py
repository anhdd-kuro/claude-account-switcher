"""Business logic for account management."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from claude_switcher import keychain
from claude_switcher.config import (
    AccountInfo,
    add_account,
    get_active_account,
    load_accounts,
    remove_account,
    set_active_account,
    DEFAULT_CONFIG_PATH,
)

CLAUDE_SERVICE = keychain.CLAUDE_SERVICE
CLAUDE_STATE_FILE = Path.home() / ".claude.json"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class _LoginSnapshot:
    credentials: str | None
    keychain_account: str | None
    oauth_account: dict | None


def _check_storage(status: dict | None = None) -> None:
    default_directory = CLAUDE_STATE_FILE.with_suffix("")
    config_directory = os.environ.get("CLAUDE_CONFIG_DIR")
    if config_directory and Path(config_directory).expanduser().resolve() != default_directory.resolve():
        raise RuntimeError("Custom CLAUDE_CONFIG_DIR profiles are not supported by this switcher")
    if (default_directory / ".credentials.json").exists():
        raise RuntimeError("Claude is using file credentials; restore macOS Keychain storage before switching")
    if status:
        directory = status.get("configDirectory")
        if directory and Path(directory).expanduser().resolve() != default_directory.resolve():
            raise RuntimeError("Claude CLI and the switcher use different configuration directories")
        if status.get("authMethod") not in (None, "claude.ai", "claudeai", "oauth"):
            raise RuntimeError("Sign in to a Claude subscription before importing its profile")


def _capture_login() -> _LoginSnapshot:
    _check_storage()
    credentials = keychain.read_credentials(CLAUDE_SERVICE)
    account = keychain.read_account_attribute(CLAUDE_SERVICE) if credentials else None
    if credentials and not account:
        raise RuntimeError("Cannot preserve the current Keychain account; login was not changed")
    return _LoginSnapshot(credentials, account, _read_oauth_account())


def _restore_login(snapshot: _LoginSnapshot) -> None:
    if snapshot.credentials:
        keychain.write_credentials(CLAUDE_SERVICE, snapshot.keychain_account, snapshot.credentials)
    else:
        while keychain.delete_credentials(CLAUDE_SERVICE):
            pass
    _write_oauth_account(snapshot.oauth_account)


def _validate_email(email: str) -> str:
    """Validate email before using it in Keychain service names."""
    if not _EMAIL_RE.match(email) or len(email) > 254:
        raise RuntimeError(f"Invalid email format: {email}")
    return email


def _read_oauth_account() -> dict | None:
    """Read the oauthAccount object from ~/.claude.json."""
    try:
        data = json.loads(CLAUDE_STATE_FILE.read_text(encoding="utf-8"))
        account = data.get("oauthAccount") if isinstance(data, dict) else None
        return account if isinstance(account, dict) else None
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError("Cannot read Claude account metadata; login was not changed") from exc


def _write_oauth_account(oauth_account: dict | None) -> None:
    """Write the oauthAccount object into ~/.claude.json (merge, not overwrite)."""
    try:
        data = json.loads(CLAUDE_STATE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if oauth_account is None:
            return
        data = {}
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError("Cannot read Claude configuration; the saved profile was not changed") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Invalid Claude configuration")
    if oauth_account is None:
        data.pop("oauthAccount", None)
    else:
        data["oauthAccount"] = oauth_account
    with tempfile.NamedTemporaryFile(mode="w", dir=CLAUDE_STATE_FILE.parent, delete=False) as temp:
        temp_path = Path(temp.name)
        try:
            json.dump(data, temp)
            temp.flush()
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, CLAUDE_STATE_FILE)
        finally:
            temp_path.unlink(missing_ok=True)


_EXTRA_PATHS = [
    Path.home() / ".local" / "bin",
    Path("/usr/local/bin"),
    Path("/opt/homebrew/bin"),
]


def _find_claude() -> str | None:
    """Find the claude binary, checking common install locations beyond PATH."""
    found = shutil.which("claude")
    if found:
        return found
    for d in _EXTRA_PATHS:
        candidate = d / "claude"
        if candidate.is_file():
            return str(candidate)
    return None


def check_claude_cli() -> bool:
    """Check if the claude CLI is available."""
    return _find_claude() is not None


def _claude_cmd() -> str:
    """Return the path to the claude binary, or 'claude' as fallback."""
    return _find_claude() or "claude"


def get_auth_status() -> dict | None:
    """Run `claude auth status --json` and return parsed JSON, or None on failure."""
    result = subprocess.run(
        [_claude_cmd(), "auth", "status", "--json"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def run_auth_logout() -> None:
    """Run `claude auth logout`."""
    subprocess.run([_claude_cmd(), "auth", "logout"], capture_output=True, text=True)


def run_auth_login() -> bool:
    """Run `claude auth login`. Returns True if successful (exit code 0)."""
    result = subprocess.run([_claude_cmd(), "auth", "login"])
    return result.returncode == 0


def import_current_account(config_path: Path = DEFAULT_CONFIG_PATH) -> AccountInfo | None:
    """Import the currently logged-in Claude Code account. Returns AccountInfo or None."""
    _check_storage()
    # Retry keychain read — after login, credentials may not be written yet
    creds = None
    for _ in range(5):
        creds = keychain.read_credentials(CLAUDE_SERVICE)
        if creds:
            break
        time.sleep(1)
    if not creds:
        return None

    acct_attr = keychain.read_account_attribute(CLAUDE_SERVICE) or "unknown"

    oauth_account = _read_oauth_account()
    status = get_auth_status()
    _check_storage(status)
    if status and status.get("email"):
        email = status["email"]
        sub_type = status.get("subscriptionType", "unknown")
        org_name = status.get("orgName", "")
    else:
        try:
            email = json.loads(creds).get("email", "unknown@unknown")
        except (json.JSONDecodeError, AttributeError):
            return None
        sub_type = "unknown"
        org_name = ""

    _validate_email(email)
    metadata = oauth_account or {}
    metadata_email = metadata.get("emailAddress")
    if metadata_email and metadata_email != email:
        raise RuntimeError("Claude login and account metadata disagree; sign in again before importing")
    status_org = status.get("orgId") if status else None
    metadata_org = metadata.get("organizationUuid")
    if status_org and metadata_org and status_org != metadata_org:
        raise RuntimeError("Claude login and organization metadata disagree; sign in again before importing")
    organization_id = status_org or metadata_org or ""
    if organization_id:
        try:
            UUID(organization_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise RuntimeError("Invalid Claude organization ID") from exc
        if not oauth_account:
            raise RuntimeError("Claude organization metadata is missing; sign in again before importing")
        oauth_account = dict(oauth_account, organizationUuid=organization_id, emailAddress=email)
    elif any(a.provider == "claude" and a.email == email and a.organization_id for a in load_accounts(config_path)):
        raise RuntimeError("Cannot identify the Claude organization; sign in again before importing")

    account = AccountInfo(
        email=email,
        subscription_type=sub_type,
        org_name=org_name,
        active=True,
        keychain_account=acct_attr,
        oauth_account=oauth_account,
        provider="claude",
        organization_id=organization_id,
    )
    account.keychain_service = f"claude-switcher:{account.profile_id}"
    keychain.write_credentials(account.credential_service, acct_attr, creds)
    add_account(account, config_path)
    set_active_account(account.profile_id, config_path, provider="claude")
    return account


def _saved_profile(profile_id: str, config_path: Path) -> AccountInfo:
    account = next(
        (a for a in load_accounts(config_path) if a.provider == "claude" and a.profile_id == profile_id),
        None,
    )
    if account is None:
        raise RuntimeError("Claude profile not found; select a saved organization profile")
    _validate_email(account.email)
    metadata = account.oauth_account or {}
    if metadata.get("emailAddress", account.email) != account.email or metadata.get("organizationUuid", "") != account.organization_id:
        raise RuntimeError("Saved profile and login metadata disagree; import this profile again")
    if any(a.provider == "claude" and a.profile_id != profile_id and a.credential_service == account.credential_service
           for a in load_accounts(config_path)):
        raise RuntimeError("Saved profiles share a legacy backup; import each profile again before switching")
    return account


def _backup_active_account(config_path: Path, snapshot: _LoginSnapshot) -> AccountInfo | None:
    active = get_active_account(config_path)
    if active:
        _saved_profile(active.profile_id, config_path)
        current_creds = snapshot.credentials
        current_oauth = snapshot.oauth_account
        if current_creds and current_oauth:
            email = current_oauth.get("emailAddress")
            org = current_oauth.get("organizationUuid") or ""
            if (email and email != active.email) or org != active.organization_id:
                imported = import_current_account(config_path)
                if imported is None:
                    raise RuntimeError("Cannot preserve the current Claude login")
                return imported
        if current_creds and (not current_oauth or not current_oauth.get("emailAddress")):
            raise RuntimeError("Cannot verify the current Claude organization; import its profile first")
        if current_creds:
            keychain.write_credentials(
                active.credential_service, active.keychain_account, current_creds
            )
        if current_oauth:
            active.oauth_account = current_oauth
            add_account(active, config_path)
    return active


def switch_account(target_profile_id: str, config_path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Switch to a saved organization profile, preserving the current login."""
    target = _saved_profile(target_profile_id, config_path)
    if not target.oauth_account:
        raise RuntimeError("Saved login metadata not found. Add this Claude profile again before switching.")
    snapshot = _capture_login()
    _backup_active_account(config_path, snapshot)
    target_account = _saved_profile(target_profile_id, config_path)
    target_creds = keychain.read_credentials(target_account.credential_service)
    if not target_creds:
        raise RuntimeError("Saved Claude credentials not found in Keychain")
    try:
        keychain.write_credentials(CLAUDE_SERVICE, target_account.keychain_account, target_creds)
        if target_account.oauth_account:
            _write_oauth_account(target_account.oauth_account)
        set_active_account(target_profile_id, config_path, provider="claude")
    except Exception:
        _restore_login(snapshot)
        raise


def add_new_account(config_path: Path = DEFAULT_CONFIG_PATH) -> AccountInfo | None:
    """Add a new account via claude auth login. Returns AccountInfo or None if cancelled."""
    snapshot = _capture_login()
    _backup_active_account(config_path, snapshot)
    try:
        while keychain.delete_credentials(CLAUDE_SERVICE):
            pass
        if run_auth_login():
            imported = import_current_account(config_path)
            if imported:
                return imported
    except Exception:
        _restore_login(snapshot)
        raise
    _restore_login(snapshot)
    return None


def remove_saved_account(profile_id: str, config_path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Remove an inactive organization profile and its credential backup."""
    account = _saved_profile(profile_id, config_path)
    if account.active:
        raise RuntimeError("Cannot remove the active Claude profile; switch first")
    keychain.delete_credentials(account.credential_service)
    remove_account(profile_id, config_path)
