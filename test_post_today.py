#!/usr/bin/env python3
"""Fail-closed gates for post_today.py. Network is injected except Live* tests."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import post_today as pt
import preflight
import sync_allowlist

ROOT = Path(__file__).resolve().parent
ALLOW = pt.load_allowlist()
QUEUE = json.loads((ROOT / "broadcast-queue.json").read_text(encoding="utf-8"))
BASE = "https://nooralhikmah.com/"
SAMPLE = next(e for e in QUEUE["queue"] if e["poem_id"] == "1501")
_LOG_FH = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
pt.LOG = Path(_LOG_FH.name)
_LOG_FH.close()


def ok_head(_url: str) -> tuple[int | None, str]:
    return 200, "image/png"


def live_ok(arabic: str):
    body = (
        '<script type="application/ld+json">'
        + json.dumps({"@type": "CreativeWork", "text": arabic}, ensure_ascii=False)
        + "</script>"
    )
    return lambda _url: (200, body)


class DenylistTests(unittest.TestCase):
    def test_quarantine_ranges_are_complete(self):
        expected = (
            {str(i) for i in range(1370, 1374)}
            | {str(i) for i in range(1491, 1494)}
            | {"1607", "1608"}
            | {str(i) for i in range(1851, 1858)}
            | {"1507", "1543", "1858"}
        )
        self.assertEqual(pt.DENYLIST, expected)

    def test_denylist_not_on_allowlist(self):
        overlap = sorted(ALLOW & pt.DENYLIST)
        self.assertEqual(overlap, [])

    def test_denylisted_id_fail_closes_even_if_allowlisted(self):
        poisoned = set(ALLOW) | {"1543"}
        miss = pt.evaluate_entry(
            "2026-08-28",
            {"poem_id": "1543", "card_path": "cards/1543.png"},
            poisoned, card_base_url=BASE, head_fn=ok_head,
        )
        self.assertEqual(miss["reason"], "denylisted")
        self.assertEqual(miss["poem_id"], "1543")

    def test_each_quarantine_cluster_fail_closes(self):
        samples = ["1370", "1491", "1608", "1851", "1857", "1507", "1858"]
        allow = set(ALLOW) | set(samples)
        for pid in samples:
            miss = pt.evaluate_entry(
                "2026-08-28",
                {"poem_id": pid, "card_path": f"cards/{pid}.png"},
                allow, card_base_url=BASE, head_fn=ok_head,
            )
            self.assertEqual(miss["reason"], "denylisted", pid)

    def test_on_list_id_is_not_denylisted(self):
        self.assertFalse(pt.is_denylisted("1501"))
        self.assertFalse(pt.is_denylisted("1"))


class AllowlistIntegrityTests(unittest.TestCase):
    def test_certified_count_is_264(self):
        self.assertEqual(len(ALLOW), pt.EXPECTED_ALLOWLIST_COUNT)
        self.assertEqual(pt.EXPECTED_ALLOWLIST_COUNT, 264)

    def test_load_allowlist_rejects_wrong_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "authenticated-ids.json"
            ids = sorted(ALLOW, key=lambda x: int(x))[:263]
            path.write_text(json.dumps({"ids": ids}), encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                pt.load_allowlist(path)
            self.assertIn("263", str(ctx.exception))

    def test_load_allowlist_rejects_denylist_intersection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "authenticated-ids.json"
            ids = [i for i in ALLOW if i != "1501"] + ["1543"]
            path.write_text(json.dumps({"ids": ids}), encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                pt.load_allowlist(path)
            self.assertIn("denylist", str(ctx.exception))

    def test_load_allowlist_rejects_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "authenticated-ids.json"
            ids = list(ALLOW) + ["1501"]
            path.write_text(json.dumps({"ids": ids}), encoding="utf-8")
            with self.assertRaises(ValueError):
                pt.load_allowlist(path)

    def test_sync_check_passes_local_without_token(self):
        env = {k: v for k, v in os.environ.items() if k != "NOOR_PRIVATE_READ_TOKEN"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(sync_allowlist.main(["--check"]), 0)


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
        # 1543 is both off-list and denylisted — denylist is checked first
        self.assertEqual(miss["reason"], "denylisted")
        self.assertEqual(miss["poem_id"], "1543")

    def test_off_list_non_deny_id_fail_closes(self):
        miss = pt.evaluate_entry(
            "2026-08-28",
            {"poem_id": "99999", "card_path": "cards/99999.png"},
            ALLOW, card_base_url=BASE, head_fn=ok_head,
        )
        self.assertEqual(miss["reason"], "not_in_allowlist")
        self.assertEqual(miss["poem_id"], "99999")

    def test_disabled_entry_fail_closes(self):
        miss = pt.evaluate_entry(
            "2026-08-28",
            {"poem_id": "1501", "card_path": "cards/1501.png", "disabled": True},
            ALLOW, card_base_url=BASE, head_fn=ok_head,
        )
        self.assertEqual(miss["reason"], "disabled")

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

    def test_card_path_must_be_cards_id_png(self):
        miss = pt.evaluate_entry(
            "2026-08-28",
            {"poem_id": "1501", "card_path": "cards/1543.png"},
            ALLOW, card_base_url=BASE, head_fn=ok_head,
        )
        self.assertEqual(miss["reason"], "missing_card")
        self.assertIn("cards/1501.png", miss["note"])

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


class CaptionGateTests(unittest.TestCase):
    def test_queue_caption_passes_when_live_matches(self):
        caption, miss = pt.evaluate_caption(
            "2026-08-26", SAMPLE, fetch_fn=live_ok(SAMPLE["arabic"]),
        )
        self.assertIsNone(miss)
        self.assertEqual(caption, SAMPLE["instagram_caption"])

    def test_invented_arabic_in_caption_fail_closes(self):
        forged = dict(SAMPLE)
        forged["instagram_caption"] = (
            "هذا بيت مخترع لم يُكتب في المصدر\n\n"
            "https://nooralhikmah.com/poems/1501"
        )
        caption, miss = pt.evaluate_caption(
            "2026-08-26", forged, fetch_fn=live_ok(SAMPLE["arabic"]),
        )
        self.assertIsNone(caption)
        self.assertEqual(miss["reason"], "unverified_caption")
        self.assertIn("not the queued verse", miss["note"])

    def test_caption_without_poem_url_fail_closes(self):
        forged = dict(SAMPLE)
        forged["instagram_caption"] = SAMPLE["arabic"] + "\n\nno url here"
        caption, miss = pt.evaluate_caption(
            "2026-08-26", forged, skip_live=True,
        )
        self.assertIsNone(caption)
        self.assertEqual(miss["reason"], "unverified_caption")
        self.assertIn("poem URL", miss["note"])

    def test_wrong_poem_url_on_entry_fail_closes(self):
        forged = dict(SAMPLE)
        forged["poem_url"] = "https://nooralhikmah.com/poems/99999"
        caption, miss = pt.evaluate_caption(
            "2026-08-26", forged, skip_live=True,
        )
        self.assertIsNone(caption)
        self.assertEqual(miss["reason"], "unverified_caption")

    def test_live_mismatch_fail_closes(self):
        caption, miss = pt.evaluate_caption(
            "2026-08-26", SAMPLE, fetch_fn=live_ok("بيتٌ آخر تماماً"),
        )
        self.assertIsNone(caption)
        self.assertEqual(miss["reason"], "unverified_caption")
        self.assertIn("does not match live", miss["note"])

    def test_live_fetch_failure_fail_closes(self):
        caption, miss = pt.evaluate_caption(
            "2026-08-26", SAMPLE, fetch_fn=lambda _u: (None, ""),
        )
        self.assertIsNone(caption)
        self.assertEqual(miss["reason"], "unverified_caption")
        self.assertIn("could not be fetched", miss["note"])

    def test_live_page_without_jsonld_fail_closes(self):
        caption, miss = pt.evaluate_caption(
            "2026-08-26", SAMPLE, fetch_fn=lambda _u: (200, "<html>no verse</html>"),
        )
        self.assertIsNone(caption)
        self.assertEqual(miss["reason"], "unverified_caption")

    def test_no_arabic_uses_safe_template_no_verse(self):
        entry = {
            "poem_id": "1501",
            "poet": "Imru' al-Qais",
            "poem_url": "https://nooralhikmah.com/poems/1501",
        }
        caption, miss = pt.evaluate_caption("2026-08-26", entry, skip_live=True)
        self.assertIsNone(miss)
        self.assertIn("https://nooralhikmah.com/poems/1501", caption)
        self.assertIn("Imru' al-Qais", caption)
        leftover = [t for t in pt.arabic_tokens(caption) if t.strip("#") not in pt.SAFE_ARABIC_TOKENS]
        self.assertEqual(leftover, [])

    def test_never_composes_arabic_from_nothing(self):
        caption, miss = pt.evaluate_caption(
            "2026-08-26",
            {"poem_id": "1501", "poet": "Imru' al-Qais",
             "poem_url": "https://nooralhikmah.com/poems/1501"},
            skip_live=True,
        )
        self.assertIsNone(miss)
        self.assertNotIn("وَلَيْلٍ", caption)

    def test_assembled_caption_uses_only_queue_fields(self):
        entry = {
            "poem_id": "1501",
            "arabic": SAMPLE["arabic"],
            "english": SAMPLE["english"],
            "poet": SAMPLE["poet"],
            "genre": SAMPLE["genre"],
            "poem_url": SAMPLE["poem_url"],
        }
        caption, miss = pt.evaluate_caption(
            "2026-08-26", entry, fetch_fn=live_ok(SAMPLE["arabic"]),
        )
        self.assertIsNone(miss)
        self.assertIn(SAMPLE["arabic"], caption)
        self.assertIn(SAMPLE["english"], caption)
        self.assertIn(SAMPLE["poet"], caption)
        self.assertIn(SAMPLE["poem_url"], caption)

    def test_extract_poem_jsonld_text(self):
        html = '<script type="application/ld+json">{"text":"بيتٌ"}</script>'
        self.assertEqual(pt.extract_poem_jsonld_text(html), "بيتٌ")
        self.assertIsNone(pt.extract_poem_jsonld_text("<html></html>"))


class EmptySecretsTests(unittest.TestCase):
    def test_missing_ig_secrets_helper(self):
        self.assertTrue(pt.missing_ig_secrets("", ""))
        self.assertTrue(pt.missing_ig_secrets("  ", "123"))
        self.assertTrue(pt.missing_ig_secrets("tok", ""))
        self.assertFalse(pt.missing_ig_secrets("tok", "123"))

    def test_empty_secrets_fail_closed_when_not_dry_run(self):
        code = pt.run(
            target="2026-08-26",
            dry=False,
            card_base_url=BASE,
            token="",
            ig_id="",
            head_fn=ok_head,
            fetch_fn=live_ok(SAMPLE["arabic"]),
        )
        self.assertEqual(code, 1)
        last = pt.LOG.read_text(encoding="utf-8").strip().splitlines()[-1]
        rec = json.loads(last)
        self.assertEqual(rec["status"], "error")
        self.assertIn("IG_ACCESS_TOKEN", rec["error"])

    def test_dry_run_succeeds_without_secrets(self):
        code = pt.run(
            target="2026-08-26",
            dry=True,
            card_base_url=BASE,
            token="",
            ig_id="",
            head_fn=ok_head,
            fetch_fn=live_ok(SAMPLE["arabic"]),
        )
        self.assertEqual(code, 0)

    def test_publish_is_not_called_when_secrets_empty(self):
        called = {"n": 0}

        def boom(*_a, **_k):
            called["n"] += 1
            raise AssertionError("Graph publish must not run without secrets")

        code = pt.run(
            target="2026-08-26",
            dry=False,
            card_base_url=BASE,
            token="",
            ig_id="",
            head_fn=ok_head,
            fetch_fn=live_ok(SAMPLE["arabic"]),
            publish_fn=boom,
        )
        self.assertEqual(code, 1)
        self.assertEqual(called["n"], 0)


class TokenHygieneTests(unittest.TestCase):
    def test_clean_secret_strips_whitespace_quotes_newlines(self):
        for raw in ("tok", "tok\n", "  tok \r\n", '"tok"', "'tok'\n",
                    "\u201ctok\u201d", ' "tok"\n ', 'tok"'):
            self.assertEqual(pt.clean_secret(raw), "tok", repr(raw))
        self.assertEqual(pt.clean_secret(None), "")
        self.assertEqual(pt.clean_secret('""'), "")
        self.assertEqual(pt.clean_secret("a-b_c.d"), "a-b_c.d")

    def test_quoted_blank_secret_counts_as_missing(self):
        self.assertTrue(pt.missing_ig_secrets('""', "123"))
        self.assertTrue(pt.missing_ig_secrets("tok", "''"))

    def test_publish_receives_cleaned_secrets(self):
        seen = {}

        def pub(image_url, caption, token, ig_id):
            seen["token"], seen["ig_id"] = token, ig_id
            return 200, {"id": "m1"}

        code = pt.run(
            target="2026-08-26", dry=False, card_base_url=BASE,
            token=' "tok123"\n', ig_id="'42'\n",
            head_fn=ok_head, fetch_fn=live_ok(SAMPLE["arabic"]), publish_fn=pub,
        )
        self.assertEqual(code, 0)
        self.assertEqual(seen, {"token": "tok123", "ig_id": "42"})


class CheckTokenTests(unittest.TestCase):
    SECRET = "SUPER-SECRET-TOKEN-VALUE"

    def _get(self, http, body, seen=None):
        def fn(url, params):
            if seen is not None:
                seen.update(url=url, params=dict(params))
            return http, body
        return fn

    def test_ok_when_user_id_matches_and_token_is_cleaned(self):
        seen = {}
        ok, msg = pt.check_token(
            f' "{self.SECRET}"\n', "1784\n",
            get_fn=self._get(200, {"user_id": "1784", "username": "u"}, seen))
        self.assertTrue(ok, msg)
        self.assertEqual(seen["url"], "https://graph.instagram.com/v23.0/me")
        self.assertEqual(seen["params"]["fields"], "user_id,username")
        self.assertEqual(seen["params"]["access_token"], self.SECRET)
        self.assertNotIn(self.SECRET, msg)
        self.assertNotIn("1784", msg)

    def test_oauth_190_fails_without_leaking_token(self):
        body = {"error": {"message": "Cannot parse access token",
                          "type": "OAuthException", "code": 190}}
        ok, msg = pt.check_token(self.SECRET, "1784", get_fn=self._get(400, body))
        self.assertFalse(ok)
        self.assertIn("190", msg)
        self.assertNotIn(self.SECRET, msg)

    def test_user_id_mismatch_fails_without_printing_ids(self):
        ok, msg = pt.check_token(self.SECRET, "1784",
                                 get_fn=self._get(200, {"user_id": "9999"}))
        self.assertFalse(ok)
        self.assertIn("does NOT match", msg)
        for v in (self.SECRET, "1784", "9999"):
            self.assertNotIn(v, msg)

    def test_empty_inputs_fail_without_network(self):
        def boom(*_a, **_k):
            raise AssertionError("no request without a token")
        self.assertFalse(pt.check_token('""', "1", get_fn=boom)[0])
        self.assertFalse(pt.check_token("tok", " ", get_fn=boom)[0])

    def test_exception_text_is_not_echoed(self):
        def fn(url, params):
            raise RuntimeError(f"boom {params['access_token']}")
        ok, msg = pt.check_token(self.SECRET, "1", get_fn=fn)
        self.assertFalse(ok)
        self.assertNotIn(self.SECRET, msg)

    def test_missing_user_id_in_response_fails(self):
        ok, _ = pt.check_token(self.SECRET, "1", get_fn=self._get(200, {}))
        self.assertFalse(ok)


class OffListQueueTests(unittest.TestCase):
    def _temp_queue(self, rows: list[dict]) -> Path:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump({"queue": rows}, tmp, ensure_ascii=False)
        tmp.close()
        return Path(tmp.name)

    def test_off_list_queue_row_fail_closes_run(self):
        path = self._temp_queue([{
            "scheduled_for": "2026-12-01",
            "poem_id": "99999",
            "card_path": "cards/99999.png",
            "arabic": "لا",
            "instagram_caption": "لا\n\nhttps://nooralhikmah.com/poems/99999",
            "poem_url": "https://nooralhikmah.com/poems/99999",
        }])
        try:
            code = pt.run(
                target="2026-12-01",
                dry=True,
                card_base_url=BASE,
                head_fn=ok_head,
                fetch_fn=live_ok("لا"),
                queue_path=path,
            )
            self.assertEqual(code, 1)
        finally:
            path.unlink(missing_ok=True)

    def test_denylisted_queue_row_fail_closes_run(self):
        path = self._temp_queue([{
            "scheduled_for": "2026-12-01",
            "poem_id": "1370",
            "card_path": "cards/1370.png",
            "arabic": "لا",
            "instagram_caption": "لا\n\nhttps://nooralhikmah.com/poems/1370",
            "poem_url": "https://nooralhikmah.com/poems/1370",
        }])
        try:
            code = pt.run(
                target="2026-12-01",
                dry=True,
                card_base_url=BASE,
                head_fn=ok_head,
                fetch_fn=live_ok("لا"),
                queue_path=path,
            )
            self.assertEqual(code, 1)
            last = pt.LOG.read_text(encoding="utf-8").strip().splitlines()[-1]
            self.assertEqual(json.loads(last)["reason"], "denylisted")
        finally:
            path.unlink(missing_ok=True)

    def test_preflight_flags_off_list_future_day(self):
        path = self._temp_queue([{
            "scheduled_for": "2026-12-01",
            "poem_id": "99999",
            "card_path": "cards/99999.png",
        }])
        try:
            misses = preflight.check_days(
                ["2026-12-01"],
                allow=ALLOW,
                card_base_url=BASE,
                skip_live_caption=True,
                skip_live_head=True,
                queue_path=path,
            )
            self.assertEqual(misses[0]["reason"], "not_in_allowlist")
        finally:
            path.unlink(missing_ok=True)

    def test_preflight_flags_missing_future_day(self):
        path = self._temp_queue([])
        try:
            misses = preflight.check_days(
                ["2026-12-01"],
                allow=ALLOW,
                card_base_url=BASE,
                skip_live_caption=True,
                skip_live_head=True,
                queue_path=path,
            )
            self.assertEqual(misses[0]["reason"], "missing_poem_id")
        finally:
            path.unlink(missing_ok=True)


class QueueContractTests(unittest.TestCase):
    def test_every_poem_id_on_allowlist(self):
        ids = [str(e["poem_id"]) for e in QUEUE["queue"]]
        off = [i for i in ids if i not in ALLOW]
        self.assertEqual(off, [], f"off-list IDs in queue: {off}")

    def test_no_denylisted_ids_in_queue(self):
        denied = [str(e["poem_id"]) for e in QUEUE["queue"] if pt.is_denylisted(e["poem_id"])]
        self.assertEqual(denied, [], f"denylisted IDs in queue: {denied}")

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

    def test_queue_captions_are_authentic_against_own_arabic(self):
        for e in QUEUE["queue"]:
            caption, miss = pt.evaluate_caption(
                e["scheduled_for"][:10], e, fetch_fn=live_ok(e["arabic"]),
            )
            self.assertIsNone(miss, f"caption miss for {e['poem_id']}: {miss}")
            self.assertIn(e["arabic"], caption)

    def test_public_card_url_joins_base_and_path(self):
        self.assertEqual(
            pt.public_card_url("cards/1.png", "https://nooralhikmah.com/"),
            "https://nooralhikmah.com/cards/1.png",
        )

    def test_preflight_next_days_pass_without_live(self):
        start = QUEUE["queue"][0]["scheduled_for"][:10]
        misses = preflight.check_days(
            preflight.upcoming_dates(start, min(14, len(QUEUE["queue"]))),
            allow=ALLOW,
            card_base_url=BASE,
            skip_live_caption=True,
            skip_live_head=True,
        )
        self.assertEqual(misses, [])


class LiveHeadTests(unittest.TestCase):
    """Public site contract: on-list PNG 200, off-list 404."""

    def test_live_on_list_png(self):
        status, ctype = pt.head_card_url("https://nooralhikmah.com/cards/1501.png")
        self.assertEqual(status, 200)
        self.assertTrue(pt.is_png_content_type(ctype), ctype)

    def test_live_off_list_404(self):
        status, ctype = pt.head_card_url("https://nooralhikmah.com/cards/1543.png")
        self.assertEqual(status, 404)
        miss = pt.evaluate_entry(
            "2026-08-28",
            {"poem_id": "1501", "card_path": "cards/1501.png"},
            ALLOW, card_base_url=BASE,
            head_fn=lambda _u: pt.head_card_url("https://nooralhikmah.com/cards/1543.png"),
        )
        self.assertEqual(miss["reason"], "missing_card")
        self.assertEqual(status, 404)
        self.assertFalse(pt.is_png_content_type(ctype))

    def test_live_poem_page_matches_queue_arabic(self):
        status, html = pt.fetch_url("https://nooralhikmah.com/poems/1501")
        self.assertEqual(status, 200)
        live = pt.extract_poem_jsonld_text(html)
        self.assertEqual(pt.normalize_verse(live), pt.normalize_verse(SAMPLE["arabic"]))


if __name__ == "__main__":
    unittest.main()
