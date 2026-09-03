import json, re, os, time, random
from curl_cffi import requests as cr

# Generates direct audio-file URLs (MP3/FLAC on *.vgmtreasurechest.com) for every
# track of every album in index.json. Per album: fetch the album page (current
# track list) plus one track page to learn the direct-URL prefix (shard + per-album
# hash) and FLAC availability, then construct all tracks' direct URLs by mapping the
# track-URL filename encoding (%25XX -> %XX). Resumable via work/direct_done.txt.

BASE = 'https://downloads.khinsider.com'
DONE_FILE = 'work/direct_done.txt'
OUT_FILE = 'work/direct_links.jsonl'
QUEUE_FILE = 'work/direct_queue.txt'
FAIL_FILE = 'work/direct_failures.log'
MAX_SECONDS = int(os.environ.get('DIRECT_MAX_SECONDS', '0') or 0)
deadline = time.time() + MAX_SECONDS if MAX_SECONDS > 0 else None

done = set(open(DONE_FILE).read().split()) if os.path.exists(DONE_FILE) else set()
idx = json.load(open('index.json'))
slugs = []
for v in idx['entries'].values():
    s = v.rsplit('/', 1)[-1]
    if s not in done:
        slugs.append(s)
print(f'todo: {len(slugs)} (already done: {len(done)})', flush=True)

out = open(OUT_FILE, 'a', buffering=1)
que = open(QUEUE_FILE, 'a', buffering=1)
dn = open(DONE_FILE, 'a', buffering=1)
fl = open(FAIL_FILE, 'a', buffering=1)

sess = cr.Session(impersonate='chrome')
link_pat = re.compile(r"https?://([a-z0-9.-]+vgmtreasurechest\.com)/soundtracks/([^/\"' <>]+)/([^/\"' <>]+)/([^/\"' <>]+?)\.(mp3|flac)", re.I)

for i, slug in enumerate(slugs, 1):
    if deadline and time.time() > deadline:
        print('DIRECT time box reached', flush=True)
        break
    time.sleep(0.4 + random.random() * 0.6)
    try:
        ra = sess.get(f'{BASE}/game-soundtracks/album/{slug}', timeout=30)
        if ra.status_code != 200:
            fl.write(f'{slug}\talbum HTTP {ra.status_code}\n')
            dn.write(slug + '\n')
            continue
        tracks = sorted(set(re.findall(r'href="(/game-soundtracks/album/' + re.escape(slug) + r'/([^"]+))"', ra.text)))
        if not tracks:
            fl.write(f'{slug}\tno tracks\n')
            dn.write(slug + '\n')
            continue
        rt = sess.get(BASE + tracks[0][0], timeout=30)
        links = link_pat.findall(rt.text)
        if not links:
            fl.write(f'{slug}\tno direct links\n')
            dn.write(slug + '\n')
            continue
        host, galbum, ghash = links[0][0], links[0][1], links[0][2]
        has_flac = any(l[4].lower() == 'flac' for l in links)
        got = set()
        for h, ga, gh, fn, ext in links:
            got.add(('https://%s/soundtracks/%s/%s/%s.%s' % (h, ga, gh, fn, ext)).lower())
        sample_name = tracks[0][1].replace('%25', '%')
        expect = ('https://%s/soundtracks/%s/%s/%s' % (host, galbum, ghash, sample_name)).lower()
        if expect not in got:
            fl.write(f'{slug}\tmapping mismatch: {sample_name}\n')
            dn.write(slug + '\n')
            continue
        n = 0
        for _path, fname in tracks:
            name = fname.replace('%25', '%')
            base = 'https://%s/soundtracks/%s/%s/%s' % (host, galbum, ghash, name)
            que.write(base + '\n')
            if has_flac:
                que.write(base.rsplit('.', 1)[0] + '.flac\n')
            n += 1
        out.write(json.dumps({'album': slug, 'host': host, 'hash': ghash, 'has_flac': has_flac, 'tracks': n}, ensure_ascii=False) + '\n')
    except Exception as e:
        fl.write(f'{slug}\t{type(e).__name__}\n')
    dn.write(slug + '\n')
    if i % 50 == 0:
        print(f'[{i}/{len(slugs)}] {slug}', flush=True)
print('DIRECT EXIT', flush=True)
