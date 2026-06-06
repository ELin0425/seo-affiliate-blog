#!/usr/bin/env python3
"""
Pinterest Pin Generator
Reads published blog posts, generates 2 pins per product:
  - Article pin  → links to the blog post roundup
  - Product pin  → links directly to the Amazon affiliate URL

Output: pins/images/*.png  +  pins/queue.json  (ready for post_pins.py)

Usage:
  python pins.py          # process all unqueued posts
  python pins.py --post   # process + immediately post (needs PINTEREST_TOKEN in .env)
"""

import json
import os
import re
import sys
import textwrap
from io import BytesIO
from pathlib import Path

import requests
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

BLOG_REPO   = Path(os.getenv("BLOG_REPO_PATH", r"C:\Users\linse\projects\passive-income\kitchen-finds"))
PINS_DIR    = Path("pins")
IMAGES_DIR  = PINS_DIR / "images"
QUEUE_FILE  = PINS_DIR / "queue.json"
BLOG_BASE   = "https://elin0425.github.io/kitchen-finds"
AFFILIATE_TAG = "merrieri0a-20"

PIN_W, PIN_H = 1000, 1500

# ── Fonts ─────────────────────────────────────────────────────────────────────

def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = []
    if bold:
        candidates += [
            r"C:\Windows\Fonts\arialbd.ttf",
            r"C:\Windows\Fonts\calibrib.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]
    else:
        candidates += [
            r"C:\Windows\Fonts\arial.ttf",
            r"C:\Windows\Fonts\calibri.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


# ── Amazon image fetch ────────────────────────────────────────────────────────

def fetch_product_image(asin: str) -> Image.Image | None:
    """Fetch the main product image from Amazon. Returns None on failure."""
    try:
        resp = requests.get(
            f"https://www.amazon.com/dp/{asin}",
            timeout=12,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        # Amazon embeds the hi-res image URL in the page's JSON data
        match = re.search(r'"large"\s*:\s*"(https://m\.media-amazon\.com/images/I/[^"]+)"', resp.text)
        if not match:
            # fallback: og:image tag
            match = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', resp.text)
        if match:
            img_url = match.group(1)
            img_resp = requests.get(img_url, timeout=10)
            return Image.open(BytesIO(img_resp.content)).convert("RGB")
    except Exception:
        pass
    return None


def _placeholder_image(product_name: str) -> Image.Image:
    """Generate a simple placeholder when product image isn't available."""
    img = Image.new("RGB", (PIN_W, 900), "#2d3561")
    draw = ImageDraw.Draw(img)
    font = _load_font(48, bold=True)
    lines = textwrap.wrap(product_name, width=22)
    y = 400 - len(lines) * 30
    for line in lines:
        w = draw.textlength(line, font=font)
        draw.text(((PIN_W - w) / 2, y), line, fill="white", font=font)
        y += 70
    return img


# ── Text helpers ──────────────────────────────────────────────────────────────

def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int, max_lines: int = 3) -> list[str]:
    words = text.split()
    lines, current = [], ""
    for word in words:
        test = f"{current} {word}".strip()
        if draw.textlength(test, font=font) <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
        if len(lines) == max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    return lines


# ── Pin designs ───────────────────────────────────────────────────────────────

def make_article_pin(article_title: str, product_image: Image.Image | None) -> Image.Image:
    """
    Article pin — designed to look like a roundup/guide.
    Top: product image (or gradient). Bottom panel: red, article title + CTA.
    """
    img = Image.new("RGB", (PIN_W, PIN_H), "#0f0f0f")
    draw = ImageDraw.Draw(img)

    # Top image area (top 58% of pin)
    top_h = 870
    src = product_image if product_image else _placeholder_image(article_title)
    src = src.copy()
    src.thumbnail((PIN_W, top_h), Image.LANCZOS)
    paste_x = (PIN_W - src.width) // 2
    paste_y = (top_h - src.height) // 2
    img.paste(src, (paste_x, paste_y))

    # Gradient overlay at the bottom of the image for smooth transition
    for y in range(top_h - 120, top_h):
        alpha = int(255 * (y - (top_h - 120)) / 120)
        draw.line([(0, y), (PIN_W, y)], fill=(20, 20, 20, alpha))

    # Red bottom panel
    draw.rectangle([0, top_h - 10, PIN_W, PIN_H], fill="#e63946")

    # Article title (bold, white)
    font_title = _load_font(52, bold=True)
    font_cta   = _load_font(40)
    font_brand = _load_font(28)

    title_lines = _wrap_text(draw, article_title, font_title, PIN_W - 80, max_lines=3)
    y = top_h + 20
    for line in title_lines:
        draw.text((40, y), line, fill="white", font=font_title)
        y += 68

    # CTA
    draw.text((40, y + 24), "Read the full guide  →", fill="#fde68a", font=font_cta)

    # Brand name bottom-right
    brand = "Kitchen Finds"
    bw = draw.textlength(brand, font=font_brand)
    draw.text((PIN_W - bw - 30, PIN_H - 48), brand, fill=(255, 255, 255, 160), font=font_brand)

    return img


def make_product_pin(product_name: str, affiliate_url: str, product_image: Image.Image | None) -> Image.Image:
    """
    Product pin — individual product spotlight, links directly to Amazon.
    Top: large product image. Bottom: dark panel with name, badge, CTA.
    """
    img = Image.new("RGB", (PIN_W, PIN_H), "#ffffff")
    draw = ImageDraw.Draw(img)

    # Top product image (top 63%)
    top_h = 940
    src = product_image if product_image else _placeholder_image(product_name)
    src = src.copy()
    src.thumbnail((PIN_W, top_h), Image.LANCZOS)
    paste_x = (PIN_W - src.width) // 2
    paste_y = (top_h - src.height) // 2
    img.paste(src, (paste_x, paste_y))

    # Clean white background for bottom panel
    draw.rectangle([0, top_h, PIN_W, PIN_H], fill="#1a1a2e")

    font_name  = _load_font(48, bold=True)
    font_badge = _load_font(38, bold=True)
    font_cta   = _load_font(40)
    font_brand = _load_font(28)

    # Product name
    name_lines = _wrap_text(draw, product_name, font_name, PIN_W - 80, max_lines=2)
    y = top_h + 30
    for line in name_lines:
        draw.text((40, y), line, fill="white", font=font_name)
        y += 62

    # "Under $50" badge
    badge_text = "Under $50"
    badge_w = int(draw.textlength(badge_text, font=font_badge)) + 30
    draw.rounded_rectangle([40, y + 16, 40 + badge_w, y + 72], radius=8, fill="#e63946")
    draw.text((55, y + 20), badge_text, fill="white", font=font_badge)
    y += 95

    # Amazon CTA
    draw.text((40, y + 10), "Check price on Amazon  →", fill="#93c5fd", font=font_cta)

    # Brand
    brand = "Kitchen Finds"
    bw = draw.textlength(brand, font=font_brand)
    draw.text((PIN_W - bw - 30, PIN_H - 48), brand, fill=(255, 255, 255, 130), font=font_brand)

    return img


# ── Blog post parser ──────────────────────────────────────────────────────────

def parse_posts() -> list[dict]:
    """Read all blog posts and extract title, URL, and product ASINs."""
    posts_dir = BLOG_REPO / "_posts"
    if not posts_dir.exists():
        print(f"  No _posts directory found at {posts_dir}")
        return []

    posts = []
    for md_file in sorted(posts_dir.glob("*.md")):
        content = md_file.read_text(encoding="utf-8")

        # Extract Jekyll frontmatter title
        title_match = re.search(r'^title:\s*"(.+?)"', content, re.MULTILINE)
        title = title_match.group(1) if title_match else md_file.stem

        # Build the live URL from the filename (YYYY-MM-DD-slug.md)
        stem = md_file.stem
        date_match = re.match(r"(\d{4})-(\d{2})-(\d{2})-(.*)", stem)
        if date_match:
            y, m, d, slug = date_match.groups()
            url = f"{BLOG_BASE}/{y}/{m}/{d}/{slug}/"
        else:
            url = f"{BLOG_BASE}/{stem}/"

        # Build a list of (position, heading_text) for all H3s in the article
        h3_positions = [
            (m.start(), m.group(1).strip())
            for m in re.finditer(r"^###\s+(.+)$", content, re.MULTILINE)
        ]

        # Extract product ASINs + affiliate URLs, matching each to the nearest H3 above it
        products = []
        for link_match in re.finditer(
            r"\[.*?Check.*?Amazon.*?\]\((https://www\.amazon\.com/dp/([A-Z0-9]{10})[^)]*)\)",
            content, re.IGNORECASE,
        ):
            affiliate_url = link_match.group(1)
            asin = link_match.group(2)
            pos = link_match.start()

            # Find the last H3 that appears before this link
            name = f"Product {asin}"
            for h3_pos, h3_text in h3_positions:
                if h3_pos < pos:
                    name = h3_text
                else:
                    break

            if not any(p["asin"] == asin for p in products):
                products.append({"name": name, "asin": asin, "url": affiliate_url})

        if products:
            posts.append({"title": title, "url": url, "slug": stem, "products": products})

    return posts


# ── Queue helpers ─────────────────────────────────────────────────────────────

def load_queue() -> list[dict]:
    if QUEUE_FILE.exists():
        return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    return []


def save_queue(queue: list[dict]):
    QUEUE_FILE.write_text(json.dumps(queue, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Main ──────────────────────────────────────────────────────────────────────

def generate_pins():
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    queue = load_queue()
    queued_keys = {p["key"] for p in queue}

    posts = parse_posts()
    if not posts:
        print("No blog posts found. Run the blog pipeline first.")
        return

    new_pins = 0
    for post in posts:
        print(f"\nPost: {post['title']}")

        # Fetch first product image — reused for the article pin
        first_product = post["products"][0]
        print(f"  Fetching image for {first_product['name']}...")
        article_img = fetch_product_image(first_product["asin"])
        if article_img:
            print("  Got image from Amazon")
        else:
            print("  Using placeholder")

        # ── Article pin (one per post) ──
        article_key = f"article_{post['slug']}"
        if article_key not in queued_keys:
            print(f"  Generating article pin...")
            pin = make_article_pin(post["title"], article_img)
            img_path = IMAGES_DIR / f"{article_key}.png"
            pin.save(str(img_path), "PNG")
            queue.append({
                "key": article_key,
                "type": "article",
                "title": post["title"],
                "description": f"Tested picks for apartment cooks and home chefs — all under $50 on Amazon. {post['title']}",
                "link": post["url"],
                "image_path": str(img_path),
                "posted": False,
            })
            queued_keys.add(article_key)
            new_pins += 1

        # ── Product pins (one per product) ──
        for product in post["products"]:
            product_key = f"product_{product['asin']}"
            if product_key in queued_keys:
                continue

            print(f"  Generating product pin for {product['name'][:40]}...")
            prod_img = fetch_product_image(product["asin"]) if product["asin"] != first_product["asin"] else article_img
            pin = make_product_pin(product["name"], product["url"], prod_img)
            img_path = IMAGES_DIR / f"{product_key}.png"
            pin.save(str(img_path), "PNG")
            queue.append({
                "key": product_key,
                "type": "product",
                "title": f"{product['name']} — Under $50 on Amazon",
                "description": f"One of the best kitchen gadgets under $50. Check price on Amazon — {product['name']}.",
                "link": product["url"],
                "image_path": str(img_path),
                "posted": False,
            })
            queued_keys.add(product_key)
            new_pins += 1

    save_queue(queue)
    unposted = sum(1 for p in queue if not p["posted"])
    print(f"\n{'='*50}")
    print(f"  {new_pins} new pins generated")
    print(f"  {unposted} pins total in queue (unposted)")
    print(f"  Queue: {QUEUE_FILE}")
    print(f"{'='*50}\n")
    print("  When your Pinterest token arrives:")
    print("  1. Add PINTEREST_ACCESS_TOKEN to .env")
    print("  2. Run: python post_pins.py")


if __name__ == "__main__":
    generate_pins()
