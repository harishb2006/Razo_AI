from datetime import datetime, timedelta, timezone

from app.policy import verdict as verdict_signing

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_token_verifies_against_the_same_inputs():
    token = verdict_signing.sign("key-a", "hash-1", "eval-1", 120, NOW)
    assert verdict_signing.verify("key-a", token, "hash-1", "eval-1", NOW)


def test_token_fails_if_the_intent_hash_changed():
    """A cart mutated after evaluation cannot be executed against a stale ALLOW."""
    token = verdict_signing.sign("key-a", "hash-1", "eval-1", 120, NOW)
    assert not verdict_signing.verify("key-a", token, "hash-2", "eval-1", NOW)


def test_token_fails_with_the_wrong_signing_key():
    token = verdict_signing.sign("key-a", "hash-1", "eval-1", 120, NOW)
    assert not verdict_signing.verify("key-b", token, "hash-1", "eval-1", NOW)


def test_token_expires_after_its_ttl():
    token = verdict_signing.sign("key-a", "hash-1", "eval-1", 120, NOW)
    later = NOW + timedelta(seconds=121)
    assert not verdict_signing.verify("key-a", token, "hash-1", "eval-1", later)


def test_token_still_valid_one_second_before_expiry():
    token = verdict_signing.sign("key-a", "hash-1", "eval-1", 120, NOW)
    just_before = NOW + timedelta(seconds=119)
    assert verdict_signing.verify("key-a", token, "hash-1", "eval-1", just_before)
