#!/usr/bin/env python3
"""Build the khinsider song title index (songs.tsv.gz).

library.json only describes albums, so the Subsonic relay cannot answer
song-name searches.  This script turns the crawled track URLs into a compact
searchable table, one row per song:

    <album slug>\t<disc>\t<track no>\t<title>

Sources (both are release assets of this repository):

  songs_cached.jsonl.gz        1.31M rows scraped from album pages in 2023,
                               with real titles:
                               {'album','n','disc','title','track_url',...}
  crawl_state_snapshot.tar.gz  work/songs_crawled.jsonl, 1.96M rows of
                               {'album','track_url'} covering the albums that
                               were missing from that 2023 scrape

The two halves are disjoint: the crawl only visited albums absent from the
cache.  Titles for the crawled half are recovered from the file name, which
khinsider double-percent-encodes in the songlist markup, so it has to be
unquoted twice ('%2520' -> '%20' -> ' ').  Measured against live album pages,
the recovered titles agree with the displayed track names for ~95% of tracks
(per-album average).  That is good enough because the relay only uses this
index to find candidate albums and then resolves every hit against the live
album page, falling back to the track number when a title has changed.

Output is ~213 MB of TSV, ~33 MB gzipped, for ~3.25M songs.
"""

import argparse
import gzip
import json
import os
import re
import sys
import tarfile
import time
import urllib.parse

EXT_RE = re.compile(r'\.(mp3|flac|ogg|m4a|wav|wma|opus)$', re.I)
# '1-05 Title', '1_05 Title'
DISC_TRACK_RE = re.compile(r'^\s*(\d{1,2})[-_](\d{1,3})\s*[.\-_]?\s*')
# '05. Title', '05 - Title', '05_Title'
TRACK_DOT_RE = re.compile(r'^\s*(\d{1,3})\s*[.\-_]\s*')
# '05 Title'
TRACK_SPACE_RE = re.compile(r'^\s*(\d{1,3})\s+')

SNAPSHOT_MEMBER = 'songs_crawled.jsonl'


def log(msg):
    print('[%s] %s' % (time.strftime('%H:%M:%S'), msg), flush=True)


def title_from_url(track_url):
    """Recover a track title from a khinsider song URL."""
    basename = track_url.rsplit('/', 1)[-1]
    # khinsider serves these hrefs double-encoded
    text = urllib.parse.unquote(urllib.parse.unquote(basename))
    return EXT_RE.sub('', text).strip()


def split_number(title):
    """Split a leading '1-05' / '05.' / '05 ' prefix off a title."""
    m = DISC_TRACK_RE.match(title)
    if m:
        return int(m.group(1)), int(m.group(2)), title[m.end():].strip()
    m = TRACK_DOT_RE.match(title) or TRACK_SPACE_RE.match(title)
    if m:
        return None, int(m.group(1)), title[m.end():].strip()
    return None, None, title


def write_row(out, slug, disc, num, title):
    out.write('%s\t%s\t%s\t%s\n' % (slug, disc or '', num or '',
                                    title.replace('\t', ' ')))


def read_cached(path, out):
    """Rows from the 2023 page scrape, which already carry real titles."""
    albums = set()
    rows = 0
    with gzip.open(path, 'rt', encoding='utf-8') as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            slug = rec.get('album')
            if not slug:
                continue
            albums.add(slug)
            title = (rec.get('title') or '').strip()
            disc, num = rec.get('disc'), rec.get('n')
            if not title:
                url = rec.get('track_url')
                if not url:
                    continue
                d, n, title = split_number(title_from_url(url))
                disc, num = disc or d, num or n
            if not title:
                continue
            write_row(out, slug, disc, num, title)
            rows += 1
    return rows, albums


def read_crawled(path, out, skip_albums):
    """Rows from the 2026 crawl, whose titles live in the file name."""
    albums = set()
    rows = 0
    numbered = 0
    with tarfile.open(path) as tar:
        member = next((m for m in tar.getmembers()
                       if m.name.endswith(SNAPSHOT_MEMBER)), None)
        if member is None:
            sys.exit('%s does not contain %s' % (path, SNAPSHOT_MEMBER))
        log('reading %s (%.1f MB)' % (member.name, member.size / 1e6))
        for line in tar.extractfile(member):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            slug, url = rec.get('album'), rec.get('track_url')
            if not slug or not url or slug in skip_albums:
                continue
            disc, num, title = split_number(title_from_url(url))
            if not title:
                continue
            albums.add(slug)
            if num:
                numbered += 1
            write_row(out, slug, disc, num, title)
            rows += 1
    return rows, albums, numbered


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--snapshot', default='work/crawl_state_snapshot.tar.gz',
                    help='crawl state snapshot containing work/songs_crawled.jsonl')
    ap.add_argument('--cached', default='work/songs_cached.jsonl.gz',
                    help='2023 song cache with real titles')
    ap.add_argument('--library', default=None,
                    help='optional library.json, only used for coverage stats')
    ap.add_argument('--out', default='work/songs.tsv.gz')
    ap.add_argument('--manifest', default='work/songs-index.json')
    ap.add_argument('--compresslevel', type=int, default=6)
    args = ap.parse_args()

    started = time.time()
    tmp = args.out + '.tsv'
    cached_rows = crawled_rows = numbered = 0
    cached_albums = crawled_albums = set()

    with open(tmp, 'w', encoding='utf-8') as out:
        if os.path.exists(args.cached):
            cached_rows, cached_albums = read_cached(args.cached, out)
            log('cached  %7d songs over %6d albums' % (cached_rows, len(cached_albums)))
        else:
            log('WARNING: %s missing, skipping the cached half' % args.cached)
        if os.path.exists(args.snapshot):
            crawled_rows, crawled_albums, numbered = read_crawled(
                args.snapshot, out, cached_albums)
            log('crawled %7d songs over %6d albums (%.1f%% kept a track number)'
                % (crawled_rows, len(crawled_albums),
                   100.0 * numbered / max(1, crawled_rows)))
        else:
            log('WARNING: %s missing, skipping the crawled half' % args.snapshot)

    albums = cached_albums | crawled_albums
    rows = cached_rows + crawled_rows
    if not rows:
        sys.exit('no input rows; refusing to publish an empty index')

    raw_bytes = os.path.getsize(tmp)
    with open(tmp, 'rb') as src, gzip.open(args.out, 'wb',
                                           compresslevel=args.compresslevel) as dst:
        while True:
            chunk = src.read(1 << 20)
            if not chunk:
                break
            dst.write(chunk)
    os.remove(tmp)
    log('wrote %s (%.1f MB raw, %.1f MB gzipped)'
        % (args.out, raw_bytes / 1e6, os.path.getsize(args.out) / 1e6))

    manifest = {
        'generated': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'songs': rows,
        'albums': len(albums),
        'songs_from_cache': cached_rows,
        'songs_from_crawl': crawled_rows,
        'bytes_raw': raw_bytes,
        'bytes_gzip': os.path.getsize(args.out),
        'build_seconds': round(time.time() - started, 1),
        'format': 'album\\tdisc\\ttrack\\ttitle',
    }

    if args.library and os.path.exists(args.library):
        with open(args.library, encoding='utf-8') as fh:
            library = json.load(fh)
        lib_slugs = {a['slug'] for a in library.get('albums', [])}
        covered = len(lib_slugs & albums)
        manifest['library_albums'] = len(lib_slugs)
        manifest['library_albums_covered'] = covered
        manifest['library_coverage_pct'] = round(
            100.0 * covered / max(1, len(lib_slugs)), 2)
        log('library coverage: %d / %d albums (%.1f%%)'
            % (covered, len(lib_slugs), manifest['library_coverage_pct']))

    with open(args.manifest, 'w', encoding='utf-8') as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
        fh.write('\n')
    log('manifest written to %s' % args.manifest)


if __name__ == '__main__':
    main()
