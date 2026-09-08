"""Contracts for the standalone, live-site-only dataset.

A complete catalogue plus validated current-generation album-page records are
required. Legacy indexes, title caches and archival snapshots are not inputs.
"""
import datetime as dt
import gzip
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import quote, unquote

SOURCE = 'khinsider-live-v2'
SCHEMA = 2
LIST_URL = 'https://downloads.khinsider.com/game-soundtracks'


class DataError(ValueError):
    pass


class IncompleteData(DataError):
    pass


def canonical_slug(value):
    if not isinstance(value, str) or not value:
        raise DataError('missing album slug')
    decoded = unquote(value)
    if decoded in ('.', '..') or any(c in decoded for c in '/\\'):
        raise DataError('invalid album path segment')
    if any(ord(c) < 32 or ord(c) == 127 for c in decoded):
        raise DataError('control character in album slug')
    return quote(decoded, safe='')


def stamp(value):
    try:
        result = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError('timezone missing')
        return result.timestamp()
    except (AttributeError, TypeError, ValueError) as exc:
        raise DataError('invalid UTC observation timestamp') from exc


def now():
    return dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def stable_bytes(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':')).encode('utf-8')


def digest_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True,
                  separators=(',', ':'))
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def jsonl(path, missing_ok=False):
    path = Path(path)
    if missing_ok and not path.exists():
        return
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8') as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError('expected object')
            except ValueError as exc:
                raise DataError(f'{path}:{line_number}: invalid record') from exc
            yield line_number, record


def catalogue_id(albums):
    semantic = [{key: value for key, value in row.items()
                 if key not in ('page', 'crawled_at')} for row in albums]
    return hashlib.sha256(stable_bytes(sorted(semantic, key=lambda a: a['slug']))).hexdigest()


def write_catalogue(rows_path, output, pages, advertised, started_at):
    """Certify only a full, nonempty listing sweep; never a capped sample."""
    if not isinstance(pages, int) or isinstance(pages, bool) or pages < 1:
        raise DataError('listing page count is missing')
    if not isinstance(advertised, int) or isinstance(advertised, bool) or advertised < 1:
        raise DataError('listing advertised album count is missing')
    started = stamp(started_at)
    rows, observed_pages = {}, set()
    for _, record in jsonl(rows_path):
        page = record.get('page')
        if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= pages:
            raise DataError('listing contains an invalid page number')
        if not isinstance(record.get('title'), str) or not record['title'].strip():
            raise DataError('listing contains an album without a title')
        slug = canonical_slug(record.get('slug'))
        row = dict(record, slug=slug)
        observed = stamp(row.get('crawled_at'))
        if observed < started:
            started, started_at = observed, row['crawled_at']
        previous = rows.get(slug)
        if previous is None or observed >= stamp(previous.get('crawled_at')):
            rows[slug] = row
        observed_pages.add(page)
    if observed_pages != set(range(1, pages + 1)):
        raise IncompleteData('not all listing pages were observed')
    if len(rows) < advertised:
        raise IncompleteData(f'listing has {len(rows)} unique albums; site advertised {advertised}')
    albums = sorted(rows.values(), key=lambda a: a['slug'])
    result = {
        'schema_version': SCHEMA, 'data_source': SOURCE, 'complete': True,
        'started_at': started_at, 'generated_at': now(),
        'catalogue_id': catalogue_id(albums),
        'listing': {'url': LIST_URL, 'pages': pages,
                    'completed_pages': sorted(observed_pages),
                    'advertised_albums': advertised},
        'albums': albums,
    }
    read_catalogue(result)
    atomic_json(output, result)
    return result


def read_catalogue(path):
    data = path if isinstance(path, dict) else json.loads(Path(path).read_text(encoding='utf-8'))
    if (not isinstance(data, dict) or data.get('data_source') != SOURCE
            or data.get('schema_version') != SCHEMA or data.get('complete') is not True):
        raise DataError('a certified live-v2 catalogue is required; legacy indexes are not accepted')
    stamp(data.get('started_at'))
    stamp(data.get('generated_at'))
    listing = data.get('listing', {})
    if not isinstance(listing, dict):
        raise DataError('invalid listing certificate')
    pages = listing.get('pages')
    expected = listing.get('advertised_albums')
    if (listing.get('url') != LIST_URL or not isinstance(pages, int)
            or isinstance(pages, bool) or pages < 1
            or listing.get('completed_pages') != list(range(1, pages + 1))):
        raise DataError('catalogue does not certify the full listing')
    albums = data.get('albums')
    if (not isinstance(albums, list) or not albums or not isinstance(expected, int)
            or isinstance(expected, bool) or expected < 1 or len(albums) < expected):
        raise IncompleteData('catalogue is empty or below the advertised album count')
    seen = set()
    for row in albums:
        if not isinstance(row, dict):
            raise DataError('invalid catalogue album')
        slug = canonical_slug(row.get('slug'))
        if slug != row['slug'] or slug in seen:
            raise DataError('catalogue contains noncanonical or duplicate slugs')
        if not isinstance(row.get('title'), str) or not row['title'].strip():
            raise DataError('catalogue album has no title')
        page = row.get('page')
        if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= pages:
            raise DataError('catalogue album has an invalid listing page')
        if stamp(row.get('crawled_at')) < stamp(data['started_at']):
            raise DataError('catalogue starts after its staged observations')
        seen.add(slug)
    if data.get('catalogue_id') != catalogue_id(albums):
        raise DataError('catalogue content fingerprint mismatch')
    return data


def validate_record(record):
    slug = canonical_slug(record.get('slug'))
    if record.get('data_source') != SOURCE:
        raise DataError(f'{slug}: legacy/unattributed metadata is not accepted')
    stamp(record.get('crawled_at'))
    if 'resolved_slug' in record and canonical_slug(record['resolved_slug']) != record['resolved_slug']:
        raise DataError('noncanonical redirect target')
    if record.get('status') == 'gone' and record.get('http_status') == 404:
        if record.get('tracks') or record.get('tracks_complete'):
            raise DataError(f'{slug}: a 404 cannot contain a complete track list')
        return slug
    if (record.get('status') != 'ok' or record.get('http_status') != 200
            or record.get('tracks_complete') is not True):
        raise DataError(f'{slug}: album page is not complete')
    tracks, count = record.get('tracks'), record.get('track_count')
    if (not isinstance(tracks, list) or not tracks or not isinstance(count, int)
            or isinstance(count, bool) or count != len(tracks)):
        raise DataError(f'{slug}: track count does not match complete track data')
    if not isinstance(record.get('title'), str) or not record['title'].strip():
        raise DataError(f'{slug}: album title is missing')
    identities = set()
    for track in tracks:
        if (not isinstance(track, dict) or not isinstance(track.get('title'), str)
                or not track['title'].strip() or not isinstance(track.get('basename'), str)
                or not track['basename']):
            raise DataError(f'{slug}: invalid track title/basename')
        for field in ('disc', 'num'):
            value = track.get(field)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < (0 if field == 'disc' else 1)):
                raise DataError(f'{slug}: invalid {field}')
        identity = str(track.get('songid') or track['basename'])
        if identity in identities:
            raise DataError(f'{slug}: duplicate track identity')
        identities.add(identity)
    return slug


def latest_records(path):
    """Keep summaries/line offsets, not millions of track objects, in RAM."""
    records = {}
    for number, record in jsonl(path, missing_ok=True):
        slug = validate_record(record)
        previous = records.get(slug)
        order = (stamp(record['crawled_at']), number)
        if previous is not None and order <= previous['_order']:
            continue
        summary = {key: value for key, value in record.items() if key != 'tracks'}
        summary.update(slug=slug, _line=number, _order=order)
        records[slug] = summary
    return records


def inspect(catalogue_path, metadata_path, recent_state=None):
    catalogue = read_catalogue(catalogue_path)
    records = latest_records(metadata_path)
    required_after = {}
    if recent_state and Path(recent_state).exists():
        state = json.loads(Path(recent_state).read_text(encoding='utf-8'))
        for item in state.get('pending', {}).values():
            slug = canonical_slug(item.get('slug'))
            required_after[slug] = max(required_after.get(slug, 0), stamp(item.get('discovered_at')))
    selected, unavailable, pending = {}, [], []
    for row in catalogue['albums']:
        slug = row['slug']
        record = records.get(slug)
        if record is None or record['_order'][0] <= required_after.get(slug, 0):
            pending.append(slug)
        elif record['status'] == 'gone':
            # A new listing can revive an album previously reported missing.
            if record['_order'][0] < stamp(catalogue['started_at']):
                pending.append(slug)
            else:
                unavailable.append(slug)
        else:
            selected[slug] = record
    summary = {
        'data_source': SOURCE, 'catalogue_id': catalogue['catalogue_id'],
        'total': len(catalogue['albums']), 'fetched': len(selected),
        'unavailable': len(unavailable), 'pending': len(pending),
        'tracks': sum(record['track_count'] for record in selected.values()),
        'complete': not pending and bool(selected),
        'fetched_percent': round(100 * len(selected) / len(catalogue['albums']), 2),
        'legacy_inputs': [],
    }
    return catalogue, selected, unavailable, pending, summary


def require_complete(catalogue, metadata, recent_state=None):
    result = inspect(catalogue, metadata, recent_state)
    if not result[-1]['complete']:
        raise IncompleteData(f"live dataset not complete: {result[-1]['pending']} albums pending; refusing publication")
    return result


def inspect_cumulative(catalogue_path, metadata_path, recent_state=None):
    """The complete accumulated snapshot, independent of the retry queue.

    The certified listing is a one-time seed, not a daily replacement. Only
    this generation's metadata and its own recent-discovery journal extend it.
    A failed refresh retains the last valid record; it never empties an album.
    """
    seed = read_catalogue(catalogue_path)
    records = latest_records(metadata_path)
    journal = Path(recent_state).with_name('recent-albums.ndjson') if recent_state else None
    rows = {row['slug']: dict(row) for row in seed['albums']}
    required_after = {}
    if journal and journal.exists():
        for _, row in jsonl(journal):
            slug = canonical_slug(row.get('slug'))
            if not isinstance(row.get('title'), str) or not row['title'].strip():
                raise DataError('recent discovery has no album title')
            stamp(row.get('discovered_at'))
            rows[slug] = dict(rows.get(slug, {}), **dict(row, slug=slug))
    if recent_state and Path(recent_state).exists():
        state = json.loads(Path(recent_state).read_text(encoding='utf-8'))
        for item in state.get('pending', {}).values():
            slug = canonical_slug(item.get('slug'))
            required_after[slug] = max(required_after.get(slug, 0), stamp(item.get('discovered_at')))
            pending_row = dict(item.get('row') or {}, slug=slug)
            pending_row.setdefault('title', slug)
            rows.setdefault(slug, pending_row)
    terminal_observations = {}
    for slug, record in records.items():
        if record['status'] == 'ok':
            target = record.get('resolved_slug') or slug
            terminal_observations[target] = max(terminal_observations.get(target, (0, 0)), record['_order'])
    aliases = {slug: record['resolved_slug'] for slug, record in records.items()
               if record.get('resolved_slug') and record['resolved_slug'] != slug
               and record['_order'] > terminal_observations.get(slug, (0, 0))}

    def resolve(slug):
        visited = set()
        while slug in aliases:
            if slug in visited:
                raise DataError('cyclic album redirect metadata')
            visited.add(slug)
            slug = aliases[slug]
        return slug

    aliases = {slug: resolve(slug) for slug in aliases}
    canonical_records = {}
    for slug, record in records.items():
        target = aliases.get(slug, slug)
        previous = canonical_records.get(target)
        if previous is None or record['_order'] > previous['_order']:
            canonical_records[target] = record if slug == target else dict(record, slug=target)
        rows.setdefault(slug, {'slug': slug, 'title': record.get('title') or slug})
    # A canonical page obtained through a redirect is already fetched; do not
    # request it again merely because its original request used an alias.
    for slug, record in canonical_records.items():
        rows.setdefault(slug, {'slug': slug, 'title': record.get('title') or slug})
    pending, missing, stale = [], set(), set()
    registry = {}
    for requested, row in rows.items():
        slug = aliases.get(requested, requested)
        # Keep only semantic listing fields in the durable registry identity.
        normalized = {key: row.get(key) for key in ('title', 'year', 'album_type', 'platforms')}
        normalized.update(slug=slug, title=normalized['title'] or slug)
        if slug not in registry or requested == slug:
            registry[slug] = normalized
        record = canonical_records.get(slug)
        observed = records.get(requested, record)
        needs_refresh = bool(observed and observed['_order'][0] <= required_after.get(requested, 0))
        if record is None:
            pending.append(requested)
            missing.add(slug)
        elif needs_refresh:
            pending.append(requested)
            stale.add(slug)
    selected = {slug: record for slug, record in canonical_records.items() if record['status'] == 'ok'}
    unavailable = sorted(slug for slug, record in canonical_records.items() if record['status'] == 'gone')
    albums = sorted(registry.values(), key=lambda row: row['slug'])
    catalogue = dict(seed, albums=albums, catalogue_id=catalogue_id(albums),
                     seed_catalogue_id=seed['catalogue_id'], aliases=aliases,
                     completeness_scope='cumulative_snapshot')
    summary = {
        'data_source': SOURCE, 'catalogue_id': catalogue['catalogue_id'],
        'total': len(albums), 'fetched': len(selected), 'unavailable': len(unavailable),
        'pending': len(pending), 'missing_albums': len(missing),
        'refresh_pending': len(stale), 'retained_last_good': len(stale & set(selected)),
        'tracks': sum(record['track_count'] for record in selected.values()),
        'complete': not pending and bool(selected), 'snapshot_complete': bool(selected),
        'completeness_scope': 'cumulative_snapshot', 'alias_count': len(aliases),
        'album_inventory_sha256': hashlib.sha256(stable_bytes(sorted(selected))).hexdigest(),
        'fetched_percent': min(99.9999, round(100 * len(selected) / len(albums), 4))
                           if len(selected) < len(albums) else 100.0, 'legacy_inputs': [],
    }
    return catalogue, selected, unavailable, sorted(set(pending)), summary


def require_cumulative(catalogue, metadata, recent_state=None):
    result = inspect_cumulative(catalogue, metadata, recent_state)
    if not result[-1]['snapshot_complete']:
        raise IncompleteData('no complete album records; refusing an empty cumulative snapshot')
    return result
