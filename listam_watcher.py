#!/usr/bin/env python3
"""
list.am watcher -> Telegram

Checks one or more list.am search URLs on a schedule and sends every NEW
listing to your Telegram chat.

Usage:
    python listam_watcher.py            # runs forever, checks every N minutes (config)
    python listam_watcher.py --once     # single check (for cron / Task Scheduler)
    python listam_watcher.py --test     # sends a test message to Telegram and exits
"""
import argparse
import html
import json
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
SEEN_PATH = HERE / "seen_ids.json"
BASE = "https://www.list.am"

# --- HTTP client: curl_cffi imitates a real Chrome (helps if the site blocks bots)
try:
    from curl_cffi import requests as http

    def fetch(url):
        return http.get(url, impersonate="chrome", timeout=30,
                        headers={"Accept-Language": "hy,en;q=0.8,ru;q=0.6"})
except ImportError:  # fallback
    import requests as http

    def fetch(url):
        return http.get(url, timeout=30, headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/131.0 Safari/537.36"),
            "Accept-Language": "hy,en;q=0.8,ru;q=0.6",
        })

import requests  # used for Telegram API
from bs4 import BeautifulSoup

ITEM_RE = re.compile(r"^/(?:[a-z]{2}/)?item/(\d+)")


def log(msg):
    print(time.strftime("[%Y-%m-%d %H:%M:%S]"), msg, flush=True)


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_listings(page_html):
    """Return list of dicts {id, url, title, price, location} found on a search page."""
    soup = BeautifulSoup(page_html, "html.parser")
    found = {}
    for a in soup.find_all("a", href=True):
        m = ITEM_RE.match(a["href"])
        if not m:
            continue
        item_id = m.group(1)
        if item_id in found:
            continue
        # list.am cards usually: div.p = price, div.l = title, div.at = location
        def pick(cls):
            el = a.find(class_=cls)
            return el.get_text(" ", strip=True) if el else ""
        title = pick("l")
        price = pick("p")
        location = pick("at")
        if not title:
            title = a.get("title") or a.get_text(" ", strip=True)
        if not title:
            img = a.find("img")
            title = (img.get("alt") if img else "") or ""
        if not title.strip():
            continue  # skip empty/image-only duplicates
        found[item_id] = {
            "id": item_id,
            "url": urljoin(BASE, f"/item/{item_id}"),
            "title": title.strip()[:200],
            "price": price,
            "location": location,
            "raw": a.get_text(" ", strip=True),
        }
    return list(found.values())


def is_unwanted(item, cfg):
    """True if the listing looks like an agency / daily rent (per config keywords).
    Checks the search-card text first, then the listing's own page."""
    words = [w.lower() for w in cfg.get("exclude_keywords", [])]
    if not words:
        return False
    text = (item.get("title", "") + " " + item.get("raw", "")).lower()
    if any(w in text for w in words):
        return True
    if cfg.get("check_item_page", True):
        try:
            r = fetch(item["url"])
            if r.status_code == 200:
                page = BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True).lower()
                time.sleep(random.uniform(1, 3))
                return any(w in page for w in words)
        except Exception as e:
            log(f"could not open {item['url']}: {e}")
    return False


def send_telegram(token, chat_id, text):
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": "false"},
        timeout=30,
    )
    if not r.ok:
        log(f"Telegram error {r.status_code}: {r.text[:300]}")
    return r.ok


def format_listing(item, search_name):
    parts = [f"🆕 <b>{html.escape(item['title'])}</b>"]
    if item["price"]:
        parts.append(f"💰 {html.escape(item['price'])}")
    if item["location"]:
        parts.append(f"📍 {html.escape(item['location'])}")
    parts.append(f"🔎 {html.escape(search_name)}")
    parts.append(item["url"])
    return "\n".join(parts)


def check_once(cfg, seen):
    token, chat_id = cfg["telegram_bot_token"], str(cfg["telegram_chat_id"])
    total_new = 0
    for search in cfg["searches"]:
        name, url = search.get("name", "list.am"), search["url"]
        key = url
        try:
            resp = fetch(url)
        except Exception as e:
            log(f"[{name}] network error: {e}")
            continue
        if resp.status_code != 200:
            log(f"[{name}] HTTP {resp.status_code} — site may be blocking; see README")
            continue
        items = parse_listings(resp.text)
        if not items:
            log(f"[{name}] 0 listings parsed (page layout may have changed)")
            continue

        known = set(seen.get(key, []))
        first_run = key not in seen
        new_items = [it for it in items if it["id"] not in known]

        if first_run and not cfg.get("send_existing_on_first_run", False):
            log(f"[{name}] first run: remembered {len(items)} current listings, nothing sent")
        else:
            # oldest first so the newest ends up at the bottom of the chat
            for it in reversed(new_items):
                if is_unwanted(it, cfg):
                    log(f"[{name}] skipped (agency/daily): {it['url']}")
                    continue
                if send_telegram(token, chat_id, format_listing(it, name)):
                    total_new += 1
                time.sleep(1)
            log(f"[{name}] {len(new_items)} new listing(s)")

        known.update(it["id"] for it in items)
        seen[key] = sorted(known, key=int)[-2000:]  # keep file small
        save_json(SEEN_PATH, seen)
        time.sleep(random.uniform(2, 5))
    return total_new


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="check once and exit")
    ap.add_argument("--test", action="store_true", help="send a Telegram test message")
    args = ap.parse_args()

    if not CONFIG_PATH.exists():
        sys.exit(f"config.json not found next to the script ({CONFIG_PATH})")
    cfg = load_json(CONFIG_PATH, {})
    # secrets from environment (GitHub Actions) override config.json
    import os
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        cfg["telegram_bot_token"] = os.environ["TELEGRAM_BOT_TOKEN"]
    if os.environ.get("TELEGRAM_CHAT_ID"):
        cfg["telegram_chat_id"] = os.environ["TELEGRAM_CHAT_ID"]

    if args.test:
        ok = send_telegram(cfg["telegram_bot_token"], str(cfg["telegram_chat_id"]),
                           "✅ list.am bot-ը աշխատում է")
        sys.exit(0 if ok else 1)

    seen = load_json(SEEN_PATH, {})
    if args.once:
        check_once(cfg, seen)
        return

    interval = max(1, int(cfg.get("check_every_minutes", 60)))
    log(f"Watching {len(cfg['searches'])} search(es), every {interval} min. Ctrl+C to stop.")
    while True:
        try:
            check_once(cfg, seen)
        except Exception as e:
            log(f"unexpected error: {e}")
        time.sleep(interval * 60 + random.uniform(0, 30))


if __name__ == "__main__":
    main()
