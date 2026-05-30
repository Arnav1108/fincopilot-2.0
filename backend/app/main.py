import os
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import router
from app.api.v1.router import router as v1_router
from app.config import settings
from app.logging import setup_logging
from app.middleware.logging import RequestLoggingMiddleware

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    os.environ["LANGCHAIN_API_KEY"] = settings.LANGSMITH_API_KEY
    os.environ["LANGCHAIN_TRACING_V2"] = "true" if settings.LANGCHAIN_TRACING_V2 else "false"
    os.environ["LANGCHAIN_PROJECT"] = settings.LANGSMITH_PROJECT

    REQUIRED = [
        ("OPENAI_API_KEY", "OpenAI API key"),
        ("CLERK_SECRET_KEY", "Clerk secret key"),
        ("CLERK_JWKS_URL", "Clerk JWKS URL"),
    ]
    missing = [label for field, label in REQUIRED if not getattr(settings, field, None)]
    if missing:
        logger.critical("startup_validation_failed", missing_fields=missing)
        raise RuntimeError(
            "Startup validation failed — missing required configuration: "
            + ", ".join(missing)
        )
    logger.info("startup_validation_passed")

    yield


app = FastAPI(title="FinCopilot API", version="2.0.0", lifespan=lifespan, redirect_slashes=False)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestLoggingMiddleware)  # outermost — runs first on every request

app.include_router(router, prefix="/api")
app.include_router(v1_router, prefix="/api/v1")
