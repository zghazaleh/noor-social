# noor-social

Fail-closed daily Instagram publishing for **Noor Al Hikmah** (@nooralhikmahapp).

This public repo exists so Instagram can fetch verse cards from the live site
and so a GitHub Action can post one **authenticated** card per day. It must
stay public: Instagram only fetches `https://nooralhikmah.com/cards/{id}.png`.

**#1 property: hallucination is impossible.** The publisher never invents a
poem ID, never substitutes another ID, and never composes Arabic verse. Any
miss exits 1 with no post.

## Publishing contract

| Rule | Detail |
|---|---|
| Allowlist only | Instagram may emit only the **264** IDs in `authenticated-ids.json`. That file is a copy of `content/poems/authenticated-ids.json` in the private product repo (`zghazaleh/Noor-Al-Hikmah-V1.1`). Do not invent IDs. |
| Hard denylist | These IDs can **never** post, even if they appear on the allowlist or queue: Qabbani shipping `1370–1373`, `1491–1493`, `1607–1608`; generated cluster `1851–1857`; known off-list `1507`, `1543`, `1858`. Encoded in `post_today.py`, not a JSON someone can quietly edit. |
| Card URL | `CARD_BASE_URL` + `card_path`. `card_path` must be `cards/{id}.png`. HEAD must be HTTP 200 with `image/png`. Cards live on the site, not in this repo. |
| Caption | Reuse fields already on the queue row. If the row has Arabic, it must match the JSON-LD `text` on `https://nooralhikmah.com/poems/{id}`. Fetch failure or mismatch → **fail closed** (no post). Do not use `/today` as a fallback — that page is a different daily verse. Never compose new Arabic poetry. |
| Secrets | `IG_ACCESS_TOKEN` and `IG_USER_ID` are repository secrets. Empty secrets fail a real post. Both are whitespace/quote/newline-stripped before use. Do not invent tokens. Do not commit them. |
| Dry-run first | Manual `workflow_dispatch` defaults to dry-run. The schedule posts for real (fail-closed) once the workflow is enabled and both secrets are set. |

```
authenticated-ids.json ──┐  264 IDs, denylist ∩ allowlist = ∅
DENYLIST (in code)       ─┤
broadcast-queue.json     ─┤→  GitHub Action (cron 05:30 UTC = 09:30 Dubai)
post_today.py            ─┤        HEAD  https://nooralhikmah.com/cards/{id}.png
                         ─┤        GET   https://nooralhikmah.com/poems/{id}
                         ─┘        → Instagram Graph API (@nooralhikmahapp)
                                    only if every gate passes
```

## How a day is resolved

`post_today.py` picks the `broadcast-queue.json` row whose `scheduled_for` is
today (Asia/Dubai, or `POST_DATE`). Then, in order:

1. Queue row exists and is not `disabled`.
2. `poem_id` present.
3. `poem_id` not on the hard denylist.
4. `poem_id` on the 264-ID allowlist (count and denylist intersection checked at load).
5. `card_path == cards/{id}.png`.
6. `CARD_BASE_URL` set (`https://nooralhikmah.com/`, trailing slash).
7. HEAD of the public card is `200` + `image/png`.
8. Caption authenticity: queued Arabic (if any) equals live `/poems/{id}` JSON-LD `text`; caption contains that URL and no extra Arabic.
9. Real post only: both Meta secrets present, and the token passes the health check (`GET /me`, `user_id` equals `IG_USER_ID`).

Any miss → exit 1, append `post-log.jsonl`, **no substitute ID**.

If a row has **no** Arabic at all, the publisher posts the image with a safe
template (poet + live poem URL + hashtags) that contains no verse. If Arabic
**is** present and cannot be verified, it does **not** fall back to that
template — it fail-closes. Posting a wrong line is worse than posting nothing.

## Workflows

| Workflow | File | Posts? | Enable? |
|---|---|---|---|
| **Daily Instagram Post** | `.github/workflows/daily-post.yml` | Yes, on schedule | Keep enabled; needs `IG_ACCESS_TOKEN` and `IG_USER_ID` (empty secrets fail the job, no post). Schedule is `30 5 * * *` (09:30 Dubai). Manual run defaults to `dry_run=true`. |
| Publish contract tests | `.github/workflows/ci.yml` | No | Keep enabled. |
| Queue preflight (14 days) | `.github/workflows/preflight.yml` | No | Keep enabled. Live HEAD + caption check of the next 14 days. Its `token-health` job (04:00 UTC, not on PRs) calls `GET /me?fields=user_id,username` and fails if the token is invalid or its `user_id` differs from `IG_USER_ID`. |
| IG token refresh | `.github/workflows/ig-token-refresh.yml` | No | Keep enabled. Monthly (`0 2 1 * *`) + manual. See [Token refresh](#token-refresh). |

The broken one-shot `post-2026-08-28.yml` has been removed. Do not add
push-triggered post workflows.

**Empty secrets fail loudly.** If `IG_ACCESS_TOKEN` or `IG_USER_ID` is empty
the job fails with no post and no invented token. Keep the Daily workflow
enabled so the schedule runs; the dry-run default only applies to manual runs.

## One-time setup (repo owner)

1. Confirm this repo is **public** (Instagram cannot fetch a private card URL).
2. Settings → Secrets and variables → Actions:

   | Secret | Value |
   |---|---|
   | `IG_ACCESS_TOKEN` | Instagram Graph API long-lived token |
   | `IG_USER_ID` | Instagram business account id |
   | `GH_SECRETS_PAT` | Optional. Fine-grained PAT for this repo only, permission *Secrets: read and write*. Lets `ig-token-refresh` store a rotated token. |
   | `NOOR_PRIVATE_READ_TOKEN` | Optional. Read-only PAT for `zghazaleh/Noor-Al-Hikmah-V1.1` so CI can confirm the 264 IDs still match `content/poems/authenticated-ids.json`. |

3. Actions → **Daily Instagram Post** → **Run workflow** with dry-run **on**.
   Confirm the log: `status=dry_run`, correct `poem_id`, public `image_url`.
4. Store the real secrets, run dry-run once more, and confirm the
   *Queue preflight* `token-health` job is green. Flip dry-run off for a
   one-shot real post if needed.

Never paste tokens into issues, the queue, or `zghazaleh/noor-assets`.

## Monthly maintenance

Refill `broadcast-queue.json` from the **264-ID allowlist only**. Every future
row must have `poem_id` on that list, not on the denylist, and
`card_path=cards/{poem_id}.png`. Copy verse text from shipping JSON or the
live poem page. **Do not invent verses. Do not pad with off-list IDs.**

```bash
python3 sync_allowlist.py --check          # 264 + denylist empty; remote compare if token set
python3 preflight.py --days 14             # upcoming days vs allowlist/denylist/live PNG/caption

# After building the next calendar from the private allowlist only:
# card_path must be cards/{poem_id}.png — images stay on nooralhikmah.com
cp /path/to/broadcast-queue.json broadcast-queue.json
python3 -m unittest test_post_today.py -v
git add authenticated-ids.json broadcast-queue.json
git commit -m "queue: refill from 264-ID allowlist"
git push
```

### Token refresh

Instagram long-lived tokens last ~60 days. Three guards keep the token healthy
(none of them ever prints it):

- `post_today.py` strips surrounding whitespace, newlines and quotes from
  `IG_ACCESS_TOKEN` / `IG_USER_ID` (a stray newline or quote in the pasted
  secret causes Graph OAuth 190 "Cannot parse access token").
- `python post_today.py --check-token` (run daily at 04:00 UTC by the preflight
  workflow and before every real post) does `GET /me?fields=user_id,username`
  and fails if the token is rejected or the returned `user_id` is not
  `IG_USER_ID`. Output is only OK/FAILED plus the Graph error code/message.
- **IG token refresh** (`ig-token-refresh.yml`, monthly + *Run workflow*) calls
  `refresh_access_token`, prints only success/failure and days remaining, and
  if Graph returns a different token it updates `IG_ACCESS_TOKEN` via
  `gh secret set` **when `GH_SECRETS_PAT` is set**. Without the PAT the run
  fails on purpose (GitHub emails the owner): update the secret by hand. The
  built-in `GITHUB_TOKEN` cannot write Actions secrets.

A token must be at least 24 hours old and not yet expired to be refreshed; if
it has already expired, mint a new one and store it as `IG_ACCESS_TOKEN`.

**Allowlist sync:** `sync_allowlist.py --write` copies the private file only
when the fetched set is 264 IDs with an empty denylist intersection. It will
not invent or keep a drifting set.

## Local / CI commands

```bash
python3 -m unittest test_post_today.py -v
CARD_BASE_URL=https://nooralhikmah.com/ DRY_RUN=true python3 post_today.py
CARD_BASE_URL=https://nooralhikmah.com/ python3 preflight.py --days 14
python3 sync_allowlist.py --check
```

`SKIP_LIVE_CAPTION=true` is for isolated tests only. The scheduled job must
verify the live poem page.

## Why public

The cards are images of public-domain classical Arabic verse, created to be
posted publicly. Instagram can only fetch images from public URLs. Those URLs
are on the live site (`nooralhikmah.com/cards/{id}.png`), gated to the 264
certified IDs — not GitHub raw. Keep secrets out of this repo and out of
`noor-assets`.
