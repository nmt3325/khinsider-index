#!/usr/bin/env python3
import gzip
import json
import os
import random
import re
import time
import urllib.parse
import urllib.parse as _urlparse

from bs4 import BeautifulSoup
from curl_cffi import requests as cr

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import album_meta
import live_data
from khinsider_player import extract_player_urls, valid_mp3_url

BASE = 'https://downloads.khinsider.com'
DONE_FILE = 'work/direct_done.txt'
OUT_FILE = 'work/direct_links.jsonl'
QUEUE_FILE = 'work/direct_queue.txt'
FAIL_FILE = 'work/direct_failures.log'
METADATA_FILE = os.environ.get('METADATA_FILE', 'work/live-v2/album-meta.ndjson')
CATALOGUE_FILE = os.environ.get('CATALOGUE_FILE', 'work/live-v2/catalogue.json')
MAX_SECONDS = int(os.environ.get('DIRECT_MAX_SECONDS', '0') or 0)
SCHEMA_VERSION = 2
LINK_PAT = re.compile(r"https?://[a-z0-9.-]*vgmtreasurechest\.com/soundtracks/[^\"' <>]+\.(?:mp3|flac)", re.I)


def open_jsonl(path):
    return gzip.open(path, 'rt', encoding='utf-8') if str(path).endswith('.gz') else open(path, encoding='utf-8')


def load_done_from_output(path):
    done = set()
    if not os.path.exists(path):
        return done
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get('schema_version') == SCHEMA_VERSION and rec.get('status') == 'ok' and rec.get('album'):
                done.add(rec['album'])
    return done


def iter_metadata(path, selected, wanted=None):
    for number, record in live_data.jsonl(path, missing_ok=True):
        slug = live_data.canonical_slug(record['slug'])
        chosen = selected.get(slug)
        if (chosen and chosen['_line'] == number and record['status'] == 'ok'
                and (wanted is None or slug in wanted)):
            yield slug, record


def load_metadata(path):
    return dict(iter_metadata(path, live_data.latest_records(path)))


def valid_audio_url(url, slug):
    if not isinstance(url, str) or not isinstance(slug, str):
        return False
    try:
        parsed = _urlparse.urlsplit(url)
        host = parsed.hostname or ''
        pieces = parsed.path.strip('/').split('/')
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443):
            return False
        if host != 'vgmtreasurechest.com' and not host.endswith('.vgmtreasurechest.com'):
            return False
        if len(pieces) < 4 or pieces[0] != 'soundtracks':
            return False
        if urllib.parse.unquote(pieces[1]) != urllib.parse.unquote(slug):
            return False
        ext = urllib.parse.unquote(pieces[-1]).lower()
        return ext.endswith('.mp3') or ext.endswith('.flac')
    except ValueError:
        return False


def extract_direct_links(html, slug):
    seen, out = set(), []
    for url in LINK_PAT.findall(html or ''):
        if valid_audio_url(url, slug):
            low = url.lower()
            if low not in seen:
                seen.add(low)
                out.append(url)
    return out


def track_page_url(slug, basename):
    return album_meta.song_page_url(slug, basename, base=BASE)


def tracks_from_album_html(slug, html):
    soup = BeautifulSoup(html, 'html.parser')
    player_urls = extract_player_urls(html, slug)
    return album_meta.parse_songlist(soup, slug, player_urls=player_urls)


def fetch_text(sess, url):
    r = sess.get(url, timeout=30)
    if r.status_code != 200:
        raise RuntimeError('HTTP %s for %s' % (r.status_code, url))
    return r.text


def resolve_track_urls(sess, slug, track):
    urls = []
    page_url = track_page_url(slug, track['basename'])
    mp3 = track.get('mp3_url')
    if valid_mp3_url(mp3, slug):
        urls.append(mp3)
    need_song_page = not urls or any(fmt != 'mp3' for fmt in track.get('formats', []))
    fetched = 0
    if need_song_page:
        fetched = 1
        song_html = fetch_text(sess, page_url)
        direct = extract_direct_links(song_html, slug)
        if direct:
            for url in direct:
                if url not in urls:
                    urls.append(url)
        elif page_url not in urls:
            urls.append(page_url)
    return urls, fetched


def process_album(sess, slug, metadata_rec=None):
    used_metadata = metadata_rec is not None
    if metadata_rec is not None:
        tracks = metadata_rec.get('tracks') or []
    else:
        tracks = tracks_from_album_html(slug, fetch_text(sess, f'{BASE}/game-soundtracks/album/{slug}'))
    if not tracks:
        raise RuntimeError('no tracks')
    queue = []
    seen = set()
    fetched_song_pages = 0
    mp3_tracks = 0
    for track in tracks:
        urls, fetched = resolve_track_urls(sess, slug, track)
        fetched_song_pages += fetched
        if any(u.lower().endswith('.mp3') for u in urls):
            mp3_tracks += 1
        for url in urls:
            key = url.lower()
            if key not in seen:
                seen.add(key)
                queue.append(url)
    if not queue:
        raise RuntimeError('no direct urls')
    return queue, {
        'schema_version': SCHEMA_VERSION,
        'album': slug,
        'status': 'ok',
        'tracks': len(tracks),
        'queued_urls': len(queue),
        'mp3_tracks': mp3_tracks,
        'fetched_song_pages': fetched_song_pages,
        'used_metadata': used_metadata,
    }


def main():
    deadline = time.time() + MAX_SECONDS if MAX_SECONDS > 0 else None
    done = load_done_from_output(OUT_FILE)
    if os.path.exists(DONE_FILE):
        done |= set(open(DONE_FILE).read().split()) & done
    # Archival history is a completion ledger, never a source of song titles.
    _, selected, _, _, _ = live_data.require_complete(CATALOGUE_FILE, METADATA_FILE)
    done = {live_data.canonical_slug(slug) for slug in done}
    slugs = sorted(set(selected) - done)
    records = iter_metadata(METADATA_FILE, selected, set(slugs))
    print(f'todo: {len(slugs)} (already done: {len(done)})', flush=True)

    out = open(OUT_FILE, 'a', buffering=1)
    que = open(QUEUE_FILE, 'a', buffering=1)
    dn = open(DONE_FILE, 'a', buffering=1)
    fl = open(FAIL_FILE, 'a', buffering=1)

    sess = cr.Session(impersonate='chrome')
    try:
        for i, (slug, metadata_record) in enumerate(records, 1):
            if deadline and time.time() > deadline:
                print('DIRECT time box reached', flush=True)
                break
            time.sleep(0.4 + random.random() * 0.6)
            try:
                queue, summary = process_album(sess, slug, metadata_record)
                for url in queue:
                    que.write(url + '\n')
                out.write(json.dumps(summary, ensure_ascii=False) + '\n')
                dn.write(slug + '\n')
            except Exception as exc:
                fl.write(f'{slug}\t{type(exc).__name__}: {exc}\n')
            if i % 50 == 0:
                print(f'[{i}/{len(slugs)}] {slug}', flush=True)
    finally:
        out.close()
        que.close()
        dn.close()
        fl.close()
    print('DIRECT EXIT', flush=True)


if __name__ == '__main__':
    main()
