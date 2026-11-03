import pytest

from app.services.access_policy import AccessDeniedError, AccessPolicy, Principal


def test_project_authorization_is_checked_before_retrieval() -> None:
    policy = AccessPolicy()
    principal = Principal(user_id="u-001", project_ids={"11111111-1111-1111-1111-111111111111"}, roles={"designer"})

    with pytest.raises(AccessDeniedError):
        policy.require_project_access(principal, "22222222-2222-2222-2222-222222222222")


def test_scope_requires_a_matching_role_when_roles_are_set() -> None:
    policy = AccessPolicy()
    principal = Principal(user_id="u-001", project_ids=set(), roles={"designer"})

    assert policy.can_read_scope({"roles": ["designer"]}, principal)
    assert not policy.can_read_scope({"roles": ["admin"]}, principal)
    assert policy.can_read_scope({}, principal)
