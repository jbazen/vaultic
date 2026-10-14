"""Shared Plaid API client factory — single source of truth for Plaid configuration."""
import json
import os

import plaid
from plaid.api import plaid_api
from plaid.model.item_remove_request import ItemRemoveRequest


def get_plaid_client() -> plaid_api.PlaidApi:
    """Create and return a configured Plaid API client."""
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


def remove_item_at_plaid(client: plaid_api.PlaidApi, access_token: str) -> None:
    """Remove an item at Plaid so it stops billing (/item/remove).

    An item Plaid no longer knows about (ITEM_NOT_FOUND) is already gone, so
    that counts as success. Any other Plaid error is raised to the caller.
    """
    try:
        client.item_remove(ItemRemoveRequest(access_token=access_token))
    except plaid.ApiException as e:
        try:
            code = json.loads(e.body or "{}").get("error_code")
        except ValueError:
            code = None
        if code != "ITEM_NOT_FOUND":
            raise
