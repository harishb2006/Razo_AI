import hashlib
import hmac
from datetime import datetime, timedelta, timezone

from app.policy.types import VerdictToken

_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _mac(signing_key: str, payload: str) -> str:
    return hmac.new(signing_key.encode(), payload.encode(), hashlib.sha256).hexdigest()


def sign(signing_key: str, intent_hash: str, evaluation_id: str, ttl_seconds: int, now: datetime) -> VerdictToken:
    expires_at = (now + timedelta(seconds=ttl_seconds)).strftime(_TS_FORMAT)
    payload = f"{intent_hash}|{evaluation_id}|{expires_at}"
    return VerdictToken(token=_mac(signing_key, payload), expires_at=expires_at)


def verify(signing_key: str, token: VerdictToken, intent_hash: str, evaluation_id: str, now: datetime) -> bool:
    """The payment service recomputes the HMAC *and* re-derives intent_hash
    from the live session document, so a token can't be replayed against a
    mutated cart — that check happens at the call site, not here."""
    expires_dt = datetime.strptime(token.expires_at, _TS_FORMAT).replace(tzinfo=timezone.utc)
    if now > expires_dt:
        return False
    payload = f"{intent_hash}|{evaluation_id}|{token.expires_at}"
    return hmac.compare_digest(_mac(signing_key, payload), token.token)
