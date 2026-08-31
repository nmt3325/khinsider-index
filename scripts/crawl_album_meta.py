#!/usr/bin/env python3
"""Crawl per-album metadata from khinsider album pages.

index.json only knows title -> album path, so it has no release year,
publisher, platform or album type. This script visits every album page once
and writes one JSON object per album to a resumable NDJSON file:

    {"slug": "nintendo-3ds-background-music", "title": "3DS Background Music",
     "letter": "0-9", "year": 2011, "publishers": ["Nintendo"],
     "developers": [], "platforms": ["3DS"], "album_type": "Gamerip",
     "date_added": "2026-04-07", "track_count": 106, "duration": 9786, ...}

build_library.py then merges index.json + this file into the library.json
consumed by khinsider-subsonic-relay, which maps the fields onto Subsonic
tags (year -> release date, publisher -> album artist, platform and album
type -> genres).

Examples:
    python crawl_album_meta.py --limit 200            # smoke test
    python crawl_album_meta.py --letters A,B,C        # one browse section
    python crawl_album_meta.py --shard 1/8            # 8 parallel jobs
    python crawl_album_meta.py                        # everything (resumable)

Re-running always resumes: albums already present in the NDJSON are skipped
unless --refresh is passed. Cloudflare challenges are retried with backoff;
permanent failures are appended to the failure log and can be retried later
with --retry-failures.
"""
import argparse
import json
import os
import random
import sys
import threading
import time
import urllib.parse

from bs4 import BeautifulSoup
from curl_cffi import requests as creq

import album_meta

BASE = 'https://downloads.khinsider.com'
ALBUM_PREFIX = '/game-soundtracks/album/'
CF_MARKERS = ('attention required', 'just a moment', 'cf-browser-verification',
              'enable javascript and cookies to continue')
_local = threading.local()
_lock = threading.Lock()


def session():
    if not hasattr(_local, 'sess'):
        _local.sess = creq.Session(impersonate='chrome')
    return _local.sess


def load_slugs(path):
    """index.json / library.json / plain slug list -> [(slug, title)]."""
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    out = []
    if isinstance(data, dict) and 'entries' in data:
        for title, url in data['entries'].items():
            slug = urllib.parse.urlparse(url).path.rstrip('/').rsplit('/', 1)[-1]
            if slug:
                out.append((slug, title))
    elif isinstance(data, dict) and 'albums' in data:
        for album in data['albums']:
            out.append((album['slug'], album.get('title') or album['slug']))
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                out.append((item, item))
            else:
                out.append((item['slug'], item.get('title') or item['slug']))
    else:
        raise SystemExit('unsupported index format: %s' % path)
    return out


def load_done(path):
    done = set()
    if not os.path.exists(path):
        return done
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                done.add(json.loads(line)['slug'])
            except Exception:
                continue
    return done


def load_failed(path):
    failed = set()
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            for line in f:
                slug = line.split('\t', 1)[0].strip()
                if slug:
                    failed.add(slug)
    return failed


def fetch(slug, retries, delay, jitter):
    """Fetch an album page. Returns (soup, status) with soup=None on failure."""
    url = BASE + ALBUM_PREFIX + urllib.parse.quote(slug)
    for attempt in range(retries):
        time.sleep(delay + random.random() * jitter)
        try:
            r = session().get(url, timeout=45)
        except Exception as exc:
            status = 'exception: %s' % exc
        else:
            status = r.status_code
            if r.status_code == 404:
                return None, 'gone'
            if r.status_code == 200:
                low = r.text[:4000].lower()
                if any(m in low for m in CF_MARKERS):
                    status = 'cloudflare'
                else:
                    return BeautifulSoup(r.text, 'html.parser'), 200
        backoff = min(60, (2 ** attempt) * 2) + random.random() * 3
        if attempt < retries - 1:
            time.sleep(backoff)
    return None, status


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--index', default=os.path.join(here, '..', 'index.json'))
    ap.add_argument('--out', default=os.path.join(here, '..', 'album-meta.ndjson'))
    ap.add_argument('--failures', default=os.path.join(here, '..', 'album-meta-failures.log'))
    ap.add_argument('--workers', type=int, default=4, help='parallel fetchers (default 4)')
    ap.add_argument('--delay', type=float, default=0.4, help='per-worker delay before each request')
    ap.add_argument('--jitter', type=float, default=0.6)
    ap.add_argument('--retries', type=int, default=5)
    ap.add_argument('--limit', type=int, default=0, help='stop after N albums (0 = no limit)')
    ap.add_argument('--letters', default='', help='only these browse sections, e.g. 0-9,A,B')
    ap.add_argument('--shard', default='', help='process shard i/n, e.g. 3/8')
    ap.add_argument('--slug', action='append', default=[], help='crawl specific slugs only')
    ap.add_argument('--refresh', action='store_true', help='re-crawl albums already in --out')
    ap.add_argument('--retry-failures', action='store_true', help='also retry slugs in the failure log')
    ap.add_argument('--progress-every', type=int, default=50)
    args = ap.parse_args()

    if args.slug:
        targets = [(s, s) for s in args.slug]
    else:
        targets = load_slugs(args.index)
        if args.letters:
            wanted = {x.strip().upper() for x in args.letters.split(',') if x.strip()}
            targets = [(s, t) for s, t in targets if album_meta.derive_letter(t) in wanted]
        if args.shard:
            i, n = (int(x) for x in args.shard.split('/'))
            targets = [row for k, row in enumerate(targets) if k % n == (i - 1) % n]

    done = set() if args.refresh else load_done(args.out)
    skip = set(done)
    if not args.retry_failures:
        skip |= load_failed(args.failures)
    todo = [(s, t) for s, t in targets if s not in skip]
    if args.limit:
        todo = todo[:args.limit]
    print('%d albums total, %d already done, %d to crawl (workers=%d)'
          % (len(targets), len(done), len(todo), args.workers), flush=True)
    if not todo:
        return

    out = open(args.out, 'a', encoding='utf-8')
    failures = open(args.failures, 'a', encoding='utf-8')
    state = {'ok': 0, 'gone': 0, 'fail': 0, 'n': 0}
    started = time.time()

    def worker(item):
        slug, title = item
        soup, status = fetch(slug, args.retries, args.delay, args.jitter)
        record, note = None, None
        if soup is not None:
            record = album_meta.album_record(slug, soup)
            if record is None:
                note = 'no album content'
        else:
            note = str(status)
        with _lock:
            state['n'] += 1
            if record is not None:
                record['crawled_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                out.write(json.dumps(record, ensure_ascii=False) + '\n')
                out.flush()
                state['ok'] += 1
            else:
                if note in ('gone', 'no album content'):
                    state['gone'] += 1
                else:
                    state['fail'] += 1
                failures.write('%s\t%s\t%s\n' % (slug, note, title))
                failures.flush()
            if state['n'] % args.progress_every == 0 or state['n'] == len(todo):
                elapsed = time.time() - started
                rate = state['n'] / elapsed if elapsed else 0
                left = (len(todo) - state['n']) / rate if rate else 0
                print('%d/%d ok=%d gone=%d fail=%d %.1f/s eta=%.1fmin'
                      % (state['n'], len(todo), state['ok'], state['gone'],
                         state['fail'], rate, left / 60), flush=True)

    threads = []
    queue = list(todo)
    qlock = threading.Lock()

    def run():
        while True:
            with qlock:
                if not queue:
                    return
                item = queue.pop()
            worker(item)

    for _ in range(max(1, args.workers)):
        th = threading.Thread(target=run, daemon=True)
        th.start()
        threads.append(th)
    try:
        for th in threads:
            while th.is_alive():
                th.join(timeout=1)
    except KeyboardInterrupt:
        with qlock:
            queue.clear()
        print('interrupted; rerun to resume', flush=True)
    out.close()
    failures.close()
    print('done: ok=%d gone=%d fail=%d in %.1fmin'
          % (state['ok'], state['gone'], state['fail'], (time.time() - started) / 60))


if __name__ == '__main__':
    main()
