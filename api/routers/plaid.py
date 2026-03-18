"""Plaid Link flow: create link token → exchange public token → store encrypted access token."""
import os
import logging

import plaid
from plaid.api import plaid_api
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.products import Products
from plaid.model.country_code import CountryCode
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.dependencies import get_current_user
from api.database import get_db
from api.encryption import encrypt
from api import sync, security_log, rate_limit

logger = logging.getLogger("vaultic.plaid")

router = APIRouter(prefix="/api/plaid", tags=["plaid"])


def _get_client():
    env_map = {
        "sandbox": plaid.Environment.Sandbox,
        "development": plaid.Environment.Sandbox,  # newer SDK dropped Development
        "production": plaid.Environment.Production,
    }
    host = env_map.get(os.environ.get("PLAID_ENV", "sandbox"), plaid.Environment.Sandbox)
    config = plaid.Configuration(
        host=host,
        api_key={
            "clientId": os.environ["PLAID_CLIENT_ID"],
            "secret": os.environ["PLAID_SECRET"],
        },
    )
    return plaid_api.PlaidApi(plaid.ApiClient(config))


@router.post("/link-token")
async def create_link_token(_user: str = Depends(get_current_user)):
    try:
        client = _get_client()
        req = LinkTokenCreateRequest(
            user=LinkTokenCreateRequestUser(client_user_id="vaultic-user"),
            client_name="Vaultic",
            products=[Products("transactions"), Products("investments"), Products("liabilities")],
            country_codes=[CountryCode("US")],
            language="en",
        )
        resp = client.link_token_create(req)
        return {"link_token": resp.link_token}
    except plaid.ApiException as e:
        logger.error(f"Plaid link_token error: {e}")
        raise HTTPException(status_code=502, detail="Failed to create Plaid link token")


class ExchangeRequest(BaseModel):
    public_token: str
    institution_id: str | None = None
    institution_name: str | None = None


@router.post("/exchange")
async def exchange_token(body: ExchangeRequest, _user: str = Depends(get_current_user)):
    try:
        client = _get_client()
        resp = client.item_public_token_exchange(
            ItemPublicTokenExchangeRequest(public_token=body.public_token)
        )
        access_token = resp.access_token
        item_id = resp.item_id
    except plaid.ApiException as e:
        logger.error(f"Plaid exchange error: {e}")
        raise HTTPException(status_code=502, detail="Failed to exchange Plaid token")

    with get_db() as conn:
        conn.execute("""
            INSERT INTO plaid_items (item_id, institution_id, institution_name, access_token_enc)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(item_id) DO UPDATE SET
                institution_id = excluded.institution_id,
                institution_name = excluded.institution_name,
                access_token_enc = excluded.access_token_enc
        """, (item_id, body.institution_id, body.institution_name, encrypt(access_token)))

    try:
        sync.sync_all()
    except Exception as e:
        logger.warning(f"Initial sync failed (non-fatal): {e}")

    return {"status": "connected", "item_id": item_id}


@router.post("/sync")
async def trigger_sync(_user: str = Depends(get_current_user)):
    limited, _ = rate_limit.check_sync(_user)
    if limited:
        security_log.log_server_event(f"SYNC_RATE_LIMITED  user={_user}")
        raise HTTPException(status_code=429, detail="Sync rate limit reached. Wait a few minutes.")
    rate_limit.record_sync(_user)
    try:
        sync.sync_all()
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Sync error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/items")
async def list_items(_user: str = Depends(get_current_user)):
    with get_db() as conn:
        items = conn.execute(
            "SELECT id, item_id, institution_name, last_synced_at, created_at FROM plaid_items"
        ).fetchall()
    return [dict(row) for row in items]


@router.delete("/items/{item_id}")
async def remove_item(item_id: str, _user: str = Depends(get_current_user)):
    with get_db() as conn:
        item = conn.execute("SELECT id FROM plaid_items WHERE item_id = ?", (item_id,)).fetchone()
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        conn.execute("UPDATE accounts SET is_active = 0 WHERE plaid_item_id = ?", (item["id"],))
        conn.execute("DELETE FROM plaid_items WHERE item_id = ?", (item_id,))
    return {"status": "removed"}
