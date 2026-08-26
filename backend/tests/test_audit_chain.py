"""The audit trail is only worth anything if tampering is detectable and
explanations are mandatory. These tests are the evidence for both."""
import pytest

from app.audit.chain import GENESIS_HASH, compute_hash, verify_chain
from app.audit.service import audit
from app.db.documents import AuditEvent


async def record_a_few() -> None:
    await audit.record(actor="buyer", action="session.started", reason="First.", session_id="s-1")
    await audit.record(actor="cart", action="cart.item_added", reason="Second.", session_id="s-1")
    await audit.record(actor="policy", action="policy.evaluated", reason="Third.", session_id="s-1")


async def raw_chain() -> list[dict]:
    events = await AuditEvent.find_all().sort("+seq").to_list()
    return [e.model_dump() for e in events]


@pytest.mark.asyncio
async def test_a_fresh_chain_verifies(db):
    await record_a_few()

    result = verify_chain(await raw_chain())

    assert result["ok"] is True
    assert result["checked"] == 3


@pytest.mark.asyncio
async def test_the_first_entry_links_to_genesis(db):
    await audit.record(actor="system", action="session.started", reason="Only entry.")

    events = await raw_chain()

    assert events[0]["prev_hash"] == GENESIS_HASH
    assert events[0]["seq"] == 1


@pytest.mark.asyncio
async def test_sequence_numbers_are_gap_free_and_ordered(db):
    await record_a_few()

    events = await raw_chain()

    assert [e["seq"] for e in events] == [1, 2, 3]
    assert events[1]["prev_hash"] == events[0]["hash"]
    assert events[2]["prev_hash"] == events[1]["hash"]


@pytest.mark.asyncio
async def test_editing_a_past_entry_is_detected(db):
    """The whole point: someone rewriting history has to also rewrite every
    hash after it, and the chain walk catches that they didn't."""
    await record_a_few()
    events = await raw_chain()

    events[1]["reason"] = "Something else entirely."

    result = verify_chain(events)
    assert result["ok"] is False
    assert result["broken_at"] == 2
    assert "altered" in result["detail"]


@pytest.mark.asyncio
async def test_recomputing_the_hash_after_an_edit_still_breaks_the_next_link(db):
    """A tamperer who is smart enough to recompute the edited entry's own
    hash still breaks the entry that follows it."""
    await record_a_few()
    events = await raw_chain()

    events[1]["reason"] = "Something else entirely."
    events[1]["hash"] = compute_hash(events[1])

    result = verify_chain(events)
    assert result["ok"] is False
    assert result["broken_at"] == 3


@pytest.mark.asyncio
async def test_deleting_an_entry_is_detected_as_a_sequence_gap(db):
    await record_a_few()
    events = await raw_chain()

    del events[1]

    result = verify_chain(events)
    assert result["ok"] is False
    assert "Sequence gap" in result["detail"]


@pytest.mark.asyncio
async def test_an_entry_without_a_reason_is_refused(db):
    """NFR-explainability: recording *what* happened without *why* is a bug,
    and the service refuses it rather than writing a useless entry."""
    with pytest.raises(ValueError, match="reason"):
        await audit.record(actor="system", action="system_error", reason="   ")


@pytest.mark.asyncio
async def test_an_unknown_action_is_refused(db):
    """The action vocabulary is closed, so the trail stays queryable by
    action instead of accumulating free text invented at call sites."""
    with pytest.raises(ValueError, match="action"):
        await audit.record(actor="system", action="something.invented", reason="Because.")


@pytest.mark.asyncio
async def test_an_unknown_actor_is_refused(db):
    with pytest.raises(ValueError, match="actor"):
        await audit.record(actor="the_vibes", action="system_error", reason="Because.")


@pytest.mark.asyncio
async def test_concurrent_writes_do_not_race_the_chain(db):
    """Two writers must not read the same tail and both claim it as their
    predecessor — the service serialises to make that impossible."""
    import asyncio

    await asyncio.gather(*[
        audit.record(actor="agent", action="tool.invoked", reason=f"Concurrent write {i}.")
        for i in range(12)
    ])

    events = await raw_chain()
    assert [e["seq"] for e in events] == list(range(1, 13))
    assert verify_chain(events)["ok"] is True
