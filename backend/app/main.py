from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.catalog import router as catalog_router
from app.config import settings
from app.db.client import connect, disconnect
from app.errors import RazoError
from app.services.catalog_service import catalog_service


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
    return JSONResponse(
        status_code=exc.http_status,
        content={"error": {"code": exc.code, "message": exc.user_message}},
    )


app.include_router(catalog_router, prefix="/api/v1")


@app.get("/.well-known/agent-catalog.json")
async def agent_catalog_manifest():
    return catalog_service.manifest()


@app.get("/health")
async def health():
    return {"status": "ok"}
