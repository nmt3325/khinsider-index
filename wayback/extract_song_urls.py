import json, glob, os

# 1) All slugs in the live 2026-09 index
idx = json.load(open('index.json'))
index_slugs = {v.rsplit('/', 1)[-1] for v in idx['entries'].values()}
print('index albums:', len(index_slugs))

# 2) Extract song URLs from cached per-album JSONs (2023 crawl)
cached_slugs = set()
n_songs = 0
with open('work/songs_cached.jsonl', 'w') as out:
    for f in glob.glob('albums/*.json'):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        slug = d.get('slug') or os.path.basename(f)[:-5]
        cached_slugs.add(slug)
        for t in d.get('tracks', []):
            rec = {
                'album': slug,
                'n': t.get('track_number'),
                'disc': t.get('disc_number'),
                'title': t.get('title'),
                'track_url': t.get('track_url'),
                'file_url': t.get('source_mp3'),
                'flac_bytes': t.get('filesize_flac_bytes'),
                'mp3_bytes': t.get('filesize_mp3_bytes'),
            }
            out.write(json.dumps(rec, ensure_ascii=False) + '\n')
            n_songs += 1
print('cached albums:', len(cached_slugs), 'cached songs:', n_songs)

# 3) Which indexed albums still need crawling?
missing = sorted(index_slugs - cached_slugs)
with open('work/missing_slugs.txt', 'w') as f:
    f.write('\n'.join(missing) + '\n')
print('albums needing crawl:', len(missing))

# 4) Deduped list of URLs to submit to Wayback (track_url = canonical song page)
seen, urls = set(), []
for line in open('work/songs_cached.jsonl'):
    u = json.loads(line)['track_url']
    if u and u not in seen:
        seen.add(u); urls.append(u)
with open('work/wayback_queue.txt', 'w') as f:
    f.write('\n'.join(urls) + '\n')
print('unique track URLs queued for wayback:', len(urls))
