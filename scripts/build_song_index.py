#!/usr/bin/env python3
"""Build the khinsider song title index (songs.tsv.gz)."""

import argparse
import gzip
import hashlib
import json
import os
import re
import sqlite3
import sys
import tarfile
import tempfile
import time
import urllib.parse

EXT_RE = re.compile(r'\.(mp3|flac|ogg|m4a|wav|wma|opus)$', re.I)
DISC_TRACK_RE = re.compile(r'^\s*(\d{1,2})[-_](\d{1,3})\s*[.\-_]?\s*')
TRACK_DOT_RE = re.compile(r'^\s*(\d{1,3})\s*[.\-_]\s*')
TRACK_SPACE_RE = re.compile(r'^\s*(\d{1,3})\s+')
SNAPSHOT_MEMBER = 'songs_crawled.jsonl'
SCHEMA_VERSION = 1


class BuildError(RuntimeError):
    pass


def log(msg):
    print('[%s] %s' % (time.strftime('%H:%M:%S'), msg), flush=True)


def digest_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def as_int(value):
    if value in (None, '') or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def title_from_url(track_url):
    basename = str(track_url or '').rsplit('/', 1)[-1]
    text = urllib.parse.unquote(urllib.parse.unquote(basename))
    return EXT_RE.sub('', text).strip()


def split_number(title):
    m = DISC_TRACK_RE.match(title)
    if m:
        return int(m.group(1)), int(m.group(2)), title[m.end():].strip()
    m = TRACK_DOT_RE.match(title) or TRACK_SPACE_RE.match(title)
    if m:
        return None, int(m.group(1)), title[m.end():].strip()
    return None, None, title


def sanitize_field(value):
    text = '' if value is None else str(value)
    text = re.sub(r'[\t\r\n]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def norm_slug(slug):
    if not slug:
        return slug
    try:
        return urllib.parse.unquote(slug)
    except Exception:
        return slug


def open_jsonl(path):
    return gzip.open(path, 'rt', encoding='utf-8') if str(path).endswith('.gz') else open(path, encoding='utf-8')


def iter_jsonl(path):
    with open_jsonl(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                raise BuildError('invalid JSON line in %s' % path)


def explicit_flags(argv):
    argv = argv or []
    return {
        'snapshot': '--snapshot' in argv,
        'cached': '--cached' in argv,
        'metadata': '--metadata' in argv,
        'library': '--library' in argv,
    }


def ensure_inputs(args, flags):
    exists = {
        'snapshot': bool(args.snapshot and os.path.exists(args.snapshot)),
        'cached': bool(args.cached and os.path.exists(args.cached)),
        'metadata': bool(args.metadata and os.path.exists(args.metadata)),
        'library': bool(args.library and os.path.exists(args.library)),
    }
    if flags['metadata'] and not exists['metadata']:
        raise BuildError('metadata missing: %s' % args.metadata)
    if not args.allow_partial and exists['cached'] != exists['snapshot']:
        raise BuildError('legacy inputs are incomplete; use --allow-partial to override')
    if flags['cached'] and not exists['cached'] and not exists['snapshot']:
        raise BuildError('cached missing: %s' % args.cached)
    if flags['snapshot'] and not exists['snapshot'] and not exists['cached']:
        raise BuildError('snapshot missing: %s' % args.snapshot)
    if flags['library'] and not exists['library']:
        raise BuildError('library missing: %s' % args.library)
    if not (exists['metadata'] or exists['cached'] or exists['snapshot']):
        raise BuildError('no usable inputs')
    return exists


def create_db(path):
    con = sqlite3.connect(path)
    con.execute('PRAGMA journal_mode=OFF')
    con.execute('PRAGMA synchronous=OFF')
    con.execute('PRAGMA temp_store=FILE')
    con.execute('CREATE TABLE canonical_albums(norm_slug TEXT PRIMARY KEY, slug TEXT NOT NULL, crawled_at TEXT, seq INT)')
    con.execute('CREATE TABLE canonical_rows(norm_slug TEXT NOT NULL, slug TEXT NOT NULL, disc INT, num INT, title TEXT NOT NULL)')
    con.execute('CREATE TABLE cached_albums(norm_slug TEXT PRIMARY KEY, slug TEXT NOT NULL, seq INT)')
    con.execute('CREATE TABLE cached_rows(norm_slug TEXT NOT NULL, slug TEXT NOT NULL, disc INT, num INT, title TEXT NOT NULL)')
    con.execute('CREATE TABLE crawled_albums(norm_slug TEXT PRIMARY KEY, slug TEXT NOT NULL, seq INT)')
    con.execute('CREATE TABLE crawled_rows(norm_slug TEXT NOT NULL, slug TEXT NOT NULL, disc INT, num INT, title TEXT NOT NULL)')
    con.execute('CREATE INDEX canonical_norm_slug ON canonical_rows(norm_slug)')
    con.execute('CREATE INDEX cached_norm_slug ON cached_rows(norm_slug)')
    con.execute('CREATE INDEX crawled_norm_slug ON crawled_rows(norm_slug)')
    return con


def complete_metadata_rows(rec, path):
    if not isinstance(rec, dict):
        raise BuildError('invalid metadata record in %s' % path)
    if rec.get('tracks_complete') is not True:
        return None
    slug = rec.get('slug')
    tracks = rec.get('tracks')
    if not slug:
        raise BuildError('complete metadata record is missing slug in %s' % path)
    slug = str(slug)
    key = norm_slug(slug)
    if not isinstance(tracks, list) or not tracks:
        raise BuildError('complete metadata for %s has no tracks in %s' % (slug, path))
    rows = []
    for idx, track in enumerate(tracks, 1):
        if not isinstance(track, dict):
            raise BuildError('complete metadata for %s has invalid track %d in %s' % (slug, idx, path))
        title = sanitize_field(track.get('title') or title_from_url(track.get('basename') or ''))
        if not title:
            raise BuildError('complete metadata for %s track %d is missing title/basename in %s'
                             % (slug, idx, path))
        rows.append((key, slug, as_int(track.get('disc')), as_int(track.get('num')), title))
    return key, slug, str(rec.get('crawled_at') or ''), rows


def ingest_metadata(con, path):
    seq = 0
    for rec in iter_jsonl(path):
        seq += 1
        parsed = complete_metadata_rows(rec, path)
        if parsed is None:
            continue
        key, slug, crawled_at, rows = parsed
        prev = con.execute('SELECT crawled_at, seq FROM canonical_albums WHERE norm_slug=?', (key,)).fetchone()
        if prev is not None and (crawled_at, seq) <= (prev[0] or '', int(prev[1] or 0)):
            continue
        con.execute('DELETE FROM canonical_rows WHERE norm_slug=?', (key,))
        con.executemany('INSERT INTO canonical_rows(norm_slug, slug, disc, num, title) VALUES (?,?,?,?,?)', rows)
        con.execute('INSERT OR REPLACE INTO canonical_albums(norm_slug, slug, crawled_at, seq) VALUES (?,?,?,?)',
                    (key, slug, crawled_at, seq))
    con.commit()


def ingest_cached(con, path):
    rows = 0
    seq = 0
    for rec in iter_jsonl(path):
        slug = rec.get('album')
        if not slug:
            continue
        slug = str(slug)
        key = norm_slug(slug)
        title = sanitize_field(rec.get('title') or '')
        disc, num = rec.get('disc'), rec.get('n')
        if not title:
            url = rec.get('track_url')
            if not url:
                continue
            dval, nval, title = split_number(title_from_url(url))
            disc, num = disc or dval, num or nval
        title = sanitize_field(title)
        if not title:
            continue
        seq += 1
        con.execute('INSERT OR IGNORE INTO cached_albums(norm_slug, slug, seq) VALUES (?,?,?)',
                    (key, slug, seq))
        con.execute('INSERT INTO cached_rows(norm_slug, slug, disc, num, title) VALUES (?,?,?,?,?)',
                    (key, slug, as_int(disc), as_int(num), title))
        rows += 1
    con.commit()
    return rows


def iter_crawled_rows(path):
    with tarfile.open(path) as tar:
        member = next((m for m in tar.getmembers() if m.name.endswith(SNAPSHOT_MEMBER)), None)
        if member is None:
            raise BuildError('%s does not contain %s' % (path, SNAPSHOT_MEMBER))
        log('reading %s (%.1f MB)' % (member.name, member.size / 1e6))
        fh = tar.extractfile(member)
        if fh is None:
            raise BuildError('failed to extract %s from %s' % (SNAPSHOT_MEMBER, path))
        for line in fh:
            try:
                yield json.loads(line)
            except ValueError:
                raise BuildError('invalid JSON line in %s' % path)


def ingest_crawled(con, path):
    rows = 0
    numbered = 0
    seq = 0
    for rec in iter_crawled_rows(path):
        slug, url = rec.get('album'), rec.get('track_url')
        if not slug or not url:
            continue
        slug = str(slug)
        key = norm_slug(slug)
        disc, num, title = split_number(title_from_url(url))
        title = sanitize_field(title)
        if not title:
            continue
        seq += 1
        con.execute('INSERT OR IGNORE INTO crawled_albums(norm_slug, slug, seq) VALUES (?,?,?)',
                    (key, slug, seq))
        con.execute('INSERT INTO crawled_rows(norm_slug, slug, disc, num, title) VALUES (?,?,?,?,?)',
                    (key, slug, as_int(disc), as_int(num), title))
        rows += 1
        if num:
            numbered += 1
    con.commit()
    return rows, numbered


def selected_albums_sql():
    return (
        'SELECT norm_slug, slug, 0 AS src FROM canonical_albums '
        'UNION ALL '
        'SELECT c.norm_slug, c.slug, 1 AS src FROM cached_albums c '
        'WHERE NOT EXISTS (SELECT 1 FROM canonical_albums a WHERE a.norm_slug=c.norm_slug) '
        'UNION ALL '
        'SELECT r.norm_slug, r.slug, 2 AS src FROM crawled_albums r '
        'WHERE NOT EXISTS (SELECT 1 FROM canonical_albums a WHERE a.norm_slug=r.norm_slug) '
        'AND NOT EXISTS (SELECT 1 FROM cached_albums c WHERE c.norm_slug=r.norm_slug)'
    )


def selected_rows_sql():
    return (
        'SELECT norm_slug, disc, num, title, 0 AS src FROM canonical_rows '
        'UNION ALL '
        'SELECT norm_slug, disc, num, title, 1 AS src FROM cached_rows '
        'UNION ALL '
        'SELECT norm_slug, disc, num, title, 2 AS src FROM crawled_rows'
    )


def count_rows(con, source):
    src = {'metadata': 0, 'cache': 1, 'crawl': 2}.get(source)
    if src is None:
        raise ValueError(source)
    sql = (
        'SELECT count(*) FROM ('
        'SELECT DISTINCT chosen.norm_slug, rows.disc, rows.num, rows.title '
        'FROM (' + selected_albums_sql() + ') chosen '
        'JOIN (' + selected_rows_sql() + ') rows '
        'ON rows.norm_slug=chosen.norm_slug AND rows.src=chosen.src '
        'WHERE chosen.src=?'
        ')'
    )
    return con.execute(sql, (src,)).fetchone()[0]


def count_albums(con):
    return con.execute('SELECT count(*) FROM (' + selected_albums_sql() + ')').fetchone()[0]


def write_row(out, slug, disc, num, title):
    line = '%s\t%s\t%s\t%s\n' % (slug, disc or '', num or '', sanitize_field(title))
    out.write(line)
    return line.encode('utf-8')


def build_raw_tsv(con, raw_path):
    sql = (
        'SELECT chosen.slug, rows.disc, rows.num, rows.title '
        'FROM (' + selected_albums_sql() + ') chosen '
        'JOIN (' + selected_rows_sql() + ') rows '
        'ON rows.norm_slug=chosen.norm_slug AND rows.src=chosen.src '
        'GROUP BY chosen.norm_slug, chosen.slug, chosen.src, rows.disc, rows.num, rows.title '
        'ORDER BY chosen.slug, chosen.src, rows.disc IS NULL, rows.disc, rows.num IS NULL, rows.num, rows.title'
    )
    rows = 0
    with open(raw_path, 'w', encoding='utf-8', newline='') as out:
        for slug, disc, num, title in con.execute(sql):
            write_row(out, slug, disc, num, title)
            rows += 1
    if not rows:
        raise BuildError('no input rows; refusing to publish an empty index')
    return rows, os.path.getsize(raw_path), digest_file(raw_path)


def gzip_deterministic(src_path, dst_path, compresslevel):
    with open(src_path, 'rb') as src, open(dst_path, 'wb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw,
                           compresslevel=compresslevel, mtime=0) as gz:
            for chunk in iter(lambda: src.read(1 << 20), b''):
                if not chunk:
                    break
                gz.write(chunk)


def validate_outputs(raw_path, gz_path, manifest):
    if manifest['songs'] <= 0:
        raise BuildError('refusing to publish zero songs')
    if manifest['albums'] <= 0:
        raise BuildError('refusing to publish zero albums')
    if manifest['content_sha256'] != digest_file(raw_path):
        raise BuildError('raw TSV digest mismatch')
    if manifest['sha256'] != digest_file(gz_path):
        raise BuildError('gzip digest mismatch')
    rows = 0
    raw_hash = hashlib.sha256()
    with gzip.open(gz_path, 'rb') as fh:
        for line in fh:
            raw_hash.update(line)
            rows += 1
    if rows != manifest['songs']:
        raise BuildError('gzip row count mismatch')
    if raw_hash.hexdigest() != manifest['content_sha256']:
        raise BuildError('gzip content digest mismatch')


def atomic_replace(src, dst):
    os.makedirs(os.path.dirname(dst) or '.', exist_ok=True)
    os.replace(src, dst)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    flags = explicit_flags(argv)
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--snapshot', default='work/crawl_state_snapshot.tar.gz',
                    help='crawl state snapshot containing work/songs_crawled.jsonl')
    ap.add_argument('--cached', default='work/songs_cached.jsonl.gz',
                    help='2023 song cache with real titles')
    ap.add_argument('--metadata', default='',
                    help='optional canonical album metadata NDJSON(.gz) with complete tracks')
    ap.add_argument('--library', default=None,
                    help='optional library.json, only used for coverage stats')
    ap.add_argument('--out', default='work/songs.tsv.gz')
    ap.add_argument('--manifest', default='work/songs-index.json')
    ap.add_argument('--compresslevel', type=int, default=6)
    ap.add_argument('--allow-partial', action='store_true',
                    help='allow building from only one half of the legacy inputs')
    args = ap.parse_args(argv)

    started = time.time()
    exists = ensure_inputs(args, flags)
    tmpdir = tempfile.mkdtemp(prefix='songs-index-', dir=os.path.dirname(args.out) or '.')
    db_path = os.path.join(tmpdir, 'songs.db')
    raw_path = os.path.join(tmpdir, 'songs.tsv')
    gz_path = os.path.join(tmpdir, 'songs.tsv.gz')
    manifest_path = os.path.join(tmpdir, 'songs-index.json')
    con = create_db(db_path)
    try:
        if exists['metadata']:
            ingest_metadata(con, args.metadata)
            log('metadata %7d songs over %6d albums'
                % (count_rows(con, 'metadata'),
                   con.execute('SELECT count(*) FROM canonical_albums').fetchone()[0]))
        else:
            log('metadata disabled')
        cached_rows = ingest_cached(con, args.cached) if exists['cached'] else 0
        if exists['cached']:
            log('cached   %7d legacy rows' % cached_rows)
        else:
            log('cached input missing')
        crawled_rows = numbered = 0
        if exists['snapshot']:
            crawled_rows, numbered = ingest_crawled(con, args.snapshot)
            log('crawled  %7d legacy rows (%.1f%% kept a track number)'
                % (crawled_rows, 100.0 * numbered / max(1, crawled_rows)))
        else:
            log('snapshot input missing')

        rows, raw_bytes, content_sha256 = build_raw_tsv(con, raw_path)
        gzip_deterministic(raw_path, gz_path, args.compresslevel)
        manifest = {
            'schema_version': SCHEMA_VERSION,
            'generated': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'songs': rows,
            'albums': count_albums(con),
            'songs_from_metadata': count_rows(con, 'metadata'),
            'songs_from_cache': count_rows(con, 'cache'),
            'songs_from_crawl': count_rows(con, 'crawl'),
            'bytes_raw': raw_bytes,
            'bytes_gzip': os.path.getsize(gz_path),
            'content_sha256': content_sha256,
            'sha256': digest_file(gz_path),
            'build_seconds': round(time.time() - started, 1),
            'format': 'album\\tdisc\\ttrack\\ttitle',
        }
        if exists['library']:
            with open(args.library, encoding='utf-8') as fh:
                library = json.load(fh)
            lib_slugs = {norm_slug(a.get('slug')) for a in library.get('albums', []) if a.get('slug')}
            chosen = {slug for (slug,) in con.execute('SELECT norm_slug FROM (' + selected_albums_sql() + ')')}
            covered = len(lib_slugs & chosen)
            manifest['library_albums'] = len(lib_slugs)
            manifest['library_albums_covered'] = covered
            manifest['library_coverage_pct'] = round(100.0 * covered / max(1, len(lib_slugs)), 2)
            log('library coverage: %d / %d albums (%.1f%%)'
                % (covered, len(lib_slugs), manifest['library_coverage_pct']))

        with open(manifest_path, 'w', encoding='utf-8', newline='') as fh:
            json.dump(manifest, fh, indent=2, sort_keys=True, ensure_ascii=False)
            fh.write('\n')
        validate_outputs(raw_path, gz_path, manifest)
        atomic_replace(gz_path, args.out)
        atomic_replace(manifest_path, args.manifest)
        log('wrote %s (%.1f MB raw, %.1f MB gzipped)'
            % (args.out, manifest['bytes_raw'] / 1e6, manifest['bytes_gzip'] / 1e6))
        log('manifest written to %s' % args.manifest)
    finally:
        con.close()
        for path in (db_path, raw_path, gz_path, manifest_path):
            if os.path.exists(path):
                os.remove(path)
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass


if __name__ == '__main__':
    try:
        main()
    except BuildError as exc:
        raise SystemExit(str(exc))
