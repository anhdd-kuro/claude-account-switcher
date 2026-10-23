import importlib.util
import sys
import threading
from threading import Thread as NativeThread
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest

from claude_switcher.config import AccountInfo, AppSettings
from claude_switcher.usage_state import UsageState, UsageWindow


class MenuItem(list):
    def __init__(self, title="", callback=None):
        super().__init__()
        self.title = title
        self.callback = callback
        self.state = 0
        self._menuitem = Mock()

    def add(self, item):
        self.append(item)

    def set_callback(self, callback):
        self.callback = callback


@pytest.fixture
def ui(monkeypatch, tmp_path):
    rumps = ModuleType("rumps")
    rumps.App = object
    rumps.MenuItem = MenuItem
    rumps.separator = None
    rumps.notification = Mock()
    rumps.alert = Mock()
    rumps.quit_application = Mock()
    foundation = __import__("Foundation")
    monkeypatch.setattr(foundation, "NSOperationQueue", Mock())
    monkeypatch.setitem(sys.modules, "rumps", rumps)
    monkeypatch.setitem(sys.modules, "Foundation", foundation)
    spec = importlib.util.spec_from_file_location(
        "profile_test_app", Path(__file__).parents[1] / "src/claude_switcher/app.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "style_provider_header", Mock())
    monkeypatch.setattr(module, "style_usage_item", Mock())
    app = module.ClaudeSwitcherApp.__new__(module.ClaudeSwitcherApp)
    app.config_path = tmp_path / "accounts.json"
    app.menu = MenuItem()
    app.title = ""
    app._usage_cache = {}
    app._usage_state_cache = {}
    app._usage_items = {}
    app._switch_in_progress = set()
    app._account_operation_lock = threading.Lock()
    app._last_auto_switch_attempt = {}
    app._refresh_in_progress = False
    app._fetch_all_usage = Mock()
    personal = AccountInfo("same@test.com", "pro", "Personal", True, "user",
                           organization_id="personal",
                           keychain_service="claude-switcher:same@test.com:personal")
    team = AccountInfo("same@test.com", "team", "Work", False, "user",
                       organization_id="team",
                       keychain_service="claude-switcher:same@test.com:team")
    accounts = [personal, team]
    monkeypatch.setattr(module, "load_accounts", lambda path: accounts)
    monkeypatch.setattr(module, "get_active_account", lambda path, provider="claude": next(
        (a for a in accounts if a.provider == provider and a.active), None,
    ))
    monkeypatch.setattr(module, "load_settings", lambda path: AppSettings(
        auto_switch={"claude": True, "codex": True},
    ))
    monkeypatch.setattr(module.keychain, "read_credentials", Mock(return_value="credentials"))
    monkeypatch.setattr(module, "set_auto_switch_enabled", Mock())
    monkeypatch.setattr(module, "switch_account", Mock())
    monkeypatch.setattr(module, "switch_codex_account", Mock())
    monkeypatch.setattr(module, "remove_saved_account", Mock())
    monkeypatch.setattr(module, "add_new_account", Mock(return_value=team))
    monkeypatch.setattr(module, "add_new_codex_account", Mock(return_value=None))
    monkeypatch.setattr(module, "import_current_account", Mock(return_value=team))
    monkeypatch.setattr(module, "check_claude_cli", lambda: True)
    monkeypatch.setattr(module, "check_codex_cli", lambda: True)
    monkeypatch.setattr(module, "_on_main_thread", lambda fn: fn())
    threads = []
    monkeypatch.setattr(module.threading, "Thread", lambda target, daemon: Mock(
        start=lambda: threads.append(target),
    ))
    return module, app, personal, team, threads


def test_menu_preserves_same_email_profiles_and_callback_identity(ui):
    module, app, personal, team, threads = ui
    app._rebuild_menu()
    switches = [item for item in app.menu if getattr(item, "callback", None) == app._on_claude_account_click]
    assert len(switches) == 2
    assert "Personal" in switches[0].title and "pro" in switches[0].title.lower()
    assert "Work" in switches[1].title and "team" in switches[1].title.lower()
    assert all("same@test.com" not in item.title for item in switches)
    assert all(item._menuitem.setToolTip_.call_args.args[0] == "same@test.com" for item in switches)
    switches[1].callback(switches[1])
    assert app._switch_in_progress == {"claude"}
    threads.pop()()
    module.switch_account.assert_called_once_with(team.profile_id, app.config_path)
    assert app._switch_in_progress == set()
    assert "Work" in module.rumps.notification.call_args.kwargs["message"]
    assert set(app._usage_items) == {("claude", personal.profile_id), ("claude", team.profile_id)}


def test_removal_uses_profile_identity_and_leaves_active_same_email_profile(ui):
    module, app, personal, team, threads = ui
    app._rebuild_menu()
    remove_menu = next(item for item in app.menu if item and "Remove account" in item.title)
    assert len(remove_menu) == 2
    assert "Work" in remove_menu[1].title and "team" in remove_menu[1].title.lower()
    remove_menu[1].callback(remove_menu[1])
    module.remove_saved_account.assert_called_once_with(team.profile_id, app.config_path)
    assert "Work" in module.rumps.notification.call_args.kwargs["message"]
    remove_menu[0].callback(remove_menu[0])
    assert module.remove_saved_account.call_count == 1
    module.rumps.alert.assert_called_once()


def test_profile_credential_check_uses_explicit_service(ui):
    module, app, personal, team, threads = ui
    assert app._has_credentials(team)
    module.keychain.read_credentials.assert_called_once_with(team.credential_service)


def test_inactive_same_email_team_fetches_saved_usage(ui, monkeypatch):
    module, app, personal, team, threads = ui
    active_fetch = Mock(return_value={"five_hour": {"utilization": 99}})
    saved_fetch = Mock(return_value={"five_hour": {"utilization": 10}})
    monkeypatch.setattr(module, "fetch_active_usage", active_fetch)
    monkeypatch.setattr(module, "fetch_usage_for_account", saved_fetch)
    assert app._fetch_usage_state(team, personal).max_percent == 10
    saved_fetch.assert_called_once_with(team)
    active_fetch.assert_not_called()
    assert app._fetch_usage_state(personal, personal).max_percent == 99
    active_fetch.assert_called_once_with()


def test_auto_switch_uses_profile_identity_and_releases_provider_lock(ui):
    module, app, personal, team, threads = ui
    app._usage_state_cache = {
        ("claude", personal.profile_id): UsageState(True, "100%", (UsageWindow("5h", 100),)),
        ("claude", team.profile_id): UsageState(True, "10%", (UsageWindow("5h", 10),)),
    }
    module.switch_account.side_effect = lambda *args: (
        app._switch_in_progress == {"claude"} or pytest.fail("provider was not locked")
    )
    result = app._attempt_auto_switch("claude")
    module.switch_account.assert_called_once_with(team.profile_id, app.config_path)
    assert result["status"] == "switched"
    app._notify_auto_switch_result(result)
    assert "Work" in module.rumps.notification.call_args.kwargs["message"]
    assert app._switch_in_progress == set()


@pytest.mark.parametrize("operation", ["_on_add_claude_account", "_on_import_claude_profile"])
def test_claude_login_and_import_block_switching_and_auto_switch(ui, operation):
    module, app, personal, team, threads = ui
    app._usage_state_cache[("claude", personal.profile_id)] = UsageState(
        True, "100%", (UsageWindow("5h", 100),),
    )
    getattr(app, operation)(None)
    assert app._switch_in_progress == {"claude"}
    getattr(app, operation)(None)
    assert len(threads) == 1
    app._switch_account("claude", team.profile_id)
    assert len(threads) == 1
    assert app._attempt_auto_switch("claude") is None
    assert "claude" not in app._last_auto_switch_attempt
    module.switch_account.assert_not_called()
    threads.pop()()
    assert app._switch_in_progress == set()


def test_import_current_profile_has_explicit_menu_entry_without_login(ui):
    module, app, personal, team, threads = ui
    app._rebuild_menu()
    item = next(item for item in app.menu if item is not None and item.title == "Import current Claude profile")
    item.callback(item)
    threads.pop()()
    module.import_current_account.assert_called_once_with(app.config_path)
    module.add_new_account.assert_not_called()
    assert "Work" in module.rumps.notification.call_args.kwargs["message"]


@pytest.mark.parametrize("operation, provider, backend", [
    ("_on_add_claude_account", "claude", "add_new_account"),
    ("_on_import_claude_profile", "claude", "import_current_account"),
    ("_on_add_codex_account", "codex", "add_new_codex_account"),
])
def test_login_and_import_release_lock_after_failure(ui, operation, provider, backend):
    module, app, personal, team, threads = ui
    getattr(module, backend).side_effect = RuntimeError("failed")
    getattr(app, operation)(None)
    assert app._switch_in_progress == {provider}
    threads.pop()()
    assert app._switch_in_progress == set()
    assert module.rumps.notification.call_args.kwargs["subtitle"] == "Error"


def test_codex_login_blocks_opposite_provider_account_operations(ui):
    module, app, personal, team, threads = ui
    app._on_add_codex_account(None)
    app._on_add_codex_account(None)
    app._switch_account("codex", "other@test.com")
    assert len(threads) == 1
    app._on_add_claude_account(None)
    app._switch_account("claude", team.profile_id)
    assert app._attempt_auto_switch("claude") is None
    assert len(threads) == 1
    assert app._switch_in_progress == {"codex"}
    module.switch_account.assert_not_called()


def test_auto_switch_releases_provider_lock_after_failure(ui):
    module, app, personal, team, threads = ui
    app._usage_state_cache[("claude", personal.profile_id)] = UsageState(
        True, "100%", (UsageWindow("5h", 100),),
    )
    module.switch_account.side_effect = RuntimeError("failed")
    result = app._attempt_auto_switch("claude")
    assert result["status"] == "error"
    assert app._switch_in_progress == set()


def test_status_bar_summary_uses_active_profiles_and_remaining_usage(ui):
    module, app, personal, team, threads = ui
    from claude_switcher.native_menu import status_summary

    codex = AccountInfo("codex@test.com", "business", "", True, "user", provider="codex")
    states = {
        ("claude", personal.profile_id): UsageState(True, "25%", (UsageWindow("5h", 25, "4h 14m"),)),
        ("claude", team.profile_id): UsageState(True, "100%", (UsageWindow("5h", 100, "1h"),)),
        ("codex", codex.profile_id): UsageState(True, "74%", (UsageWindow("7d", 74, "6d 14h"),)),
    }
    assert status_summary([personal, team, codex], states) == "75% · 4h | 26% · 6d"
    assert status_summary([team], states) == ""
    app._usage_state_cache = states
    app._update_usage_labels()
    assert app.title == "75% · 4h"


def test_native_usage_view_shows_separate_remaining_limits_and_accessibility():
    from AppKit import NSMenuItem
    from types import SimpleNamespace
    from claude_switcher.native_menu import style_usage_item

    item = SimpleNamespace(_menuitem=NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Usage", None, ""))
    state = UsageState(True, "", (UsageWindow("5h", 25, "4h 14m"), UsageWindow("7j", 100, "1d 11h")))
    style_usage_item(item, state, active=True)
    view = item._menuitem.view()
    fields = [subview.stringValue() for subview in view.subviews() if hasattr(subview, "stringValue")]
    assert "5h" in fields and "7d" in fields
    assert "75%" in fields and "0%" in fields
    assert "4h" in fields and "1d" in fields
    assert view.frame().size.height <= 56
    bars = [subview for subview in view.subviews() if subview.accessibilityRole() == "AXProgressIndicator"]
    assert len(bars) == 2
    assert [bar.accessibilityValue() for bar in bars] == [75, 0]
    assert all("remaining" in bar.accessibilityLabel() for bar in bars)
    assert [bar.accessibilityHelp() for bar in bars] == ["Resets in 4h 14m", "Resets in 1d 11h"]


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_native_provider_headers_load_upstream_brand_logos(provider):
    from AppKit import NSMenuItem
    from types import SimpleNamespace
    from claude_switcher.native_menu import style_provider_header

    native = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(provider, None, "")
    style_provider_header(SimpleNamespace(_menuitem=native), provider)
    assert native.image() is not None
    assert native.image().isValid()
    assert not native.image().isTemplate()
    assert native.title() == provider


def test_invalid_profile_service_is_unavailable_without_keychain_access(ui):
    module, app, personal, team, threads = ui
    team.keychain_service = personal.keychain_service
    assert app._has_credentials(team) is False
    module.keychain.read_credentials.assert_not_called()


def test_removal_waits_for_profile_switch_or_login(ui):
    module, app, personal, team, threads = ui
    app._rebuild_menu()
    remove_menu = next(item for item in app.menu if item and "Remove account" in item.title)
    app._switch_in_progress.add("claude")
    remove_menu[1].callback(remove_menu[1])
    module.remove_saved_account.assert_not_called()


def test_usage_refresh_skips_provider_during_login(ui, monkeypatch):
    module, app, personal, team, threads = ui
    app._switch_in_progress.add("claude")
    fetch = Mock()
    monkeypatch.setattr(app, "_fetch_usage_state", fetch)
    module.ClaudeSwitcherApp._fetch_all_usage(app)
    threads.pop()()
    fetch.assert_not_called()
    assert app._switch_in_progress == {"claude"}
    assert not app._refresh_in_progress


def test_usage_refresh_uses_current_profile_after_queued_switch(ui, monkeypatch):
    module, app, personal, team, threads = ui
    fetch = Mock(return_value=UsageState(False, "Unavailable"))
    monkeypatch.setattr(app, "_fetch_usage_state", fetch)
    module.ClaudeSwitcherApp._fetch_all_usage(app)
    personal.active, team.active = False, True
    threads.pop()()
    assert [call.args[1] for call in fetch.call_args_list] == [team, team]
    assert app._switch_in_progress == set()


def test_concurrent_opposite_providers_cannot_both_claim_shared_config(ui):
    module, app, personal, team, threads = ui
    ready = threading.Barrier(2)
    outcomes = {}

    def claim(provider):
        ready.wait(timeout=2)
        outcomes[provider] = app._begin_account_operation(provider, notify=False)

    workers = [NativeThread(target=claim, args=(provider,)) for provider in ("claude", "codex")]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=2)
        assert not worker.is_alive()
    assert sorted(outcomes.values()) == [False, True]
    assert len(app._switch_in_progress) == 1


def test_settings_toggle_cannot_write_during_opposite_provider_login(ui):
    from types import SimpleNamespace

    module, app, personal, team, threads = ui
    app._on_add_codex_account(None)
    app._on_toggle_auto_switch(SimpleNamespace(_provider="claude"))
    module.set_auto_switch_enabled.assert_not_called()
    assert app._switch_in_progress == {"codex"}


def test_settings_toggle_claims_and_releases_shared_config_lock(ui):
    from types import SimpleNamespace

    module, app, personal, team, threads = ui
    observed = []
    module.set_auto_switch_enabled.side_effect = lambda *args: observed.append(set(app._switch_in_progress))
    app._on_toggle_auto_switch(SimpleNamespace(_provider="claude"))
    module.set_auto_switch_enabled.assert_called_once_with("claude", False, app.config_path)
    assert observed == [{"claude"}]
    assert app._switch_in_progress == set()
