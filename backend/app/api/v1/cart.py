"""Direct cart edits, for a buyer who clicked rather than typed.

Adding a known SKU needs no intelligence, so it does not go through the model:
it calls the same cart_service the `add_to_cart` tool calls, writes the same
audit events and is bound by the same rules at checkout. Keeping the model out
of a deterministic action makes it faster, cheaper, and impossible to talk out
of doing what the button says.
"""
from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.audit.service import audit
from app.services.cart_service import cart_service

router = APIRouter(prefix="/cart", tags=["cart"])


class AddItemRequest(BaseModel):
    sku: str
    qty: int = Field(default=1, gt=0, le=10)


class UpdateItemRequest(BaseModel):
    qty: int = Field(ge=0, le=10)  # 0 removes the line


@router.get("/{session_id}")
async def get_cart(session_id: str):
    return await cart_service.get(session_id)


@router.post("/{session_id}/items")
async def add_item(session_id: str, req: AddItemRequest):
    result = await cart_service.add(session_id, req.sku, req.qty)
    await audit.record(
        actor="buyer", action="cart.item_added", session_id=session_id,
        subject={"type": "product", "id": req.sku},
        input={"sku": req.sku, "qty": req.qty},
        reason=f"The buyer added {req.qty}×{req.sku} themselves, by clicking rather than asking the "
               "assistant. The price still came from the catalog and the rulebook still applies.",
    )
    return result


@router.patch("/{session_id}/items/{sku}")
async def update_item(session_id: str, sku: str, req: UpdateItemRequest):
    result = await cart_service.update_qty(session_id, sku, req.qty)
    what = f"set {sku} to {req.qty}" if req.qty else f"removed {sku}"
    await audit.record(
        actor="buyer", action="cart.item_updated", session_id=session_id,
        subject={"type": "product", "id": sku},
        input={"sku": sku, "qty": req.qty},
        reason=f"The buyer {what} directly, without going through the assistant.",
    )
    return result
