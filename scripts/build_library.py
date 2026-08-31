#!/usr/bin/env python3
"""Merge index.json + album-meta.ndjson into library.json for the relay.

library.json is what khinsider-subsonic-relay loads at startup:

    {
      "library_version": "2026-09-01",
      "generated_at": "2026-09-01T02:00:00Z",
      "index_version": "live-2026-09-01",
      "album_count": 104453,
      "metadata_count": 104100,
      "albums": [
        {"slug": "nintendo-3ds-background-music",
         "title": "3DS Background Music",
         "letter": "0-9",
         "year": 2011,
         "publishers": ["Nintendo"],
         "platforms": ["3DS"],
         "album_type": "Gamerip",
         "date_added": "2026-04-07",
         "track_count": 106,
         "duration": 9786}
      ]
    }

Albums with no crawled metadata still get slug/title/letter, so a partial
album-meta.ndjson is fine - the relay falls back to reading the album page
live for anything missing.

Examples:
    python build_library.py                       # ../library.json
    python build_library.py --gzip                # ../library.json.gz too
    python build_library.py --min-metadata 90     # fail if coverage is low
"""
import argparse
import gzip
import json
import os
import time

import album_meta
from crawl_album_meta import load_slugs

LIST_FIELDS = ('publishers', 'developers', 'platforms', 'formats')
SCALAR_FIELDS = ('year', 'album_type', 'catalog_number', 'date_added',
                 'track_count', 'duration', 'cover')


def load_meta(path):
    meta = {}
    if not os.path.exists(path):
        return meta
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
            old = meta.get(slug)
            # later lines win, so a --refresh re-crawl overrides an old record
            if old is None or rec.get('crawled_at', '') >= old.get('crawled_at', ''):
                meta[slug] = rec
    return meta


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--index', default=os.path.join(here, '..', 'index.json'))
    ap.add_argument('--meta', default=os.path.join(here, '..', 'album-meta.ndjson'))
    ap.add_argument('--out', default=os.path.join(here, '..', 'library.json'))
    ap.add_argument('--gzip', action='store_true', help='also write <out>.gz')
    ap.add_argument('--pretty', action='store_true')
    ap.add_argument('--min-metadata', type=float, default=0.0,
                    help='exit non-zero if metadata coverage is below this percentage')
    args = ap.parse_args()

    with open(args.index, encoding='utf-8') as f:
        index_version = json.load(f).get('index_version')
    entries = load_slugs(args.index)
    meta = load_meta(args.meta)

    albums, with_meta = [], 0
    stats = {'year': 0, 'publishers': 0, 'platforms': 0, 'album_type': 0, 'date_added': 0}
    for slug, title in entries:
        rec = meta.get(slug) or {}
        album = {
            'slug': slug,
            'title': rec.get('title') or title,
            'letter': rec.get('letter') or album_meta.derive_letter(rec.get('title') or title),
        }
        for key in LIST_FIELDS:
            values = [v for v in (rec.get(key) or []) if v]
            if values:
                album[key] = values
        for key in SCALAR_FIELDS:
            value = rec.get(key)
            if value not in (None, '', []):
                album[key] = value
        if rec:
            with_meta += 1
            for key in stats:
                if album.get(key):
                    stats[key] += 1
        albums.append(album)

    albums.sort(key=lambda a: (a['letter'] != '0-9', a['letter'], a['title'].lower(), a['slug']))
    payload = {
        'library_version': time.strftime('%Y-%m-%d'),
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'source': 'https://github.com/nmt3325/khinsider-index',
        'index_version': index_version,
        'album_count': len(albums),
        'metadata_count': with_meta,
        'albums': albums,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=1 if args.pretty else None)
    with open(args.out, 'w', encoding='utf-8') as f:
        f.write(text)
    print('wrote %s (%d albums, %.1f MB)'
          % (args.out, len(albums), len(text.encode('utf-8')) / 1048576.0))
    if args.gzip:
        with gzip.open(args.out + '.gz', 'wb', compresslevel=9) as f:
            f.write(text.encode('utf-8'))
        print('wrote %s.gz (%.1f MB)'
              % (args.out, os.path.getsize(args.out + '.gz') / 1048576.0))

    pct = 100.0 * with_meta / len(albums) if albums else 0.0
    print('metadata coverage: %d/%d (%.1f%%)' % (with_meta, len(albums), pct))
    for key in ('year', 'publishers', 'platforms', 'album_type', 'date_added'):
        print('  %-12s %d' % (key, stats[key]))
    if pct < args.min_metadata:
        raise SystemExit('metadata coverage %.1f%% is below --min-metadata %.1f%%'
                         % (pct, args.min_metadata))


if __name__ == '__main__':
    main()
