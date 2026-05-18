import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from svix.webhooks import Webhook, WebhookVerificationError

from app.config import settings
from app.database import get_db
from app.models.user import User

router = APIRouter()


@router.post("/webhooks/clerk")
async def clerk_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    log = structlog.get_logger()

    if not settings.CLERK_WEBHOOK_SECRET:
        log.error("webhook_secret_not_configured")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid webhook signature",
        )

    # Raw bytes must be read before any JSON parsing — Svix signs the raw body
    body = await request.body()
    svix_headers = {
        "svix-id": request.headers.get("svix-id", ""),
        "svix-timestamp": request.headers.get("svix-timestamp", ""),
        "svix-signature": request.headers.get("svix-signature", ""),
    }

    try:
        event = Webhook(settings.CLERK_WEBHOOK_SECRET).verify(body, svix_headers)
    except WebhookVerificationError as exc:
        log.error("webhook_signature_invalid", reason=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid webhook signature",
        )

    event_type = event.get("type")
    data = event.get("data", {})

    if event_type == "user.created":
        clerk_user_id = data["id"]
        email_list = data.get("email_addresses", [])
        email = email_list[0]["email_address"] if email_list else None
        fn = data.get("first_name") or ""
        ln = data.get("last_name") or ""
        display_name = f"{fn} {ln}".strip() or None

        stmt = (
            pg_insert(User)
            .values(clerk_user_id=clerk_user_id, email=email, display_name=display_name)
            .on_conflict_do_nothing(index_elements=["clerk_user_id"])
        )
        await db.execute(stmt)
        await db.commit()
        log.info("webhook_user_created", clerk_user_id=clerk_user_id)

    elif event_type == "user.deleted":
        clerk_user_id = data["id"]
        result = await db.execute(
            delete(User).where(User.clerk_user_id == clerk_user_id)
        )
        await db.commit()
        if result.rowcount == 0:
            log.warning("webhook_user_not_found_for_delete", clerk_user_id=clerk_user_id)
        else:
            log.info("webhook_user_deleted", clerk_user_id=clerk_user_id)

    else:
        # Return 200 for unknown types — Clerk retries on non-200
        log.info("webhook_unhandled_event", event_type=event_type)

    return {"received": True}
