from __future__ import annotations

import pytest

from agentos.provider.failures import (
    ProviderFailureKind,
    ProviderRecoveryAction,
    classify_provider_error,
    decide_recovery_action,
)


def test_provider_request_budget_exhausted_is_context_overflow() -> None:
    assert (
        classify_provider_error(
            provider_name="openrouter",
            status_code=None,
            raw_code="provider_request_budget_exhausted",
            message='{"fallback_reason":"provider_request_budget_exhausted"}',
        )
        is ProviderFailureKind.CONTEXT_OVERFLOW
    )


@pytest.mark.parametrize(
    ("kind", "expected_action"),
    [
        (ProviderFailureKind.CONTEXT_OVERFLOW, ProviderRecoveryAction.COMPACT_AND_RETRY),
        (ProviderFailureKind.EMPTY_RESPONSE, ProviderRecoveryAction.RETRY),
        (ProviderFailureKind.MALFORMED_RESPONSE, ProviderRecoveryAction.RETRY),
        (ProviderFailureKind.PROVIDER_OVERLOADED, ProviderRecoveryAction.RETRY_THEN_FALLBACK),
        (ProviderFailureKind.TRANSPORT_TRANSIENT, ProviderRecoveryAction.RETRY_THEN_FALLBACK),
        (ProviderFailureKind.RATE_LIMITED, ProviderRecoveryAction.FALLBACK_PROVIDER),
        (ProviderFailureKind.INSUFFICIENT_CREDITS, ProviderRecoveryAction.FALLBACK_PROVIDER),
        (ProviderFailureKind.MODEL_NOT_FOUND, ProviderRecoveryAction.FALLBACK_PROVIDER),
        (ProviderFailureKind.UNSUPPORTED_FEATURE, ProviderRecoveryAction.FALLBACK_PROVIDER),
        (ProviderFailureKind.AUTH_INVALID, ProviderRecoveryAction.FAIL_CONFIG),
        (ProviderFailureKind.POLICY_REFUSAL, ProviderRecoveryAction.SURFACE),
        (ProviderFailureKind.BAD_REQUEST, ProviderRecoveryAction.SURFACE),
        (ProviderFailureKind.UNKNOWN, ProviderRecoveryAction.SURFACE),
    ],
)
def test_decide_recovery_action(
    kind: ProviderFailureKind, expected_action: ProviderRecoveryAction
) -> None:
    assert decide_recovery_action(kind) is expected_action


def test_all_provider_recovery_actions_are_reachable() -> None:
    reachable_actions = {decide_recovery_action(kind) for kind in ProviderFailureKind}
    for action in ProviderRecoveryAction:
        assert action in reachable_actions, f"ProviderRecoveryAction.{action.name} is unreachable"

