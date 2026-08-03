#!/usr/bin/env python3
"""Retry image fetch for posts that failed with default search terms."""
import base64, os, re, subprocess, sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import anthropic, requests
from dotenv import load_dotenv

load_dotenv()

BLOG_REPO = Path(os.getenv("BLOG_REPO_PATH", r"C:\Users\linse\projects\passive-income\kitchen-finds"))
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

TARGETS = [
    ("kitchen gadgets small apartment cooking",   "best-kitchen-gadgets-for-college-students",  "2026-06-15", "best kitchen gadgets for college students"),
    ("kitchen small appliances counter cooking",  "best-mini-appliances-for-dorm-rooms",        "2026-06-17", "best mini appliances for dorm rooms"),
    ("kitchen knife cutting board food prep",     "best-knife-sharpeners-under-50",             "2026-06-19", "best knife sharpeners under $50"),
]


def qa_image(img_data: bytes) -> bool:
    b64 = base64.standard_b64encode(img_data).decode("utf-8")
    try:
        r = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=10,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": "Does this image show food, cooking, or kitchen items in an appealing, high-quality way? Reply PASS or FAIL only."},
            ]}],
        )
        return "PASS" in r.content[0].text.upper()
    except Exception:
        return False


def fetch(search_query: str, slug: str, date_str: str) -> tuple:
    key = os.getenv("UNSPLASH_ACCESS_KEY")
    resp = requests.get(
        "https://api.unsplash.com/search/photos",
        params={"query": search_query, "per_page": 8, "orientation": "landscape"},
        headers={"Authorization": f"Client-ID {key}"}, timeout=10,
    )
    results = resp.json().get("results", [])
    img_dir = BLOG_REPO / "assets" / "images" / "posts"
    img_dir.mkdir(parents=True, exist_ok=True)

    for r in results:
        photographer = r["user"]["name"]
        username = r["user"]["username"]
        try:
            img_data = requests.get(r["urls"]["regular"], timeout=15).content
        except Exception:
            continue
        print(f"  QA-ing image by {photographer}...")
        if qa_image(img_data):
            img_path = img_dir / f"{date_str}-{slug}.jpg"
            img_path.write_bytes(img_data)
            credit = f"Photo by [{photographer}](https://unsplash.com/@{username}) on [Unsplash](https://unsplash.com)"
            print(f"  PASS — saved {img_path.name}")
            return img_path, credit
        print("  FAIL")
    return None, None


def inject(post_path: Path, image_url: str, hero_block: str):
    text = post_path.read_text(encoding="utf-8")
    if "image:" in text:
        return
    if "\nfaq:" in text:
        text = text.replace("\nfaq:", f'\nimage: "{image_url}"\nfaq:', 1)
    else:
        parts = text.split("---", 2)
        parts[1] = parts[1].rstrip() + f'\nimage: "{image_url}"\n'
        text = "---".join(parts)
    parts = text.split("---", 2)
    if len(parts) == 3 and not parts[2].lstrip().startswith("!"):
        parts[2] = "\n\n" + hero_block + parts[2].lstrip("\n")
        text = "---".join(parts)
    post_path.write_text(text, encoding="utf-8")


def main():
    updated = []
    for search_q, slug, date_str, topic in TARGETS:
        post_file = BLOG_REPO / "_posts" / f"{date_str}-{slug}.md"
        if not post_file.exists():
            print(f"SKIP (not found): {post_file.name}")
            continue
        if "image:" in post_file.read_text(encoding="utf-8"):
            print(f"SKIP (already has image): {post_file.name}")
            continue

        print(f"\n[{slug}]  search: '{search_q}'")
        hero_path, credit = fetch(search_q, slug, date_str)
        if not hero_path:
            print("  No suitable image found")
            continue

        image_url = f"/assets/images/posts/{hero_path.name}"
        inject(post_file, image_url, f"![{topic}]({image_url})\n*{credit}*\n\n")
        print(f"  Injected into {post_file.name}")
        updated.append(post_file.name)

    if not updated:
        print("\nNothing updated.")
        return

    print(f"\nCommitting {len(updated)} post(s)...")
    subprocess.run(["git", "add", "-A"], cwd=BLOG_REPO, check=True)
    subprocess.run(["git", "commit", "-m", f"feat: backfill hero images (retry) for {len(updated)} posts"], cwd=BLOG_REPO, check=True)
    subprocess.run(["git", "push", "origin", "master"], cwd=BLOG_REPO, check=True)
    print("Done.")


if __name__ == "__main__":
    main()
