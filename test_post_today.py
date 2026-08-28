#!/usr/bin/env python3
"""Fail-closed gates for post_today.py. HEAD is injected; no Graph post."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

import post_today as pt

ROOT = Path(__file__).resolve().parent
ALLOW = pt.load_allowlist()
QUEUE = json.loads((ROOT / "broadcast-queue.json").read_text(encoding="utf-8"))
BASE = "https://nooralhikmah.com/"


def ok_head(_url: str) -> tuple[int | None, str]:
    return 200, "image/png"


class EvaluateEntryTests(unittest.TestCase):
    def test_missing_queue_day(self):
        miss = pt.evaluate_entry("1999-01-01", None, ALLOW, card_base_url=BASE, head_fn=ok_head)
        self.assertIsNotNone(miss)
        self.assertEqual(miss["reason"], "missing_poem_id")

    def test_missing_poem_id(self):
        miss = pt.evaluate_entry(
            "2026-08-28", {"scheduled_for": "2026-08-28"}, ALLOW,
            card_base_url=BASE, head_fn=ok_head,
        )
        self.assertEqual(miss["reason"], "missing_poem_id")

    def test_off_list_id_fail_closes(self):
        miss = pt.evaluate_entry(
            "2026-08-28",
            {"poem_id": "1543", "card_path": "cards/1543.png"},
            ALLOW, card_base_url=BASE, head_fn=ok_head,
        )
        self.assertEqual(miss["reason"], "not_in_allowlist")
        self.assertEqual(miss["poem_id"], "1543")

    def test_missing_card_path(self):
        miss = pt.evaluate_entry(
            "2026-08-28", {"poem_id": "1501"}, ALLOW,
            card_base_url=BASE, head_fn=ok_head,
        )
        self.assertEqual(miss["reason"], "missing_card")

    def test_url_not_png_path(self):
        miss = pt.evaluate_entry(
            "2026-08-28",
            {"poem_id": "1501", "card_path": "cards/1501.jpg"},
            ALLOW, card_base_url=BASE, head_fn=ok_head,
        )
        self.assertEqual(miss["reason"], "missing_card")
        self.assertIn("not a PNG", miss["note"])

    def test_head_404_fail_closes(self):
        miss = pt.evaluate_entry(
            "2026-08-28",
            {"poem_id": "1501", "card_path": "cards/1501.png"},
            ALLOW, card_base_url=BASE,
            head_fn=lambda _u: (404, "text/html; charset=utf-8"),
        )
        self.assertEqual(miss["reason"], "missing_card")
        self.assertEqual(miss["http"], 404)

    def test_head_200_html_fail_closes(self):
        miss = pt.evaluate_entry(
            "2026-08-28",
            {"poem_id": "1501", "card_path": "cards/1501.png"},
            ALLOW, card_base_url=BASE,
            head_fn=lambda _u: (200, "text/html"),
        )
        self.assertEqual(miss["reason"], "missing_card")

    def test_head_200_jpeg_fail_closes(self):
        miss = pt.evaluate_entry(
            "2026-08-28",
            {"poem_id": "1501", "card_path": "cards/1501.png"},
            ALLOW, card_base_url=BASE,
            head_fn=lambda _u: (200, "image/jpeg"),
        )
        self.assertEqual(miss["reason"], "missing_card")

    def test_head_network_error_fail_closes(self):
        miss = pt.evaluate_entry(
            "2026-08-28",
            {"poem_id": "1501", "card_path": "cards/1501.png"},
            ALLOW, card_base_url=BASE,
            head_fn=lambda _u: (None, ""),
        )
        self.assertEqual(miss["reason"], "missing_card")

    def test_missing_card_base_url(self):
        miss = pt.evaluate_entry(
            "2026-08-28",
            {"poem_id": "1501", "card_path": "cards/1501.png"},
            ALLOW, card_base_url="", head_fn=ok_head,
        )
        self.assertEqual(miss["reason"], "missing_card")

    def test_head_200_png_passes(self):
        miss = pt.evaluate_entry(
            "2026-08-28",
            {"poem_id": "1501", "card_path": "cards/1501.png"},
            ALLOW, card_base_url=BASE, head_fn=ok_head,
        )
        self.assertIsNone(miss)

    def test_png_content_type_ignores_charset(self):
        miss = pt.evaluate_entry(
            "2026-08-28",
            {"poem_id": "1501", "card_path": "cards/1501.png"},
            ALLOW, card_base_url=BASE,
            head_fn=lambda _u: (200, "image/png; charset=binary"),
        )
        self.assertIsNone(miss)

    def test_does_not_check_local_disk(self):
        # Site cards are not in this repo. Passing HEAD must not require a local PNG.
        miss = pt.evaluate_entry(
            "2026-08-28",
            {"poem_id": "1", "card_path": "cards/1.png"},
            ALLOW, card_base_url=BASE, head_fn=ok_head,
        )
        self.assertIsNone(miss)
        self.assertFalse((ROOT / "cards" / "1.png").exists())


class QueueContractTests(unittest.TestCase):
    def test_every_poem_id_on_allowlist(self):
        ids = [str(e["poem_id"]) for e in QUEUE["queue"]]
        off = [i for i in ids if i not in ALLOW]
        self.assertEqual(off, [], f"off-list IDs in queue: {off}")

    def test_every_card_path_is_cards_id_png(self):
        bad = []
        for e in QUEUE["queue"]:
            expected = f"cards/{e['poem_id']}.png"
            if e.get("card_path") != expected:
                bad.append((e["poem_id"], e.get("card_path"), expected))
        self.assertEqual(bad, [], f"card_path mismatches: {bad}")

    def test_captions_point_at_live_poems(self):
        for e in QUEUE["queue"]:
            url = f"https://nooralhikmah.com/poems/{e['poem_id']}"
            self.assertEqual(e.get("poem_url"), url)
            self.assertIn(url, e.get("instagram_caption") or "")

    def test_queue_rows_pass_evaluate_with_ok_head(self):
        for e in QUEUE["queue"]:
            miss = pt.evaluate_entry(
                e["scheduled_for"][:10], e, ALLOW,
                card_base_url=BASE, head_fn=ok_head,
            )
            self.assertIsNone(miss, f"unexpected miss for {e['poem_id']}: {miss}")

    def test_public_card_url_joins_base_and_path(self):
        self.assertEqual(
            pt.public_card_url("cards/1.png", "https://nooralhikmah.com/"),
            "https://nooralhikmah.com/cards/1.png",
        )


class LiveHeadTests(unittest.TestCase):
    """Public site contract (28 Aug 2026): on-list PNG 200, off-list 404."""

    def test_live_on_list_png(self):
        status, ctype = pt.head_card_url("https://nooralhikmah.com/cards/1501.png")
        self.assertEqual(status, 200)
        self.assertTrue(pt.is_png_content_type(ctype), ctype)

    def test_live_off_list_404(self):
        status, ctype = pt.head_card_url("https://nooralhikmah.com/cards/1543.png")
        self.assertEqual(status, 404)
        miss = pt.evaluate_entry(
            "2026-08-28",
            {"poem_id": "1501", "card_path": "cards/1543.png"},
            ALLOW, card_base_url=BASE, head_fn=pt.head_card_url,
        )
        # 1501 is on the allowlist, but cards/1543.png is not a public PNG
        self.assertEqual(miss["reason"], "missing_card")
        self.assertEqual(status, 404)
        self.assertFalse(pt.is_png_content_type(ctype))


if __name__ == "__main__":
    unittest.main()
