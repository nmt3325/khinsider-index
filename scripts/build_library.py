#!/usr/bin/env python3
"""Merge the crawl outputs into the library.json that the relay loads.

Inputs, cheapest first:

  index.json              title -> album path for the whole archive
  album-list.ndjson       platform / album type / year, from ~210 list pages
  facet-publisher.ndjson  album -> publisher, from ~1.7k facet pages
  facet-developer.ndjson  album -> developer, from ~1k facet pages
  album-meta.ndjson       everything else (date added, track count, duration,
                          catalog number, formats, cover), one album page each

The output distinguishes "not looked at yet" from "looked, there is nothing
there", because the residual crawler needs to tell them apart:

  key absent        never fetched from a source that could know
  null / []         a source that would know reported nothing
  value             known

So an album with "publishers": [] is finished, while an album with no
publishers key at all is still work for residual_slugs.py. A coverage block
in the output records how much of each field is known, empty or unknown.

Examples:
    python build_library.py
    python build_library.py --gzip --min-list-coverage 99
    python build_library.py --pretty --out /tmp/library.json
"""
import argparse
import gzip
import json
import os
import time

import album_list
import album_meta
from crawl_album_meta import load_slugs

# fields the album page owns outright
RICH_LIST_FIELDS = ('formats',)
RICH_SCALAR_FIELDS = ('catalog_number', 'date_added', 'track_count', 'duration', 'cover')


def load_ndjson(path):
    if not os.path.exists(path):
        return
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def load_list_rows(path):
    """album-list.ndjson -> {norm slug: row}, later pages winning."""
    rows = {}
    for record in load_ndjson(path):
        slug = record.get('slug')
        if not slug:
            continue
        key = album_list.norm_slug(slug)
        old = rows.get(key)
        if old is None or record.get('crawled_at', '') >= old.get('crawled_at', ''):
            rows[key] = record
    return rows


def load_facet(path):
    """facet-*.ndjson -> {norm slug: [names]} preserving first-seen order."""
    out = {}
    for record in load_ndjson(path):
        slug, name = record.get('slug'), record.get('name')
        if not slug or not name:
            continue
        names = out.setdefault(album_list.norm_slug(slug), [])
        if name not in names:
            names.append(name)
    return out


def load_meta(path):
    """album-meta.ndjson -> {norm slug: record}, newest crawl winning."""
    meta = {}
    for record in load_ndjson(path):
        slug = record.get('slug')
        if not slug:
            continue
        key = album_list.norm_slug(slug)
        old = meta.get(key)
        if old is None or record.get('crawled_at', '') >= old.get('crawled_at', ''):
            meta[key] = record
    return meta


def count_lines(path):
    if not os.path.exists(path):
        return 0
    with open(path, encoding='utf-8') as f:
        return sum(1 for line in f if line.strip())


def facet_summary(stats_path):
    """facet-*-stats.ndjson -> {entities, advertised, found, incomplete}."""
    entities = advertised = found = incomplete = 0
    for record in load_ndjson(stats_path):
        entities += 1
        advertised += record.get('expected') or 0
        found += record.get('found') or 0
        if not record.get('complete'):
            incomplete += 1
    return {
        'entities_swept': entities,
        'albums_advertised': advertised,
        'albums_found': found,
        'entities_incomplete': incomplete,
    }


def merge_field(album, field, primary, secondary, is_list=False):
    """Take the first real value, but remember when a source looked and found none."""
    for source in (primary, secondary):
        if field not in source:
            continue
        value = source.get(field)
        if is_list:
            value = [v for v in (value or []) if v]
            if value:
                album[field] = value
                return
        elif value not in (None, '', []):
            album[field] = value
            return
    if field in primary or field in secondary:
        album[field] = [] if is_list else None


def merge_names(album, field, page_record, facet_names):
    """Company names: the album page wins when it has any, else the facet sweep.

    A facet hit is positive evidence from the same site, so it beats an empty
    album page. Only when neither has a name - and at least the album page
    looked - is the field recorded as known-empty.
    """
    values = [v for v in (page_record.get(field) or []) if v]
    if values:
        album[field] = values
    elif facet_names:
        album[field] = list(facet_names)
    elif field in page_record:
        album[field] = []


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--index', default=os.path.join(here, '..', 'index.json'))
    ap.add_argument('--list', dest='list_path',
                    default=os.path.join(here, '..', 'album-list.ndjson'))
    ap.add_argument('--publishers', default=os.path.join(here, '..', 'facet-publisher.ndjson'))
    ap.add_argument('--developers', default=os.path.join(here, '..', 'facet-developer.ndjson'))
    ap.add_argument('--meta', default=os.path.join(here, '..', 'album-meta.ndjson'))
    ap.add_argument('--list-state', default=os.path.join(here, '..', 'album-list.pages'))
    ap.add_argument('--publisher-stats',
                    default=os.path.join(here, '..', 'facet-publisher-stats.ndjson'))
    ap.add_argument('--developer-stats',
                    default=os.path.join(here, '..', 'facet-developer-stats.ndjson'))
    ap.add_argument('--out', default=os.path.join(here, '..', 'library.json'))
    ap.add_argument('--gzip', action='store_true', help='also write <out>.gz')
    ap.add_argument('--pretty', action='store_true')
    ap.add_argument('--index-only', action='store_true',
                    help='ignore albums the list sweep found but index.json lacks')
    ap.add_argument('--min-metadata', type=float, default=0.0,
                    help='fail if fewer than this %% of albums have any metadata')
    ap.add_argument('--min-list-coverage', type=float, default=0.0,
                    help='fail if fewer than this %% of albums got a list row')
    ap.add_argument('--min-publisher-coverage', type=float, default=0.0,
                    help='fail if fewer than this %% of albums got a publisher')
    args = ap.parse_args()

    with open(args.index, encoding='utf-8') as f:
        index_version = json.load(f).get('index_version')
    entries = load_slugs(args.index)
    list_rows = load_list_rows(args.list_path)
    publishers = load_facet(args.publishers)
    developers = load_facet(args.developers)
    meta = load_meta(args.meta)

    universe, seen = [], set()
    for slug, title in entries:
        key = album_list.norm_slug(slug)
        if key not in seen:
            seen.add(key)
            universe.append((slug, title, key))
    added = 0
    if not args.index_only:
        # the list sweep is fresher than index.json, so it can add albums
        for key, row in list_rows.items():
            if key in seen:
                continue
            seen.add(key)
            universe.append((row.get('slug') or key, row.get('title') or key, key))
            added += 1

    tracked = ('year', 'platforms', 'album_type', 'publishers', 'developers', 'date_added')
    tally = {f: {'known': 0, 'empty': 0, 'unknown': 0} for f in tracked}
    albums, with_any, with_list, with_page = [], 0, 0, 0

    for slug, title, key in universe:
        row = list_rows.get(key) or {}
        rec = meta.get(key) or {}
        name = rec.get('title') or row.get('title') or title
        album = {
            'slug': slug,
            'title': name,
            'letter': rec.get('letter') or album_meta.derive_letter(name),
        }
        merge_field(album, 'year', rec, row)
        merge_field(album, 'album_type', rec, row)
        merge_field(album, 'platforms', rec, row, is_list=True)
        merge_names(album, 'publishers', rec, publishers.get(key))
        merge_names(album, 'developers', rec, developers.get(key))
        for field in RICH_LIST_FIELDS:
            if field in rec:
                album[field] = [v for v in (rec.get(field) or []) if v]
        for field in RICH_SCALAR_FIELDS:
            if field in rec:
                value = rec.get(field)
                album[field] = value if value not in ('', []) else None

        for field in tracked:
            if field not in album:
                tally[field]['unknown'] += 1
            elif album[field] in (None, '', []):
                tally[field]['empty'] += 1
            else:
                tally[field]['known'] += 1
        if row:
            with_list += 1
        if rec:
            with_page += 1
        if row or rec or publishers.get(key) or developers.get(key):
            with_any += 1
        albums.append(album)

    albums.sort(key=lambda a: (a['letter'] != '0-9', a['letter'], a['title'].lower(), a['slug']))
    coverage = {
        'albums': len(albums),
        'from_index': len(entries),
        'added_from_list': added,
        'sources': {
            'album_list': {
                'rows': count_lines(args.list_path),
                'albums': with_list,
                'pages_swept': count_lines(args.list_state),
            },
            'facet_publisher': dict(facet_summary(args.publisher_stats),
                                    pairs=count_lines(args.publishers),
                                    albums_assigned=len(publishers)),
            'facet_developer': dict(facet_summary(args.developer_stats),
                                    pairs=count_lines(args.developers),
                                    albums_assigned=len(developers)),
            'album_page': {'albums': with_page},
        },
        'fields': tally,
    }
    payload = {
        'library_version': time.strftime('%Y-%m-%d'),
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'source': 'https://github.com/nmt3325/khinsider-index',
        'index_version': index_version,
        'album_count': len(albums),
        'metadata_count': with_any,
        'coverage': coverage,
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

    total = len(albums) or 1
    pct = lambda n: 100.0 * n / total
    print('%d/%d albums have some metadata (%.1f%%), %d from the list sweep, '
          '%d from album pages, %d added by the sweep'
          % (with_any, len(albums), pct(with_any), with_list, with_page, added))
    for field in tracked:
        row = tally[field]
        print('  %-12s known=%-7d empty=%-7d unknown=%-7d (%.1f%% known)'
              % (field, row['known'], row['empty'], row['unknown'], pct(row['known'])))

    problems = []
    if pct(with_any) < args.min_metadata:
        problems.append('metadata coverage %.1f%% < %.1f%%' % (pct(with_any), args.min_metadata))
    if pct(with_list) < args.min_list_coverage:
        problems.append('list coverage %.1f%% < %.1f%%' % (pct(with_list), args.min_list_coverage))
    if pct(tally['publishers']['known']) < args.min_publisher_coverage:
        problems.append('publisher coverage %.1f%% < %.1f%%'
                        % (pct(tally['publishers']['known']), args.min_publisher_coverage))
    if problems:
        raise SystemExit('; '.join(problems))


if __name__ == '__main__':
    main()
