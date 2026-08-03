"""
Pinterest OAuth token management. Auto-refreshes the access token using the
saved refresh token, so nothing ever has to notice a 30-day expiry manually.
"""

import base64
import json
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

APP_ID = "1572183"
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pinterest_token.json")
API = "https://api.pinterest.com/v5"
REFRESH_BUFFER_SECONDS = 3 * 86400  # refresh 3 days before actual expiry


def _load() -> dict | None:
    if not os.path.exists(TOKEN_FILE):
        return None
    with open(TOKEN_FILE) as f:
        return json.load(f)


def _save(data: dict):
    data["saved_at"] = time.time()
    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _refresh(refresh_token: str, app_secret: str) -> dict:
    credentials = base64.b64encode(f"{APP_ID}:{app_secret}".encode()).decode()
    resp = requests.post(
        f"{API}/oauth/token",
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def get_valid_token() -> str:
    """Returns a valid access token, refreshing automatically if it's close to expiring."""
    data = _load()
    if not data:
        raise RuntimeError(f"No token found at {TOKEN_FILE}. Run the OAuth flow first.")

    saved_at = data.get("saved_at", 0)
    expires_in = data.get("expires_in", 0)

    if time.time() < saved_at + expires_in - REFRESH_BUFFER_SECONDS:
        return data["access_token"]

    app_secret = os.getenv("PINTEREST_APP_SECRET")
    if not app_secret:
        raise RuntimeError("PINTEREST_APP_SECRET not set in .env -- needed to refresh the token.")

    print("  Pinterest token nearing expiry, refreshing...")
    new_data = _refresh(data["refresh_token"], app_secret)
    # Pinterest's refresh response doesn't always include a fresh refresh_token; keep the old one if so
    if "refresh_token" not in new_data:
        new_data["refresh_token"] = data["refresh_token"]
        new_data["refresh_token_expires_in"] = data.get("refresh_token_expires_in")
    _save(new_data)
    print("  Token refreshed successfully.")
    return new_data["access_token"]
