# noor-social

Always-on daily Instagram publishing for **Noor Al Hikmah** (@nooralhikmahapp).

This repo exists so daily posting never depends on a laptop being awake. A
scheduled GitHub Action posts one authenticated verse card per day from
`broadcast-queue.json`. Instagram fetches the card from the live site:
`https://nooralhikmah.com/cards/{id}.png`. GitHub raw is no longer the fetch URL.

**Publishing is on.** `authenticated-ids.json` is the contract: Instagram may emit only those 264 certified poem IDs. `post_today.py` fail-closes (exit 1, no post, no substitute ID) if today's `poem_id` is missing, not on the allowlist, or the public card URL is not HTTP 200 with an `image/png` content-type.

## How it works

```
authenticated-ids.json ──┐
broadcast-queue.json     ─┤→  GitHub Action (cron 05:30 UTC)
post_today.py            ─┤        HEAD https://nooralhikmah.com/cards/{id}.png
                         ─┘        → Instagram Graph API (@nooralhikmahapp)
```

- **`authenticated-ids.json`** — copy of the product allowlist (264 IDs). The Action fail-closes against this file so it does not need the private app repo.
- **`post_today.py`** — picks the entry whose `scheduled_for` is today (Asia/Dubai), checks the allowlist, HEADs `CARD_BASE_URL` + `card_path`, then (only if every gate passes) posts via the two-step Graph API flow. Site cards are not stored in this repo; a local-file check would block every post.
- **`.github/workflows/daily-post.yml`** — daily cron `30 5 * * *` (05:30 UTC = 09:30 Asia/Dubai). `CARD_BASE_URL` is `https://nooralhikmah.com/` (trailing slash required). Manual `workflow_dispatch` defaults to dry-run.
- **`post-log.jsonl`** — append-only record of every run (posted / dry_run / miss / error).

Captions link to `https://nooralhikmah.com/poems/{id}`, not `/app`. Verse text in the queue is copied from shipping JSON / the live poem page — never composed.

## One-time setup (repo owner)

Add two repository secrets (Settings → Secrets and variables → Actions):

| Secret | Value |
|---|---|
| `IG_ACCESS_TOKEN` | Instagram Graph API long-lived token (`meta.ig_access_token`) |
| `IG_USER_ID` | Instagram business account id (`meta.instagram_business_account_id`) |

Actions → **Daily Instagram Post** → **Run workflow** (leave dry-run on) → check the log output. Flip dry-run off for a real post. The scheduled job posts for real.

## Monthly maintenance (the only recurring task)

Only IDs on `authenticated-ids.json` (264) may be queued, and only when `https://nooralhikmah.com/cards/{id}.png` returns HTTP 200 with an `image/png` content-type. Do not pad with off-list IDs. Do not invent verses. Queue `card_path` is always `cards/{poem_id}.png` — do not copy PNGs into this repo.

```bash
python3 ~/Noor-Engine/scripts/build_content_queue.py     # next calendar from the allowlist
# card_path must be cards/{poem_id}.png — images stay on nooralhikmah.com
cp ~/Noor-Engine/state/broadcast-queue.json broadcast-queue.json
git add -A && git commit -m "queue: refill" && git push
```

**Token refresh:** Instagram long-lived tokens last ~60 days. Refresh with
`~/Noor-Engine/scripts/refresh_meta_token.py` and update the `IG_ACCESS_TOKEN`
secret before expiry (do it alongside the monthly refill).

## Why public

The cards are images of public-domain classical Arabic verse, created
to be posted publicly. Instagram can only fetch images from public URLs.
Those URLs are on the live site (`nooralhikmah.com/cards/{id}.png`), gated
to the 264 certified IDs — not GitHub raw.
