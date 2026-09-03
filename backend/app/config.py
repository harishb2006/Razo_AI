from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    offline_mode: bool = False

    mongodb_uri: str = ""
    mongodb_db: str = "razo_ai"
    mongodb_audit_uri: str = ""

    mongo_max_pool_size: int = 10

    api_key: str = "dev-local-key"
    cors_origins: list[str] = ["http://localhost:5173"]

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
