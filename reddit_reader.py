#!/usr/bin/env python3
"""tapos-reddit-reader: a read-only counter of project mentions in public subreddit listings.

What it does, and all it does:
  * reads the public JSON listing of each subreddit named on the command line
    (https://www.reddit.com/r/<sub>/new.json and /hot.json), read-only, with a descriptive
    User-Agent; when OAuth client credentials are present in the environment it uses
    https://oauth.reddit.com instead, per Reddit's Data API terms;
  * stays at or under 10 requests a minute (a 6-second sleep between requests) and stops
    after MAX_REQUESTS in one run;
  * keeps only post id, subreddit, created time, title, score and comment count in a local
    SQLite file; no user names, no comment trees, no media;
  * counts how many titles in the last 7 days and the 7 days before mention each name given;
  * never posts, comments, votes, messages, or writes anything to Reddit.

Usage:
  python3 reddit_reader.py --db mentions.sqlite --subs CryptoCurrency solana base \
      --names "Venice" "VVV" "Gensyn" [--listing new hot] [--limit 100]

Environment (optional, only when Reddit issues them):
  REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET   -- OAuth client credentials (script app)
  REDDIT_USER_AGENT                        -- overrides the default descriptive agent
"""
import argparse
import base64
import json
import os
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

DEFAULT_AGENT = "tapos-reddit-reader/0.1 (read-only research counter; contact via GitHub shawn-j/tapos-reddit-reader)"
SLEEP_SECONDS = 6.0          # 10 requests a minute, at most
MAX_REQUESTS = 60            # hard stop per run
WEEK = 7 * 24 * 3600

_requests_made = 0


def _get(url, headers):
    global _requests_made
    if _requests_made >= MAX_REQUESTS:
        raise RuntimeError("request cap reached for this run (%d)" % MAX_REQUESTS)
    if _requests_made:
        time.sleep(SLEEP_SECONDS)
    _requests_made += 1
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _token(client_id, client_secret, agent):
    auth = base64.b64encode(("%s:%s" % (client_id, client_secret)).encode()).decode()
    data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    req = urllib.request.Request(
        "https://www.reddit.com/api/v1/access_token", data=data,
        headers={"Authorization": "Basic " + auth, "User-Agent": agent})
    global _requests_made
    _requests_made += 1
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))["access_token"]


def fetch_listing(sub, listing, limit, headers, base):
    url = "%s/r/%s/%s.json?limit=%d&raw_json=1" % (base, sub, listing, limit)
    payload = _get(url, headers)
    for child in payload.get("data", {}).get("children", []):
        d = child.get("data", {})
        if d.get("id"):
            yield (d["id"], sub, int(d.get("created_utc", 0)), d.get("title", ""),
                   int(d.get("score", 0)), int(d.get("num_comments", 0)))


def store(conn, rows):
    conn.executemany(
        "INSERT OR REPLACE INTO posts (id, subreddit, created_utc, title, score, num_comments, fetched_utc)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        [r + (int(time.time()),) for r in rows])
    conn.commit()


def count_mentions(conn, names, now):
    out = []
    for name in names:
        needle = "%" + name.lower() + "%"
        this_week = conn.execute(
            "SELECT COUNT(*) FROM posts WHERE created_utc >= ? AND lower(title) LIKE ?",
            (now - WEEK, needle)).fetchone()[0]
        last_week = conn.execute(
            "SELECT COUNT(*) FROM posts WHERE created_utc >= ? AND created_utc < ? AND lower(title) LIKE ?",
            (now - 2 * WEEK, now - WEEK, needle)).fetchone()[0]
        out.append((name, this_week, last_week))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, help="SQLite file to keep titles in")
    ap.add_argument("--subs", nargs="+", required=True, help="subreddit names without r/")
    ap.add_argument("--names", nargs="+", required=True, help="project names or tickers to count")
    ap.add_argument("--listing", nargs="+", default=["new"], choices=["new", "hot"], help="listings to read")
    ap.add_argument("--limit", type=int, default=100, help="posts per listing request (max 100)")
    args = ap.parse_args()

    agent = os.environ.get("REDDIT_USER_AGENT", DEFAULT_AGENT)
    headers = {"User-Agent": agent}
    base = "https://www.reddit.com"
    cid, csec = os.environ.get("REDDIT_CLIENT_ID"), os.environ.get("REDDIT_CLIENT_SECRET")
    if cid and csec:
        headers["Authorization"] = "bearer " + _token(cid, csec, agent)
        base = "https://oauth.reddit.com"

    conn = sqlite3.connect(args.db)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS posts (id TEXT PRIMARY KEY, subreddit TEXT, created_utc INTEGER,"
        " title TEXT, score INTEGER, num_comments INTEGER, fetched_utc INTEGER)")

    for sub in args.subs:
        for listing in args.listing:
            try:
                rows = list(fetch_listing(sub, listing, min(args.limit, 100), headers, base))
            except Exception as exc:  # one failed listing does not stop the run; it is reported
                print("r/%s %s: FAILED %s" % (sub, listing, exc), file=sys.stderr)
                continue
            store(conn, rows)
            print("r/%s %s: %d posts stored" % (sub, listing, len(rows)))

    now = int(time.time())
    print("\nname\tthis_week\tlast_week")
    for name, a, b in count_mentions(conn, args.names, now):
        print("%s\t%d\t%d" % (name, a, b))
    print("\nrequests made: %d" % _requests_made)


if __name__ == "__main__":
    main()
