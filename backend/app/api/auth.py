import time
from datetime import datetime, timezone

import httpx
import structlog
from fastapi import Depends, HTTPException, Request, status
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.user import User

_jwks_cache: dict | None = None
_jwks_cache_at: float = 0.0
_JWKS_TTL = 300.0  # 5 minutes


async def _get_jwks() -> dict:
    global _jwks_cache, _jwks_cache_at
    if _jwks_cache is None or time.monotonic() - _jwks_cache_at >= _JWKS_TTL:
        async with httpx.AsyncClient() as client:
            resp = await client.get(settings.CLERK_JWKS_URL)
            resp.raise_for_status()
            _jwks_cache = resp.json()
            _jwks_cache_at = time.monotonic()
    return _jwks_cache


async def clerk_auth(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    log = structlog.get_logger()

    # 1. Extract Bearer token
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        log.warning("auth_failed", reason="missing_bearer_token")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    token = auth_header.removeprefix("Bearer ").strip()

    # 2. Validate JWT
    try:
        jwks = await _get_jwks()
        payload = jwt.decode(token, jwks, algorithms=["RS256"], options={"verify_aud": False})
        clerk_user_id: str = payload.get("sub")
        if not clerk_user_id:
            log.warning("auth_failed", reason="missing_sub_claim")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token claims")
    except JWTError as exc:
        log.warning("auth_failed", reason=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token validation failed: {exc}",
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        log.error("jwks_fetch_failed", reason=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth service unavailable",
        ) from exc

    # 3. Upsert: SELECT then INSERT or UPDATE updated_at
    #    Not ON CONFLICT DO UPDATE — must not overwrite email/display_name set by webhook
    result = await db.execute(select(User).where(User.clerk_user_id == clerk_user_id))
    user: User | None = result.scalar_one_or_none()

    if user is None:
        user = User(clerk_user_id=clerk_user_id)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        log.info("user_created_via_auth", clerk_user_id=clerk_user_id, user_id=str(user.id))
    else:
        user.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(user)

    # 4. Active check
    if not user.is_active:
        log.warning("auth_failed", reason="account_disabled", clerk_user_id=clerk_user_id)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")

    # 5. Attach to request state and return
    log.debug("auth_ok", clerk_user_id=clerk_user_id, user_id=str(user.id))
    request.state.user = user
    return user
