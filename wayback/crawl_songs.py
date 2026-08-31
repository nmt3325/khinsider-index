import json, re, os, time, random
from curl_cffi import requests as cr

BASE = 'https://downloads.khinsider.com'
DONE_FILE = 'work/crawl_done.txt'
OUT_FILE = 'work/songs_crawled.jsonl'
QUEUE_FILE = 'work/wayback_queue_crawled.txt'
FAIL_FILE = 'work/crawl_failures.log'

done = set(open(DONE_FILE).read().split()) if os.path.exists(DONE_FILE) else set()
slugs = [s for s in open('work/missing_slugs.txt').read().split() if s and s not in done]
print(f'todo: {len(slugs)} (already done: {len(done)})', flush=True)

out = open(OUT_FILE, 'a', buffering=1)
que = open(QUEUE_FILE, 'a', buffering=1)
dn = open(DONE_FILE, 'a', buffering=1)
fl = open(FAIL_FILE, 'a', buffering=1)

s = cr.Session(impersonate='chrome')
for i, slug in enumerate(slugs, 1):
    url = f'{BASE}/game-soundtracks/album/{slug}'
    ok = False
    for attempt in range(4):
        try:
            r = s.get(url, timeout=30)
            if r.status_code == 200:
                hrefs = sorted(set(re.findall(r'href="(/game-soundtracks/album/' + re.escape(slug) + r'/[^"]+)"', r.text)))
                for h in hrefs:
                    u = BASE + h
                    out.write(json.dumps({'album': slug, 'track_url': u}, ensure_ascii=False) + '\n')
                    que.write(u + '\n')
                ok = True
                break
            elif r.status_code in (403, 429, 503):
                time.sleep(20 * (attempt + 1))
            else:
                fl.write(f'{slug}\tHTTP {r.status_code}\n'); ok = True; break
        except Exception as e:
            time.sleep(10 * (attempt + 1))
    if not ok:
        fl.write(f'{slug}\tfailed after retries\n')
    dn.write(slug + '\n')
    if i % 100 == 0:
        print(f'[{i}/{len(slugs)}] {slug}', flush=True)
    time.sleep(0.4 + random.random() * 0.6)
print('CRAWL COMPLETE', flush=True)
