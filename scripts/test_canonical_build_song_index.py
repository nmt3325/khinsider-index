import gzip
import json
import tarfile

import build_song_index


def write_jsonl_gz(path, rows):
    with gzip.open(path, 'wt', encoding='utf-8') as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + '\n')


def write_snapshot(path, rows):
    payload = ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows).encode('utf-8')
    inner = path.parent / 'songs_crawled.jsonl'
    inner.write_bytes(payload)
    with tarfile.open(path, 'w:gz') as tar:
        tar.add(inner, arcname='work/songs_crawled.jsonl')
    inner.unlink()


def read_gz_lines(path):
    with gzip.open(path, 'rt', encoding='utf-8') as fh:
        return [line.rstrip('\n') for line in fh]


def test_metadata_replaces_old_rows_and_is_deterministic(tmp_path):
    cached = tmp_path / 'cached.jsonl.gz'
    snapshot = tmp_path / 'snapshot.tar.gz'
    metadata = tmp_path / 'album-meta.ndjson'
    out1 = tmp_path / 'songs1.tsv.gz'
    out2 = tmp_path / 'songs2.tsv.gz'
    manifest1 = tmp_path / 'songs1.json'
    manifest2 = tmp_path / 'songs2.json'

    write_jsonl_gz(cached, [
        {'album': 'album-a', 'n': 1, 'disc': 1, 'title': 'Old Title', 'track_url': 'https://x/a.mp3'},
        {'album': 'album-b', 'n': 1, 'disc': 1, 'title': 'Cache Only', 'track_url': 'https://x/b.mp3'},
    ])
    write_snapshot(snapshot, [
        {'album': 'album-c', 'track_url': 'https://downloads.khinsider.com/game-soundtracks/album/album-c/01.%2520From%2520URL.mp3'},
    ])
    metadata.write_text('\n'.join([
        json.dumps({'slug': 'album-a', 'crawled_at': '2026-01-01T00:00:00Z', 'tracks_complete': True,
                    'tracks': [{'basename': '01.%20Old.mp3', 'num': 1, 'disc': 1, 'title': 'Old Canon'}]}),
        json.dumps({'slug': 'album-a', 'crawled_at': '2026-02-01T00:00:00Z', 'tracks_complete': True,
                    'tracks': [{'basename': '01.%20New.mp3', 'num': 1, 'disc': 1, 'title': 'New\tTitle'}]}),
    ]) + '\n', encoding='utf-8')

    args = ['--cached', str(cached), '--snapshot', str(snapshot), '--metadata', str(metadata)]
    build_song_index.main(args + ['--out', str(out1), '--manifest', str(manifest1)])
    build_song_index.main(args + ['--out', str(out2), '--manifest', str(manifest2)])

    assert read_gz_lines(out1) == [
        'album-a\t1\t1\tNew Title',
        'album-b\t1\t1\tCache Only',
        'album-c\t\t1\tFrom URL',
    ]
    assert out1.read_bytes() == out2.read_bytes()
    m1 = json.loads(manifest1.read_text(encoding='utf-8'))
    m2 = json.loads(manifest2.read_text(encoding='utf-8'))
    assert m1['schema_version'] == 1
    assert m1['content_sha256'] == m2['content_sha256']
    assert m1['sha256'] == m2['sha256']
    assert m1['songs_from_metadata'] == 1
    assert m1['songs_from_cache'] == 1
    assert m1['songs_from_crawl'] == 1


def test_encoded_cache_replaced_by_decoded_canonical_slug(tmp_path):
    cached = tmp_path / 'cached.jsonl.gz'
    snapshot = tmp_path / 'snapshot.tar.gz'
    metadata = tmp_path / 'album-meta.ndjson'
    out = tmp_path / 'songs.tsv.gz'
    manifest = tmp_path / 'songs.json'

    write_jsonl_gz(cached, [
        {'album': 'caf%C3%A9', 'n': 1, 'disc': 1, 'title': 'Legacy Song', 'track_url': 'https://x/legacy.mp3'},
    ])
    write_snapshot(snapshot, [])
    metadata.write_text(json.dumps({
        'slug': 'café',
        'crawled_at': '2026-03-01T00:00:00Z',
        'tracks_complete': True,
        'tracks': [{'basename': '01.%20New%20Song.mp3', 'num': 1, 'disc': 1, 'title': 'New Song'}],
    }, ensure_ascii=False) + '\n', encoding='utf-8')

    build_song_index.main([
        '--cached', str(cached), '--snapshot', str(snapshot), '--metadata', str(metadata),
        '--out', str(out), '--manifest', str(manifest),
    ])

    assert read_gz_lines(out) == [
        'café\t1\t1\tNew Song',
    ]
    data = json.loads(manifest.read_text(encoding='utf-8'))
    assert data['albums'] == 1
    assert data['songs_from_metadata'] == 1
    assert data['songs_from_cache'] == 0
    assert data['songs_from_crawl'] == 0


def test_decoded_cache_replaced_by_encoded_canonical_slug(tmp_path):
    cached = tmp_path / 'cached.jsonl.gz'
    snapshot = tmp_path / 'snapshot.tar.gz'
    metadata = tmp_path / 'album-meta.ndjson'
    out = tmp_path / 'songs.tsv.gz'
    manifest = tmp_path / 'songs.json'

    write_jsonl_gz(cached, [
        {'album': 'café', 'n': 1, 'disc': 1, 'title': 'Legacy Song', 'track_url': 'https://x/legacy.mp3'},
    ])
    write_snapshot(snapshot, [])
    metadata.write_text(json.dumps({
        'slug': 'caf%C3%A9',
        'crawled_at': '2026-03-01T00:00:00Z',
        'tracks_complete': True,
        'tracks': [{'basename': '01.%20Encoded%20Canon.mp3', 'num': 1, 'disc': 1, 'title': 'Encoded Canon'}],
    }, ensure_ascii=False) + '\n', encoding='utf-8')

    build_song_index.main([
        '--cached', str(cached), '--snapshot', str(snapshot), '--metadata', str(metadata),
        '--out', str(out), '--manifest', str(manifest),
    ])

    assert read_gz_lines(out) == [
        'caf%C3%A9\t1\t1\tEncoded Canon',
    ]
    data = json.loads(manifest.read_text(encoding='utf-8'))
    assert data['albums'] == 1
    assert data['songs_from_metadata'] == 1
    assert data['songs_from_cache'] == 0
    assert data['songs_from_crawl'] == 0


def test_newest_canonical_alias_wins_per_normalized_slug(tmp_path):
    metadata = tmp_path / 'album-meta.ndjson'
    out = tmp_path / 'songs.tsv.gz'
    manifest = tmp_path / 'songs.json'

    metadata.write_text('\n'.join([
        json.dumps({'slug': 'caf%C3%A9', 'crawled_at': '2026-01-01T00:00:00Z', 'tracks_complete': True,
                    'tracks': [{'basename': '01.%20Old.mp3', 'num': 1, 'disc': 1, 'title': 'Old Alias'}]}, ensure_ascii=False),
        json.dumps({'slug': 'café', 'crawled_at': '2026-02-01T00:00:00Z', 'tracks_complete': True,
                    'tracks': [{'basename': '01.%20New.mp3', 'num': 1, 'disc': 1, 'title': 'New Alias'}]}, ensure_ascii=False),
    ]) + '\n', encoding='utf-8')

    build_song_index.main([
        '--metadata', str(metadata),
        '--out', str(out), '--manifest', str(manifest),
    ])

    assert read_gz_lines(out) == [
        'café\t1\t1\tNew Alias',
    ]
    data = json.loads(manifest.read_text(encoding='utf-8'))
    assert data['albums'] == 1
    assert data['songs_from_metadata'] == 1
    assert data['songs_from_cache'] == 0
    assert data['songs_from_crawl'] == 0


def test_cache_beats_crawl_across_slug_aliases_and_counts_once(tmp_path):
    cached = tmp_path / 'cached.jsonl.gz'
    snapshot = tmp_path / 'snapshot.tar.gz'
    out = tmp_path / 'songs.tsv.gz'
    manifest = tmp_path / 'songs.json'

    write_jsonl_gz(cached, [
        {'album': 'caf%C3%A9', 'n': 1, 'disc': 1, 'title': 'Cache Song', 'track_url': 'https://x/cache.mp3'},
    ])
    write_snapshot(snapshot, [
        {'album': 'café', 'track_url': 'https://downloads.khinsider.com/game-soundtracks/album/caf%C3%A9/01.%2520Cache%2520Song.mp3'},
        {'album': 'café', 'track_url': 'https://downloads.khinsider.com/game-soundtracks/album/caf%C3%A9/01.%2520Cache%2520Song.mp3'},
    ])

    build_song_index.main([
        '--cached', str(cached), '--snapshot', str(snapshot),
        '--out', str(out), '--manifest', str(manifest),
    ])

    assert read_gz_lines(out) == [
        'caf%C3%A9\t1\t1\tCache Song',
    ]
    data = json.loads(manifest.read_text(encoding='utf-8'))
    assert data['albums'] == 1
    assert data['songs_from_metadata'] == 0
    assert data['songs_from_cache'] == 1
    assert data['songs_from_crawl'] == 0


def test_crawl_alias_duplicates_collapse_to_one_album_and_song(tmp_path):
    snapshot = tmp_path / 'snapshot.tar.gz'
    out = tmp_path / 'songs.tsv.gz'
    manifest = tmp_path / 'songs.json'

    write_snapshot(snapshot, [
        {'album': 'caf%C3%A9', 'track_url': 'https://downloads.khinsider.com/game-soundtracks/album/caf%C3%A9/01.%2520Only%2520Song.mp3'},
        {'album': 'café', 'track_url': 'https://downloads.khinsider.com/game-soundtracks/album/caf%C3%A9/01.%2520Only%2520Song.mp3'},
    ])

    build_song_index.main([
        '--snapshot', str(snapshot), '--allow-partial',
        '--out', str(out), '--manifest', str(manifest),
    ])

    assert read_gz_lines(out) == [
        'caf%C3%A9\t\t1\tOnly Song',
    ]
    data = json.loads(manifest.read_text(encoding='utf-8'))
    assert data['albums'] == 1
    assert data['songs_from_metadata'] == 0
    assert data['songs_from_cache'] == 0
    assert data['songs_from_crawl'] == 1


def test_non_complete_metadata_is_skipped_and_complete_basename_fallback_still_works(tmp_path):
    cached = tmp_path / 'cached.jsonl.gz'
    snapshot = tmp_path / 'snapshot.tar.gz'
    metadata = tmp_path / 'album-meta.ndjson'
    out = tmp_path / 'songs.tsv.gz'
    manifest = tmp_path / 'songs.json'

    write_jsonl_gz(cached, [
        {'album': 'album-a', 'n': 1, 'disc': 1, 'title': 'Keep Cache', 'track_url': 'https://x/a.mp3'},
    ])
    write_snapshot(snapshot, [
        {'album': 'album-b', 'track_url': 'https://downloads.khinsider.com/game-soundtracks/album/album-b/02.%2520From%2520Crawl.mp3'},
    ])
    metadata.write_text('\n'.join([
        json.dumps({'slug': 'album-a', 'tracks_complete': False, 'tracks': 'legacy summary'}),
        json.dumps({'slug': 'album-c', 'crawled_at': '2026-03-01T00:00:00Z', 'tracks_complete': True,
                    'tracks': [{'basename': 'Bonus%20Track.mp3'}]}),
    ]) + '\n', encoding='utf-8')

    build_song_index.main([
        '--cached', str(cached), '--snapshot', str(snapshot), '--metadata', str(metadata),
        '--out', str(out), '--manifest', str(manifest),
    ])

    assert read_gz_lines(out) == [
        'album-a\t1\t1\tKeep Cache',
        'album-b\t\t2\tFrom Crawl',
        'album-c\t\t\tBonus Track',
    ]
    data = json.loads(manifest.read_text(encoding='utf-8'))
    assert data['songs_from_metadata'] == 1
    assert data['songs_from_cache'] == 1
    assert data['songs_from_crawl'] == 1


def test_invalid_complete_metadata_keeps_last_good_outputs(tmp_path):
    cached = tmp_path / 'cached.jsonl.gz'
    snapshot = tmp_path / 'snapshot.tar.gz'
    out = tmp_path / 'songs.tsv.gz'
    manifest = tmp_path / 'songs.json'

    write_jsonl_gz(cached, [
        {'album': 'demo', 'n': 1, 'title': 'A', 'track_url': 'https://x/a.mp3'},
        {'album': 'demo', 'n': 2, 'title': 'B', 'track_url': 'https://x/b.mp3'},
    ])
    write_snapshot(snapshot, [])
    build_song_index.main([
        '--cached', str(cached), '--snapshot', str(snapshot),
        '--out', str(out), '--manifest', str(manifest),
    ])
    before_out = out.read_bytes()
    before_manifest = manifest.read_text(encoding='utf-8')

    cases = [
        (
            'malformed-track',
            json.dumps({'slug': 'demo', 'crawled_at': '2026-02-01T00:00:00Z', 'tracks_complete': True,
                        'tracks': [{'num': 1, 'title': 'A'}, {}]}),
            'complete metadata',
        ),
        (
            'empty-tracks',
            json.dumps({'slug': 'demo', 'crawled_at': '2026-02-01T00:00:00Z', 'tracks_complete': True,
                        'tracks': []}),
            'has no tracks',
        ),
    ]

    for name, payload, expected in cases:
        metadata = tmp_path / f'{name}.ndjson'
        metadata.write_text('\n'.join([
            json.dumps({'slug': 'demo', 'crawled_at': '2026-01-01T00:00:00Z', 'tracks_complete': True,
                        'tracks': [{'num': 1, 'title': 'A'}, {'num': 2, 'title': 'B'}]}),
            payload,
        ]) + '\n', encoding='utf-8')

        try:
            build_song_index.main([
                '--cached', str(cached), '--snapshot', str(snapshot), '--metadata', str(metadata),
                '--out', str(out), '--manifest', str(manifest),
            ])
        except build_song_index.BuildError as exc:
            assert expected in str(exc)
        else:
            raise AssertionError('expected BuildError')

        assert out.read_bytes() == before_out
        assert manifest.read_text(encoding='utf-8') == before_manifest


def test_invalid_metadata_record_keeps_last_good_outputs(tmp_path):
    cached = tmp_path / 'cached.jsonl.gz'
    snapshot = tmp_path / 'snapshot.tar.gz'
    metadata = tmp_path / 'album-meta.ndjson'
    out = tmp_path / 'songs.tsv.gz'
    manifest = tmp_path / 'songs.json'

    write_jsonl_gz(cached, [
        {'album': 'album-a', 'n': 1, 'disc': 1, 'title': 'Keep', 'track_url': 'https://x/a.mp3'},
    ])
    write_snapshot(snapshot, [
        {'album': 'album-b', 'track_url': 'https://downloads.khinsider.com/game-soundtracks/album/album-b/01.%2520Also%2520Keep.mp3'},
    ])

    build_song_index.main([
        '--cached', str(cached), '--snapshot', str(snapshot),
        '--out', str(out), '--manifest', str(manifest),
    ])
    before_out = out.read_bytes()
    before_manifest = manifest.read_text(encoding='utf-8')

    metadata.write_text(json.dumps('not a dict') + '\n', encoding='utf-8')
    try:
        build_song_index.main([
            '--cached', str(cached), '--snapshot', str(snapshot), '--metadata', str(metadata),
            '--out', str(out), '--manifest', str(manifest),
        ])
    except build_song_index.BuildError as exc:
        assert 'invalid metadata record' in str(exc)
    else:
        raise AssertionError('expected BuildError')

    assert out.read_bytes() == before_out
    assert manifest.read_text(encoding='utf-8') == before_manifest


def test_partial_legacy_input_is_guarded_and_last_good_is_kept(tmp_path):
    cached = tmp_path / 'cached.jsonl.gz'
    out = tmp_path / 'songs.tsv.gz'
    manifest = tmp_path / 'songs.json'
    write_jsonl_gz(cached, [{'album': 'album-a', 'n': 1, 'disc': 1, 'title': 'Keep', 'track_url': 'https://x/a.mp3'}])
    out.write_bytes(b'last-good')
    manifest.write_text('{"ok":true}\n', encoding='utf-8')
    try:
        build_song_index.main(['--cached', str(cached), '--out', str(out), '--manifest', str(manifest)])
    except build_song_index.BuildError as exc:
        assert 'allow-partial' in str(exc)
    else:
        raise AssertionError('expected BuildError')
    assert out.read_bytes() == b'last-good'
    assert manifest.read_text(encoding='utf-8') == '{"ok":true}\n'


def test_explicit_missing_input_errors(tmp_path):
    try:
        build_song_index.main(['--snapshot', str(tmp_path / 'missing.tar.gz')])
    except build_song_index.BuildError as exc:
        assert 'snapshot missing' in str(exc)
    else:
        raise AssertionError('expected BuildError')
