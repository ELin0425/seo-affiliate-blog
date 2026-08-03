#!/usr/bin/env python3
"""Backfill hero images for posts that were published without one."""

import re
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from main import BLOG_REPO, fetch_hero_image

POSTS = [
    ("best air fryers under $50",                 "best-air-fryers-under-50",                  "2026-06-07"),
    ("best meal prep gadgets under $50",           "best-meal-prep-gadgets-under-50",           "2026-06-12"),
    ("best kitchen gadgets for college students",  "best-kitchen-gadgets-for-college-students", "2026-06-15"),
    ("best mini appliances for dorm rooms",        "best-mini-appliances-for-dorm-rooms",       "2026-06-17"),
    ("best knife sharpeners under $50",            "best-knife-sharpeners-under-50",            "2026-06-19"),
]


def inject_image(post_path, image_url: str, hero_block: str):
    text = post_path.read_text(encoding="utf-8")

    # 1. Add image: line to front matter (before faq: or before closing ---)
    if "image:" not in text:
        if "\nfaq:" in text:
            text = text.replace("\nfaq:", f'\nimage: "{image_url}"\nfaq:', 1)
        else:
            # insert before the closing --- of front matter
            parts = text.split("---", 2)
            parts[1] = parts[1].rstrip() + f'\nimage: "{image_url}"\n'
            text = "---".join(parts)

    # 2. Add hero image block at start of article body (after front matter)
    parts = text.split("---", 2)
    if len(parts) == 3 and not parts[2].lstrip().startswith("!"):
        parts[2] = "\n\n" + hero_block + parts[2].lstrip("\n")
        text = "---".join(parts)

    post_path.write_text(text, encoding="utf-8")


def main():
    updated = []

    for topic, slug, date_str in POSTS:
        post_file = BLOG_REPO / "_posts" / f"{date_str}-{slug}.md"
        if not post_file.exists():
            print(f"  SKIP (file not found): {post_file.name}")
            continue

        # Already has an image
        if "image:" in post_file.read_text(encoding="utf-8"):
            print(f"  SKIP (already has image): {post_file.name}")
            continue

        print(f"\n[{slug}]")
        hero_path, credit = fetch_hero_image(topic, slug, date_str)

        if not hero_path:
            print("  No image found — skipping")
            continue

        image_url = f"/assets/images/posts/{hero_path.name}"
        hero_block = f"![{topic}]({image_url})\n*{credit}*\n\n"
        inject_image(post_file, image_url, hero_block)
        print(f"  Injected into {post_file.name}")
        updated.append(post_file.name)

    if not updated:
        print("\nNothing to update.")
        return

    print(f"\nCommitting {len(updated)} updated post(s)...")
    subprocess.run(["git", "add", "-A"], cwd=BLOG_REPO, check=True)
    subprocess.run(
        ["git", "commit", "-m", f"feat: backfill hero images for {len(updated)} posts"],
        cwd=BLOG_REPO, check=True,
    )
    subprocess.run(["git", "push", "origin", "master"], cwd=BLOG_REPO, check=True)
    print("Done — GitHub Pages will rebuild in ~1 minute.")


if __name__ == "__main__":
    main()
