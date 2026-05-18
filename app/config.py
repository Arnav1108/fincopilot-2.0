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

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
