#!/usr/bin/env python3
import gzip
import json
import os
import random
import time

from bs4 import BeautifulSoup
from curl_cffi import requests as cr

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import album_meta

BASE = 'https://downloads.khinsider.com'
DONE_FILE = 'work/crawl_done.txt'
OUT_FILE = 'work/songs_crawled.jsonl'
QUEUE_FILE = 'work/wayback_queue_crawled.txt'
FAIL_FILE = 'work/crawl_failures.log'
METADATA_FILE = os.environ.get('METADATA_FILE', 'album-meta.ndjson')
MAX_SECONDS = int(os.environ.get('CRAWL_MAX_SECONDS', '0') or 0)


def open_jsonl(path):
    return gzip.open(path, 'rt', encoding='utf-8') if str(path).endswith('.gz') else open(path, encoding='utf-8')


def load_metadata(path):
    out = {}
    if not path or not os.path.exists(path):
        return out
    seq = 0
    with open_jsonl(path) as fh:
        for line in fh:
            seq += 1
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            slug = rec.get('slug')
            tracks = rec.get('tracks')
            if not slug or rec.get('tracks_complete') is not True or not isinstance(tracks, list) or not tracks:
                continue
            prev = out.get(slug)
            cur_key = (str(rec.get('crawled_at') or ''), seq)
            prev_key = (str((prev or {}).get('crawled_at') or ''), (prev or {}).get('_seq', -1))
            if prev is None or cur_key >= prev_key:
                copy = dict(rec)
                copy['_seq'] = seq
                out[slug] = copy
    return out


def track_urls_from_metadata(slug, rec):
    seen, urls = set(), []
    for track in rec.get('tracks', []):
        basename = track.get('basename')
        if not basename:
            continue
        url = album_meta.song_page_url(slug, basename, base=BASE)
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def track_urls_from_html(slug, html):
    soup = BeautifulSoup(html, 'html.parser')
    tracks = album_meta.parse_songlist(soup, slug)
    seen, urls = set(), []
    for track in tracks:
        url = album_meta.song_page_url(slug, track['basename'], base=BASE)
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def fetch_album_html(sess, slug):
    r = sess.get(f'{BASE}/game-soundtracks/album/{slug}', timeout=30)
    if r.status_code != 200:
        raise RuntimeError('HTTP %s' % r.status_code)
    return r.text


def main():
    deadline = time.time() + MAX_SECONDS if MAX_SECONDS > 0 else None
    done = set(open(DONE_FILE).read().split()) if os.path.exists(DONE_FILE) else set()
    slugs = [s for s in open('work/missing_slugs.txt').read().split() if s and s not in done]
    metadata = load_metadata(METADATA_FILE)
    print(f'todo: {len(slugs)} (already done: {len(done)})', flush=True)

    out = open(OUT_FILE, 'a', buffering=1)
    que = open(QUEUE_FILE, 'a', buffering=1)
    dn = open(DONE_FILE, 'a', buffering=1)
    fl = open(FAIL_FILE, 'a', buffering=1)

    s = cr.Session(impersonate='chrome')
    try:
        for i, slug in enumerate(slugs, 1):
            if deadline and time.time() > deadline:
                print('CRAWL time box reached', flush=True)
                break
            ok = False
            urls = []
            try:
                if slug in metadata:
                    urls = track_urls_from_metadata(slug, metadata[slug])
                else:
                    urls = track_urls_from_html(slug, fetch_album_html(s, slug))
                for u in urls:
                    out.write(json.dumps({'album': slug, 'track_url': u}, ensure_ascii=False) + '\n')
                    que.write(u + '\n')
                ok = bool(urls)
                if not ok:
                    fl.write(f'{slug}\tempty songlist\n')
            except Exception as exc:
                fl.write(f'{slug}\t{type(exc).__name__}: {exc}\n')
            if ok:
                dn.write(slug + '\n')
            if i % 100 == 0:
                print(f'[{i}/{len(slugs)}] {slug}', flush=True)
            time.sleep(0.4 + random.random() * 0.6)
    finally:
        out.close()
        que.close()
        dn.close()
        fl.close()
    print('CRAWL EXIT', flush=True)


if __name__ == '__main__':
    main()
