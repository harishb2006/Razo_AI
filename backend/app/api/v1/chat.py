from datetime import datetime, timezone

from fastapi import APIRouter
from ulid import ULID

from app.agent.orchestrator import handle_turn
from app.api.v1.schemas.chat import (
    CreateSessionRequest, CreateSessionResponse, MessageView, SendMessageRequest, SessionView, TurnResponse,
)
from app.db.documents import Message, Session
from app.errors import RazoError

router = APIRouter(prefix="/chat", tags=["chat"])


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@router.post("/sessions", response_model=CreateSessionResponse)
async def create_session(req: CreateSessionRequest):
    session_id = str(ULID())
    await Session(
        id=session_id, channel=req.channel, actor_ref=req.actor_ref,
        mandate=req.mandate, created_at=_now(),
    ).insert()
    return CreateSessionResponse(session_id=session_id)


@router.post("/sessions/{session_id}/messages", response_model=TurnResponse)
async def send_message(session_id: str, req: SendMessageRequest):
    return await handle_turn(session_id, req.text)


@router.get("/sessions/{session_id}", response_model=SessionView)
async def get_session(session_id: str):
    session = await Session.get(session_id)
    if session is None:
        raise RazoError("SESSION_NOT_FOUND", 404, "I couldn't find that session.")
    return SessionView(
        session_id=session.id, channel=session.channel, state=session.state,
        turn_count=session.turn_count, cart=session.cart.model_dump(),
    )


@router.get("/sessions/{session_id}/messages", response_model=list[MessageView])
async def get_messages(session_id: str):
    msgs = await Message.find(Message.session_id == session_id).sort("turn").to_list()
    return [MessageView(turn=m.turn, role=m.role, content=m.content, tool_name=m.tool_name) for m in msgs]
