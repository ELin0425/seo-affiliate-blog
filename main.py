#!/usr/bin/env python3
"""
SEO Affiliate Blog Pipeline — Kitchen Gadgets Under $50

Generates SEO-optimized affiliate articles, QA-reviews them for readability,
and saves them as publish-ready markdown files.

Usage:
  python main.py                                  # next topic from topics.txt
  python main.py "best air fryers under $50"      # specific topic
"""

import base64
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import anthropic
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

# Force UTF-8 output so → and other Unicode chars don't crash on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

AFFILIATE_TAG = "merrieri0a-20"
OUTPUT_DIR = Path("articles")
TOPICS_FILE = Path("topics.txt")
MODEL = "claude-sonnet-4-6"
BLOG_REPO = Path(os.getenv("BLOG_REPO_PATH", r"C:\Users\linse\projects\passive-income\kitchen-finds"))

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

# ── Topic Management ──────────────────────────────────────────────────────────

class SkipTopicError(Exception):
    """Raised when a topic can't be processed and the pipeline should try the next one."""


def get_candidate_topics() -> list[str]:
    """Return all unprocessed topics from topics.txt, in order.

    Always excludes topics already marked DONE: in topics.txt AND topics that
    already have a matching post file in _posts/ (catches duplicates on local runs too).
    """
    lines = TOPICS_FILE.read_text(encoding="utf-8").splitlines()
    candidates = [
        l.strip() for l in lines
        if l.strip() and not l.strip().startswith("#") and not l.strip().startswith("DONE:")
    ]

    if not candidates:
        raise ValueError("No more topics in topics.txt — add some!")

    published = {
        re.sub(r"^\d{4}-\d{2}-\d{2}-", "", f.stem)
        for f in (BLOG_REPO / "_posts").glob("*.md")
    } if (BLOG_REPO / "_posts").exists() else set()

    unpublished = [
        t for t in candidates
        if re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")[:60] not in published
    ]

    if not unpublished:
        raise ValueError("All topics already published — add new ones to topics.txt!")

    return unpublished


def mark_topic_done(topic: str) -> None:
    """Mark a topic as DONE in topics.txt (local runs only)."""
    if os.getenv("GITHUB_ACTIONS"):
        return
    lines = TOPICS_FILE.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.strip() == topic:
            lines[i] = f"DONE: {topic}"
            TOPICS_FILE.write_text("\n".join(lines), encoding="utf-8")
            return


# ── Competitor Research ───────────────────────────────────────────────────────

def search_competitors(keyword: str) -> list[dict]:
    """Search DuckDuckGo for top competing articles and extract their structure."""
    print("  Searching competitor articles...")
    results = []
    skip_domains = ["amazon.com", "reddit.com", "quora.com", "youtube.com", "pinterest.com", "walmart.com"]

    try:
        with DDGS() as ddgs:
            hits = list(ddgs.text(f"{keyword} blog best", max_results=8))

        for hit in hits:
            url = hit.get("href", "")
            if any(d in url for d in skip_domains):
                continue

            structure = _fetch_page_structure(url)
            if structure:
                results.append(structure)
                print(f"    Got structure from {url[:60]}")

            if len(results) >= 2:
                break

            time.sleep(1.5)
    except Exception as e:
        print(f"  Competitor search failed (non-fatal): {e}")

    return results


def _fetch_page_structure(url: str) -> dict | None:
    """Fetch a competitor page and extract headings + intro text."""
    try:
        resp = requests.get(url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        headings = []
        for tag in soup.find_all(["h1", "h2", "h3"]):
            text = tag.get_text(strip=True)
            if text and len(text) > 3:
                headings.append(f"{tag.name.upper()}: {text}")

        paragraphs = [p.get_text(strip=True) for p in soup.find_all("p") if len(p.get_text(strip=True)) > 60]
        intro = " ".join(paragraphs[:3])[:500]

        if not headings:
            return None

        return {
            "url": url,
            "headings": headings[:20],
            "intro": intro,
        }
    except Exception:
        return None


# ── Product Research ──────────────────────────────────────────────────────────

def research_products(keyword: str) -> list[dict]:
    """Find Amazon products with real ASINs via web search."""
    print("  Researching products and ASINs...")
    products = []
    seen_asins = set()

    # Try two different search angles to get a good product mix
    queries = [
        f"site:amazon.com {keyword}",
        f"amazon best {keyword} reviews",
    ]

    for query in queries:
        if len(products) >= 7:
            break
        try:
            with DDGS() as ddgs:
                hits = list(ddgs.text(query, max_results=12))

            for hit in hits:
                url = hit.get("href", "")
                asin = _extract_asin(url)
                if not asin or asin in seen_asins:
                    continue

                title = _clean_title(hit.get("title", ""))
                if not title:
                    continue

                seen_asins.add(asin)
                products.append({
                    "name": title,
                    "asin": asin,
                    "url": f"https://www.amazon.com/dp/{asin}?tag={AFFILIATE_TAG}",
                    "snippet": hit.get("body", "")[:120],
                })

        except Exception as e:
            print(f"  Product search failed for '{query}' (non-fatal): {e}")

        time.sleep(1)

    if len(products) < 3:
        raise SkipTopicError(
            f"Product research for '{keyword}' found only {len(products)} valid product(s) — skipping to next topic."
        )

    # Validate all ASINs are live before handing them to the writer
    print("  Validating product links...")
    valid_products = []
    for p in products:
        if _validate_asin(p["asin"]):
            valid_products.append(p)
        else:
            print(f"  Skipping dead ASIN {p['asin']} ({p['name'][:50]})")
    products = valid_products

    if len(products) < 2:
        raise ValueError(f"Only {len(products)} valid product(s) found — cannot write a useful article. Check your search queries or topics.txt.")

    if len(products) < 3:
        print(f"  Warning: only {len(products)} valid products found — article may be short.")

    print(f"  Found {len(products)} valid products")
    return products[:8]


def _extract_asin(url: str) -> str | None:
    """Pull the 10-char ASIN from an Amazon URL."""
    match = re.search(r"/dp/([A-Z0-9]{10})", url)
    return match.group(1) if match else None


def _validate_asin(asin: str) -> bool:
    """Return True if the Amazon product page returns HTTP 200."""
    url = f"https://www.amazon.com/dp/{asin}"
    try:
        resp = requests.get(url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }, stream=True)
        resp.close()
        return resp.status_code == 200
    except Exception:
        return False


def _clean_title(title: str) -> str:
    """Strip Amazon boilerplate from a product title."""
    title = re.sub(r"\s*[\|\-]\s*Amazon\.com.*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*:\s*Amazon\.com.*$", "", title, flags=re.IGNORECASE)
    return title.strip()[:100]




# ── Hero Image ───────────────────────────────────────────────────────────────

def fetch_hero_image(topic: str, slug: str, date_str: str) -> tuple[Path | None, str | None]:
    """Search Unsplash, QA each candidate with Claude vision, save the first that passes."""
    key = os.getenv("UNSPLASH_ACCESS_KEY")
    if not key:
        print("  No UNSPLASH_ACCESS_KEY in .env — skipping hero image")
        return None, None

    search_query = re.sub(r"\bunder\s*\$\d+\b|\bbest\b", "", topic, flags=re.IGNORECASE).strip()
    search_query = f"kitchen {search_query} food"

    try:
        resp = requests.get(
            "https://api.unsplash.com/search/photos",
            params={"query": search_query, "per_page": 5, "orientation": "landscape"},
            headers={"Authorization": f"Client-ID {key}"},
            timeout=10,
        )
        results = resp.json().get("results", [])
    except Exception as e:
        print(f"  Unsplash search failed: {e}")
        return None, None

    img_dir = BLOG_REPO / "assets" / "images" / "posts"
    img_dir.mkdir(parents=True, exist_ok=True)

    for result in results[:3]:
        photographer = result["user"]["name"]
        username = result["user"]["username"]
        try:
            img_data = requests.get(result["urls"]["regular"], timeout=15).content
        except Exception:
            continue

        print(f"  QA-ing image by {photographer}...")
        if _qa_image(img_data, topic):  # uses Haiku — just PASS/FAIL
            img_path = img_dir / f"{date_str}-{slug}.jpg"
            img_path.write_bytes(img_data)
            credit = f"Photo by [{photographer}](https://unsplash.com/@{username}) on [Unsplash](https://unsplash.com)"
            print(f"  PASS — saved {img_path.name}")
            return img_path, credit
        else:
            print("  FAIL — trying next candidate")

    print("  No suitable hero image found after 3 candidates")
    return None, None


def _qa_image(img_data: bytes, topic: str) -> bool:
    """Claude vision check — PASS if image is relevant and appealing for this topic."""
    b64 = base64.standard_b64encode(img_data).decode("utf-8")
    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=20,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                    {"type": "text", "text": (
                        f"Blog post topic: '{topic}'. "
                        "Does this image show food, cooking, or kitchen items in an appealing, high-quality way? "
                        "Reply PASS or FAIL only."
                    )},
                ],
            }],
        )
        return "PASS" in response.content[0].text.upper()
    except Exception:
        return False


# ── Article Writer ────────────────────────────────────────────────────────────

WRITER_SYSTEM = """\
You are a seasoned affiliate content writer who has been writing kitchen product roundups for eight years.
Your articles rank on Google, get shared on Pinterest, and actually help real people buy better things.

You write like a knowledgeable friend who has done the legwork — not a product catalog, not a press release.
Your voice is warm, direct, and occasionally dry. You never write filler. Every sentence earns its place.

ARTICLE STRUCTURE (follow this exactly):

1. **H1 title** — keyword-rich, compelling, specific. CRITICAL: the exact product count is stated in the prompt. Use ONLY that number in the title. If 3 products are listed, the title must say "3". If 5 products, say "5". Never use any other number. Check: count your H3 product sections and verify they match the number in your H1 before finishing.

2. **Intro** (2–3 short paragraphs) — open with a relatable frustration or surprising fact, then promise what they'll leave with. No "In this article we will..." openers.

3. **Quick Picks** — a short bolded list of all products. Format each line as:
   - **[Label]:** [Product Name]: [one-line reason]
   Use labels like: Best overall, Best for beginners, Best compact, Best value, Best no-frills, etc.

4. **Comparison table** — a markdown table immediately after Quick Picks. Columns: Product | Best For | Capacity | Key Perk. Fill in what you know from the product context; use "N/A" if a spec is unknown. Example:
   | Product | Best For | Capacity | Key Perk |
   |---|---|---|---|
   | DASH Tasti-Crisp | Beginners | 2.6 Qt | One-dial simplicity |

5. **H2: How We Picked These** — 3–4 sentences on your selection criteria. Short, credible, no fluff.

6. **H2: The Best [Topic]** — the main product list. For each product:
   - **H3** with the product name
   - 2–3 sentences: what problem it solves + one specific standout detail (not vague praise)
   - `**Best for:**` [one specific use case, inline bold, NOT a heading] e.g. "**Best for:** solo cooks who hate reading manuals"
   - **Pros:** 2–3 bullet points, concrete and specific (e.g. "Basket fits a full chicken breast", not "good capacity")
   - **Cons:** 1–2 bullet points, honest (e.g. "2.6 Qt feels cramped cooking for 3+")
   - Affiliate link on its own line: `[→ Check price on Amazon](URL)`

7. **H2: What to Skip** — 2–3 short bullets on things to avoid in this category (builds trust)

8. **H2: Frequently Asked Questions** — exactly 3 Q&As targeting real search queries about this topic

9. **H2: The Bottom Line** — 2–3 sentences wrapping up

SEO RULES:
- Target keyword appears in H1, first 100 words, and at least 2 H2 headings
- Use natural keyword variations — don't repeat the exact phrase more than 3 times
- Aim for 1300–1600 words total (the comparison table and pros/cons add length)
- At the very end, add: <!-- META: your 150-char meta description here -->

STYLE RULES:
- Never use em dashes (—). Use a comma, colon, or rewrite the sentence instead.

Output clean markdown only. No preamble.\
"""


GUIDE_SYSTEM = """\
You are an experienced home cook who has been writing practical kitchen guides for ten years.
Your articles are read by real people who want clear, useful answers, not padding.

You write like a knowledgeable friend: direct, warm, occasionally dry. Every sentence earns its place.

ARTICLE STRUCTURE (follow this exactly):

1. **H1 title** — keyword-rich, specific, and useful-sounding. No clickbait.

2. **Intro** (2–3 short paragraphs) — open with a relatable frustration or a surprising fact.
   Hook the reader in the first sentence. No "In this article we will..." openers.

3. **H2 sections** covering the full topic in logical order. Use as many H2s as the topic needs.
   Each section should be 2–4 short paragraphs. Use H3s only when a section has distinct sub-steps.
   Write in plain English. If there are steps, number them. If there are options, compare them honestly.

4. **H2: Frequently Asked Questions** — exactly 3 Q&As targeting real follow-up searches about this topic.

5. **H2: The Bottom Line** — 2–3 sentences wrapping up the key takeaway.

STYLE RULES:
- No em dashes (—). Use a comma, colon, or rewrite the sentence instead.
- No filler phrases: "With that being said," / "It is worth noting" / "Without further ado"
- No vague advice: say exactly what to do, how long, at what temperature, with what tool
- If a related product is genuinely useful to mention, mention it naturally in context. Do not force it.
- At the natural end of the article (before FAQ), add one short paragraph that links internally to a
  related roundup post on the site if one exists. Keep it 1–2 sentences, not salesy.

SEO RULES:
- Target keyword in H1, first 100 words, and at least 2 H2 headings
- Aim for 900–1200 words total
- At the very end, add: <!-- META: your 150-char meta description here -->

Output clean markdown only. No preamble.\
"""

_INFORMATIONAL_PREFIXES = (
    "how ", "when ", "why ", "what ", "is ", "are ", "does ", "can ",
    "should ", "tips ", "guide ", "the best way",
)


def is_informational(topic: str) -> bool:
    return topic.lower().startswith(_INFORMATIONAL_PREFIXES)


def write_article(topic: str, competitor_data: list, products: list) -> str:
    """Generate the full SEO article with Claude."""
    print("  Writing article draft...")

    competitor_notes = ""
    if competitor_data:
        for comp in competitor_data:
            headings_text = "\n".join(f"  {h}" for h in comp["headings"])
            competitor_notes += f"\nCompetitor: {comp['url'][:70]}\nHeadings:\n{headings_text}\nIntro excerpt: {comp['intro'][:200]}\n"

    product_list = "\n".join(
        f"- {p['name']} | ASIN: {p['asin']} | Affiliate URL: {p['url']}"
        + (f" | Context: {p['snippet']}" if p["snippet"] else "")
        for p in products
    )

    num_products = len(products)
    prompt = f"""Write a complete SEO article for this keyword:

**Target keyword:** {topic}

**Products to feature** — feature ALL {num_products} products listed below, in the order given. Do not skip any. Use their exact affiliate URLs:
{product_list}

**Competitor articles for reference** (study their H2 topics and what they cover; write something better):
{competitor_notes if competitor_notes else "(none found — rely on your expertise)"}

Write the full article now."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=6000,
        system=WRITER_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def write_guide(topic: str, competitor_data: list) -> str:
    """Generate an informational guide (no product list)."""
    print("  Writing guide draft...")

    competitor_notes = ""
    if competitor_data:
        for comp in competitor_data:
            headings_text = "\n".join(f"  {h}" for h in comp["headings"])
            competitor_notes += f"\nCompetitor: {comp['url'][:70]}\nHeadings:\n{headings_text}\nIntro excerpt: {comp['intro'][:200]}\n"

    # Build a list of existing roundup posts for potential internal linking
    related_posts = []
    if (BLOG_REPO / "_posts").exists():
        for f in sorted((BLOG_REPO / "_posts").glob("*.md"), reverse=True):
            m = re.match(r"(\d{4})-(\d{2})-(\d{2})-(.+)", f.stem)
            if not m:
                continue
            year, month, day, slug = m.groups()
            content = f.read_text(encoding="utf-8")
            title_m = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', content, re.MULTILINE)
            title = title_m.group(1).strip("\"'") if title_m else slug.replace("-", " ").title()
            url = f"https://kitchen-finds.com/{year}/{month}/{day}/{slug}/"
            related_posts.append(f"- [{title}]({url})")

    related_section = "\n".join(related_posts[:5]) if related_posts else "(none yet)"

    prompt = f"""Write a complete how-to guide for this topic:

**Topic:** {topic}

**Existing posts on this site you can link to naturally** (pick the most relevant one if it fits):
{related_section}

**Competitor articles for reference** (study their H2 structure; write something better and more useful):
{competitor_notes if competitor_notes else "(none found — rely on your expertise)"}

Write the full guide now."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=6000,
        system=GUIDE_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


# ── QA Reviewer ───────────────────────────────────────────────────────────────

QA_SYSTEM = """\
You are a sharp editor who reviews affiliate blog articles before they go live.
Your job is to make sure the article sounds like it was written by a real person who actually cooks — not an AI.

Before editing, silently count the H3 product sections (call this N) and fix any number mismatches:
- The digit in the H1 title must equal N
- Any number word/digit in the intro paragraphs must equal N
- The Quick Picks list must have exactly N items
- The comparison table must have exactly N data rows (excluding header and separator)
Fix these silently — do not write any notes, headings, or commentary about the check.

READ THE ARTICLE AS A SKEPTICAL HUMAN. Fix these specific issues wherever you find them:

1. **Robotic openers** — "In this article, we will explore..." or "Are you looking for..." → rewrite with a hook
2. **Padding phrases** — "With that being said," / "It is worth noting that" / "Without further ado" → cut them
3. **Vague product praise** — "great quality" / "very useful" / "highly recommended" → make it specific; name what makes it good
4. **Vague pros/cons** — "Easy to use" or "Small size" alone are not useful → add the why (e.g. "Easy to use: single dial, nothing to learn")
5. **Stiff affiliate link text** — "click here to purchase" / "buy now" → use "→ Check price on Amazon"
6. **Intro that doesn't hook** — the first sentence must make someone want to keep reading; if it doesn't, rewrite it
7. **Overly formal tone** — this is a friendly advice blog, not a whitepaper; loosen any stiff language
8. **Em dashes** — replace every em dash (—) with a comma, colon, or reworded sentence. Em dashes are an AI tell and real people rarely write with them.

DO NOT change:
- The heading structure (H1, H2, H3 hierarchy)
- The Best for / Pros / Cons structure under each product
- The target keyword usage
- The product selection or affiliate links

Return the full revised article in clean markdown.
On the very last line, add: <!-- QA: [one sentence summarizing your main changes] -->\
"""


def qa_review(draft: str, topic: str) -> str:
    """QA pass — improve readability, catch AI-sounding language."""
    print("  Running QA review...")

    response = client.messages.create(
        model=MODEL,
        max_tokens=6000,
        system=QA_SYSTEM,
        messages=[{
            "role": "user",
            "content": f"Review and improve this article. Topic: {topic}\n\n---\n\n{draft}",
        }],
    )
    return response.content[0].text


# ── Save & Publish ────────────────────────────────────────────────────────────

def _yaml_str(s: str) -> str:
    """Escape a string for safe embedding in a YAML double-quoted scalar."""
    s = " ".join(s.split())
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    return f'"{s}"'


def _extract_faq(article: str) -> list[dict]:
    """Extract FAQ Q&A pairs for JSON-LD schema markup."""
    faq_match = re.search(r"## Frequently Asked Questions\s*\n([\s\S]+?)(?:\n---|\n## |\Z)", article)
    if not faq_match:
        return []

    faq_block = faq_match.group(1).strip()
    # re.split with a capturing group yields [pre, q1, a1, q2, a2, ...]
    chunks = re.split(r"\n*\*\*([^*]+?\?)\*\*\s*\n", faq_block)

    pairs = []
    i = 1
    while i < len(chunks) - 1:
        q = chunks[i].strip()
        a = re.split(r"\n\n", chunks[i + 1].strip())[0].strip()
        if q and a:
            pairs.append({"q": q, "a": a})
        i += 2
    return pairs


def _inject_related_posts(article: str, current_slug: str) -> str:
    """Append a 'Related Articles' section linking to other posts on the site."""
    if "## Related Articles" in article:
        return article

    posts_dir = BLOG_REPO / "_posts"
    if not posts_dir.exists():
        return article

    posts = []
    for filepath in sorted(posts_dir.glob("*.md"), reverse=True):
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})-(.+)", filepath.stem)
        if not m:
            continue
        year, month, day, slug = m.groups()
        if slug == current_slug:
            continue

        content = filepath.read_text(encoding="utf-8")
        title_m = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', content, re.MULTILINE)
        title = title_m.group(1).strip("\"'") if title_m else slug.replace("-", " ").title()
        url = f"https://kitchen-finds.com/{year}/{month}/{day}/{slug}/"
        posts.append({"title": title, "url": url})

    if not posts:
        return article

    links = "\n".join(f"- [{p['title']}]({p['url']})" for p in posts[:3])
    return article + f"\n\n---\n\n## Related Articles\n\n{links}\n"


def _make_frontmatter(topic: str, article: str, layout: str = "post", image_url: str = None, faq: list = None) -> tuple[str, str, str]:
    """Return (frontmatter, slug, date_str) for an article."""
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:60]
    date_str = datetime.now().strftime("%Y-%m-%d")

    meta_match = re.search(r"<!--\s*META:\s*(.+?)\s*-->", article)
    meta_desc = meta_match.group(1) if meta_match else f"The best {topic} — tested picks with real Amazon affiliate links."

    title_match = re.search(r"^#\s+(.+)$", article, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else topic

    image_line = f'image: "{image_url}"\n' if image_url else ""

    faq_yaml = ""
    if faq:
        lines = ["faq:"]
        for item in faq:
            lines.append(f"  - q: {_yaml_str(item['q'])}")
            lines.append(f"    a: {_yaml_str(item['a'])}")
        faq_yaml = "\n".join(lines) + "\n"

    frontmatter = (
        f"---\n"
        f"layout: {layout}\n"
        f'title: "{title}"\n'
        f"date: {date_str}\n"
        f'description: "{meta_desc}"\n'
        f"categories: [kitchen, gadgets]\n"
        f"{image_line}"
        f"{faq_yaml}"
        f"---\n\n"
    )
    return frontmatter, slug, date_str


def _clean_for_publish(article: str) -> str:
    """Strip H1, disclosure, and internal pipeline comments before publishing."""
    article = re.sub(r"^#\s+.+\n?", "", article, count=1, flags=re.MULTILINE)
    article = re.sub(r"^\*Disclosure:.*?\*\n?", "", article, flags=re.MULTILINE)
    article = re.sub(r"<!--\s*META:.*?-->", "", article)
    article = re.sub(r"<!--\s*QA:.*?-->", "", article)
    # Hard remove any em dashes that slipped through the prompts
    article = article.replace(" — ", ", ")  # spaced em dash -> comma
    article = article.replace("—", ", ")      # unspaced em dash -> comma
    return article.strip()


def save_article(topic: str, article: str, faq: list = None) -> Path:
    """Save a local review copy to articles/."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    frontmatter, slug, date_str = _make_frontmatter(topic, article, faq=faq)
    filepath = OUTPUT_DIR / f"{date_str}-{slug}.md"
    filepath.write_text(frontmatter + article, encoding="utf-8")
    return filepath


def publish_to_blog(topic: str, article: str, hero_path: Path | None = None, hero_credit: str | None = None, faq: list = None) -> str:
    """Copy article to the blog repo and push to GitHub Pages."""
    posts_dir = BLOG_REPO / "_posts"
    posts_dir.mkdir(parents=True, exist_ok=True)

    image_url = f"/assets/images/posts/{hero_path.name}" if (hero_path and hero_path.exists()) else None
    frontmatter, slug, date_str = _make_frontmatter(topic, article, layout="post", image_url=image_url, faq=faq)
    clean_article = _clean_for_publish(article)

    hero_block = ""
    if image_url:
        hero_block = f"![{topic}]({image_url})\n"
        hero_block += f"*{hero_credit}*\n\n" if hero_credit else "\n"

    filename = f"{date_str}-{slug}.md"
    filepath = posts_dir / filename
    filepath.write_text(frontmatter + hero_block + clean_article, encoding="utf-8")

    year, month, day = date_str.split("-")
    live_url = f"https://kitchen-finds.com/{year}/{month}/{day}/{slug}/"

    if os.getenv("GITHUB_ACTIONS"):
        return live_url

    try:
        subprocess.run(["git", "config", "user.email", "bot@kitchen-finds.com"], cwd=BLOG_REPO, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Kitchen Finds Bot"], cwd=BLOG_REPO, check=True, capture_output=True)
        files_to_add = [str(filepath)]
        if hero_path and hero_path.exists():
            files_to_add.append(str(hero_path))
        subprocess.run(["git", "add"] + files_to_add, cwd=BLOG_REPO, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", f"post: {topic[:60]}"], cwd=BLOG_REPO, check=True, capture_output=True)
        subprocess.run(["git", "push"], cwd=BLOG_REPO, check=True, capture_output=True)
        return live_url
    except subprocess.CalledProcessError as e:
        print(f"  Git push failed: {e.stderr.decode()}")
        return str(filepath)


# ── Link Validation & Auto-Fix ───────────────────────────────────────────────

def _dead_asins(article: str) -> list[str]:
    """Return unique ASINs in the article that return 404."""
    seen, broken = set(), []
    for asin in re.findall(r"amazon\.com/dp/([A-Z0-9]{10})", article):
        if asin not in seen:
            seen.add(asin)
            if not _validate_asin(asin):
                broken.append(asin)
    return broken


def _find_replacement_asin(product_name: str) -> str | None:
    """Search Amazon directly for a product and return the first live ASIN."""
    query = product_name.replace(" ", "+")
    url = f"https://www.amazon.com/s?k={query}"
    try:
        resp = requests.get(url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        })
        soup = BeautifulSoup(resp.text, "html.parser")
        # ASINs are in data-asin attributes on result cards
        for tag in soup.find_all(attrs={"data-asin": True}):
            asin = tag["data-asin"].strip()
            if re.match(r"^[A-Z0-9]{10}$", asin) and _validate_asin(asin):
                return asin
    except Exception:
        pass
    return None


_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_NUM_WORDS_REV = {v: k for k, v in _NUM_WORDS.items()}


def _fix_article_formatting(article: str, num_products: int) -> str:
    """Deterministic post-processing — fixes all number inconsistencies and formatting issues.

    1. Title count: correct any digit in H1 to match num_products.
    2. Intro numbers: fix digit or word numbers in the intro section.
    3. Quick Picks count: remove extra items if count exceeds num_products.
    4. Comparison table: remove extra data rows if count exceeds num_products.
    5. 'Best for:' headings: normalize to inline bold.
    """
    # 1. Fix title number
    title_match = re.search(r"^(#\s+.*?)$", article, re.MULTILINE)
    if title_match:
        title_line = title_match.group(1)
        fixed = re.sub(r"\b\d+\b", str(num_products), title_line, count=1)
        if fixed != title_line:
            print(f"  [fix] Title count: '{title_line.strip()}' -> '{fixed.strip()}'")
            article = article.replace(title_line, fixed, 1)

    # 2. Fix number words/digits in intro (text before first ## heading)
    intro_end = article.find("\n##")
    if intro_end > 0:
        intro = article[:intro_end]
        # Fix word numbers
        for word, num in _NUM_WORDS.items():
            if num != num_products and re.search(rf"\b{word}\b", intro, re.IGNORECASE):
                correct = _NUM_WORDS_REV.get(num_products, str(num_products))
                fixed_intro = re.sub(rf"\b{word}\b", correct, intro, count=1, flags=re.IGNORECASE)
                if fixed_intro != intro:
                    print(f"  [fix] Intro number word: '{word}' -> '{correct}'")
                    article = fixed_intro + article[intro_end:]
                    intro = fixed_intro
                    break
        # Fix digit numbers (re-read intro in case it changed)
        intro = article[:article.find("\n##")]
        for digit in range(2, 15):
            if digit != num_products and re.search(rf"\b{digit}\b", intro):
                fixed_intro = re.sub(rf"\b{digit}\b", str(num_products), intro, count=1)
                if fixed_intro != intro:
                    print(f"  [fix] Intro digit: '{digit}' -> '{num_products}'")
                    article = fixed_intro + article[article.find("\n##"):]
                    break

    # 3. Fix Quick Picks — remove extra bullet lines beyond num_products
    qp_match = re.search(r"(## Quick Picks\s*\n)((?:- \*\*.+\n?)+)", article)
    if qp_match:
        qp_items = re.findall(r"^- \*\*.+$", qp_match.group(2), re.MULTILINE)
        if len(qp_items) > num_products:
            kept = "\n".join(qp_items[:num_products]) + "\n"
            article = article[:qp_match.start(2)] + kept + article[qp_match.end(2):]
            print(f"  [fix] Quick Picks trimmed from {len(qp_items)} -> {num_products} items")

    # 4. Fix comparison table — remove extra data rows beyond num_products
    table_match = re.search(r"(\|.+\|\n\|[-| :]+\|\n)((?:\|.+\|\n?)*)", article)
    if table_match:
        data_rows = re.findall(r"^\|.+\|$", table_match.group(2), re.MULTILINE)
        if len(data_rows) > num_products:
            kept = "\n".join(data_rows[:num_products]) + "\n"
            article = article[:table_match.start(2)] + kept + article[table_match.end(2):]
            print(f"  [fix] Comparison table trimmed from {len(data_rows)} -> {num_products} rows")

    # 5. Fix 'Best for:' formatted as any level heading (H3–H6) -> bold inline label
    before = article
    article = re.sub(
        r"^#{3,6}\s+\*{0,2}[Bb]est\s+[Ff]or:?\*{0,2}\s*",
        "**Best for:** ",
        article,
        flags=re.MULTILINE,
    )
    if article != before:
        print("  [fix] 'Best for:' heading(s) normalized to bold inline label")

    return article


def _products_missing_links(article: str) -> list[str]:
    """Return names of H3 product sections that contain no affiliate link."""
    missing = []
    parts = re.split(r'^(### .+)$', article, flags=re.MULTILINE)
    i = 1
    while i < len(parts) - 1:
        heading = parts[i]
        body = parts[i + 1]
        if "amazon.com/dp/" not in body:
            missing.append(heading.lstrip("#").strip())
        i += 2
    return missing


def _fix_missing_links(article: str) -> str:
    """For each H3 product section missing an affiliate link, search for a live ASIN
    and inject the link. Sections where no ASIN can be found are removed so the
    article never publishes with a linkless placeholder product."""
    parts = re.split(r'^(### .+)$', article, flags=re.MULTILINE)
    rebuilt = [parts[0]]

    i = 1
    while i < len(parts) - 1:
        heading = parts[i]
        body = parts[i + 1]
        name = heading.lstrip("#").strip()

        if "amazon.com/dp/" not in body:
            print(f"    Searching ASIN for: '{name}'...")
            asin = _find_replacement_asin(name)
            if asin:
                url = f"https://www.amazon.com/dp/{asin}?tag={AFFILIATE_TAG}"
                body = re.sub(r'(\n\n---)', f'\n[→ Check price on Amazon]({url})\n\n---', body, count=1)
                print(f"    Injected {asin} for '{name}'")
                rebuilt += [heading, body]
            else:
                print(f"    No ASIN found for '{name}' — section removed")
        else:
            rebuilt += [heading, body]

        i += 2

    return "".join(rebuilt)


def fix_broken_links(article: str) -> str:
    """Replace dead ASINs with live ones. If no replacement found, strips the link text
    so the product description stays but the dead URL is removed. Always returns a
    publishable article — never aborts the pipeline."""
    for asin in _dead_asins(article):
        # Extract nearest H3 product name to guide the search
        name_match = re.search(rf"###\s+(.+?)\n[\s\S]{{1,600}}?{asin}", article)
        product_name = name_match.group(1).strip() if name_match else ""
        label = product_name[:55] or asin
        print(f"  Dead ASIN {asin} ({label}) — searching for replacement...")
        replacement = _find_replacement_asin(product_name or asin)
        if replacement:
            article = article.replace(asin, replacement)
            print(f"  Replaced {asin} -> {replacement}")
        else:
            # Keep the product write-up, just remove the dead link
            article = re.sub(
                rf"\[→ Check price on Amazon\]\(https://www\.amazon\.com/dp/{asin}[^)]*\)",
                "*Check Amazon for current availability.*",
                article,
            )
            print(f"  No replacement found for {asin} — link removed, description kept")
    return article


# ── Pre-publish Validation ────────────────────────────────────────────────────

_QA_POISON = re.compile(
    r"(?:NUMBER CONSISTENCY CHECK|PLACEHOLDER|DO NOT PUBLISH|flagging this for|"
    r"cannot publish|I cannot|I'm flagging|cannot submit|needs to be replaced)",
    re.IGNORECASE,
)


def _assert_article_valid(article: str, topic: str) -> None:
    """Skip this topic if the QA output looks like a critique instead of an article."""
    h3_count = len(re.findall(r'^### ', article, re.MULTILINE))
    if h3_count == 0:
        raise SkipTopicError(
            f"QA output for '{topic}' has 0 H3 product sections — looks like a critique leaked through. Skipping to next topic."
        )

    match = _QA_POISON.search(article)
    if match:
        raise SkipTopicError(
            f"QA output for '{topic}' contains suspicious text: '{match.group()}'. Skipping to next topic."
        )

    title_match = re.search(r"^#\s+(.+)$", article, re.MULTILINE)
    if title_match:
        title = title_match.group(1)
        if _QA_POISON.search(title):
            raise SkipTopicError(
                f"QA output H1 title looks like a meta-heading: '{title}'. Skipping to next topic."
            )


# ── Pipeline ──────────────────────────────────────────────────────────────────

def _run_one(topic: str) -> str:
    """Run the full pipeline for a single topic. Returns the live URL on success.
    Raises SkipTopicError if the topic should be skipped."""
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:60]
    date_str = datetime.now().strftime("%Y-%m-%d")

    print(f"\n{'='*60}")
    print(f"  Topic: {topic}")
    print(f"{'='*60}\n")

    guide_mode = is_informational(topic)
    post_type = "guide" if guide_mode else "roundup"
    print(f"  Type: {post_type}\n")

    print("Step 1/6 — Competitor research...")
    competitors = search_competitors(topic)
    print(f"  {len(competitors)} competitor articles analyzed\n")

    if guide_mode:
        print("Step 2/6 — Skipping product research (informational post)\n")
        products = []

        print("Step 3/6 — Writing guide...")
        draft = write_guide(topic, competitors)
        print(f"  Draft: ~{len(draft.split())} words\n")

        print("Step 4/6 — QA review...")
        final = qa_review(draft, topic)

        print("Step 5/6 — Skipping affiliate link validation (informational post)\n")

    else:
        print("Step 2/6 — Product research...")
        products = research_products(topic)
        print(f"  {len(products)} valid products ready\n")

        print("Step 3/6 — Writing article...")
        draft = write_article(topic, competitors, products)
        print(f"  Draft: ~{len(draft.split())} words\n")

        print("Step 4/6 — QA review...")
        final = qa_review(draft, topic)
        _assert_article_valid(final, topic)
        final = _fix_article_formatting(final, len(products))

        print("Step 5/6 — Validating affiliate links...")
        broken = _dead_asins(final)
        if broken:
            print(f"  {len(broken)} dead link(s) found — auto-fixing...")
            final = fix_broken_links(final)
            remaining = _dead_asins(final)
            if remaining:
                print(f"  Could not replace {len(remaining)} ASIN(s): {', '.join(remaining)} — links stripped, article still publishing")
            else:
                print(f"  All broken links fixed")
        else:
            print(f"  All affiliate links OK")

        no_link = _products_missing_links(final)
        if no_link:
            print(f"  {len(no_link)} product section(s) missing links — searching for ASINs...")
            final = _fix_missing_links(final)
            actual_count = len(re.findall(r'^### ', final, re.MULTILINE))
            final = _fix_article_formatting(final, actual_count)
            still_missing = _products_missing_links(final)
            if still_missing:
                print(f"  WARNING: could not resolve links for: {still_missing} — publishing without them")
            else:
                print(f"  All missing links resolved")

    print("Step 5b/6 — Internal links + FAQ schema...")
    final = _inject_related_posts(final, slug)
    faq_data = _extract_faq(final)
    if faq_data:
        print(f"  Related articles injected; {len(faq_data)} FAQ items extracted for schema")
    else:
        print("  No FAQ section found — schema skipped")

    print("Step 6/6 — Hero image...")
    hero_path, hero_credit = fetch_hero_image(topic, slug, date_str)

    local_path = save_article(topic, final, faq=faq_data)
    live_url = publish_to_blog(topic, final, hero_path=hero_path, hero_credit=hero_credit, faq=faq_data)

    word_count = len(re.sub(r"---.*?---", "", final, flags=re.DOTALL).split())
    qa_match = re.search(r"<!--\s*QA:\s*(.+?)\s*-->", final)

    print(f"\n{'='*60}")
    print(f"  Local copy: {local_path}")
    print(f"  Live at:    {live_url}")
    print(f"  Word count: ~{word_count}")
    if hero_path:
        print(f"  Hero image: {hero_path.name}")
    if qa_match:
        print(f"  QA note: {qa_match.group(1)}")
    print(f"{'='*60}\n")

    mark_topic_done(topic)
    return live_url


def run_pipeline(topic: str = None):
    """Try topics in order until one publishes successfully."""
    if topic:
        # Explicit topic: run once, let errors surface normally
        _run_one(topic)
        return

    candidates = get_candidate_topics()
    for candidate in candidates:
        try:
            _run_one(candidate)
            return
        except SkipTopicError as e:
            print(f"\n  SKIP: {e}")
            print(f"  Trying next topic...\n")

    raise RuntimeError(
        f"All {len(candidates)} candidate topic(s) failed — nothing published this run."
    )


if __name__ == "__main__":
    topic = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else None
    run_pipeline(topic)
