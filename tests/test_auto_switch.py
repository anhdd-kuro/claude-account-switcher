from claude_switcher.auto_switch import (
    account_key,
    should_auto_switch,
    choose_auto_switch_target,
)
from claude_switcher.config import AccountInfo
from claude_switcher.usage_state import UsageState, UsageWindow


def _account(email, provider="claude", active=False):
    return AccountInfo(email, "pro", "", active, email, provider=provider)


def _usage(percent):
    return UsageState(True, f"{percent}%", (UsageWindow("test", percent),))


def test_should_auto_switch_requires_enabled_and_exhausted_usage():
    assert should_auto_switch(_usage(100), True, 100) is True
    assert should_auto_switch(_usage(99.9), True, 100) is False
    assert should_auto_switch(_usage(100), False, 100) is False
    assert should_auto_switch(UsageState(False, "Usage unavailable"), True, 100) is False


def test_choose_target_same_provider_only():
    active = _account("active@test.com", "claude", True)
    target = _account("target@test.com", "claude")
    other_provider_same_email = _account("target@test.com", "codex")

    chosen = choose_auto_switch_target(
        "claude",
        [active, other_provider_same_email, target],
        "active@test.com",
        {account_key(target): _usage(12), account_key(other_provider_same_email): _usage(0)},
        lambda account: True,
    )

    assert chosen == target


def test_choose_target_ignores_missing_credentials():
    active = _account("active@test.com", "claude", True)
    no_creds = _account("no-creds@test.com", "claude")
    target = _account("target@test.com", "claude")

    chosen = choose_auto_switch_target(
        "claude",
        [active, no_creds, target],
        "active@test.com",
        {account_key(no_creds): _usage(0), account_key(target): _usage(10)},
        lambda account: account.email != "no-creds@test.com",
    )

    assert chosen == target


def test_choose_target_returns_none_when_all_targets_exhausted():
    active = _account("active@test.com", "claude", True)
    exhausted = _account("exhausted@test.com", "claude")

    chosen = choose_auto_switch_target(
        "claude",
        [active, exhausted],
        "active@test.com",
        {account_key(exhausted): _usage(100)},
        lambda account: True,
    )

    assert chosen is None


def test_choose_target_uses_unknown_usage_as_fallback():
    active = _account("active@test.com", "claude", True)
    unknown = _account("unknown@test.com", "claude")

    chosen = choose_auto_switch_target(
        "claude",
        [active, unknown],
        "active@test.com",
        {},
        lambda account: True,
    )

    assert chosen == unknown


def test_same_email_profiles_have_distinct_usage_keys():
    personal = AccountInfo("same@test.com", "pro", "Personal", True, "user",
                           oauth_account={"organizationUuid": "personal"})
    team = AccountInfo("same@test.com", "team", "Work", False, "user",
                       oauth_account={"organizationUuid": "team"})

    assert account_key(personal) == ("claude", "same@test.com:personal")
    assert account_key(team) == ("claude", "same@test.com:team")
    assert len({account_key(personal): _usage(100), account_key(team): _usage(15)}) == 2


def test_choose_target_can_switch_to_same_email_organization():
    personal = AccountInfo("same@test.com", "pro", "Personal", True, "user",
                           oauth_account={"organizationUuid": "personal"})
    team = AccountInfo("same@test.com", "team", "Work", False, "user",
                       oauth_account={"organizationUuid": "team"})
    usage = {("claude", "same@test.com:personal"): _usage(100),
             ("claude", "same@test.com:team"): _usage(15)}

    chosen = choose_auto_switch_target(
        "claude", [personal, team], "same@test.com:personal", usage, lambda account: True,
    )
    assert chosen is team


def test_choose_target_does_not_reselect_exhausted_active_profile():
    personal = AccountInfo("same@test.com", "pro", "Personal", True, "user",
                           oauth_account={"organizationUuid": "personal"})
    team = AccountInfo("same@test.com", "team", "Work", False, "user",
                       oauth_account={"organizationUuid": "team"})
    usage = {("claude", "same@test.com:personal"): _usage(100),
             ("claude", "same@test.com:team"): _usage(100)}

    assert choose_auto_switch_target(
        "claude", [personal, team], "same@test.com:personal", usage, lambda account: True,
    ) is None
