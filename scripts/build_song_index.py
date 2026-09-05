#!/usr/bin/env python3
"""Build a complete song index exclusively from current live album-page data."""
import argparse
import gzip
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time

import live_data

SCHEMA_VERSION = 1  # Four-column TSV remains compatible with the relay.
BuildError = live_data.DataError


def log(message):
    print('[%s] %s' % (time.strftime('%H:%M:%S'), message), flush=True)


def digest_file(path):
    return live_data.digest_file(path)


def sanitize_field(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def create_db(path):
    connection = sqlite3.connect(path)
    connection.execute('PRAGMA journal_mode=OFF')
    connection.execute('PRAGMA synchronous=OFF')
    connection.execute('PRAGMA temp_store=FILE')
    connection.execute('CREATE TABLE albums(slug TEXT PRIMARY KEY)')
    connection.execute('CREATE TABLE tracks(slug TEXT, position INT, disc INT, num INT, title TEXT)')
    connection.execute('CREATE INDEX track_slug ON tracks(slug)')
    return connection


def ingest_metadata(connection, path, selected):
    for number, record in live_data.jsonl(path):
        slug = live_data.canonical_slug(record.get('slug'))
        chosen = selected.get(slug)
        if chosen is None or number != chosen['_line']:
            continue
        live_data.validate_record(record)
        connection.execute('INSERT INTO albums VALUES (?)', (slug,))
        connection.executemany('INSERT INTO tracks VALUES (?,?,?,?,?)', [
            (slug, position, track.get('disc'), track.get('num'), sanitize_field(track['title']))
            for position, track in enumerate(record['tracks'])
        ])
    connection.commit()
    if connection.execute('SELECT count(*) FROM albums').fetchone()[0] != len(selected):
        raise BuildError('metadata changed during build or a selected album is missing')


def build_raw_tsv(connection, path):
    count = 0
    with open(path, 'w', encoding='utf-8', newline='') as stream:
        # Preserve every observed track, including distinct tracks with equal
        # title/disc/number. Legacy UNION/DISTINCT fallback logic is removed.
        query = 'SELECT slug, disc, num, title FROM tracks ORDER BY slug, position'
        for slug, disc, num, title in connection.execute(query):
            stream.write('%s\t%s\t%s\t%s\n' % (slug, disc or '', num or '', title))
            count += 1
    if not count:
        raise BuildError('refusing to generate an empty song index')
    return count, os.path.getsize(path), digest_file(path)


def gzip_deterministic(source, output, compresslevel=6):
    with open(source, 'rb') as src, open(output, 'wb') as target:
        with gzip.GzipFile(filename='', fileobj=target, mode='wb',
                           compresslevel=compresslevel, mtime=0) as stream:
            for chunk in iter(lambda: src.read(1024 * 1024), b''):
                stream.write(chunk)


def validate_outputs(raw, archive, manifest):
    import hashlib
    if manifest['songs'] < 1 or manifest['albums'] < 1:
        raise BuildError('refusing to publish an empty index')
    if digest_file(raw) != manifest['content_sha256'] or digest_file(archive) != manifest['sha256']:
        raise BuildError('song index digest mismatch')
    digest, count = hashlib.sha256(), 0
    with gzip.open(archive, 'rb') as stream:
        for line in stream:
            if len(line.decode('utf-8').rstrip('\n').split('\t')) != 4:
                raise BuildError('invalid TSV row')
            digest.update(line)
            count += 1
    if count != manifest['songs'] or digest.hexdigest() != manifest['content_sha256']:
        raise BuildError('compressed song index is incomplete')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalogue', default='catalogue.json')
    parser.add_argument('--metadata', default='album-meta.ndjson')
    parser.add_argument('--recent-state', default=None)
    parser.add_argument('--out', default='songs.tsv.gz')
    parser.add_argument('--manifest', default='songs-index.json')
    parser.add_argument('--compresslevel', type=int, default=6)
    args = parser.parse_args(argv)
    started = time.time()
    _, selected, unavailable, _, progress = live_data.require_complete(
        args.catalogue, args.metadata, args.recent_state)
    before = digest_file(args.metadata)
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    Path(args.manifest).parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='songs-index-', dir=output.parent) as temporary:
        directory = Path(temporary)
        connection = create_db(directory / 'songs.db')
        raw, archive = directory / 'songs.tsv', directory / 'songs.tsv.gz'
        try:
            ingest_metadata(connection, args.metadata, selected)
            count, size, digest = build_raw_tsv(connection, raw)
        finally:
            connection.close()
        if digest_file(args.metadata) != before or count != progress['tracks']:
            raise BuildError('metadata changed or the generated track count is incomplete')
        gzip_deterministic(raw, archive, args.compresslevel)
        manifest = {
            'schema_version': SCHEMA_VERSION, 'dataset_schema_version': live_data.SCHEMA,
            'data_source': live_data.SOURCE, 'complete': True, 'legacy_inputs': [],
            'generated': live_data.now(), 'catalogue_id': progress['catalogue_id'],
            'songs': count, 'albums': len(selected), 'songs_from_metadata': count,
            'library_albums': len(selected), 'library_albums_covered': len(selected),
            'library_coverage_pct': 100.0,
            'listed_albums': progress['total'], 'unavailable_albums': unavailable,
            'bytes_raw': size, 'bytes_gzip': archive.stat().st_size,
            'content_sha256': digest, 'sha256': digest_file(archive),
            'metadata_sha256': before, 'build_seconds': round(time.time() - started, 1),
            'format': 'album\\tdisc\\ttrack\\ttitle',
        }
        validate_outputs(raw, archive, manifest)
        manifest_temp = Path(str(args.manifest) + '.tmp')
        try:
            manifest_temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            os.replace(archive, output)
            os.replace(manifest_temp, args.manifest)
        finally:
            if manifest_temp.exists():
                manifest_temp.unlink()
    log(f'wrote {count} tracks from {len(selected)} complete live albums; no legacy inputs')
    return manifest


if __name__ == '__main__':
    try:
        main()
    except BuildError as exc:
        raise SystemExit(str(exc))
