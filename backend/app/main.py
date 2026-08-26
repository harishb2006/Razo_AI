import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError

from app.api.v1.approvals import router as approvals_router
from app.api.v1.audit import router as audit_router
from app.api.v1.catalog import router as catalog_router
from app.api.v1.chat import router as chat_router
from app.api.v1.checkout import router as checkout_router
from app.api.v1.metrics import router as metrics_router
from app.api.v1.webhooks import router as webhooks_router
from app.audit.service import audit_safe
from app.config import settings
from app.db.client import connect, disconnect
from app.errors import RazoError
from app.services.catalog_service import catalog_service

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect()
    yield
    await disconnect()


app = FastAPI(title="Razo_AI", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RazoError)
async def razo_error_handler(request: Request, exc: RazoError):
    if exc.http_status >= 500:
        await audit_safe(
            actor="system", action="system_error",
            subject={"type": "request", "id": request.url.path},
            output={"code": exc.code},
            reason=f"{exc.code} on {request.method} {request.url.path}: {exc.user_message}",
            outcome="failed",
        )
    return JSONResponse(
        status_code=exc.http_status,
        content={"error": {"code": exc.code, "message": exc.user_message}},
    )


@app.exception_handler(PyMongoError)
async def mongo_error_handler(request: Request, exc: PyMongoError):
    """F11: an unreachable database is a known, handled failure — the buyer
    gets a clear next step, not a 500. Browsing keeps working from the boot
    snapshot; taking orders does not, and we say so."""
    log.warning("MongoDB unreachable on %s %s: %s", request.method, request.url.path, type(exc).__name__)
    await audit_safe(
        actor="system", action="db.unavailable",
        subject={"type": "request", "id": request.url.path},
        output={"exception": type(exc).__name__},
        reason=f"MongoDB was unreachable during {request.method} {request.url.path}. "
               "The request was refused rather than answered from stale data.",
        outcome="failed",
    )
    return JSONResponse(
        status_code=503,
        content={"error": {
            "code": "DB_UNAVAILABLE",
            "message": "Can't take orders this moment — browsing still works.",
        }},
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    """FR7: no stack trace ever reaches a client. Anything not already a
    RazoError is recorded as SYSTEM_ERROR and answered generically."""
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    await audit_safe(
        actor="system", action="system_error",
        subject={"type": "request", "id": request.url.path},
        output={"exception": type(exc).__name__},
        reason=f"An unhandled {type(exc).__name__} escaped on {request.method} {request.url.path}.",
        outcome="failed",
    )
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "SYSTEM_ERROR", "message": "Something went wrong."}},
    )


app.include_router(catalog_router, prefix="/api/v1")
app.include_router(chat_router, prefix="/api/v1")
app.include_router(checkout_router, prefix="/api/v1")
app.include_router(approvals_router, prefix="/api/v1")
app.include_router(audit_router, prefix="/api/v1")
app.include_router(metrics_router, prefix="/api/v1")
app.include_router(webhooks_router, prefix="/api/v1")


@app.get("/.well-known/agent-catalog.json")
async def agent_catalog_manifest():
    return catalog_service.manifest()


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/health/ready")
async def readiness():
    """Liveness is not readiness: the process can be perfectly healthy while
    Mongo is unreachable, in which case browsing works and checkout does not.
    The probe says which."""
    from app.agent.llm.router import llm_router
    from app.db.client import get_client

    try:
        await get_client().admin.command("ping")
        db_ok = True
    except Exception:
        db_ok = False

    return JSONResponse(
        status_code=200 if db_ok else 503,
        content={
            "status": "ready" if db_ok else "degraded",
            "database": "up" if db_ok else "unreachable",
            "catalog_snapshot": catalog_service.snapshot_size,
            "can_browse": db_ok or catalog_service.snapshot_size > 0,
            "can_checkout": db_ok,
            "llm_breakers": llm_router.breaker_states(),
        },
    )
