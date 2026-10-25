# hkcourtbuddy-data

Scheduled fetcher for [HK Court Buddy](https://hkcourtbuddy.sandolife.site). It reads LCSD SmartPLAY court
availability, encrypts it, and publishes one file, `feed.bin`, to the `data` branch. There is no UI in this repo.

```
GitHub Actions (this repo, public, free minutes)
  └─ every ~15 min (30 min overnight)  fetcher/fetch.py  ──►  data branch: feed.bin
                                                                   │
        hkcourtbuddy page (private repo, Vercel)  ◄── reads ───────┤
        sandolife-rss court-digest (Telegram)     ◄── reads ───────┘
```

## How it runs

`.github/workflows/publish.yml` is a self-dispatching chain: each run waits until 15 minutes (30 overnight) after
it started, then starts the next run with `gh workflow run`. GitHub's own `cron` skips most slots on this repo, so
the cron (`4,34 * * * *`) is only a backup that restarts the chain; it does nothing if a run is already active.

Each run: connect to a Hong Kong WireGuard exit → random 20–40 s jitter → fetch 7 sports × 8 dates (+ English
names) → gzip → AES-256-GCM encrypt → force-push `feed.bin` to the `data` branch (no history kept).

Sports and their SmartPLAY `faCode`s are in `fetcher/fetch.py` (`SPORTS`). Add a sport there and in the page's
`SPORTS` list.

## Secrets

| Secret | What it is |
|--------|-----------|
| `WG_CONFIG` | Full WireGuard config for a Hong Kong exit (Surfshark HK). Optional if GitHub's IPs are not blocked. |
| `FEED_SALT` | Passphrase the feed key is derived from (see below). |

## `FEED_SALT`: how to generate and where it goes

The feed key is `SHA-256(FEED_SALT)`. It is a **deterrent, not security**: the page needs the same value to decrypt,
so anyone who reads the page source can decrypt the feed. It only stops casual copy-paste scraping.

**Generate a new one**

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(18))"
```

**It must be identical in all three places**

1. This repo: Settings → Secrets and variables → Actions → `FEED_SALT`
   (or `gh secret set FEED_SALT -R IchiNiSando/hkcourtbuddy-data`).
2. The court page (private repo `hkcourtbuddy`): `const SALT = '...'` in `index.html`, then deploy.
3. The Telegram digest (private repo `sandolife-rss`): secret `FEED_SALT`.

**Rotating**

Change all three, then trigger this workflow once (Actions → publish-feed → Run workflow). Until the page is
redeployed and a new feed is published, old pages cannot read the new feed, so do it in one sitting: set the
secrets, deploy the page, run the workflow.

**Lost it?** It can't be read back from GitHub. Copy it from the live page source (`const SALT = '...'`), or
generate a new one and rotate as above.

## Feed format

`feed.bin` = 12-byte IV + AES-256-GCM ciphertext (16-byte tag at the end) of `gzip(JSON)`.

```json
{
  "updatedAt": "2026-10-25T13:08:41+08:00",
  "dates": ["2026-10-25", "..."],
  "districts":   { "EN": "東區" },
  "districtsEn": { "EN": "Eastern" },
  "sports": { "badminton": { "2026-10-25": [
    { "venueId": 212, "name": "柴灣體育館", "en": "Chai Wan Sports Centre", "dist": "EN",
      "hours": { "07": 2, "08": 0 } } ] } }
}
```

`hours[hh]` = free court slots for that hour (0 = full; a missing hour = no data).

## Local test

```bash
pip install -r fetcher/requirements.txt
FEED_SALT=anything python fetcher/fetch.py --enc /tmp/feed.bin --plain /tmp/data.json
```

SmartPLAY only answers requests with a browser-like User-Agent; no login or cookies are needed.
