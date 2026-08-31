#!/usr/bin/env python3
"""
Live index crawler for downloads.khinsider.com (2026 rebuild method).

The original indexer (the marcus-crane/khinsider Go CLI, `khinsider index`)
stopped working: the site is now behind Cloudflare bot protection which
hard-blocks datacenter HTTP clients and even headless browsers
("Sorry, you have been blocked").

This script rebuilds index.json from the LIVE site using curl_cffi with a
Chrome TLS (JA3) fingerprint, which passes the protection without a browser.

Usage:
    pip install curl_cffi
    python3 scripts/crawl_live.py

Output (KHINSIDER_OUTDIR, default /tmp/khinsider-live/):
    index.json                   - same schema as the original index
    added_since_2024-01-30.txt   - diff vs v0.0.2888
    removed_since_2024-01-30.txt
    report.txt / crawl.log / ckpt/ (per-letter resume checkpoints)

Crawls all 27 browse sections (~210 pages) in ~8 minutes.
"""
import json, re, time, os, html, random
from curl_cffi import requests as creq

OUTDIR = os.environ.get('KHINSIDER_OUTDIR', '/tmp/khinsider-live')
CKPT = os.path.join(OUTDIR, 'ckpt')
os.makedirs(CKPT, exist_ok=True)

BASE = 'https://downloads.khinsider.com'
sess = creq.Session(impersonate='chrome')

def log(msg):
    print(msg, flush=True)

def pace(base=1.0, jitter=1.0):
    time.sleep(base + random.random() * jitter)

def fetch(url, retries=5):
    for i in range(retries):
        try:
            r = sess.get(url, timeout=40)
            body = r.text
            if r.status_code == 200 and 'Attention Required' not in body and 'Just a moment' not in body:
                return body
            wait = 15 + 5 * i
            log(f'  [{r.status_code}/CF] {url} -> wait {wait}s')
            time.sleep(wait)
        except Exception as e:
            wait = 5 * (i + 1)
            log(f'  fetch error ({i+1}/{retries}) {url}: {type(e).__name__} {e} -> wait {wait}s')
            time.sleep(wait)
    return None

LETTERS = ['0-9'] + [chr(c) for c in range(ord('A'), ord('Z') + 1)]
row_re = re.compile(r'<a\s+href="(/game-soundtracks/album/[^"]+)"[^>]*>(.*?)</a>', re.S | re.I)

def parse_page(text):
    out = {}
    for m in row_re.finditer(text):
        path, inner = m.group(1), m.group(2)
        slug = path.split('/game-soundtracks/album/')[-1].strip('/')
        if not slug:
            continue
        t = html.unescape(re.sub(r'<[^>]+>', '', inner)).strip()
        if t:
            out[slug] = (t, path)
        else:
            out.setdefault(slug, (None, path))
    return out

def max_page_from(text):
    pages = [int(x) for x in re.findall(r'[?&]page=(\d+)', text)]
    return max(pages) if pages else 1

all_entries = {}
letter_counts = {}
missing = []

for L in LETTERS:
    ck = os.path.join(CKPT, f'{L}.json')
    if os.path.exists(ck):
        with open(ck) as f:
            e = {s: tuple(v) for s, v in json.load(f).items()}
        all_entries.update(e)
        letter_counts[L] = len(e)
        log(f'[{L}] checkpoint ({len(e)} albums)')
        continue
    url1 = f'{BASE}/game-soundtracks/browse/{L}'
    text = fetch(url1)
    pace()
    if text is None:
        missing.append([L, 1]); log(f'[{L}] page1 FAILED'); continue
    maxp = max_page_from(text)
    entries = parse_page(text)
    log(f'[{L}] page 1/{maxp}: {len(entries)} albums')
    zero_pages = []
    for page in range(2, maxp + 1):
        text = fetch(f'{url1}?page={page}')
        pace()
        if text is None:
            missing.append([L, page]); log(f'[{L}] page {page}/{maxp}: FAILED'); continue
        e = parse_page(text)
        if not e:
            zero_pages.append(page)
            log(f'[{L}] page {page}/{maxp}: 0 parsed (retry later)')
        else:
            entries.update(e)
        log(f'[{L}] page {page}/{maxp}: +{len(e)} (cum {len(entries)})')
    # immediate slow retry for zero-parse pages
    for page in zero_pages:
        time.sleep(10)
        text = fetch(f'{url1}?page={page}')
        pace()
        e = parse_page(text) if text else {}
        if e:
            entries.update(e)
            log(f'[{L}] page {page}: retry OK +{len(e)}')
        else:
            missing.append([L, page])
            log(f'[{L}] page {page}: retry FAILED')
    with open(ck, 'w') as f:
        json.dump({s: v for s, v in entries.items()}, f, ensure_ascii=False)
    letter_counts[L] = len(entries)
    all_entries.update(entries)
    log(f'[{L}] DONE total {len(entries)}')

entries_out = {}
for slug, (title, path) in sorted(all_entries.items(), key=lambda kv: kv[1][1]):
    t = title or slug
    entries_out[f'{t} ({slug})'] = path

index = {
    'index_version': 'live-2026-09-01',
    'source': 'live crawl of downloads.khinsider.com/game-soundtracks/browse/*',
    'entries': entries_out,
}
with open(os.path.join(OUTDIR, 'index.json'), 'w') as f:
    json.dump(index, f, ensure_ascii=False, indent=2)

report = []
report.append(f'total albums (live): {len(entries_out)}')
report.append('per-letter: ' + json.dumps(letter_counts, sort_keys=True))
report.append(f'missing pages: {missing}')

try:
    import tarfile, io
    r = creq.get('https://github.com/marcus-crane/khinsider-index/releases/download/v0.0.2888/index.tar.gz', timeout=120)
    with tarfile.open(fileobj=io.BytesIO(r.content), mode='r:gz') as tf:
        old_data = json.loads(tf.extractfile('index.json').read().decode('utf-8'))
    old_slugs = set(v.split('/game-soundtracks/album/')[-1] for v in old_data['entries'].values())
    new_slugs = set(all_entries.keys())
    added = new_slugs - old_slugs
    removed = old_slugs - new_slugs
    report.append(f'old total (v0.0.2888, 2024-01-30): {len(old_slugs)}')
    report.append(f'added since: {len(added)}')
    report.append(f'removed since: {len(removed)}')
    with open(os.path.join(OUTDIR, 'added_since_2024-01-30.txt'), 'w') as f:
        for s in sorted(added):
            t = all_entries[s][0] or s
            f.write(f'{t} ({s})\n')
    with open(os.path.join(OUTDIR, 'removed_since_2024-01-30.txt'), 'w') as f:
        for s in sorted(removed):
            f.write(s + '\n')
except Exception as e:
    report.append(f'diff skipped: {type(e).__name__} {e}')

with open(os.path.join(OUTDIR, 'report.txt'), 'w') as f:
    f.write('\n'.join(report))
log('=== REPORT ===')
for line in report:
    log(line)
log('ALL DONE')
