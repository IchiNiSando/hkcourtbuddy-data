"""Fetch SmartPLAY court availability and write an encrypted feed.

The availability endpoint is public: no login, no cookies. It only needs a
browser-like User-Agent (without one it answers 200 with an empty body).

Usage: FEED_SALT=... python fetch.py [--enc feed.bin] [--plain data.json]
"""
import argparse
import gzip
import hashlib
import json
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

BASE = "https://www.smartplay.lcsd.gov.hk"
HK = timezone(timedelta(hours=8))
SPORTS = {
    "badminton": "BADC", "tabletennis": "TABT", "tennis": "TENC",
    "football": "FOTP", "basketball": "BASC", "squash": "SQUC", "volleyball": "VOLC",
}
PERIODS = ("morning", "afternoon", "evening")
# Deterrent, not security: the page needs this salt to decrypt, so anyone reading the
# page source can too. It only stops casual copy/paste scraping of the feed.
SALT = os.environ.get("FEED_SALT", "")
HEADERS = {
    "Channel": "INTERNET",
    "Accept": "application/json",
    "Accept-Language": "zh-hk",
    "Origin": BASE,
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
}


def get_catalog(session, fa, date, tries=3, lang="zh-hk"):
    url = f"{BASE}/rest/facility-catalog/api/v1/publ/facilities"
    for i in range(tries):
        try:
            r = session.get(url, params={"distCode": "", "faCode": fa, "playDate": date},
                            headers={"Accept-Language": lang}, timeout=30)
            j = r.json()
            if r.status_code == 200 and str(j.get("code")) == "0" and j.get("data"):
                return j["data"]
            err = f"{r.status_code} code={j.get('code')} data={'yes' if j.get('data') else 'empty'}"
        except Exception as e:
            err = repr(e)
        if i < tries - 1:
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"{fa} {date}: {err}")


def parse(data, venues, dists):
    """Merge one catalog response into {venueId: venue} with per-hour free counts."""
    for period in PERIODS:
        for dist in (data.get(period) or {}).get("distList") or []:
            dists[dist["distCode"]] = (dist.get("distName") or dist["distCode"]).strip()
            for v in dist.get("venueList") or []:
                row = venues.setdefault(v["venueId"], {
                    "venueId": v["venueId"], "name": v.get("venueName"),
                    "dist": dist["distCode"], "hours": {}})
                for fat in v.get("fatList") or []:
                    for s in fat.get("sessionList") or []:
                        hh = s["ssnStartTime"][:2]
                        n = (s.get("sessionCount") or 0) if s.get("available") else 0
                        row["hours"][hh] = row["hours"].get(hh, 0) + n


def parse_en(data, names, dists_en):
    """Collect official English venue / district names from an en-us response."""
    for period in PERIODS:
        for dist in (data.get(period) or {}).get("distList") or []:
            dists_en[dist["distCode"]] = (dist.get("distName") or dist["distCode"]).strip()
            for v in dist.get("venueList") or []:
                if v.get("venueName"):
                    names[v["venueId"]] = v["venueName"].strip()


def encrypt_feed(payload: bytes) -> bytes:
    """gzip -> AES-256-GCM. Layout: 12-byte IV + ciphertext+tag. Key = SHA-256(SALT)."""
    iv = os.urandom(12)
    key = hashlib.sha256(SALT.encode()).digest()
    return iv + AESGCM(key).encrypt(iv, gzip.compress(payload, 9), None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--enc", default="feed.bin", help="encrypted feed the page loads")
    ap.add_argument("--plain", default="", help="optional plain JSON copy (local debugging only)")
    args = ap.parse_args()
    if not SALT:
        sys.exit("FEED_SALT is not set")

    s = requests.Session()
    s.headers.update(HEADERS)

    # The first response lists every bookable date in venueCountList.
    today = datetime.now(HK).date().isoformat()
    first = get_catalog(s, SPORTS["badminton"], today)
    dates = [x["playDate"] for x in first.get("venueCountList") or []] or [today]

    out = {"updatedAt": datetime.now(HK).isoformat(timespec="seconds"),
           "dates": dates, "districts": {}, "sports": {}}
    dists, dists_en, failures = {}, {}, 0
    for sport, fa in SPORTS.items():
        out["sports"][sport] = {}
        # Official English names (venue ids are stable, so first + last date is enough).
        names_en = {}
        for d in {dates[0], dates[-1]}:
            try:
                parse_en(get_catalog(s, fa, d, lang="en-us"), names_en, dists_en)
            except Exception as e:
                print(f"skip english names {sport} {d}: {e}", file=sys.stderr)
            time.sleep(random.uniform(0.7, 1.6))
        for d in dates:
            venues = {}
            try:
                data = first if (sport == "badminton" and d == today) else get_catalog(s, fa, d)
                parse(data, venues, dists)
            except Exception as e:
                failures += 1
                print(f"skip {sport} {d}: {e}", file=sys.stderr)
                continue
            for v in venues.values():
                if v["venueId"] in names_en:
                    v["en"] = names_en[v["venueId"]]
            out["sports"][sport][d] = sorted(venues.values(), key=lambda x: x["name"] or "")
            time.sleep(random.uniform(0.7, 1.6))

    total = len(SPORTS) * len(dates)
    if failures > total // 2:
        sys.exit(f"too many failures ({failures}/{total}); keeping previous data")
    out["districts"] = dists
    out["districtsEn"] = dists_en
    payload = json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if args.plain:
        with open(args.plain, "wb") as f:
            f.write(payload)
    tmp = args.enc + ".tmp"
    with open(tmp, "wb") as f:
        f.write(encrypt_feed(payload))
    os.replace(tmp, args.enc)
    print(f"wrote {args.enc}: {len(dates)} dates, {failures} failures, "
          f"{len(payload) // 1024} KB plain / {os.path.getsize(args.enc) // 1024} KB encrypted")


if __name__ == "__main__":
    main()
