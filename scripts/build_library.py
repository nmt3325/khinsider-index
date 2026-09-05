#!/usr/bin/env python3
"""Build the relay library from a complete live catalogue and album pages only."""
import argparse
import gzip
import json
import os
from pathlib import Path

import album_meta
import live_data

RICH_LIST_FIELDS = ('formats', 'publishers', 'developers')
RICH_SCALAR_FIELDS = ('catalog_number', 'date_added', 'track_count', 'duration', 'cover')


def merge_field(album, field, primary, secondary, is_list=False):
    for source in (primary, secondary):
        value = source.get(field)
        if is_list:
            if value:
                album[field] = list(value)
                return
        elif value not in (None, '', []):
            album[field] = value
            return
    album[field] = [] if is_list else None


def build_payload(args):
    catalogue, records, unavailable, _, summary = live_data.require_complete(
        args.catalogue, args.meta, args.recent_state)
    albums = []
    tracked = ('year', 'platforms', 'album_type', 'publishers', 'developers', 'date_added')
    tally = {field: {'known': 0, 'empty': 0, 'unknown': 0} for field in tracked}
    for row in catalogue['albums']:
        slug = row['slug']
        if slug not in records:
            continue  # Only explicitly reported HTTP-404 albums reach this branch.
        record = records[slug]
        name = record['title']
        album = {'slug': slug, 'title': name,
                 'letter': record.get('letter') or album_meta.derive_letter(name)}
        merge_field(album, 'year', record, row)
        merge_field(album, 'album_type', record, row)
        merge_field(album, 'platforms', record, row, is_list=True)
        for field in RICH_LIST_FIELDS:
            album[field] = list(record.get(field) or [])
        for field in RICH_SCALAR_FIELDS:
            album[field] = record.get(field)
        for field in tracked:
            tally[field]['empty' if album[field] in (None, '', []) else 'known'] += 1
        albums.append(album)
    albums.sort(key=lambda a: (a['letter'] != '0-9', a['letter'], a['title'].lower(), a['slug']))
    return {
        'library_version': live_data.now()[:10], 'generated_at': live_data.now(),
        'source': 'https://github.com/nmt3325/khinsider-index',
        'data_source': live_data.SOURCE, 'dataset_schema_version': live_data.SCHEMA,
        'complete': True, 'catalogue_id': catalogue['catalogue_id'],
        'album_count': len(albums), 'metadata_count': len(albums),
        'unavailable_albums': unavailable, 'legacy_inputs': [],
        'coverage': {
            'albums': len(albums), 'listed_albums': summary['total'],
            'pending': 0, 'unavailable': len(unavailable),
            'sources': {
                'album_list': {'albums': len(albums), 'rows': summary['total'],
                               'pages_swept': catalogue['listing']['pages']},
                'album_page': {'albums': len(albums)},
            },
            'fields': tally,
        },
        'albums': albums,
    }


def render_payload(payload, pretty=False):
    return json.dumps(payload, ensure_ascii=False, indent=1 if pretty else None)


def write_gzip(path, text, compresslevel=9):
    with open(path, 'wb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw,
                           compresslevel=compresslevel, mtime=0) as stream:
            stream.write(text.encode('utf-8'))


def build_arg_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalogue', default='catalogue.json')
    parser.add_argument('--meta', default='album-meta.ndjson')
    parser.add_argument('--recent-state', default=None)
    parser.add_argument('--out', default='library.json')
    parser.add_argument('--gzip', action='store_true')
    parser.add_argument('--pretty', action='store_true')
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    # All completeness validation happens before touching existing outputs.
    payload = build_payload(args)
    text = render_payload(payload, args.pretty)
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = str(output) + '.tmp'
    gzip_temporary = str(output) + '.gz.tmp'
    try:
        Path(temporary).write_text(text, encoding='utf-8')
        if args.gzip:
            write_gzip(gzip_temporary, text)
        os.replace(temporary, output)
        if args.gzip:
            os.replace(gzip_temporary, str(output) + '.gz')
    finally:
        for path in (temporary, gzip_temporary):
            if os.path.exists(path):
                os.unlink(path)
    print(f"wrote {output}: {payload['album_count']} complete live albums; no legacy inputs")
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except live_data.DataError as exc:
        raise SystemExit(str(exc))
