from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://fincopilot:fincopilot@localhost:5432/fincopilot"
    REDIS_URL: str = "redis://localhost:6379/0"
    CLERK_SECRET_KEY: str = ""
    CLERK_JWKS_URL: str = ""
    OPENAI_API_KEY: str = ""
    LOG_LEVEL: str = "INFO"
    CLERK_WEBHOOK_SECRET: str = ""
    APP_ENV: str = "development"
    MAX_UPLOAD_BYTES: int = 50_000_000
    TAVILY_API_KEY: str = ""
    SEC_EDGAR_CONTACT_EMAIL: str = ""
    langsmith_api_key: str = os.getenv("LANGSMITH_API_KEY", "")
    LANGSMITH_PROJECT: str = "fincopilot-dev"
    LANGCHAIN_TRACING_V2: bool = True

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
