import hashlib
import json

GENESIS_HASH = "0" * 64

# Fields that go into the hash, in a fixed order. `hash` itself is excluded
# (it is the output) and so is `_id`, which Mongo may echo back in a
# different type than it was written.
_HASHED_FIELDS = (
    "seq", "session_id", "trace_id", "actor", "action",
    "subject", "input", "output", "reason", "outcome", "created_at", "prev_hash",
)


def canonical_json(doc: dict) -> str:
    """Sorted keys, no whitespace — two equal documents must produce
    byte-identical input to the hash, or verification is meaningless."""
    payload = {k: doc.get(k) for k in _HASHED_FIELDS}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def compute_hash(doc: dict) -> str:
    """Each entry's fingerprint covers the entry before it, so altering any
    past entry breaks every hash after it."""
    return hashlib.sha256((doc["prev_hash"] + canonical_json(doc)).encode()).hexdigest()


def verify_chain(events: list[dict]) -> dict:
    """Walks the chain in `seq` order and reports the first break. Returns
    `{ok, checked, broken_at, detail}` rather than raising, so the endpoint
    and the CLI can both render it."""
    expected_prev = GENESIS_HASH
    expected_seq = None

    for i, event in enumerate(events):
        if expected_seq is not None and event["seq"] != expected_seq:
            return {
                "ok": False, "checked": i, "broken_at": event["seq"],
                "detail": f"Sequence gap: expected seq {expected_seq}, found {event['seq']}.",
            }
        if event["prev_hash"] != expected_prev:
            return {
                "ok": False, "checked": i, "broken_at": event["seq"],
                "detail": f"Entry {event['seq']} does not link to the previous entry's hash.",
            }
        if compute_hash(event) != event["hash"]:
            return {
                "ok": False, "checked": i, "broken_at": event["seq"],
                "detail": f"Entry {event['seq']} has been altered since it was written.",
            }
        expected_prev = event["hash"]
        expected_seq = event["seq"] + 1

    return {"ok": True, "checked": len(events), "broken_at": None, "detail": "Chain intact."}
