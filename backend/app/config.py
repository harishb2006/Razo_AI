import json
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    offline_mode: bool = False

    mongodb_uri: str = ""
    mongodb_db: str = "razo_ai"
    mongodb_audit_uri: str = ""

    mongo_max_pool_size: int = 10

    api_key: str = "dev-local-key"
    # NoDecode: keep the env source from json-decoding this before the
    # validator below gets a chance to make sense of it.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    # A misformatted CORS_ORIGINS used to crash-loop the container on boot,
    # which is a brutal way to find out you forgot the JSON quotes in a
    # dashboard field. Accept the shapes people actually type.
    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, v):
        if not isinstance(v, str):
            return v
        raw = v.strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                return json.loads(raw)
            except ValueError:
                raw = raw[1:-1] if raw.endswith("]") else raw[1:]
        return [o for o in (p.strip().strip("\"'") for p in raw.replace(",", " ").split()) if o]

    catalog_search_limit: int = 10

    gemini_api_key: str = ""
    groq_api_key: str = ""
    # Overridable, because providers retire model ids on their own schedule —
    # a retirement should be an .env edit, not a code change.
    gemini_model: str = "gemini-flash-latest"
    groq_model: str = "openai/gpt-oss-20b"
    llm_provider_chain: str = "gemini,groq,echo"
    llm_timeout_s: float = 12.0

    llm_max_attempts: int = 3
    llm_rate_limit_per_minute: float = 12.0  # sits under the free-tier RPM caps

    breaker_failure_threshold: int = 3
    breaker_window_s: float = 60.0
    breaker_cool_off_s: float = 30.0

    agent_max_tool_iters: int = 6
    agent_turn_budget_s: float = 20.0

    verdict_signing_key: str = ""
    verdict_token_ttl_s: int = 120

    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""
    payment_link_expiry_minutes: int = 30


settings = Settings()
