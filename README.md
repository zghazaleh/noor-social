# noor-social

Always-on daily Instagram publishing for **Noor Al Hikmah** (@nooralhikmahapp).

This repo exists so daily posting never depends on a laptop being awake. A
scheduled GitHub Action posts one authenticated verse card per day from
`broadcast-queue.json`, fetching the card image from this repo's own public
`raw.githubusercontent` URL.

## How it works

```
broadcast-queue.json   ──┐
assets/profile/feed/*.png ┤→  GitHub Action (daily 05:30 UTC)  →  Instagram Graph API
post_today.py            ─┘        post_today.py                    @nooralhikmahapp
```

- **`post_today.py`** — picks the entry whose `scheduled_for` is today (Asia/Dubai),
  builds the public image URL, and posts via the two-step Graph API flow.
- **`.github/workflows/daily-post.yml`** — the daily cron + a manual `workflow_dispatch`
  (defaults to dry-run) for testing. Commits the post-log each run, which also keeps
  the scheduled workflow from auto-disabling after 60 days of inactivity.
- **`post-log.jsonl`** — append-only record of every run (posted / dry_run / no_entry / error).

## One-time setup (repo owner)

Add two repository secrets (Settings → Secrets and variables → Actions):

| Secret | Value |
|---|---|
| `IG_ACCESS_TOKEN` | Instagram Graph API long-lived token (`meta.ig_access_token`) |
| `IG_USER_ID` | Instagram business account id (`meta.instagram_business_account_id`) |

Then test: Actions → **Daily Instagram Post** → **Run workflow** (leave dry-run on) →
check the log output. Flip dry-run off for a real post.

## Monthly maintenance (the only recurring task)

Run on the engine machine, then push:

```bash
python3 ~/Noor-Engine/scripts/build_content_queue.py     # next 30-day calendar
# render any new cards, then:
cp ~/Noor-Engine/state/broadcast-queue.json broadcast-queue.json
cp ~/Noor-Engine/assets/profile/feed/*.png assets/profile/feed/
git add -A && git commit -m "queue: refill" && git push
```

**Token refresh:** Instagram long-lived tokens last ~60 days. Refresh with
`~/Noor-Engine/scripts/refresh_meta_token.py` and update the `IG_ACCESS_TOKEN`
secret before expiry (do it alongside the monthly refill).

## Why public

The cards are 1080×1080 images of public-domain classical Arabic verse, created
to be posted publicly. Instagram can only fetch images from public URLs, so the
cards live in a public repo — decoupled from the private app source.
