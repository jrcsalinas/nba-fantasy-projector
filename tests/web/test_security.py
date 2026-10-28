"""Tests for password hashing and verification."""

from nbaproj.web.security import hash_password, verify_password

from tests.web.conftest import TEST_PASSWORD


def test_verify_password_accepts_the_right_password(password_hash):
    """The password the hash was made from verifies."""
    assert verify_password(password_hash, TEST_PASSWORD) is True


def test_verify_password_rejects_wrong_empty_and_malformed_hashes(password_hash):
    """Anything that is not the password is False, and a bad hash never raises."""
    assert verify_password(password_hash, "wrong password") is False
    assert verify_password(password_hash, "") is False
    assert verify_password("not-an-argon2-hash", TEST_PASSWORD) is False
    assert verify_password("", TEST_PASSWORD) is False


def test_hash_password_is_salted_and_verifiable():
    """The same password hashes differently each time, as argon2id, and both verify."""
    first = hash_password(TEST_PASSWORD)
    second = hash_password(TEST_PASSWORD)

    assert first != second
    assert first.startswith("$argon2id$")
    assert verify_password(first, TEST_PASSWORD) is True
    assert verify_password(second, TEST_PASSWORD) is True
    assert verify_password(first, TEST_PASSWORD + "x") is False
