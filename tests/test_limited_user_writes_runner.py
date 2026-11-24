import pytest

from e2e.run_standalone_limited_user_writes import has_system_manager_role


def test_user_roles_absent_fails_closed():
    with pytest.raises(ValueError, match="roles could not be verified"):
        has_system_manager_role({"name": "user@example.com"})


def test_user_roles_malformed_fails_closed():
    with pytest.raises(ValueError, match="roles could not be verified"):
        has_system_manager_role({"roles": [{"role": None}]})


def test_user_with_system_manager_role_is_detected():
    assert has_system_manager_role({"roles": [{"role": "System Manager"}]})


def test_user_without_system_manager_role_passes_gate():
    assert not has_system_manager_role({"roles": [{"role": "Website User"}]})
    assert not has_system_manager_role({"roles": []})
