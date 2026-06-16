#!/usr/bin/env python3
"""
post_today.py — self-contained daily Instagram verse publisher for GitHub Actions.

Runs in the cloud (always-on), independent of any local machine. Reads the
day's entry from broadcast-queue.json, builds the public card image URL from
this repo's own raw.githubusercontent path, and posts via the Instagram Graph
API (two-step: create container → publish).

Config via environment (set as GitHub Actions secrets / workflow env):
  IG_ACCESS_TOKEN  — Instagram Graph API long-lived token        (secret)
  IG_USER_ID       — Instagram business account id               (secret)
  CARD_BASE_URL    — public raw base, e.g.
                     https://raw.githubusercontent.com/<owner>/noor-social/main/
  DRY_RUN          — "true" to resolve + print without posting    (optional)
  POST_DATE        — ISO date override (default: today, Asia/Dubai) (optional)

Exit codes: 0 = posted or nothing-due (both fine for a daily cron);
            1 = misconfig or API failure.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

DUBAI = timezone(timedelta(hours=4))
ROOT = Path(__file__).resolve().parent
QUEUE = ROOT / "broadcast-queue.json"
LOG = ROOT / "post-log.jsonl"
GRAPH = "https://graph.instagram.com/v23.0"


def log(rec: dict) -> None:
    rec["logged_at"] = datetime.now(timezone.utc).isoformat()
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps({k: v for k, v in rec.items() if k != "caption"}, ensure_ascii=False))


def entry_for(target: str) -> dict | None:
    data = json.loads(QUEUE.read_text(encoding="utf-8"))
    for e in data.get("queue", []):
        if (e.get("scheduled_for") or "")[:10] == target:
            return e
    return None


def main() -> int:
    target = os.environ.get("POST_DATE") or datetime.now(DUBAI).date().isoformat()
    dry = os.environ.get("DRY_RUN", "").lower() == "true"

    entry = entry_for(target)
    if not entry:
        log({"date": target, "status": "no_entry",
             "note": "queue exhausted or no card scheduled — refill broadcast-queue.json"})
        return 0

    base = os.environ.get("CARD_BASE_URL", "").rstrip("/") + "/"
    image_url = base + entry["card_path"].lstrip("./")
    caption = entry.get("instagram_caption") or entry.get("english") or ""

    if dry:
        log({"date": target, "status": "dry_run", "poet": entry.get("poet"),
             "image_url": image_url, "caption_len": len(caption), "caption": caption})
        return 0

    token = os.environ.get("IG_ACCESS_TOKEN")
    ig_id = os.environ.get("IG_USER_ID")
    if not token or not ig_id:
        log({"date": target, "status": "error", "error": "missing IG_ACCESS_TOKEN / IG_USER_ID"})
        return 1

    import requests

    # Step 1 — create media container
    create = requests.post(f"{GRAPH}/{ig_id}/media",
                           params={"image_url": image_url, "caption": caption,
                                   "access_token": token}, timeout=60)
    if create.status_code != 200:
        log({"date": target, "status": "error", "step": "create",
             "http": create.status_code, "body": create.text[:300], "image_url": image_url})
        return 1
    creation_id = create.json().get("id")

    # Step 2 — wait for the container to finish, then publish
    for _ in range(10):
        st = requests.get(f"{GRAPH}/{creation_id}",
                          params={"fields": "status_code", "access_token": token}, timeout=60)
        if st.json().get("status_code") == "FINISHED":
            break
        time.sleep(5)

    pub = requests.post(f"{GRAPH}/{ig_id}/media_publish",
                        params={"creation_id": creation_id, "access_token": token}, timeout=60)
    if pub.status_code != 200:
        log({"date": target, "status": "error", "step": "publish",
             "http": pub.status_code, "body": pub.text[:300]})
        return 1

    log({"date": target, "status": "posted", "poet": entry.get("poet"),
         "genre": entry.get("genre"), "media_id": pub.json().get("id"), "image_url": image_url})
    return 0


if __name__ == "__main__":
    sys.exit(main())
