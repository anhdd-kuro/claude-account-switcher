import json
import pytest
from pathlib import Path

from claude_switcher.config import (
    AccountInfo,
    AppSettings,
    load_accounts,
    save_accounts,
    add_account,
    remove_account,
    get_active_account,
    set_active_account,
    load_settings,
    save_settings,
    is_auto_switch_enabled,
    set_auto_switch_enabled,
)


class TestAccountInfo:
    def test_create_account(self):
        acc = AccountInfo(
            email="test@test.com",
            subscription_type="pro",
            org_name="Test Org",
            active=True,
            keychain_account="testuser",
        )
        assert acc.email == "test@test.com"
        assert acc.active is True


class TestLoadSave:
    def test_load_empty_returns_empty_list(self, tmp_path):
        result = load_accounts(tmp_path / "accounts.json")
        assert result == []

    def test_load_corrupted_json_returns_empty_list(self, tmp_path):
        path = tmp_path / "accounts.json"
        path.write_text("not valid json{{{")
        result = load_accounts(path)
        assert result == []

    def test_load_missing_accounts_key_returns_empty_list(self, tmp_path):
        path = tmp_path / "accounts.json"
        path.write_text('{"other": []}')
        result = load_accounts(path)
        assert result == []

    def test_save_and_load_roundtrip(self, tmp_path):
        path = tmp_path / "accounts.json"
        accounts = [
            AccountInfo("a@test.com", "pro", "Org A", True, "usera"),
            AccountInfo("b@test.com", "pro", "Org B", False, "userb"),
        ]
        save_accounts(accounts, path)
        loaded = load_accounts(path)
        assert len(loaded) == 2
        assert loaded[0].email == "a@test.com"
        assert loaded[1].active is False

    def test_failed_atomic_replacement_preserves_saved_accounts(self, tmp_path, monkeypatch):
        path = tmp_path / "accounts.json"
        original = AccountInfo("old@test.com", "max", "Personal", True, "user")
        save_accounts([original], path)
        before = path.read_bytes()

        def fail_replace(source, target):
            raise OSError("disk unavailable")

        monkeypatch.setattr("claude_switcher.config.os.replace", fail_replace)
        with pytest.raises(OSError):
            save_accounts([], path)
        assert path.read_bytes() == before
        assert list(tmp_path.iterdir()) == [path]

    def test_corrupt_registry_cannot_be_overwritten_by_adding_account(self, tmp_path):
        path = tmp_path / "accounts.json"
        path.write_text('{"accounts":')
        with pytest.raises(RuntimeError, match="configuration"):
            add_account(AccountInfo("new@test.com", "max", "", True, "user"), path)
        assert path.read_text() == '{"accounts":'


class TestAccountOperations:
    def test_add_account(self, tmp_path):
        path = tmp_path / "accounts.json"
        acc = AccountInfo("a@test.com", "pro", "Org", False, "usera")
        add_account(acc, path)
        loaded = load_accounts(path)
        assert len(loaded) == 1

    def test_add_existing_updates(self, tmp_path):
        path = tmp_path / "accounts.json"
        acc1 = AccountInfo("a@test.com", "pro", "Org", True, "usera")
        acc2 = AccountInfo("a@test.com", "team", "New Org", True, "usera")
        add_account(acc1, path)
        add_account(acc2, path)
        loaded = load_accounts(path)
        assert len(loaded) == 1
        assert loaded[0].subscription_type == "team"

    def test_remove_account(self, tmp_path):
        path = tmp_path / "accounts.json"
        add_account(AccountInfo("a@test.com", "pro", "Org", True, "u"), path)
        add_account(AccountInfo("b@test.com", "pro", "Org", False, "u"), path)
        remove_account("a@test.com", path)
        loaded = load_accounts(path)
        assert len(loaded) == 1
        assert loaded[0].email == "b@test.com"

    def test_get_active_account(self, tmp_path):
        path = tmp_path / "accounts.json"
        add_account(AccountInfo("a@test.com", "pro", "Org", True, "u"), path)
        add_account(AccountInfo("b@test.com", "pro", "Org", False, "u"), path)
        active = get_active_account(path)
        assert active is not None
        assert active.email == "a@test.com"

    def test_set_active_account(self, tmp_path):
        path = tmp_path / "accounts.json"
        add_account(AccountInfo("a@test.com", "pro", "Org", True, "u"), path)
        add_account(AccountInfo("b@test.com", "pro", "Org", False, "u"), path)
        set_active_account("b@test.com", path)
        loaded = load_accounts(path)
        assert loaded[0].active is False
        assert loaded[1].active is True


class TestProviderField:
    def test_default_provider_is_claude(self):
        acc = AccountInfo("test@test.com", "pro", "", True, "user")
        assert acc.provider == "claude"

    def test_codex_provider(self):
        acc = AccountInfo(
            "test@test.com",
            "plus",
            "",
            True,
            "user",
            provider="codex",
        )
        assert acc.provider == "codex"

    def test_load_legacy_config_defaults_to_claude(self, tmp_path):
        path = tmp_path / "accounts.json"
        path.write_text(json.dumps({
            "accounts": [{
                "email": "old@test.com",
                "subscription_type": "pro",
                "org_name": "",
                "active": True,
                "keychain_account": "user",
            }]
        }))

        accounts = load_accounts(path)

        assert len(accounts) == 1
        assert accounts[0].provider == "claude"

    def test_same_email_different_providers(self, tmp_path):
        path = tmp_path / "accounts.json"
        add_account(AccountInfo("user@test.com", "pro", "", True, "u", provider="claude"), path)
        add_account(AccountInfo("user@test.com", "plus", "", True, "u", provider="codex"), path)

        accounts = load_accounts(path)

        assert len(accounts) == 2
        assert {a.provider for a in accounts} == {"claude", "codex"}

    def test_remove_only_matching_provider(self, tmp_path):
        path = tmp_path / "accounts.json"
        add_account(AccountInfo("user@test.com", "pro", "", True, "u", provider="claude"), path)
        add_account(AccountInfo("user@test.com", "plus", "", False, "u", provider="codex"), path)

        remove_account("user@test.com", path, provider="codex")

        accounts = load_accounts(path)
        assert len(accounts) == 1
        assert accounts[0].provider == "claude"

    def test_set_active_only_affects_same_provider(self, tmp_path):
        path = tmp_path / "accounts.json"
        add_account(AccountInfo("claude@test.com", "pro", "", True, "u", provider="claude"), path)
        add_account(AccountInfo("codex-a@test.com", "plus", "", True, "u", provider="codex"), path)
        add_account(AccountInfo("codex-b@test.com", "plus", "", False, "u", provider="codex"), path)

        set_active_account("codex-b@test.com", path, provider="codex")

        accounts = load_accounts(path)
        claude = next(a for a in accounts if a.provider == "claude")
        codex_a = next(a for a in accounts if a.email == "codex-a@test.com")
        codex_b = next(a for a in accounts if a.email == "codex-b@test.com")
        assert claude.active is True
        assert codex_a.active is False
        assert codex_b.active is True


class TestSettings:
    def test_default_auto_switch_disabled(self, tmp_path):
        path = tmp_path / "accounts.json"
        settings = load_settings(path)
        assert settings.auto_switch == {"claude": False, "codex": False}
        assert settings.auto_switch_threshold == 100.0

    def test_settings_roundtrip(self, tmp_path):
        path = tmp_path / "accounts.json"
        save_settings(AppSettings(auto_switch={"claude": True, "codex": False}), path)
        assert is_auto_switch_enabled("claude", path) is True
        assert is_auto_switch_enabled("codex", path) is False

    def test_toggle_auto_switch(self, tmp_path):
        path = tmp_path / "accounts.json"
        set_auto_switch_enabled("codex", True, path)
        assert is_auto_switch_enabled("codex", path) is True

    def test_save_accounts_preserves_settings(self, tmp_path):
        path = tmp_path / "accounts.json"
        set_auto_switch_enabled("claude", True, path)
        save_accounts([AccountInfo("a@test.com", "pro", "", True, "u")], path)
        assert is_auto_switch_enabled("claude", path) is True

class TestOrganizationProfiles:
    def test_same_email_profiles_save_activate_and_remove_independently(self, tmp_path):
        path = tmp_path / "accounts.json"
        personal = AccountInfo(
            "user@test.com", "max", "Personal", True, "user",
            organization_id="11111111-1111-4111-8111-111111111111",
        )
        team = AccountInfo(
            "user@test.com", "team", "Work", False, "user",
            organization_id="22222222-2222-4222-8222-222222222222",
        )
        codex = AccountInfo("user@test.com", "plus", "", True, "user", provider="codex")
        for account in (personal, team, codex):
            add_account(account, path)

        assert len(load_accounts(path)) == 3
        set_active_account(team.profile_id, path)
        loaded = load_accounts(path)
        assert [a.profile_id for a in loaded if a.provider == "claude" and a.active] == [team.profile_id]
        assert next(a for a in loaded if a.provider == "codex").active

        remove_account(personal.profile_id, path)
        assert {(a.provider, a.profile_id) for a in load_accounts(path)} == {
            ("claude", team.profile_id), ("codex", codex.profile_id),
        }

    def test_legacy_organization_metadata_retains_existing_credential_service(self, tmp_path):
        path = tmp_path / "accounts.json"
        path.write_text(json.dumps({"version": 2, "accounts": [{
            "email": "user@test.com", "subscription_type": "max", "org_name": "Personal",
            "active": True, "keychain_account": "user",
            "oauth_account": {"organizationUuid": "11111111-1111-4111-8111-111111111111"},
        }]}))

        account = load_accounts(path)[0]
        assert account.organization_id == "11111111-1111-4111-8111-111111111111"
        assert account.profile_id == "user@test.com:11111111-1111-4111-8111-111111111111"
        assert account.credential_service == "claude-switcher:user@test.com"
        add_account(account, path)
        assert load_accounts(path)[0].credential_service == "claude-switcher:user@test.com"
