# noor-social

Always-on daily Instagram publishing for **Noor Al Hikmah** (@nooralhikmahapp).

This repo exists so daily posting never depends on a laptop being awake. A
scheduled GitHub Action posts one authenticated verse card per day from
`broadcast-queue.json`, fetching the card image from this repo's own public
`raw.githubusercontent` URL.

**Publishing is frozen until `.github/workflows/daily-post.yml` is explicitly re-enabled.** `authenticated-ids.json` is the contract: Instagram may emit only those 264 certified poem IDs. `post_today.py` fail-closes (exit 1, no post, no substitute ID) if today's `poem_id` is missing, not on the allowlist, or the card PNG is absent.

## How it works

```
authenticated-ids.json ──┐
broadcast-queue.json     ─┤
assets/profile/feed-v2/*.png ┤→  GitHub Action (disabled)  →  Instagram Graph API
post_today.py            ─┘        post_today.py                    @nooralhikmahapp
```

- **`authenticated-ids.json`** — copy of the product allowlist (264 IDs). The Action fail-closes against this file so it does not need the private app repo.
- **`post_today.py`** — picks the entry whose `scheduled_for` is today (Asia/Dubai), checks the allowlist and card file, then (only if every gate passes) posts via the two-step Graph API flow.
- **`.github/workflows/daily-post.yml`** — daily cron is commented out and the job is `if: false`. Do not restore either until publishing is deliberately turned back on. Manual `workflow_dispatch` remains in the file (defaults to dry-run) for when it is re-enabled.
- **`post-log.jsonl`** — append-only record of every run (posted / dry_run / miss / error).

Captions link to `https://nooralhikmah.com/poems/{id}`, not `/app`. Verse text in the queue is copied from shipping JSON / the live poem page — never composed.

## One-time setup (repo owner)

Add two repository secrets (Settings → Secrets and variables → Actions):

| Secret | Value |
|---|---|
| `IG_ACCESS_TOKEN` | Instagram Graph API long-lived token (`meta.ig_access_token`) |
| `IG_USER_ID` | Instagram business account id (`meta.instagram_business_account_id`) |

The workflow stays disabled. After it is explicitly re-enabled: Actions → **Daily Instagram Post** → **Run workflow** (leave dry-run on) → check the log output. Flip dry-run off for a real post.

## Monthly maintenance (the only recurring task)

Only IDs on `authenticated-ids.json` may be queued, and only when a card PNG already exists. Do not pad with off-list IDs or invent cards / verses.

```bash
python3 ~/Noor-Engine/scripts/build_content_queue.py     # next calendar from the allowlist
# render any new cards, then:
cp ~/Noor-Engine/state/broadcast-queue.json broadcast-queue.json
cp ~/Noor-Engine/assets/profile/feed-v2/*.png assets/profile/feed-v2/
git add -A && git commit -m "queue: refill" && git push
```

**Token refresh:** Instagram long-lived tokens last ~60 days. Refresh with
`~/Noor-Engine/scripts/refresh_meta_token.py` and update the `IG_ACCESS_TOKEN`
secret before expiry (do it alongside the monthly refill).

## Why public

The cards are 1080×1080 images of public-domain classical Arabic verse, created
to be posted publicly. Instagram can only fetch images from public URLs, so the
cards live in a public repo — decoupled from the private app source.
