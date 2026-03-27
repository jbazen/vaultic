"""Shared Plaid API client factory — single source of truth for Plaid configuration."""
import os

import plaid
from plaid.api import plaid_api


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
