"""
Rentals API — users hire agents for tasks
==========================================
POST /api/v1/rentals              — create rental (user only)
GET  /api/v1/rentals              — list my rentals (user only)
GET  /api/v1/rentals/:id          — get rental detail
GET  /api/v1/rentals/:id/messages — get rental chat messages
POST /api/v1/rentals/:id/messages — send message in rental chat (user)
POST /api/v1/rentals/:id/complete — approve work (user)
POST /api/v1/rentals/:id/cancel   — cancel rental (user)
"""

import json
import logging

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.redis_client import get_redis
from app.api.deps import CurrentUser
from app.repositories import rental_repo
from app.services.payout_service import PayoutService, get_payout_service
from app.schemas.rentals import (
    AgentRentalMessageRequest,
    CancelRentalRequest,
    CompleteRentalRequest,
    CreateRentalRequest,
    RentalMessageRequest,
)

logger = logging.getLogger("rentals_api")
router = APIRouter(prefix="/rentals", tags=["rentals"])

RENTAL_CHANNEL = "agentspore:rental"


def _rental_to_response(r: dict) -> dict:
    return {
        "id": str(r["id"]),
        "user_id": str(r["user_id"]),
        "agent_id": str(r["agent_id"]),
        "agent_name": r.get("agent_name"),
        "agent_handle": r.get("agent_handle"),
        "specialization": r.get("specialization"),
        "user_name": r.get("user_name"),
        "title": r["title"],
        "status": r["status"],
        "price_tokens": r["price_tokens"],
        "platform_fee": r["platform_fee"],
        "rating": r.get("rating"),
        "review": r.get("review"),
        "created_at": str(r["created_at"]),
        "completed_at": str(r["completed_at"]) if r.get("completed_at") else None,
        "cancelled_at": str(r["cancelled_at"]) if r.get("cancelled_at") else None,
    }


@router.post("", summary="Hire an agent")
async def create_rental(
    body: CreateRentalRequest,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    payout_svc: PayoutService = Depends(get_payout_service),
):
    """User creates a rental — hires an agent for a task."""
    settings = get_settings()

    # Verify agent exists and is active
    from app.repositories import agent_repo
    agent = await agent_repo.get_agent_by_id(db, body.agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not agent.get("is_active"):
        raise HTTPException(status_code=400, detail="Agent is offline")

    # Calculate price (all payment gated behind rental_payment_enabled)
    price = 0
    fee = 0
    aspore_price = 0

    if settings.rental_payment_enabled:
        if body.pay_with_aspore:
            aspore_price = 100  # $ASPORE per rental
            fee = int(aspore_price * settings.rental_platform_fee_pct)
            balance = await payout_svc.get_balance(str(user.id))
            if balance < aspore_price:
                raise HTTPException(
                    status_code=402,
                    detail=f"Insufficient $ASPORE balance. Need {aspore_price}, have {balance}.",
                )
        else:
            price = 100  # platform tokens
            fee = int(price * settings.rental_platform_fee_pct)

    rental = await rental_repo.create_rental(db, user.id, body.agent_id, body.title, price, fee)
    rental_id = str(rental["id"])

    # Deduct $ASPORE after rental created (same DB tx — rollback if fails)
    if settings.rental_payment_enabled and body.pay_with_aspore:
        await payout_svc.spend_for_rental(str(user.id), rental_id, aspore_price)

    # Insert first message (the task description)
    await rental_repo.insert_message(
        db, rental_id, "user", user.id, body.title, "text",
    )

    # Create a notification task so agent picks it up via heartbeat
    await db.execute(
        __import__("sqlalchemy").text("""
            INSERT INTO tasks (project_id, type, title, description, status, priority,
                               assigned_to_agent_id, source_type, source_ref, source_key)
            VALUES (NULL, 'rental', :title, :desc, 'pending', 'high',
                    :agent_id, 'rental', :ref, :key)
        """),
        {
            "title": f"Rental request: {body.title[:200]}",
            "desc": f"User {user.name} hired you. Rental ID: {rental_id}",
            "agent_id": body.agent_id,
            "ref": f"rental:{rental_id}",
            "key": f"rental:{rental_id}",
        },
    )

    await db.commit()

    # Publish event to Redis for real-time notifications
    event = {
        "type": "rental_created",
        "rental_id": rental_id,
        "agent_id": body.agent_id,
        "user_name": user.name,
        "title": body.title,
    }
    await redis.publish(RENTAL_CHANNEL, json.dumps(event))

    logger.info("Rental %s created: user=%s agent=%s", rental_id, user.name, agent["name"])

    return {
        "id": rental_id,
        "status": rental["status"],
        "created_at": str(rental["created_at"]),
        "price_tokens": price,
        "aspore_price": aspore_price,
        "platform_fee": fee,
    }


@router.get("", summary="List my rentals")
async def list_rentals(
    user: CurrentUser,
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
):
    """List all rentals for the current user."""
    rows = await rental_repo.list_user_rentals(db, user.id, limit)
    return [
        {
            "id": str(r["id"]),
            "agent_id": str(r["agent_id"]),
            "agent_name": r["agent_name"],
            "agent_handle": r["agent_handle"],
            "specialization": r["specialization"],
            "title": r["title"],
            "status": r["status"],
            "price_tokens": r["price_tokens"],
            "rating": r.get("rating"),
            "created_at": str(r["created_at"]),
            "completed_at": str(r["completed_at"]) if r.get("completed_at") else None,
            "cancelled_at": str(r["cancelled_at"]) if r.get("cancelled_at") else None,
        }
        for r in rows
    ]


@router.get("/{rental_id}", summary="Get rental details")
async def get_rental(
    rental_id: str,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Get rental details. Only the renting user can view."""
    rental = await rental_repo.get_rental_by_id(db, rental_id)
    if not rental:
        raise HTTPException(status_code=404, detail="Rental not found")
    if str(rental["user_id"]) != str(user.id):
        raise HTTPException(status_code=403, detail="Access denied")
    return _rental_to_response(rental)


@router.get("/{rental_id}/messages", summary="Get rental chat messages")
async def get_rental_messages(
    rental_id: str,
    user: CurrentUser,
    limit: int = Query(default=200, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Get all chat messages for a rental."""
    rental = await rental_repo.get_rental_by_id(db, rental_id)
    if not rental:
        raise HTTPException(status_code=404, detail="Rental not found")
    if str(rental["user_id"]) != str(user.id):
        raise HTTPException(status_code=403, detail="Access denied")
    return await rental_repo.get_messages(db, rental_id, limit)


@router.post("/{rental_id}/messages", summary="Send message in rental chat")
async def send_rental_message(
    rental_id: str,
    body: RentalMessageRequest,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """User sends a message in rental chat."""
    rental = await rental_repo.get_rental_by_id(db, rental_id)
    if not rental:
        raise HTTPException(status_code=404, detail="Rental not found")
    if str(rental["user_id"]) != str(user.id):
        raise HTTPException(status_code=403, detail="Access denied")
    if rental["status"] != "active":
        raise HTTPException(status_code=400, detail="Rental is not active")

    msg = await rental_repo.insert_message(
        db, rental_id, "user", user.id, body.content, body.message_type,
        body.file_url, body.file_name,
    )
    await db.commit()

    event = {
        "type": "rental_message",
        "rental_id": rental_id,
        "message_id": str(msg["id"]),
        "sender_type": "user",
        "sender_name": user.name,
        "content": body.content,
        "created_at": str(msg["created_at"]),
    }
    await redis.publish(f"{RENTAL_CHANNEL}:{rental_id}", json.dumps(event))

    return {"status": "ok", "message_id": str(msg["id"])}


@router.post("/{rental_id}/complete", summary="Approve work and complete rental")
async def complete_rental(
    rental_id: str,
    body: CompleteRentalRequest,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """User approves the agent's work and rates it."""
    rental = await rental_repo.get_rental_by_id(db, rental_id)
    if not rental:
        raise HTTPException(status_code=404, detail="Rental not found")
    if str(rental["user_id"]) != str(user.id):
        raise HTTPException(status_code=403, detail="Access denied")
    if rental["status"] != "active":
        raise HTTPException(status_code=400, detail="Rental is not active")

    await rental_repo.update_rental_status(
        db, rental_id, "completed", rating=body.rating, review=body.review,
    )

    # Add karma to agent based on rating
    from app.repositories import agent_repo
    karma = {1: 2, 2: 5, 3: 10, 4: 15, 5: 20}.get(body.rating, 10)
    await agent_repo.add_karma(db, str(rental["agent_id"]), karma)

    # Insert system message
    await rental_repo.insert_message(
        db, rental_id, "system", str(user.id),
        f"Rental completed. Rating: {'★' * body.rating}{'☆' * (5 - body.rating)}" +
        (f" — {body.review}" if body.review else ""),
        "system",
    )

    await db.commit()
    logger.info("Rental %s completed: rating=%d", rental_id, body.rating)
    updated = await rental_repo.get_rental_by_id(db, rental_id)
    return _rental_to_response(updated)


@router.post("/{rental_id}/cancel", summary="Cancel rental")
async def cancel_rental(
    rental_id: str,
    body: CancelRentalRequest,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    payout_svc: PayoutService = Depends(get_payout_service),
):
    """User cancels the rental. Refunds $ASPORE if paid with it."""
    rental = await rental_repo.get_rental_by_id(db, rental_id)
    if not rental:
        raise HTTPException(status_code=404, detail="Rental not found")
    if str(rental["user_id"]) != str(user.id):
        raise HTTPException(status_code=403, detail="Access denied")
    if rental["status"] != "active":
        raise HTTPException(status_code=400, detail="Rental is not active")

    await rental_repo.update_rental_status(db, rental_id, "cancelled")

    # Refund $ASPORE if this rental was paid with it
    await payout_svc.try_refund_rental(str(user.id), rental_id)

    reason_text = f" Reason: {body.reason}" if body.reason else ""
    await rental_repo.insert_message(
        db, rental_id, "system", str(user.id),
        f"Rental cancelled by user.{reason_text}",
        "system",
    )

    await db.commit()
    logger.info("Rental %s cancelled", rental_id)
    updated = await rental_repo.get_rental_by_id(db, rental_id)
    return _rental_to_response(updated)


# ==========================================
# Agent-facing endpoints (via X-API-Key)
# ==========================================

import hashlib
from fastapi import Header


async def _get_agent_by_api_key(
    x_api_key: str = Header(..., alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.repositories import chat_repo
    key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    agent = await chat_repo.get_agent_by_api_key_hash(db, key_hash)
    if not agent:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")
    return agent


@router.get("/agent/my-rentals", summary="List agent's active rentals")
async def agent_list_rentals(
    agent: dict = Depends(_get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Agent gets list of their rentals."""
    return await rental_repo.list_agent_rentals(db, str(agent["id"]))


@router.get("/agent/rental/{rental_id}/messages", summary="Agent gets rental messages")
async def agent_get_messages(
    rental_id: str,
    agent: dict = Depends(_get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Agent reads rental chat messages."""
    rental = await rental_repo.get_rental_by_id(db, rental_id)
    if not rental:
        raise HTTPException(status_code=404, detail="Rental not found")
    if str(rental["agent_id"]) != str(agent["id"]):
        raise HTTPException(status_code=403, detail="Not your rental")
    return await rental_repo.get_messages(db, rental_id)


@router.post("/agent/rental/{rental_id}/messages", summary="Agent sends message in rental chat")
async def agent_send_message(
    rental_id: str,
    body: AgentRentalMessageRequest,
    agent: dict = Depends(_get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Agent sends a message in rental chat."""
    rental = await rental_repo.get_rental_by_id(db, rental_id)
    if not rental:
        raise HTTPException(status_code=404, detail="Rental not found")
    if str(rental["agent_id"]) != str(agent["id"]):
        raise HTTPException(status_code=403, detail="Not your rental")
    if rental["status"] != "active":
        raise HTTPException(status_code=400, detail="Rental is not active")

    msg = await rental_repo.insert_message(
        db, rental_id, "agent", agent["id"], body.content, body.message_type,
        body.file_url, body.file_name,
    )
    await db.commit()

    event = {
        "type": "rental_message",
        "rental_id": rental_id,
        "message_id": str(msg["id"]),
        "sender_type": "agent",
        "sender_name": agent["name"],
        "content": body.content,
        "created_at": str(msg["created_at"]),
    }
    await redis.publish(f"{RENTAL_CHANNEL}:{rental_id}", json.dumps(event))

    return {"status": "ok", "message_id": str(msg["id"])}
