"""``_validate_sha256`` contract — a digest is 64 hex characters, nothing else.

The helper guards the value that becomes a filename under the media root
(``transcript_material_path``) and the ``sha256`` / ``material_id`` fields
of every attachment ref, so its acceptance set is worth pinning explicitly.
"""

from __future__ import annotations

import pytest

from agentos.attachment_refs import _validate_sha256, make_attachment_ref

VALID = "a" * 64


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("a" * 32 + "_" + "a" * 31, id="digit-group-underscore"),
        pytest.param(" " + "a" * 63, id="leading-whitespace"),
        pytest.param("a" * 63 + " ", id="trailing-whitespace"),
        pytest.param("+" + "a" * 63, id="leading-plus"),
        pytest.param("-" + "a" * 63, id="leading-minus"),
    ],
)
def test_int_parseable_non_hex_is_rejected(value: str) -> None:
    """int(value, 16) accepts all of these; a SHA-256 digest is none of them."""
    assert len(value) == 64, "each case must clear the length check to be meaningful"
    with pytest.raises(ValueError, match="sha256 is invalid"):
        _validate_sha256(value)


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("g" * 64, id="non-hex-letter"),
        pytest.param("a" * 63, id="too-short"),
        pytest.param("a" * 65, id="too-long"),
        pytest.param("", id="empty"),
        pytest.param("0x" + "a" * 62, id="hex-prefix"),
    ],
)
def test_malformed_digest_is_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="sha256 is invalid"):
        _validate_sha256(value)


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(None, id="none"),
        pytest.param(123, id="int"),
        pytest.param(b"a" * 64, id="bytes"),
    ],
)
def test_non_string_is_rejected(value: object) -> None:
    with pytest.raises(ValueError, match="sha256 is invalid"):
        _validate_sha256(value)


def test_lowercase_digest_round_trips() -> None:
    assert _validate_sha256(VALID) == VALID


def test_uppercase_digest_is_normalized() -> None:
    """Existing behaviour: a valid digest is accepted and lowercased."""
    assert _validate_sha256("A" * 64) == VALID


def test_mixed_case_digest_is_normalized() -> None:
    assert _validate_sha256("aB" * 32) == "ab" * 32


def test_make_attachment_ref_rejects_a_non_hex_digest() -> None:
    """The guard holds at the public constructor, not just the private helper."""
    with pytest.raises(ValueError, match="sha256 is invalid"):
        make_attachment_ref(
            sha256=" " + "a" * 63,
            name="report.csv",
            mime="text/csv",
            size=1,
            session_id="s1",
            source="test",
        )
