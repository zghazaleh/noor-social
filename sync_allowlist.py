#!/usr/bin/env python3
"""
sync_allowlist.py — integrity check (and optional copy) of authenticated-ids.json.

Local contract (always):
  - exactly 264 unique IDs
  - empty intersection with the hard denylist
  - no invented IDs

Optional remote compare against the private product allowlist:
  content/poems/authenticated-ids.json in zghazaleh/Noor-Al-Hikmah-V1.1

This public repo cannot read that file with the default Actions GITHUB_TOKEN.
Set NOOR_PRIVATE_READ_TOKEN (read-only PAT / fine-grained repo read) to enable
the compare. If the token is absent, remote compare is skipped — local
integrity still runs.

--write copies the fetched private file only when it passes integrity.
Never invents IDs. Never writes a denylisted or wrong-count set.

Usage:
  python3 sync_allowlist.py --check
  NOOR_PRIVATE_READ_TOKEN=ghp_... python3 sync_allowlist.py --check
  NOOR_PRIVATE_READ_TOKEN=ghp_... python3 sync_allowlist.py --write
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import post_today as pt

PRIVATE_OWNER = "zghazaleh"
PRIVATE_REPO = "Noor-Al-Hikmah-V1.1"
PRIVATE_PATH = "content/poems/authenticated-ids.json"
API_URL = (
    f"https://api.github.com/repos/{PRIVATE_OWNER}/{PRIVATE_REPO}/contents/{PRIVATE_PATH}"
)


def ids_from_payload(data: dict) -> set[str]:
    raw = data.get("ids")
    if not isinstance(raw, list) or not raw:
        raise ValueError("allowlist empty or malformed")
    ids = {str(i).strip() for i in raw if str(i).strip()}
    if len(ids) != len(raw):
        raise ValueError("allowlist has blank or duplicate IDs")
    return ids


def fetch_private_allowlist(token: str) -> tuple[set[str], str]:
    """Return (ids, raw_text) from the private product repo."""
    req = urllib.request.Request(
        API_URL,
        headers={
            "User-Agent": "noor-social-sync-allowlist/1.0",
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    encoded = payload.get("content")
    if not encoded:
        raise ValueError("GitHub contents API returned no content")
    raw = base64.b64decode(encoded.replace("\n", "")).decode("utf-8")
    data = json.loads(raw)
    return ids_from_payload(data), raw


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check / sync the 264-ID allowlist.")
    parser.add_argument("--check", action="store_true", default=True,
                        help="Integrity check (default)")
    parser.add_argument("--write", action="store_true",
                        help="Overwrite local file from the private repo if it passes")
    parser.add_argument("--path", default=str(pt.ALLOWLIST),
                        help="Local authenticated-ids.json path")
    args = parser.parse_args(argv)

    path = Path(args.path)
    try:
        local = pt.load_allowlist(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "stage": "local", "error": str(exc)}))
        return 1

    report: dict = {
        "ok": True,
        "local_count": len(local),
        "expected_count": pt.EXPECTED_ALLOWLIST_COUNT,
        "denylist_intersection": [],
        "remote": "skipped",
    }

    token = (os.environ.get("NOOR_PRIVATE_READ_TOKEN") or "").strip()
    if not token:
        report["remote"] = (
            "skipped — set NOOR_PRIVATE_READ_TOKEN (read-only) to compare "
            f"{PRIVATE_OWNER}/{PRIVATE_REPO}:{PRIVATE_PATH}"
        )
        print(json.dumps(report, indent=2))
        print(
            "ALLOWLIST OK (local): 264 IDs, denylist intersection empty. "
            "Remote compare skipped."
        )
        return 0

    try:
        remote_ids, remote_raw = fetch_private_allowlist(token)
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
        print(json.dumps({"ok": False, "stage": "remote_fetch", "error": str(exc)}))
        return 1

    err = pt.allowlist_integrity_error(remote_ids)
    if err:
        print(json.dumps({"ok": False, "stage": "remote_integrity", "error": err}))
        return 1

    drift = {
        "only_local": sorted(local - remote_ids),
        "only_remote": sorted(remote_ids - local),
    }
    report["remote"] = "compared"
    report["remote_count"] = len(remote_ids)
    report["drift"] = drift
    if drift["only_local"] or drift["only_remote"]:
        report["ok"] = False
        print(json.dumps(report, indent=2))
        print("ALLOWLIST DRIFT versus private product repo — fail closed.", file=sys.stderr)
        return 1

    if args.write:
        path.write_text(remote_raw if remote_raw.endswith("\n") else remote_raw + "\n",
                        encoding="utf-8")
        report["wrote"] = str(path)

    print(json.dumps(report, indent=2))
    print("ALLOWLIST OK: local matches private 264-ID contract.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
