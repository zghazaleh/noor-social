#!/usr/bin/env python3
"""
post_today.py — fail-closed daily Instagram verse publisher for GitHub Actions.

Reads today's entry from broadcast-queue.json (Asia/Dubai unless POST_DATE is
set). Posts only if every gate passes. Never substitutes another poem ID,
never invents verse text, never posts a missing card.

Fail-closed gates (any miss → exit 1, no post):
  - no queue entry for today (poem_id missing)
  - entry has no poem_id, or is disabled
  - poem_id is on the hard denylist (even if also on the allowlist)
  - poem_id is not in authenticated-ids.json
  - allowlist fails integrity (count != 255 or denylist intersection)
  - card_path is missing, is not cards/{id}.png, or HEAD of
    CARD_BASE_URL + card_path is not HTTP 200 with an image/png content-type
  - caption cannot be authenticated (verse not on the queue entry and/or
    not present on the live poem page)

Caption policy (safer option: fail closed, never invent Arabic):
  - Captions may only reuse fields already on the queue entry.
  - If the entry has Arabic, it must appear on
    https://nooralhikmah.com/poems/{id} (JSON-LD ``text``). Mismatch or
    fetch failure → no post. Do not fall back to /today (that page is a
    different daily verse).
  - Extra Arabic in the caption that is not the queued verse or a known
    hashtag/label → no post.
  - If there is no Arabic on the queue, post the image with a safe
    template (poet + live poem URL + hashtags) that contains no verse.

Site cards are not in this repo. A local-file check would block every post.
The card gate is a HEAD of the public URL.

Config via environment (GitHub Actions secrets / workflow env):
  IG_ACCESS_TOKEN  — Instagram Graph API long-lived token        (secret)
  IG_USER_ID       — Instagram business account id               (secret)
  CARD_BASE_URL    — public site base with trailing slash, e.g.
                     https://nooralhikmah.com/
  DRY_RUN          — "true" to resolve + print without posting    (optional)
  POST_DATE        — ISO date override (default: today, Asia/Dubai) (optional)
  SKIP_LIVE_CAPTION — "true" to skip the live poem-page check     (tests only)

Exit codes: 0 = posted or successful dry-run;
            1 = miss, misconfig, or API failure.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Callable

DUBAI = timezone(timedelta(hours=4))
ROOT = Path(__file__).resolve().parent
QUEUE = ROOT / "broadcast-queue.json"
ALLOWLIST = ROOT / "authenticated-ids.json"
LOG = ROOT / "post-log.jsonl"
GRAPH = "https://graph.instagram.com/v23.0"
SITE_ORIGIN = "https://nooralhikmah.com"
HEAD_TIMEOUT = 20
HEAD_UA = "noor-social-post-today/1.0"
PNG_TYPES = frozenset({"image/png", "image/x-png"})
EXPECTED_ALLOWLIST_COUNT = 255

# Hard denylist — never post, even if the ID is on the allowlist or queue.
# Qabbani shipping leaks, generated cluster, and known off-list IDs.
DENYLIST: frozenset[str] = frozenset(
    {str(i) for i in range(1370, 1374)}  # Qabbani shipping 1370–1373
    | {str(i) for i in range(1491, 1494)}  # Qabbani shipping 1491–1493
    | {"1607", "1608"}  # Qabbani shipping
    | {str(i) for i in range(1851, 1858)}  # generated cluster 1851–1857
    | {"1507", "1543", "1858"}  # known off-list
)

# Arabic tokens allowed in a caption besides the queued verse itself.
SAFE_ARABIC_TOKENS = frozenset({
    "نور_الحكمة",
    "الشعر_العربي",
    "شعر",
    "بيت",
    "اليوم",
    "النوع",
})

_ARABIC_TOKEN = re.compile(
    r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF_]+"
)
_JSONLD_RE = re.compile(
    r'<script type="application/ld\+json">(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)

HeadFn = Callable[[str], tuple[int | None, str]]
FetchFn = Callable[[str], tuple[int | None, str]]


def log(rec: dict) -> None:
    rec["logged_at"] = datetime.now(timezone.utc).isoformat()
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps({k: v for k, v in rec.items() if k != "caption"}, ensure_ascii=False))


def expected_card_path(poem_id: str) -> str:
    return f"cards/{poem_id}.png"


def poem_page_url(poem_id: str) -> str:
    return f"{SITE_ORIGIN}/poems/{poem_id}"


def is_denylisted(poem_id: str) -> bool:
    return str(poem_id).strip() in DENYLIST


def normalize_verse(text: str) -> str:
    return " ".join((text or "").split())


def arabic_tokens(text: str) -> list[str]:
    return _ARABIC_TOKEN.findall(text or "")


def allowlist_integrity_error(ids: set[str]) -> str | None:
    """Return a reason if the allowlist is not the 255-ID certified set."""
    if len(ids) != EXPECTED_ALLOWLIST_COUNT:
        return (
            f"allowlist count is {len(ids)}, expected {EXPECTED_ALLOWLIST_COUNT} "
            "— refuse to post rather than invent or drop IDs"
        )
    overlap = sorted(ids & DENYLIST)
    if overlap:
        return f"allowlist intersects denylist: {overlap}"
    return None


def load_allowlist(path: Path = ALLOWLIST) -> set[str]:
    if not path.is_file():
        raise FileNotFoundError(f"allowlist missing: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data.get("ids")
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"allowlist empty or malformed: {path}")
    ids = {str(i).strip() for i in raw if str(i).strip()}
    if len(ids) != len(raw):
        raise ValueError(f"allowlist has blank or duplicate IDs: {path}")
    err = allowlist_integrity_error(ids)
    if err:
        raise ValueError(err)
    return ids


def entry_for(target: str, queue_path: Path = QUEUE) -> dict | None:
    data = json.loads(queue_path.read_text(encoding="utf-8"))
    for e in data.get("queue", []):
        if (e.get("scheduled_for") or "")[:10] == target:
            return e
    return None


def load_queue(queue_path: Path = QUEUE) -> list[dict]:
    data = json.loads(queue_path.read_text(encoding="utf-8"))
    rows = data.get("queue", [])
    if not isinstance(rows, list):
        raise ValueError(f"queue malformed: {queue_path}")
    return rows


def public_card_url(card_path: str, base: str | None = None) -> str:
    if base is None:
        base = os.environ.get("CARD_BASE_URL", "")
    return base.rstrip("/") + "/" + card_path.lstrip("./")


def is_png_content_type(content_type: str) -> bool:
    mime = (content_type or "").split(";")[0].strip().lower()
    return mime in PNG_TYPES


def head_card_url(url: str, timeout: float = HEAD_TIMEOUT) -> tuple[int | None, str]:
    """HEAD a public card URL. Returns (status, content-type). Never raises."""
    req = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": HEAD_UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), resp.headers.get("Content-Type", "") or ""
    except urllib.error.HTTPError as exc:
        ctype = ""
        if exc.headers is not None:
            ctype = exc.headers.get("Content-Type", "") or ""
        return int(exc.code), ctype
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None, ""


def fetch_url(url: str, timeout: float = HEAD_TIMEOUT) -> tuple[int | None, str]:
    """GET a public URL. Returns (status, body text). Never raises."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": HEAD_UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return int(resp.status), body
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return int(exc.code), body
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None, ""


def extract_poem_jsonld_text(html: str) -> str | None:
    for match in _JSONLD_RE.finditer(html or ""):
        raw = match.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            text = data.get("text")
            if isinstance(text, str) and text.strip():
                return text
    return None


def missing_ig_secrets(token: str | None, ig_id: str | None) -> bool:
    return not (token or "").strip() or not (ig_id or "").strip()


def _miss(target: str, reason: str, **extra) -> dict:
    rec = {
        "date": target,
        "status": "miss",
        "reason": reason,
        "note": extra.pop("note", "fail closed, no substitute"),
    }
    rec.update(extra)
    return rec


def evaluate_entry(
    target: str,
    entry: dict | None,
    allow: set[str],
    card_base_url: str = "",
    head_fn: HeadFn | None = None,
) -> dict | None:
    """Return a miss record to log, or None if the ID/card gates pass.

    Fail closed: never pick a substitute ID. Card presence is a HEAD of
    CARD_BASE_URL + card_path (site cards are not in this repo).
    Denylist wins even if the ID is also on the allowlist.
    """
    if not entry:
        return _miss(
            target,
            "missing_poem_id",
            note="no queue entry for today — fail closed, no substitute",
        )

    if entry.get("disabled") is True or str(entry.get("status") or "").lower() == "disabled":
        return _miss(
            target,
            "disabled",
            poem_id=str(entry.get("poem_id") or ""),
            note="queue entry is disabled — fail closed, no substitute",
        )

    poem_id = str(entry.get("poem_id") or "").strip()
    if not poem_id:
        return _miss(
            target,
            "missing_poem_id",
            note="queue entry has no poem_id — fail closed, no substitute",
        )

    if is_denylisted(poem_id):
        return _miss(
            target,
            "denylisted",
            poem_id=poem_id,
            note="poem_id is on the hard denylist — fail closed, no substitute",
        )

    if poem_id not in allow:
        return _miss(
            target,
            "not_in_allowlist",
            poem_id=poem_id,
            note="poem_id is not on authenticated-ids.json — fail closed, no substitute",
        )

    card_path = str(entry.get("card_path") or "").strip()
    if not card_path:
        return _miss(
            target,
            "missing_card",
            poem_id=poem_id,
            note="queue entry has no card_path — fail closed, no substitute",
        )

    if not card_path.lower().endswith(".png"):
        return _miss(
            target,
            "missing_card",
            poem_id=poem_id,
            card_path=card_path,
            note="card URL is not a PNG — fail closed, no substitute",
        )

    expected = expected_card_path(poem_id)
    if card_path != expected:
        return _miss(
            target,
            "missing_card",
            poem_id=poem_id,
            card_path=card_path,
            note=f"card_path must be {expected} — fail closed, no substitute",
        )

    base = (card_base_url or "").strip()
    if not base:
        return _miss(
            target,
            "missing_card",
            poem_id=poem_id,
            card_path=card_path,
            note="CARD_BASE_URL is not set — fail closed, no substitute",
        )

    image_url = public_card_url(card_path, base)
    fn = head_fn or head_card_url
    status, content_type = fn(image_url)
    if status != 200 or not is_png_content_type(content_type):
        return _miss(
            target,
            "missing_card",
            poem_id=poem_id,
            card_path=card_path,
            image_url=image_url,
            http=status,
            content_type=content_type,
            note="card PNG is not a public image/png (HEAD) — fail closed, no substitute",
        )

    return None


def leftover_arabic(caption: str, verse: str) -> list[str]:
    """Arabic tokens in caption that are not the queued verse or safe labels."""
    remainder = normalize_verse(caption)
    verse_norm = normalize_verse(verse)
    if verse_norm:
        remainder = remainder.replace(verse_norm, " ")
    leftover = []
    for token in arabic_tokens(remainder):
        cleaned = token.strip("#").strip()
        if cleaned and cleaned not in SAFE_ARABIC_TOKENS:
            leftover.append(cleaned)
    return leftover


def safe_template_caption(entry: dict) -> str:
    """Image-only caption: no Arabic verse, only queued poet + live URL."""
    poem_id = str(entry.get("poem_id") or "").strip()
    poet = str(entry.get("poet") or "").strip()
    url = poem_page_url(poem_id)
    byline = f"— {poet}\n\n" if poet else ""
    return (
        f"{byline}{url}\n\n"
        "#نور_الحكمة #الشعر_العربي #شعر #NoorAlHikmah "
        "#ClassicalArabicPoetry #ArabicPoetry"
    )


def caption_from_queue_fields(entry: dict) -> str:
    """Assemble a caption only from fields already on the queue entry."""
    arabic = str(entry.get("arabic") or "").strip()
    english = str(entry.get("english") or "").strip()
    poet = str(entry.get("poet") or "").strip()
    genre = str(entry.get("genre") or "").strip()
    poem_id = str(entry.get("poem_id") or "").strip()
    url = str(entry.get("poem_url") or "").strip() or poem_page_url(poem_id)
    parts: list[str] = []
    if arabic:
        parts.append(arabic)
    if english:
        parts.append(english)
    footer = []
    if poet:
        footer.append(f"— {poet}")
    if genre:
        footer.append(f"Genre: {genre.split('—')[0].strip()}")
    if footer:
        parts.append("\n".join(footer))
    parts.append(url)
    parts.append(
        "#نور_الحكمة #الشعر_العربي #شعر #NoorAlHikmah "
        "#ClassicalArabicPoetry #ArabicPoetry"
    )
    return "\n\n".join(parts)


def evaluate_caption(
    target: str,
    entry: dict,
    fetch_fn: FetchFn | None = None,
    skip_live: bool = False,
) -> tuple[str | None, dict | None]:
    """Return (caption, None) or (None, miss). Never invents verse text.

    Safer option: if Arabic cannot be verified against the live poem page,
    fail closed. Image-only safe template is used only when the queue entry
    itself has no Arabic (so there is no verse to invent or mis-attribute).
    """
    poem_id = str(entry.get("poem_id") or "").strip()
    expected_url = poem_page_url(poem_id)
    queued_url = str(entry.get("poem_url") or "").strip()
    if queued_url and queued_url != expected_url:
        return None, _miss(
            target,
            "unverified_caption",
            poem_id=poem_id,
            note="poem_url is not the live /poems/{id} page — fail closed",
        )

    arabic = str(entry.get("arabic") or "").strip()
    provided = str(entry.get("instagram_caption") or "").strip()

    if provided:
        if expected_url not in provided:
            return None, _miss(
                target,
                "unverified_caption",
                poem_id=poem_id,
                note="caption is missing the live poem URL — fail closed",
            )
        if arabic and normalize_verse(arabic) not in normalize_verse(provided):
            return None, _miss(
                target,
                "unverified_caption",
                poem_id=poem_id,
                note="caption Arabic is not the queued verse — fail closed, no invented lines",
            )
        extra = leftover_arabic(provided, arabic)
        if extra:
            return None, _miss(
                target,
                "unverified_caption",
                poem_id=poem_id,
                extra_arabic=extra[:8],
                note="caption contains Arabic that is not on the queue entry — fail closed",
            )
        caption = provided
    elif arabic:
        caption = caption_from_queue_fields(entry)
    else:
        # No verse on the queue: do not compose one. Image + link only.
        return safe_template_caption(entry), None

    if skip_live:
        return caption, None

    fn = fetch_fn or fetch_url
    status, body = fn(expected_url)
    if status != 200 or not body:
        return None, _miss(
            target,
            "unverified_caption",
            poem_id=poem_id,
            poem_url=expected_url,
            http=status,
            note="live poem page could not be fetched — fail closed, no invented verse",
        )

    live_text = extract_poem_jsonld_text(body)
    if not live_text:
        return None, _miss(
            target,
            "unverified_caption",
            poem_id=poem_id,
            poem_url=expected_url,
            note="live poem page has no JSON-LD verse — fail closed",
        )

    if normalize_verse(arabic) != normalize_verse(live_text):
        return None, _miss(
            target,
            "unverified_caption",
            poem_id=poem_id,
            poem_url=expected_url,
            note="queued Arabic does not match live /poems/{id} — fail closed, no substitute",
        )

    return caption, None


def evaluate_post(
    target: str,
    entry: dict | None,
    allow: set[str],
    card_base_url: str = "",
    head_fn: HeadFn | None = None,
    fetch_fn: FetchFn | None = None,
    skip_live_caption: bool = False,
) -> tuple[str | None, dict | None]:
    """ID + card + caption gates. Returns (caption, None) or (None, miss)."""
    miss = evaluate_entry(
        target, entry, allow, card_base_url=card_base_url, head_fn=head_fn,
    )
    if miss:
        return None, miss
    assert entry is not None
    return evaluate_caption(
        target, entry, fetch_fn=fetch_fn, skip_live=skip_live_caption,
    )


def run(
    *,
    target: str,
    dry: bool,
    card_base_url: str,
    token: str = "",
    ig_id: str = "",
    head_fn: HeadFn | None = None,
    fetch_fn: FetchFn | None = None,
    skip_live_caption: bool = False,
    queue_path: Path = QUEUE,
    allowlist_path: Path = ALLOWLIST,
    publish_fn: Callable[[str, str, str, str], tuple[int, dict]] | None = None,
) -> int:
    try:
        allow = load_allowlist(allowlist_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        log({"date": target, "status": "miss", "reason": "allowlist_unreadable",
             "error": str(exc), "note": "cannot fail-close without authenticated-ids.json"})
        return 1

    if not queue_path.is_file():
        log({"date": target, "status": "miss", "reason": "missing_poem_id",
             "note": "broadcast-queue.json is missing — fail closed, no substitute"})
        return 1

    try:
        entry = entry_for(target, queue_path)
    except (OSError, json.JSONDecodeError) as exc:
        log({"date": target, "status": "miss", "reason": "queue_unreadable",
             "error": str(exc)})
        return 1

    caption, miss = evaluate_post(
        target,
        entry,
        allow,
        card_base_url=card_base_url,
        head_fn=head_fn,
        fetch_fn=fetch_fn,
        skip_live_caption=skip_live_caption,
    )
    if miss:
        log(miss)
        return 1

    assert entry is not None
    assert caption is not None
    image_url = public_card_url(entry["card_path"], card_base_url)

    if dry:
        log({"date": target, "status": "dry_run", "poet": entry.get("poet"),
             "poem_id": entry.get("poem_id"), "image_url": image_url,
             "caption_len": len(caption), "caption": caption})
        return 0

    if missing_ig_secrets(token, ig_id):
        log({"date": target, "status": "error",
             "error": "missing IG_ACCESS_TOKEN / IG_USER_ID",
             "note": "secrets are empty — fail closed. Do not Enable the "
                     "schedule until CoS mints tokens. Never invent tokens."})
        return 1

    if publish_fn is not None:
        http, payload = publish_fn(image_url, caption, token, ig_id)
        if http != 200:
            log({"date": target, "status": "error", "step": "publish",
                 "http": http, "body": str(payload)[:300]})
            return 1
        log({"date": target, "status": "posted", "poet": entry.get("poet"),
             "poem_id": entry.get("poem_id"), "genre": entry.get("genre"),
             "media_id": payload.get("id"), "image_url": image_url})
        return 0

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


def main() -> int:
    target = os.environ.get("POST_DATE") or datetime.now(DUBAI).date().isoformat()
    dry = os.environ.get("DRY_RUN", "").lower() == "true"
    base = os.environ.get("CARD_BASE_URL", "")
    skip_live = os.environ.get("SKIP_LIVE_CAPTION", "").lower() == "true"
    return run(
        target=target,
        dry=dry,
        card_base_url=base,
        token=os.environ.get("IG_ACCESS_TOKEN", ""),
        ig_id=os.environ.get("IG_USER_ID", ""),
        skip_live_caption=skip_live,
    )


if __name__ == "__main__":
    sys.exit(main())
