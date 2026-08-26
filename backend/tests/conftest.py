"""Shared offline test database. Everything here runs on mongomock with no
keys and no network — the suite is the artifact that proves the system can
be checked with the AI unplugged."""
import os

os.environ["OFFLINE_MODE"] = "True"

import pytest_asyncio
from beanie import init_beanie
from mongomock_motor import AsyncMongoMockClient

from app.config import settings
from app.db import client as db_client
from app.db.client import DOCUMENT_MODELS


@pytest_asyncio.fixture
async def db(monkeypatch):
    """Points the app's module-level client at a fresh in-memory database, so
    services that reach for it directly (the audit writer, the sequence
    counter) work exactly as they do in production. Beanie and the raw-motor
    call sites must land in the *same* database, hence settings.mongodb_db."""
    mock = AsyncMongoMockClient()
    monkeypatch.setattr(db_client, "_client", mock)
    monkeypatch.setattr(db_client, "_audit_client", None)
    await init_beanie(database=mock[settings.mongodb_db], document_models=DOCUMENT_MODELS)
    yield mock
