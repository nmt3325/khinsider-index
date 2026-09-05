#!/usr/bin/env python3
"""Crawl per-album metadata from khinsider album pages."""
import argparse
import calendar
import collections
import hashlib
import json
import os
import random
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
PERMANENT_NOTES = ('gone', 'no album content')
TIME_FMT = '%Y-%m-%dT%H:%M:%SZ'
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
        for item in data['albums']:
            out.append((item['slug'], item.get('title') or item['slug']))
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                out.append((item, item))
            else:
                out.append((item['slug'], item.get('title') or item['slug']))
    else:
        raise SystemExit('unsupported index format: %s' % path)
    return out


def dedupe_targets(rows):
    seen = set()
    out = []
    for slug, title in rows:
        if slug and slug not in seen:
            seen.add(slug)
            out.append((slug, title))
    return out


def _parse_crawled_at(text):
    if not text:
        return None
    try:
        return int(calendar.timegm(time.strptime(text, TIME_FMT)))
    except (OverflowError, TypeError, ValueError):
        return None


def load_done(path):
    """Latest successful crawl timestamp per slug from the NDJSON output."""
    done = {}
    if not os.path.exists(path):
        return done
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            slug = rec.get('slug')
            if not slug:
                continue
            cur = _parse_crawled_at(rec.get('crawled_at')) or 0
            prev = done.get(slug, 0)
            if cur >= prev:
                done[slug] = cur
    return done


def load_failed(path, permanent_only=True):
    """Slugs from the failure log."""
    failed = set()
    if not os.path.exists(path):
        return failed
    with open(path, encoding='utf-8') as f:
        for line in f:
            parts = line.rstrip('\n').split('\t')
            slug = parts[0].strip()
            if not slug:
                continue
            note = parts[1].strip() if len(parts) > 1 else ''
            if permanent_only and note not in PERMANENT_NOTES:
                continue
            failed.add(slug)
    return failed


def should_refresh(last_done, refresh_days, now=None):
    if not refresh_days:
        return False
    if not last_done:
        return True
    now = now or time.time()
    return last_done <= now - refresh_days * 86400


def filter_targets(targets, done, failures_path, refresh=False,
                   refresh_days=0, retry_failures=False, now=None):
    skip = set()
    if not retry_failures:
        skip |= load_failed(failures_path, permanent_only=True)
    todo = []
    for slug, title in targets:
        if slug in skip:
            continue
        if refresh:
            todo.append((slug, title))
            continue
        last_done = done.get(slug)
        if last_done and not should_refresh(last_done, refresh_days, now=now):
            continue
        todo.append((slug, title))
    return todo


def fetch(slug, retries, delay, jitter):
    """Fetch an album page. Returns (html, status) with html=None on failure."""
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
                    return r.text, 200
        backoff = min(60, (2 ** attempt) * 2) + random.random() * 3
        if attempt < retries - 1:
            time.sleep(backoff)
    return None, status


def main(argv=None):
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
    ap.add_argument('--slugs-file', default='',
                    help='newline-separated slugs to crawl, e.g. from residual_slugs.py')
    ap.add_argument('--order', choices=('index', 'hash', 'file'), default='index',
                    help='queue order: index.json order, deterministic hash, or as given')
    ap.add_argument('--deadline-minutes', type=float, default=0.0,
                    help='stop cleanly after N minutes (0 = no deadline)')
    ap.add_argument('--refresh', action='store_true', help='re-crawl albums already in --out')
    ap.add_argument('--refresh-days', type=float, default=0.0,
                    help='re-crawl records older than N days (0 = disabled)')
    ap.add_argument('--retry-failures', action='store_true', help='also retry slugs in the failure log')
    ap.add_argument('--progress-every', type=int, default=50)
    args = ap.parse_args(argv)

    if args.slug or args.slugs_file:
        targets = [(s, s) for s in args.slug]
        if args.slugs_file:
            with open(args.slugs_file, encoding='utf-8') as f:
                for line in f:
                    slug = line.strip()
                    if slug:
                        targets.append((slug, slug))
    else:
        targets = load_slugs(args.index)
        if args.letters:
            wanted = {x.strip().upper() for x in args.letters.split(',') if x.strip()}
            targets = [(s, t) for s, t in targets if album_meta.derive_letter(t) in wanted]
        if args.shard:
            i, n = (int(x) for x in args.shard.split('/'))
            targets = [row for k, row in enumerate(targets) if k % n == (i - 1) % n]

    targets = dedupe_targets(targets)
    if args.order == 'hash':
        targets = sorted(targets, key=lambda row: hashlib.md5(row[0].encode('utf-8')).hexdigest())

    done = {} if args.refresh else load_done(args.out)
    todo = filter_targets(targets, done, args.failures, refresh=args.refresh,
                          refresh_days=args.refresh_days, retry_failures=args.retry_failures)
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
    stop = threading.Event()
    qlock = threading.Lock()
    queue = collections.deque(todo)
    deadline = started + args.deadline_minutes * 60 if args.deadline_minutes else 0

    def worker(item):
        slug, title = item
        html, status = fetch(slug, args.retries, args.delay, args.jitter)
        record, note = None, None
        if html is not None:
            soup = BeautifulSoup(html, 'html.parser')
            try:
                record = album_meta.album_record(slug, soup, html=html)
                if record is None:
                    note = 'no album content'
            except album_meta.SonglistError as exc:
                note = 'songlist parse error: %s' % exc
            except Exception as exc:
                note = 'parse exception: %s' % exc
        else:
            note = str(status)
        with _lock:
            state['n'] += 1
            if record is not None:
                record['crawled_at'] = time.strftime(TIME_FMT, time.gmtime())
                out.write(json.dumps(record, ensure_ascii=False) + '\n')
                out.flush()
                state['ok'] += 1
            else:
                if note in PERMANENT_NOTES:
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

    def run():
        while not stop.is_set():
            if deadline and time.time() >= deadline:
                stop.set()
                return
            with qlock:
                if not queue:
                    return
                item = queue.popleft()
            worker(item)

    threads = []
    for _ in range(max(1, args.workers)):
        th = threading.Thread(target=run, daemon=True)
        th.start()
        threads.append(th)
    try:
        for th in threads:
            while th.is_alive():
                th.join(timeout=1)
    except KeyboardInterrupt:
        stop.set()
        with qlock:
            queue.clear()
        print('interrupted; waiting for in-flight workers to finish', flush=True)
    finally:
        for th in threads:
            th.join()
        out.close()
        failures.close()
    if deadline and time.time() >= deadline:
        print('deadline reached; rerun to resume', flush=True)
    print('done: ok=%d gone=%d fail=%d in %.1fmin'
          % (state['ok'], state['gone'], state['fail'], (time.time() - started) / 60), flush=True)


if __name__ == '__main__':
    main()
