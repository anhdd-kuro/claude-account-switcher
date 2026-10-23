import json
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from claude_switcher.core import (
    check_claude_cli,
    get_auth_status,
    run_auth_logout,
    run_auth_login,
    import_current_account,
    switch_account,
    add_new_account,
    remove_saved_account,
)
from claude_switcher.config import AccountInfo


@pytest.fixture(autouse=True)
def isolate_claude_state(monkeypatch, tmp_path):
    monkeypatch.setattr("claude_switcher.core.CLAUDE_STATE_FILE", tmp_path / "claude.json")
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)


class TestClaudeCLI:
    @patch("claude_switcher.core.shutil.which")
    def test_check_cli_found(self, mock_which):
        mock_which.return_value = "/usr/local/bin/claude"
        assert check_claude_cli() is True

    @patch("claude_switcher.core.Path.is_file", return_value=False)
    @patch("claude_switcher.core.shutil.which", return_value=None)
    def test_check_cli_not_found(self, mock_which, mock_is_file):
        assert check_claude_cli() is False

    @patch("claude_switcher.core.subprocess.run")
    def test_get_auth_status(self, mock_run):
        status = {"loggedIn": True, "email": "test@test.com", "subscriptionType": "pro", "orgName": "Org"}
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(status))
        result = get_auth_status()
        assert result["email"] == "test@test.com"

    @patch("claude_switcher.core.subprocess.run")
    def test_get_auth_status_failure_returns_none(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = get_auth_status()
        assert result is None


class TestImportCurrentAccount:
    @patch("claude_switcher.core._read_oauth_account")
    @patch("claude_switcher.core.get_auth_status")
    @patch("claude_switcher.core.keychain")
    def test_import_success(self, mock_kc, mock_status, mock_oauth, tmp_path):
        mock_status.return_value = {"email": "test@test.com", "subscriptionType": "pro", "orgName": "Org"}
        mock_kc.read_credentials.return_value = '{"accessToken":"tok"}'
        mock_kc.read_account_attribute.return_value = "testuser"
        mock_oauth.return_value = {"emailAddress": "test@test.com"}

        config_path = tmp_path / "accounts.json"
        result = import_current_account(config_path)

        assert result is not None
        assert result.email == "test@test.com"
        assert result.oauth_account == {"emailAddress": "test@test.com"}
        mock_kc.write_credentials.assert_called_once_with(
            "claude-switcher:test@test.com", "testuser", '{"accessToken":"tok"}'
        )

    @patch("claude_switcher.core.get_auth_status")
    @patch("claude_switcher.core.keychain")
    def test_import_no_credentials(self, mock_kc, mock_status, tmp_path):
        mock_kc.read_credentials.return_value = None
        result = import_current_account(tmp_path / "accounts.json")
        assert result is None


class TestSwitchAccount:
    @patch("claude_switcher.core._write_oauth_account")
    @patch("claude_switcher.core._read_oauth_account")
    @patch("claude_switcher.core.keychain")
    def test_switch_saves_current_then_loads_target(self, mock_kc, mock_read_oauth, mock_write_oauth, tmp_path):
        config_path = tmp_path / "accounts.json"
        from claude_switcher.config import add_account, AccountInfo
        add_account(AccountInfo("a@test.com", "pro", "Org A", True, "usera"), config_path)
        add_account(AccountInfo("b@test.com", "pro", "Org B", False, "userb",
                                oauth_account={"emailAddress": "b@test.com"}), config_path)

        mock_kc.read_credentials.side_effect = [
            '{"accessToken":"refreshed-a"}',
            '{"accessToken":"tok-b"}',
        ]
        mock_read_oauth.return_value = {"emailAddress": "a@test.com"}

        switch_account("b@test.com", config_path)

        mock_kc.write_credentials.assert_any_call(
            "claude-switcher:a@test.com", "usera", '{"accessToken":"refreshed-a"}'
        )
        mock_kc.write_credentials.assert_any_call(
            "Claude Code-credentials", "userb", '{"accessToken":"tok-b"}'
        )
        mock_write_oauth.assert_called_once_with({"emailAddress": "b@test.com"})

    @patch("claude_switcher.core._write_oauth_account")
    @patch("claude_switcher.core._read_oauth_account")
    @patch("claude_switcher.core.keychain")
    def test_switch_missing_keychain_entry_raises(self, mock_kc, mock_read_oauth, mock_write_oauth, tmp_path):
        config_path = tmp_path / "accounts.json"
        from claude_switcher.config import add_account, AccountInfo
        add_account(AccountInfo("a@test.com", "pro", "Org", True, "u"), config_path)
        add_account(AccountInfo("b@test.com", "pro", "Org", False, "u"), config_path)
        mock_kc.read_credentials.side_effect = ['{"tok":"a"}', None]
        mock_read_oauth.return_value = None

        try:
            switch_account("b@test.com", config_path)
            assert False, "Should have raised"
        except RuntimeError as e:
            assert "not found" in str(e).lower()


class TestAddNewAccount:
    @patch("claude_switcher.core._read_oauth_account")
    @patch("claude_switcher.core.get_auth_status")
    @patch("claude_switcher.core.run_auth_login")
    @patch("claude_switcher.core.run_auth_logout")
    @patch("claude_switcher.core.keychain")
    def test_add_account_full_flow(self, mock_kc, mock_logout, mock_login, mock_status, mock_oauth, tmp_path):
        config_path = tmp_path / "accounts.json"
        from claude_switcher.config import add_account, AccountInfo
        add_account(AccountInfo("a@test.com", "pro", "Org A", True, "usera"), config_path)

        mock_kc.read_credentials.side_effect = [
            '{"accessToken":"tok-a"}',
            '{"accessToken":"tok-new"}',
        ]
        mock_kc.read_account_attribute.side_effect = ["usera", "newuser"]
        mock_kc.delete_credentials.return_value = False
        mock_login.return_value = True
        mock_status.return_value = {"email": "new@test.com", "subscriptionType": "pro", "orgName": "New Org"}
        mock_oauth.side_effect = [
            {"emailAddress": "a@test.com"},
            {"emailAddress": "new@test.com"},
        ]

        result = add_new_account(config_path)
        assert result is not None
        assert result.email == "new@test.com"
        mock_logout.assert_not_called()
        mock_login.assert_called_once()

    @patch("claude_switcher.core.run_auth_login")
    @patch("claude_switcher.core.run_auth_logout")
    @patch("claude_switcher.core.keychain")
    def test_add_account_login_cancelled(self, mock_kc, mock_logout, mock_login, tmp_path):
        config_path = tmp_path / "accounts.json"
        mock_kc.read_credentials.return_value = None
        mock_kc.delete_credentials.return_value = False
        mock_login.return_value = False

        result = add_new_account(config_path)
        assert result is None


class TestRemoveSavedAccount:
    @patch("claude_switcher.core.keychain")
    def test_remove_account(self, mock_kc, tmp_path):
        config_path = tmp_path / "accounts.json"
        from claude_switcher.config import add_account, AccountInfo
        add_account(AccountInfo("a@test.com", "pro", "Org", False, "u"), config_path)

        remove_saved_account("a@test.com", config_path)

        mock_kc.delete_credentials.assert_called_once_with("claude-switcher:a@test.com")
        from claude_switcher.config import load_accounts
        assert len(load_accounts(config_path)) == 0


class TestCoreWithMixedProviders:
    @patch("claude_switcher.core._write_oauth_account")
    @patch("claude_switcher.core._read_oauth_account")
    @patch("claude_switcher.core.keychain")
    def test_switch_claude_ignores_codex_account_with_same_email(
        self, mock_kc, mock_read_oauth, mock_write_oauth, tmp_path
    ):
        config_path = tmp_path / "accounts.json"
        from claude_switcher.config import add_account, load_accounts

        add_account(
            AccountInfo("user@test.com", "pro", "", True, "claude-user", provider="claude"),
            config_path,
        )
        add_account(
            AccountInfo(
                "other@test.com",
                "pro",
                "",
                False,
                "claude-other",
                oauth_account={"emailAddress": "other@test.com"},
                provider="claude",
            ),
            config_path,
        )
        add_account(
            AccountInfo("user@test.com", "plus", "", True, "codex-user", provider="codex"),
            config_path,
        )
        mock_kc.read_credentials.side_effect = ['{"token":"current"}', '{"token":"target"}']
        mock_read_oauth.return_value = {"emailAddress": "user@test.com"}

        switch_account("other@test.com", config_path)

        accounts = load_accounts(config_path)
        codex = next(a for a in accounts if a.provider == "codex")
        claude_target = next(a for a in accounts if a.email == "other@test.com")
        assert codex.active is True
        assert claude_target.active is True
        mock_write_oauth.assert_called_once_with({"emailAddress": "other@test.com"})


@pytest.fixture
def profile_store(monkeypatch, tmp_path):
    from claude_switcher import core

    store = {}
    monkeypatch.setattr(core.keychain, "read_credentials", store.get)
    monkeypatch.setattr(core.keychain, "write_credentials", lambda service, account, value: store.__setitem__(service, value))
    monkeypatch.setattr(core.keychain, "delete_credentials", lambda service: store.pop(service, None) is not None)
    monkeypatch.setattr(core.keychain, "read_account_attribute", lambda service: "local-user")
    monkeypatch.setattr(core, "CLAUDE_STATE_FILE", tmp_path / "claude.json")
    monkeypatch.setattr(core.time, "sleep", lambda _: None)
    monkeypatch.setattr(core, "run_auth_login", lambda: False)
    monkeypatch.setattr(core, "get_auth_status", lambda: None)
    return store, tmp_path / "accounts.json", core.CLAUDE_STATE_FILE


def test_same_email_profiles_import_switch_and_remove_without_crossing_credentials(profile_store, monkeypatch):
    from claude_switcher import core
    from claude_switcher.config import load_accounts, get_active_account

    store, config, state = profile_store
    email = "user@test.com"
    personal_id = "11111111-1111-4111-8111-111111111111"
    team_id = "22222222-2222-4222-8222-222222222222"

    def login(organization_id, plan, token):
        oauth = {"emailAddress": email, "organizationUuid": organization_id}
        state.write_text(json.dumps({"oauthAccount": oauth, "keep": "unchanged"}))
        store[core.CLAUDE_SERVICE] = json.dumps({"claudeAiOauth": {"accessToken": token}})
        monkeypatch.setattr(core, "get_auth_status", lambda: {
            "email": email, "orgId": organization_id, "orgName": plan, "subscriptionType": plan,
        })
        return core.import_current_account(config)

    personal = login(personal_id, "max", "personal-token")
    team = login(team_id, "team", "team-token")
    assert len(load_accounts(config)) == 2
    assert personal.credential_service != team.credential_service
    assert "personal-token" in store[personal.credential_service]
    assert "team-token" in store[team.credential_service]

    store[core.CLAUDE_SERVICE] = '{"claudeAiOauth":{"accessToken":"refreshed-team"}}'
    core.switch_account(personal.profile_id, config)
    assert "personal-token" in store[core.CLAUDE_SERVICE]
    assert "refreshed-team" in store[team.credential_service]
    assert json.loads(state.read_text()) == {
        "oauthAccount": personal.oauth_account, "keep": "unchanged",
    }
    assert get_active_account(config).profile_id == personal.profile_id

    core.switch_account(team.profile_id, config)
    assert "refreshed-team" in store[core.CLAUDE_SERVICE]
    assert json.loads(state.read_text())["oauthAccount"] == team.oauth_account
    core.remove_saved_account(personal.profile_id, config)
    assert personal.credential_service not in store
    assert "refreshed-team" in store[team.credential_service]
    assert [a.profile_id for a in load_accounts(config)] == [team.profile_id]


def test_import_rejects_conflicting_organization_metadata_before_saving(profile_store, monkeypatch):
    from claude_switcher import core
    from claude_switcher.config import load_accounts

    store, config, state = profile_store
    store[core.CLAUDE_SERVICE] = '{"claudeAiOauth":{"accessToken":"active-token"}}'
    state.write_text(json.dumps({"oauthAccount": {
        "emailAddress": "user@test.com", "organizationUuid": "11111111-1111-4111-8111-111111111111",
    }}))
    monkeypatch.setattr(core, "get_auth_status", lambda: {
        "email": "user@test.com", "orgId": "22222222-2222-4222-8222-222222222222",
    })
    with pytest.raises(RuntimeError, match="organization"):
        core.import_current_account(config)
    assert load_accounts(config) == []
    assert list(store) == [core.CLAUDE_SERVICE]


def test_cancelled_profile_login_restores_credentials_and_metadata(profile_store, monkeypatch):
    from claude_switcher import core
    from claude_switcher.config import add_account, get_active_account

    store, config, state = profile_store
    personal = AccountInfo("user@test.com", "max", "Personal", True, "local-user", oauth_account={
        "emailAddress": "user@test.com", "organizationUuid": "11111111-1111-4111-8111-111111111111",
    })
    add_account(personal, config)
    store[core.CLAUDE_SERVICE] = "personal-token"
    state.write_text(json.dumps({"oauthAccount": personal.oauth_account, "keep": True}))

    def cancel_login():
        state.write_text(json.dumps({"oauthAccount": {"organizationUuid": "partial-team"}, "keep": True}))
        return False

    monkeypatch.setattr(core, "run_auth_login", cancel_login)
    monkeypatch.setattr(core, "run_auth_logout", lambda: None)
    assert core.add_new_account(config) is None
    assert store[core.CLAUDE_SERVICE] == "personal-token"
    assert json.loads(state.read_text())["oauthAccount"] == personal.oauth_account
    assert get_active_account(config).profile_id == personal.profile_id


def test_missing_profile_does_not_change_active_credentials(profile_store):
    from claude_switcher import core

    store, config, state = profile_store
    store[core.CLAUDE_SERVICE] = "original"
    with pytest.raises(RuntimeError, match="not found"):
        core.switch_account("user@test.com:missing", config)
    assert store == {core.CLAUDE_SERVICE: "original"}


def test_legacy_profile_without_metadata_requires_reimport_before_switching(profile_store, monkeypatch):
    from claude_switcher import core
    from claude_switcher.config import add_account, get_active_account

    store, config, state = profile_store
    legacy = AccountInfo("user@test.com", "max", "Personal", False, "local-user")
    team = AccountInfo("user@test.com", "team", "Work", True, "local-user", oauth_account={
        "emailAddress": "user@test.com", "organizationUuid": "22222222-2222-4222-8222-222222222222",
    })
    team.keychain_service = f"claude-switcher:{team.profile_id}"
    for account in (legacy, team):
        add_account(account, config)
    store[legacy.credential_service] = "personal-token"
    store[team.credential_service] = "team-token"
    store[core.CLAUDE_SERVICE] = "team-token"
    state.write_text(json.dumps({"oauthAccount": team.oauth_account}))

    with pytest.raises(RuntimeError, match="metadata"):
        core.switch_account(legacy.profile_id, config)
    assert store[core.CLAUDE_SERVICE] == "team-token"
    assert json.loads(state.read_text())["oauthAccount"] == team.oauth_account
    assert get_active_account(config).profile_id == team.profile_id

    personal_org = "11111111-1111-4111-8111-111111111111"
    state.write_text(json.dumps({"oauthAccount": {
        "emailAddress": "user@test.com", "organizationUuid": personal_org,
    }}))
    store[core.CLAUDE_SERVICE] = "personal-token"
    monkeypatch.setattr(core, "get_auth_status", lambda: {
        "email": "user@test.com", "orgId": personal_org, "subscriptionType": "max",
    })
    personal = core.import_current_account(config)
    core.switch_account(team.profile_id, config)
    core.switch_account(personal.profile_id, config)
    assert store[core.CLAUDE_SERVICE] == "personal-token"
    assert json.loads(state.read_text())["oauthAccount"]["organizationUuid"] == personal_org


@pytest.mark.parametrize("original_oauth", [None, {"emailAddress": "user@test.com"}])
def test_cancelled_login_restores_unregistered_session_and_metadata_absence(profile_store, monkeypatch, original_oauth):
    from claude_switcher import core

    store, config, state = profile_store
    store[core.CLAUDE_SERVICE] = "unregistered-token"
    original = {"keep": True}
    if original_oauth is not None:
        original["oauthAccount"] = original_oauth
    state.write_text(json.dumps(original))

    def cancel():
        store[core.CLAUDE_SERVICE] = "partial-token"
        state.write_text(json.dumps({"keep": True, "oauthAccount": {"organizationUuid": "partial"}}))
        return False

    monkeypatch.setattr(core, "run_auth_login", cancel)
    assert core.add_new_account(config) is None
    assert store[core.CLAUDE_SERVICE] == "unregistered-token"
    assert json.loads(state.read_text()) == original


def test_custom_config_directory_is_rejected_before_touching_default_login(profile_store, monkeypatch, tmp_path):
    from claude_switcher import core

    store, config, state = profile_store
    store[core.CLAUDE_SERVICE] = "default-token"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "custom"))
    with pytest.raises(RuntimeError, match="CLAUDE_CONFIG_DIR"):
        core.add_new_account(config)
    assert store == {core.CLAUDE_SERVICE: "default-token"}


def test_profile_metadata_must_match_saved_organization_before_switch(profile_store):
    from claude_switcher import core
    from claude_switcher.config import add_account

    store, config, state = profile_store
    account = AccountInfo("user@test.com", "max", "Personal", False, "local-user",
        organization_id="11111111-1111-4111-8111-111111111111",
        oauth_account={"emailAddress": "user@test.com", "organizationUuid": "22222222-2222-4222-8222-222222222222"})
    add_account(account, config)
    store[core.CLAUDE_SERVICE] = "current-token"
    with pytest.raises(RuntimeError, match="metadata"):
        core.switch_account(account.profile_id, config)
    assert store == {core.CLAUDE_SERVICE: "current-token"}


def test_profiles_cannot_share_a_legacy_credential_backup(profile_store):
    from claude_switcher import core
    from claude_switcher.config import add_account

    store, config, state = profile_store
    for org in ("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"):
        account = AccountInfo("user@test.com", "max", "Profile", False, "local-user",
            oauth_account={"emailAddress": "user@test.com", "organizationUuid": org})
        add_account(account, config)
    store[core.CLAUDE_SERVICE] = "current-token"
    with pytest.raises(RuntimeError, match="share"):
        core.switch_account(account.profile_id, config)
    assert store == {core.CLAUDE_SERVICE: "current-token"}


def test_unidentified_live_session_cannot_overwrite_legacy_backup(profile_store):
    from claude_switcher import core
    from claude_switcher.config import add_account

    store, config, state = profile_store
    legacy = AccountInfo("a@test.com", "max", "Personal", True, "local-user",
                         oauth_account={"emailAddress": "a@test.com"})
    add_account(legacy, config)
    store[legacy.credential_service] = "saved-a"
    store[core.CLAUDE_SERVICE] = "unregistered-b"
    state.write_text(json.dumps({"keep": True}))
    with pytest.raises(RuntimeError, match="Cannot verify"):
        core.add_new_account(config)
    assert store[legacy.credential_service] == "saved-a"
    assert store[core.CLAUDE_SERVICE] == "unregistered-b"
    assert json.loads(state.read_text()) == {"keep": True}
