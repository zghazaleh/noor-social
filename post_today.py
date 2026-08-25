#!/usr/bin/env python3
"""
post_today.py — fail-closed daily Instagram verse publisher for GitHub Actions.

Reads today's entry from broadcast-queue.json (Asia/Dubai unless POST_DATE is
set). Posts only if every gate passes. Never substitutes another poem ID,
never invents verse text, never posts a missing card.

Fail-closed gates (any miss → exit 1, no post):
  - no queue entry for today (poem_id missing)
  - entry has no poem_id
  - poem_id is not in authenticated-ids.json
  - card_path is missing or the PNG is not on disk

Config via environment (GitHub Actions secrets / workflow env):
  IG_ACCESS_TOKEN  — Instagram Graph API long-lived token        (secret)
  IG_USER_ID       — Instagram business account id               (secret)
  CARD_BASE_URL    — public raw base, e.g.
                     https://raw.githubusercontent.com/<owner>/noor-social/main/
  DRY_RUN          — "true" to resolve + print without posting    (optional)
  POST_DATE        — ISO date override (default: today, Asia/Dubai) (optional)

Exit codes: 0 = posted or successful dry-run;
            1 = miss, misconfig, or API failure.
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
ALLOWLIST = ROOT / "authenticated-ids.json"
LOG = ROOT / "post-log.jsonl"
GRAPH = "https://graph.instagram.com/v23.0"


def log(rec: dict) -> None:
    rec["logged_at"] = datetime.now(timezone.utc).isoformat()
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps({k: v for k, v in rec.items() if k != "caption"}, ensure_ascii=False))


def load_allowlist(path: Path = ALLOWLIST) -> set[str]:
    if not path.is_file():
        raise FileNotFoundError(f"allowlist missing: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    ids = data.get("ids")
    if not isinstance(ids, list) or not ids:
        raise ValueError(f"allowlist empty or malformed: {path}")
    return {str(i) for i in ids}


def entry_for(target: str, queue_path: Path = QUEUE) -> dict | None:
    data = json.loads(queue_path.read_text(encoding="utf-8"))
    for e in data.get("queue", []):
        if (e.get("scheduled_for") or "")[:10] == target:
            return e
    return None


def evaluate_entry(
    target: str,
    entry: dict | None,
    allow: set[str],
    root: Path = ROOT,
) -> dict | None:
    """Return a miss record to log, or None if the entry is safe to post.

    Fail closed: never pick a substitute ID.
    """
    if not entry:
        return {
            "date": target,
            "status": "miss",
            "reason": "missing_poem_id",
            "note": "no queue entry for today — fail closed, no substitute",
        }

    poem_id = str(entry.get("poem_id") or "").strip()
    if not poem_id:
        return {
            "date": target,
            "status": "miss",
            "reason": "missing_poem_id",
            "note": "queue entry has no poem_id — fail closed, no substitute",
        }

    if poem_id not in allow:
        return {
            "date": target,
            "status": "miss",
            "reason": "not_in_allowlist",
            "poem_id": poem_id,
            "note": "poem_id is not on authenticated-ids.json — fail closed, no substitute",
        }

    card_path = str(entry.get("card_path") or "").strip()
    if not card_path:
        return {
            "date": target,
            "status": "miss",
            "reason": "missing_card",
            "poem_id": poem_id,
            "note": "queue entry has no card_path — fail closed, no substitute",
        }

    card_file = (root / card_path).resolve()
    try:
        card_file.relative_to(root.resolve())
    except ValueError:
        return {
            "date": target,
            "status": "miss",
            "reason": "missing_card",
            "poem_id": poem_id,
            "card_path": card_path,
            "note": "card_path escapes repo root — fail closed, no substitute",
        }

    if not card_file.is_file():
        return {
            "date": target,
            "status": "miss",
            "reason": "missing_card",
            "poem_id": poem_id,
            "card_path": card_path,
            "note": "card PNG is not on disk — fail closed, no substitute",
        }

    return None


def main() -> int:
    target = os.environ.get("POST_DATE") or datetime.now(DUBAI).date().isoformat()
    dry = os.environ.get("DRY_RUN", "").lower() == "true"

    try:
        allow = load_allowlist()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        log({"date": target, "status": "miss", "reason": "allowlist_unreadable",
             "error": str(exc), "note": "cannot fail-close without authenticated-ids.json"})
        return 1

    if not QUEUE.is_file():
        log({"date": target, "status": "miss", "reason": "missing_poem_id",
             "note": "broadcast-queue.json is missing — fail closed, no substitute"})
        return 1

    try:
        entry = entry_for(target)
    except (OSError, json.JSONDecodeError) as exc:
        log({"date": target, "status": "miss", "reason": "queue_unreadable",
             "error": str(exc)})
        return 1

    miss = evaluate_entry(target, entry, allow)
    if miss:
        log(miss)
        return 1

    assert entry is not None
    base = os.environ.get("CARD_BASE_URL", "").rstrip("/") + "/"
    image_url = base + entry["card_path"].lstrip("./")
    caption = entry.get("instagram_caption") or entry.get("english") or ""

    if dry:
        log({"date": target, "status": "dry_run", "poet": entry.get("poet"),
             "poem_id": entry.get("poem_id"), "image_url": image_url,
             "caption_len": len(caption), "caption": caption})
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
         "poem_id": entry.get("poem_id"), "genre": entry.get("genre"),
         "media_id": pub.json().get("id"), "image_url": image_url})
    return 0


if __name__ == "__main__":
    sys.exit(main())
