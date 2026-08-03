#!/usr/bin/env python3
"""
Pinterest Pin Poster
Reads pins/queue.json and posts unposted pins to Pinterest.

Requires PINTEREST_ACCESS_TOKEN in .env (get from Pinterest developer app
after Standard Access is approved → Configure → Generate Access Token).

Usage:
  python post_pins.py           # post all unposted pins (1/day pacing)
  python post_pins.py --all     # post all at once (skip pacing)
  python post_pins.py --dry-run # preview what would be posted
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

import pinterest_auth
from pins import fetch_product_price

load_dotenv()

QUEUE_FILE = Path("pins/queue.json")
BOARD_NAME = "Kitchen Gadgets Under $50"
PINTEREST_API = "https://api.pinterest.com/v5"

# ── Auth ──────────────────────────────────────────────────────────────────────

def get_token() -> str:
    try:
        return pinterest_auth.get_valid_token()
    except RuntimeError as e:
        print(f"Error: {e}")
        sys.exit(1)


# ── Board lookup ──────────────────────────────────────────────────────────────

def get_or_create_board(token: str) -> str:
    """Return the board ID for BOARD_NAME, creating it if it doesn't exist."""
    headers = {"Authorization": f"Bearer {token}"}

    resp = requests.get(f"{PINTEREST_API}/boards", headers=headers, timeout=10)
    resp.raise_for_status()
    boards = resp.json().get("items", [])

    for board in boards:
        if board["name"].lower() == BOARD_NAME.lower():
            print(f"  Using board: {board['name']} ({board['id']})")
            return board["id"]

    # Create it
    print(f"  Creating board: {BOARD_NAME}")
    resp = requests.post(
        f"{PINTEREST_API}/boards",
        headers=headers,
        json={"name": BOARD_NAME, "description": "The best kitchen gadgets under $50 — tested picks with Amazon affiliate links."},
        timeout=10,
    )
    resp.raise_for_status()
    board_id = resp.json()["id"]
    print(f"  Created board ID: {board_id}")
    return board_id


# ── Upload image ──────────────────────────────────────────────────────────────

def upload_image(token: str, image_path: str) -> str:
    """Register an image with Pinterest and return the media ID."""
    headers = {"Authorization": f"Bearer {token}"}

    # Step 1: Register upload
    resp = requests.post(
        f"{PINTEREST_API}/media",
        headers=headers,
        json={"media_type": "video"},  # Pinterest uses 'video' type for image uploads too
        timeout=10,
    )
    # Simpler: use image_url if image is publicly accessible, or image_base64 for local files
    # For local files we use image_base64 (base64-encoded PNG)
    import base64
    with open(image_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode()

    return image_b64


# ── Post a single pin ─────────────────────────────────────────────────────────

def post_pin(token: str, board_id: str, pin: dict, dry_run: bool = False) -> str:
    """Post one pin to Pinterest. Returns 'posted', 'skipped_price', or 'failed'."""
    import base64

    if dry_run:
        print(f"  [DRY RUN] Would post: {pin['title'][:60]}")
        print(f"    Type: {pin['type']} | Link: {pin['link'][:60]}")
        return "posted"

    # Final live price re-check right before posting -- pins can sit in the
    # queue for days/weeks, and prices drift in that window (this is exactly
    # how a false "Under $50" claim went live on 2026-07-23).
    if pin["type"] == "product":
        ceiling_match = re.search(r"Under \$(\d+)", pin["title"])
        asin_match = re.search(r"amazon\.com/dp/([A-Z0-9]{10})", pin["link"])
        if ceiling_match and asin_match:
            ceiling = int(ceiling_match.group(1))
            price = fetch_product_price(asin_match.group(1))
            if price is None:
                time.sleep(5)  # one retry in case of a transient rate-limit
                price = fetch_product_price(asin_match.group(1))

            if price is not None and price > ceiling:
                print(f"  SKIPPED (price now ${price:.2f}, over ${ceiling}): {pin['title'][:55]}")
                return "skipped_price"
            if price is None:
                print(f"  Could not verify current price after retry, not risking a false claim: {pin['title'][:50]}")
                return "failed"  # stays in queue, retry next run rather than permanently skipping

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Load image as base64
    try:
        with open(pin["image_path"], "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode()
    except FileNotFoundError:
        print(f"  Image not found: {pin['image_path']} — run pins.py first")
        return "failed"

    payload = {
        "board_id": board_id,
        "title": pin["title"],
        "description": pin["description"],
        "link": pin["link"],
        "media_source": {
            "source_type": "image_base64",
            "content_type": "image/png",
            "data": image_b64,
        },
    }

    resp = requests.post(f"{PINTEREST_API}/pins", headers=headers, json=payload, timeout=30)

    if resp.status_code == 201:
        pin_id = resp.json().get("id", "?")
        print(f"  Posted: {pin['title'][:55]} (ID: {pin_id})")
        return "posted"
    else:
        print(f"  Failed ({resp.status_code}): {resp.text[:120]}")
        return "failed"


# ── Main ──────────────────────────────────────────────────────────────────────

def run(post_all: bool = False, dry_run: bool = False):
    if not QUEUE_FILE.exists():
        print("No queue found. Run pins.py first.")
        return

    queue = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    unposted = [p for p in queue if not p["posted"] and not p.get("skipped_price")]

    if not unposted:
        print("All pins already posted. Run pins.py to generate more.")
        return

    print(f"{len(unposted)} pins to post\n")

    token = get_token()
    board_id = get_or_create_board(token)

    # Posting strategy: alternate article and product pins so the board stays varied
    article_pins  = [p for p in unposted if p["type"] == "article"]
    product_pins  = [p for p in unposted if p["type"] == "product"]
    ordered = []
    while article_pins or product_pins:
        if product_pins:
            ordered.append(product_pins.pop(0))
        if article_pins:
            ordered.append(article_pins.pop(0))
        if product_pins:
            ordered.append(product_pins.pop(0))

    for pin in ordered:
        result = post_pin(token, board_id, pin, dry_run=dry_run)
        if not dry_run:
            if result == "posted":
                pin["posted"] = True
            elif result == "skipped_price":
                pin["skipped_price"] = True

        if not post_all and not dry_run:
            # Save progress after each pin
            QUEUE_FILE.write_text(json.dumps(queue, indent=2, ensure_ascii=False), encoding="utf-8")

        time.sleep(2)  # be gentle with the API

        # One successful post per run is the daily quota -- a failed/skipped pin
        # (e.g. discontinued product) should not block the whole run, so keep
        # trying the next pin in queue order instead of stopping after pin #1.
        if result == "posted" and not post_all:
            break

    if not dry_run:
        QUEUE_FILE.write_text(json.dumps(queue, indent=2, ensure_ascii=False), encoding="utf-8")

    remaining = sum(1 for p in queue if not p["posted"] and not p.get("skipped_price"))
    skipped = sum(1 for p in queue if p.get("skipped_price"))
    if skipped:
        print(f"  {skipped} pins permanently skipped (over their claimed price ceiling)")
    print(f"\n  {remaining} pins still in queue")
    if remaining and not post_all:
        print("  Run again tomorrow or use --all to post everything")


if __name__ == "__main__":
    post_all  = "--all" in sys.argv
    dry_run   = "--dry-run" in sys.argv
    run(post_all=post_all, dry_run=dry_run)
