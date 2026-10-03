"""Temporary automation containment; no network, credentials or publishing."""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent

class PublicationPauseTests(unittest.TestCase):
    def test_no_scheduled_trigger(self):
        text = (ROOT / ".github/workflows/daily-post.yml").read_text()
        self.assertNotRegex(text, r"(?m)^  schedule:")
        self.assertNotRegex(text, r"(?m)^\s*- cron:")

    def test_manual_dispatch_cannot_publish(self):
        text = (ROOT / ".github/workflows/daily-post.yml").read_text()
        self.assertRegex(text, r"(?ms)^  post:\n\s*#[^\n]*\n    if: \$\{\{ false \}\}\n    runs-on:")
        self.assertIn("workflow_dispatch:", text)

    def test_existing_20_queue_holds_preserved(self):
        queue = json.loads((ROOT / "broadcast-queue.json").read_text())["queue"]
        self.assertEqual(len(queue), 80)
        self.assertEqual(sum(row.get("disabled") is True for row in queue), 20)

if __name__ == "__main__":
    unittest.main()
