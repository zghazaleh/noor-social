#!/usr/bin/env python3
"""
preflight.py — fail-closed check of the next N queue days.

Verifies each upcoming broadcast-queue.json row against:
  - hard denylist
  - authenticated-ids.json (255)
  - card_path == cards/{id}.png
  - live PNG HEAD (CARD_BASE_URL + card_path → 200 image/png)
  - caption authenticity (queued Arabic == live /poems/{id} JSON-LD)

Never substitutes an ID. Never invents verse text. Exit 1 on any miss.

Usage:
  CARD_BASE_URL=https://nooralhikmah.com/ python3 preflight.py
  python3 preflight.py --days 14 --start 2026-09-05
  python3 preflight.py --skip-live   # ID/card/caption-shape only (CI unit)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta

import post_today as pt


def upcoming_dates(start: str, days: int) -> list[str]:
    first = datetime.fromisoformat(start).date()
    return [(first + timedelta(days=i)).isoformat() for i in range(days)]


def check_days(
    dates: list[str],
    *,
    allow: set[str],
    card_base_url: str,
    head_fn: pt.HeadFn | None = None,
    fetch_fn: pt.FetchFn | None = None,
    skip_live_caption: bool = False,
    skip_live_head: bool = False,
    queue_path=pt.QUEUE,
) -> list[dict]:
    misses: list[dict] = []
    for day in dates:
        try:
            entry = pt.entry_for(day, queue_path)
        except (OSError, json.JSONDecodeError) as exc:
            misses.append(pt._miss(day, "queue_unreadable", error=str(exc)))
            continue

        head = (lambda _u: (200, "image/png")) if skip_live_head else head_fn
        caption, miss = pt.evaluate_post(
            day,
            entry,
            allow,
            card_base_url=card_base_url,
            head_fn=head,
            fetch_fn=fetch_fn,
            skip_live_caption=skip_live_caption,
        )
        if miss:
            misses.append(miss)
            continue
        assert caption is not None
    return misses


def check_future_queue(rows: list[dict], *, start: str, allow: set[str]) -> list[dict]:
    """Check every enabled future row, including dates beyond the live window.

    Disabled rows are retained as holds, not treated as approved content.
    This local check does not authorize publication or replace live preflight.
    """
    error = pt.allowlist_integrity_error(allow)
    if error:
        return [pt._miss(start, "allowlist_unreadable", error=error)]
    first = datetime.fromisoformat(start).date()
    misses = []
    for entry in rows:
        day = datetime.fromisoformat(entry["scheduled_for"]).date()
        if day < first:
            continue
        if entry.get("disabled") is True or str(entry.get("status") or "").lower() == "disabled":
            continue
        _, miss = pt.evaluate_post(
            day.isoformat(), entry, allow,
            card_base_url="https://nooralhikmah.com/",
            head_fn=lambda _url: (200, "image/png"), skip_live_caption=True,
        )
        if miss:
            misses.append(miss)
    return misses


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preflight the next N queue days.")
    parser.add_argument("--days", type=int, default=14, help="How many days ahead (default 14)")
    parser.add_argument(
        "--queue-only", action="store_true",
        help="Validate all enabled future rows locally; preserve disabled holds (no network)",
    )
    parser.add_argument(
        "--start",
        default="",
        help="First ISO date (default: today Asia/Dubai)",
    )
    parser.add_argument(
        "--skip-live",
        action="store_true",
        help="Skip live PNG HEAD and live poem-page caption verify",
    )
    args = parser.parse_args(argv)

    start = args.start or datetime.now(pt.DUBAI).date().isoformat()
    base = os.environ.get("CARD_BASE_URL", "https://nooralhikmah.com/")
    try:
        allow = pt.load_allowlist()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "miss", "reason": "allowlist_unreadable", "error": str(exc)}))
        return 1

    if args.queue_only:
        try:
            rows = json.loads(pt.QUEUE.read_text(encoding="utf-8"))["queue"]
            misses = check_future_queue(rows, start=start, allow=allow)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(json.dumps({"status": "miss", "reason": "queue_unreadable", "error": str(exc)}))
            return 1
        print(json.dumps({"start": start, "misses": misses, "ok": not misses}, indent=2))
        return 1 if misses else 0

    dates = upcoming_dates(start, args.days)
    misses = check_days(
        dates,
        allow=allow,
        card_base_url=base,
        skip_live_caption=args.skip_live,
        skip_live_head=args.skip_live,
    )

    report = {
        "start": start,
        "days": args.days,
        "checked": dates,
        "misses": misses,
        "ok": len(dates) - len(misses),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if misses:
        print(
            f"PREFLIGHT FAIL: {len(misses)} of {len(dates)} upcoming days "
            "are not safe to post (fail closed, no substitute).",
            file=sys.stderr,
        )
        return 1
    print(f"PREFLIGHT OK: {len(dates)} upcoming days pass allowlist+denylist+card+caption.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
