import sys
import re
import os
import yaml
import feedparser
import requests
import time
from datetime import datetime
from urllib.parse import urljoin, urlparse, parse_qs
from bs4 import BeautifulSoup

from db import connect, init_db, insert_item, get_existing_hashes
from utils import make_content_hash, to_iso_datetime, strip_html


DCREGS_ACTIVITY_TARGETS = [
    ("ctl00$MainContent$lnkPrRuleMakings", "dcregs_proposed", "Proposed Rulemaking"),
    ("ctl00$MainContent$lblEmerRuleMakings", "dcregs_emergency", "Emergency Rulemaking"),
]


ARTICLE_FETCH_SKIP_SOURCES = {
    "granicus_rss",
    "granicus_captions",
    "youtube",
    "youtube_live",
    "youtube_upcoming",
    "dcregs",
    "dcregs_proposed",
    "dcregs_emergency",
    "performance_oversight",
}


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_google_redirect(url: str) -> str:
    """
    Google Alerts often uses redirect links like:
      https://www.google.com/url?rct=j&sa=t&url=REAL_URL&...

    Best case: REAL_URL is in the query string.
    Fallback: follow redirects with a HEAD/GET.
    """
    if not url:
        return url

    # Fast path: extract the real "url=" parameter if present
    if url.startswith("https://www.google.com/url") and "url=" in url:
        try:
            from urllib.parse import urlparse, parse_qs

            qs = parse_qs(urlparse(url).query)
            if "url" in qs and len(qs["url"]) > 0:
                return qs["url"][0]
        except Exception:
            pass

    # Fallback: follow redirects (some sources use shorteners / tracking)
    try:
        resp = requests.get(
            url,
            allow_redirects=True,
            timeout=10,
            headers={"User-Agent": "dc-digest-bot/0.1"},
            stream=True,
        )
        return resp.url
    except Exception:
        return url


def _extract_granicus_clip_id(url: str) -> str | None:
    if not url:
        return None
    try:
        qs = parse_qs(urlparse(url).query)
        clip_id = (qs.get("clip_id") or [None])[0]
        return str(clip_id) if clip_id else None
    except Exception:
        return None


def normalize_granicus_video_url(raw_link: str, summary_html: str) -> str:
    """
    Prefer Granicus MediaPlayer URLs so clicking opens the player page
    instead of triggering a direct file download.
    """
    cleaned_link = (raw_link or "").replace("&amp;", "&")

    if "MediaPlayer.php" in cleaned_link:
        return cleaned_link

    if summary_html:
        media_match = re.search(r'href="(https://dc\.granicus\.com/MediaPlayer\.php[^"]+)"', summary_html)
        if media_match:
            return media_match.group(1).replace("&amp;", "&")

    clip_id = _extract_granicus_clip_id(cleaned_link)
    if not clip_id and summary_html:
        download_match = re.search(r'href="(https://dc\.granicus\.com/DownloadFile\.php[^"]+)"', summary_html)
        if download_match:
            clip_id = _extract_granicus_clip_id(download_match.group(1).replace("&amp;", "&"))

    if clip_id:
        return f"https://dc.granicus.com/MediaPlayer.php?view_id=2&clip_id={clip_id}"

    return cleaned_link


FEED_USER_AGENTS = (
    "dc-digest-bot/0.1",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
)

# washingtonpost.com answers its RSS in ~10s, which left no room under the old
# 20s ceiling once a retry was involved.
FEED_TIMEOUT_SECONDS = 30


def fetch_feed(url: str, source: str):
    """Fetch one feed, retrying transient blocks and timeouts.

    Publishers rate-limit and time out intermittently rather than
    permanently — a 403 from washingtontimes.com and a read timeout from
    washingtonpost.com both cleared on a later attempt — so a failure here
    silently drops a whole source for the day. Retry with backoff, rotating
    the user agent, before giving up.
    """
    last_error: Exception | None = None

    for attempt, user_agent in enumerate(FEED_USER_AGENTS):
        if attempt:
            time.sleep(2 ** attempt)
        try:
            resp = requests.get(
                url,
                timeout=FEED_TIMEOUT_SECONDS,
                headers={
                    "User-Agent": user_agent,
                    "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.7",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            resp.raise_for_status()
            if attempt:
                print(f"  {source}: recovered on attempt {attempt + 1}")
            return feedparser.parse(resp.text)
        except Exception as e:
            last_error = e

    raise last_error


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _entry_content_text(entry) -> str:
    content_parts = []
    for content_item in entry.get("content", []) or []:
        value = content_item.get("value") if isinstance(content_item, dict) else ""
        if value:
            content_parts.append(strip_html(value))
    return _normalize_whitespace(" ".join(content_parts))


def extract_article_text(html_text: str) -> str:
    soup = BeautifulSoup(html_text or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "form", "nav", "footer", "header"]):
        tag.decompose()

    candidates = []
    for selector in ["article", "main", '[role="main"]']:
        candidates.extend(soup.select(selector))
    candidates.append(soup.body or soup)

    best_text = ""
    for candidate in candidates:
        paragraphs = [
            _normalize_whitespace(p.get_text(" ", strip=True))
            for p in candidate.find_all(["p", "li"])
        ]
        paragraphs = [p for p in paragraphs if len(p) >= 40]
        text = _normalize_whitespace(" ".join(paragraphs))
        if len(text) > len(best_text):
            best_text = text

    return best_text


def fetch_article_content(url: str, source: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return ""
    if source in ARTICLE_FETCH_SKIP_SOURCES:
        return ""
    if re.search(r"\.(pdf|mp3|mp4|mov|zip)(?:$|\?)", parsed.path, flags=re.IGNORECASE):
        return ""

    headers = {"User-Agent": "dc-digest-bot/0.1"}
    try:
        resp = requests.get(url, timeout=20, headers=headers)
        resp.raise_for_status()
    except Exception:
        return ""

    content_type = (resp.headers.get("Content-Type") or "").lower()
    if content_type and "html" not in content_type:
        return ""

    return extract_article_text(resp.text)[:12000]


def parse_feed(feed_name: str, source: str, url: str):
    try:
        parsed = fetch_feed(url, source)
    except Exception as e:
        print(f"Failed to fetch {feed_name} ({source}): {e}")
        return
    if parsed.bozo:
        # bozo means parsing had issues; still might have entries
        pass

    for entry in parsed.entries:
        title = entry.get("title", "").strip() or "(no title)"

        raw_link = entry.get("link", "").strip()
        if not raw_link:
            continue

        published_raw = entry.get("published") or entry.get("updated") or ""
        published_at = to_iso_datetime(published_raw) if published_raw else None

        # 1) Keep the raw HTML summary for Granicus parsing
        summary_raw = entry.get("summary", "") or entry.get("description", "")

        # 2) Clean summary into plain text for readability
        summary = strip_html(summary_raw)
        entry_content = _entry_content_text(entry)

        source_item_id = entry.get("id") or entry.get("guid")

        # 3) Decide which URL to store
        link = raw_link

        # Granicus: store the player page URL (not direct DownloadFile URLs)
        if source == "granicus_rss":
            link = normalize_granicus_video_url(raw_link, summary_raw)

        # Google Alerts: resolve to real destination instead of google.com/url tracking
        if source == "google_alerts":
            link = resolve_google_redirect(link)

        content = entry_content or fetch_article_content(link, source)

        # Use final link in the hash so duplicates collapse correctly
        content_hash = make_content_hash(title, link)

        yield {
            "source": source,
            "source_item_id": source_item_id,
            "title": title,
            "url": link,
            "published_at": published_at,
            "summary": summary,
            "content": content,
            "content_hash": content_hash,
        }


def _find_table_with_headers(soup: BeautifulSoup, required_headers: list[str]):
    for table in soup.find_all("table"):
        headers = [th.get_text(" ", strip=True).lower() for th in table.find_all("th")]
        if all(h in headers for h in required_headers):
            return table, headers
    return None, []


def _extract_caption_link(row, page_url: str, headers: list[str]):
    for a in row.find_all("a"):
        if a.get_text(" ", strip=True).lower() == "captions" and a.get("href"):
            return urljoin(page_url, a.get("href").strip())

    try:
        index = {h: i for i, h in enumerate(headers)}
        if "captions" not in index:
            return None
        cells = row.find_all("td")
        if not cells or len(cells) <= index["captions"]:
            return None
        captions_cell = cells[index["captions"]]
        caption_link = captions_cell.find("a") if captions_cell else None
        if caption_link and caption_link.get("href"):
            return urljoin(page_url, caption_link.get("href").strip())
    except Exception:
        return None

    return None


def _looks_like_caption_text(text: str) -> bool:
    if not text:
        return False
    head = text[:400]
    if "WEBVTT" in head:
        return True
    if re.search(r"\d{2}:\d{2}:\d{2}[\.,]\d{3}\s+-->\s+\d{2}:\d{2}:\d{2}", head):
        return True
    return False


def _clean_caption_text(raw_text: str) -> str:
    lines = []
    for line in raw_text.splitlines():
        t = line.strip()
        if not t:
            continue
        if t.upper() == "WEBVTT":
            continue
        if re.match(r"^\d+$", t):
            continue
        if re.match(r"^\d{2}:\d{2}:\d{2}[\.,]\d{3}\s+-->\s+\d{2}:\d{2}:\d{2}", t):
            continue
        if re.match(r"^\d{2}:\d{2}:\d{2}\s+-->\s+\d{2}:\d{2}:\d{2}", t):
            continue
        lines.append(t)
    return " ".join(lines)


def _fetch_caption_text(caption_url: str) -> str:
    try:
        resp = requests.get(caption_url, timeout=20, headers={"User-Agent": "dc-digest-bot/0.1"})
        resp.raise_for_status()
    except Exception:
        return ""

    content_type = (resp.headers.get("Content-Type") or "").lower()
    if "video" in content_type or "audio" in content_type:
        return ""

    raw_text = resp.text or ""

    if "text/html" in content_type or "<html" in raw_text.lower():
        soup = BeautifulSoup(raw_text, "html.parser")
        caption_divs = soup.find_all("div", class_="caption")
        if not caption_divs:
            return ""
        lines = [d.get_text(" ", strip=True) for d in caption_divs if d.get_text(strip=True)]
        return " ".join(lines)

    if not _looks_like_caption_text(raw_text):
        return ""

    return _clean_caption_text(raw_text)


def parse_granicus_captions(page_url: str, existing_hashes: set[str] | None = None):
    try:
        resp = requests.get(page_url, timeout=20, headers={"User-Agent": "dc-digest-bot/0.1"})
        resp.raise_for_status()
    except Exception as e:
        print(f"Failed to fetch Granicus captions page: {e}")
        return

    soup = BeautifulSoup(resp.text, "html.parser")
    table, headers = _find_table_with_headers(soup, ["name", "date", "captions"])
    if not table:
        print("Failed to find Granicus captions table.")
        return

    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if not cells or len(cells) < len(headers):
            continue

        index = {h: i for i, h in enumerate(headers)}
        name = cells[index["name"]].get_text(" ", strip=True) if "name" in index else ""
        date_text = cells[index["date"]].get_text(" ", strip=True) if "date" in index else ""
        published_at = to_iso_datetime(date_text) if date_text else None

        caption_url = _extract_caption_link(row, page_url, headers)
        if not caption_url:
            continue

        clip_id = None
        try:
            qs = parse_qs(urlparse(caption_url).query)
            clip_id = (qs.get("clip_id") or [None])[0]
        except Exception:
            pass

        title = f"{name} (Captions)"
        if date_text:
            title = f"{title} - {date_text}"

        content_hash = make_content_hash(title, caption_url)
        if existing_hashes is not None and content_hash in existing_hashes:
            continue

        caption_text = _fetch_caption_text(caption_url)
        if not caption_text:
            continue

        summary = caption_text[:8000]

        yield {
            "source": "granicus_captions",
            "source_item_id": clip_id,
            "title": title,
            "url": caption_url,
            "published_at": published_at,
            "summary": summary,
            "content": caption_text,
            "content_hash": content_hash,
        }


def _extract_hidden_inputs(html_text: str) -> dict[str, str]:
    soup = BeautifulSoup(html_text, "html.parser")
    form = soup.find("form")
    if not form:
        return {}

    hidden_fields: dict[str, str] = {}
    for input_el in form.find_all("input", {"type": "hidden"}):
        name = input_el.get("name")
        if not name:
            continue
        hidden_fields[name] = input_el.get("value", "")
    return hidden_fields


def _postback(session: requests.Session, page_url: str, base_html: str, event_target: str) -> str:
    payload = _extract_hidden_inputs(base_html)
    if not payload:
        return ""
    payload["__EVENTTARGET"] = event_target
    payload["__EVENTARGUMENT"] = ""
    try:
        resp = session.post(page_url, data=payload, timeout=30)
        resp.raise_for_status()
        return resp.text
    except Exception:
        return ""


def _find_dcregs_notice_table(html_text: str):
    soup = BeautifulSoup(html_text, "html.parser")
    for table in soup.find_all("table"):
        headers = [th.get_text(" ", strip=True).lower() for th in table.find_all("th")]
        if {"notice id", "agency name", "subject"}.issubset(set(headers)):
            return table, headers
    return None, []


def parse_dcregs_recent_activities(
    page_url: str,
    max_items_per_type: int = 25,
    council_keywords: list[str] | None = None,
):
    session = requests.Session()
    session.headers.update({"User-Agent": "dc-digest-bot/0.1"})

    try:
        home_resp = session.get(page_url, timeout=30)
        home_resp.raise_for_status()
        home_html = home_resp.text
    except Exception as e:
        print(f"Failed to fetch DCRegs home page: {e}")
        return

    for event_target, source_name, label in DCREGS_ACTIVITY_TARGETS:
        activity_html = _postback(session, page_url, home_html, event_target)
        if not activity_html:
            continue

        table, headers = _find_dcregs_notice_table(activity_html)
        if not table:
            continue

        index = {h: i for i, h in enumerate(headers)}
        candidates = []
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if not cells:
                continue

            values = [c.get_text(" ", strip=True) for c in cells]
            if not "".join(values).strip():
                continue

            notice_id = values[index["notice id"]] if "notice id" in index and len(values) > index["notice id"] else ""
            agency = values[index["agency name"]] if "agency name" in index and len(values) > index["agency name"] else ""
            subject = values[index["subject"]] if "subject" in index and len(values) > index["subject"] else ""
            register_issue = values[index["register issue"]] if "register issue" in index and len(values) > index["register issue"] else ""
            action_date = values[index["action date"]] if "action date" in index and len(values) > index["action date"] else ""

            if not notice_id and not subject:
                continue

            title_parts = [label]
            if notice_id:
                title_parts.append(notice_id)
            if subject:
                title_parts.append(subject)
            title = " | ".join(title_parts)

            summary_parts = []
            if agency:
                summary_parts.append(f"Agency: {agency}")
            if register_issue:
                summary_parts.append(f"Register issue: {register_issue}")
            summary = " | ".join(summary_parts)

            combined_text = " ".join([notice_id, agency, subject, register_issue]).strip()
            if council_keywords and not matches_keywords(combined_text, council_keywords):
                continue

            item_url = page_url
            if notice_id:
                item_url = f"{page_url}#notice-{notice_id}"

            content_hash = make_content_hash(title, item_url)

            candidates.append(
                {
                "source": source_name,
                "source_item_id": notice_id or None,
                "title": title,
                "url": item_url,
                "published_at": to_iso_datetime(action_date) if action_date else None,
                "summary": summary,
                "content": " | ".join([part for part in [notice_id, agency, subject, register_issue] if part]),
                "content_hash": content_hash,
                }
            )

        candidates.sort(key=lambda x: x.get("published_at") or "", reverse=True)
        for item in candidates[:max_items_per_type]:
            yield item


def _normalize_source_id(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return normalized or "item"


def _performance_oversight_url(url_template: str, year: int | None = None) -> tuple[str, int]:
    resolved_year = int(year or datetime.now().year)
    if "{year}" in url_template:
        return url_template.format(year=resolved_year), resolved_year

    match = re.search(r"performance-oversight-(\d{4})", url_template)
    if match:
        resolved_year = int(match.group(1))
    return url_template, resolved_year


def _performance_oversight_content_root(soup: BeautifulSoup):
    for selector in ["article", "main", '[role="main"]', ".entry-content", ".page-content"]:
        root = soup.select_one(selector)
        if root:
            return root
    return soup.body or soup


def _performance_oversight_committee_links(root, page_url: str) -> list[tuple[str, str]]:
    skip_labels = {
        "previous years' responses",
        "previous years’ responses",
        "performance oversight & budget schedules",
        "dc council seal",
    }
    links = []
    seen_urls = set()
    for a in root.find_all("a"):
        label = _normalize_whitespace(a.get_text(" ", strip=True))
        href = (a.get("href") or "").strip()
        if not label or not href:
            continue

        label_lc = label.lower()
        if label_lc in skip_labels:
            continue
        if label_lc in {"rss", "get updates", "press center", "facebook", "twitter", "youtube"}:
            continue

        url = urljoin(page_url, href)
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            continue
        if "dccouncil.gov" not in parsed.netloc and "dccouncil.us" not in parsed.netloc:
            continue
        if url in seen_urls:
            continue

        seen_urls.add(url)
        links.append((label, url))
    return links


def parse_performance_oversight(page_url_template: str, year: int | None = None):
    page_url, resolved_year = _performance_oversight_url(page_url_template, year)
    try:
        resp = requests.get(page_url, timeout=20, headers={"User-Agent": "dc-digest-bot/0.1"})
        if resp.status_code == 404:
            print(f"Performance oversight page not found for {resolved_year}: {page_url}")
            return
        resp.raise_for_status()
    except Exception as e:
        print(f"Failed to fetch performance oversight page for {resolved_year}: {e}")
        return

    soup = BeautifulSoup(resp.text, "html.parser")
    root = _performance_oversight_content_root(soup)

    page_title = _normalize_whitespace((soup.find("h1") or soup.find("title") or root).get_text(" ", strip=True))
    if not page_title:
        page_title = f"Performance Oversight {resolved_year}"

    summary = (
        f"Official DC Council performance oversight page for {resolved_year}, "
        "including committee oversight documents and related materials."
    )
    page_item_title = f"{page_title}: Overview"
    yield {
        "source": "performance_oversight",
        "source_item_id": f"{resolved_year}:overview",
        "title": page_item_title,
        "url": page_url,
        "published_at": None,
        "summary": summary,
        "content": _normalize_whitespace(root.get_text(" ", strip=True))[:12000],
        "content_hash": make_content_hash(page_item_title, page_url),
    }

    for label, url in _performance_oversight_committee_links(root, page_url):
        title = f"Performance Oversight {resolved_year}: {label}"
        yield {
            "source": "performance_oversight",
            "source_item_id": f"{resolved_year}:{_normalize_source_id(label)}",
            "title": title,
            "url": url,
            "published_at": None,
            "summary": f"Committee oversight document linked from the official Performance Oversight {resolved_year} page.",
            "content": f"{page_title} | {label} | {url}",
            "content_hash": make_content_hash(title, url),
        }


def matches_keywords(text: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    haystack = text.lower()
    return any(k.lower() in haystack for k in keywords)


def main() -> int:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    config_path = os.path.join(repo_root, "config.yaml")
    if len(sys.argv) > 1:
        config_path = sys.argv[1]

    cfg = load_config(config_path)
    db_path = cfg["storage"]["db_path"]
    if not os.path.isabs(db_path):
        db_path = os.path.join(repo_root, db_path)

    conn = connect(db_path)
    init_db(conn)

    keywords = cfg.get("filters", {}).get("dc_council_keywords", [])
    official_sources = {"granicus_rss", "granicus_captions", "council_rss", "youtube", "performance_oversight"}

    total_new = 0
    dcregs_cfg = cfg.get("dcregs", {})
    dcregs_max_items = int(dcregs_cfg.get("max_items_per_type", 25))
    dcregs_council_keywords = dcregs_cfg.get("council_keywords", [])
    for f in cfg.get("feeds", []):
        name = f["name"]
        source = f["source"]
        url = f["url"]
        start = time.monotonic()
        print(f"Fetching: {name} ({source})")

        existing_hashes = None
        if source == "granicus_captions":
            existing_hashes = get_existing_hashes(conn, source)
            items_iter = parse_granicus_captions(url, existing_hashes)
        elif source == "dcregs":
            items_iter = parse_dcregs_recent_activities(
                url,
                max_items_per_type=dcregs_max_items,
                council_keywords=dcregs_council_keywords,
            )
        elif source == "performance_oversight":
            items_iter = parse_performance_oversight(url)
        else:
            items_iter = parse_feed(name, source, url)

        for item in items_iter:
            if source not in official_sources and keywords:
                combined = f"{item.get('title', '')} {item.get('summary', '')}"
                if not matches_keywords(combined, keywords):
                    continue
            inserted = insert_item(conn, item)
            if inserted:
                if existing_hashes is not None:
                    existing_hashes.add(item.get("content_hash", ""))
                total_new += 1

        elapsed = time.monotonic() - start
        print(f"Done: {name} in {elapsed:.1f}s")

    print(f"Done. New items saved: {total_new}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
