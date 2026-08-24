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


settings = Settings()
