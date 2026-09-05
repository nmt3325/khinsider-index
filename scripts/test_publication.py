import gzip
import json
import tempfile
import unittest
from pathlib import Path

import build_library
import publication


class BuildLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'scripts').mkdir()
        self.index = self.root / 'index.json'
        self.list_path = self.root / 'album-list.ndjson'
        self.recent = self.root / 'recent-albums.ndjson'
        self.meta = self.root / 'album-meta.ndjson'
        self.publishers = self.root / 'facet-publisher.ndjson'
        self.developers = self.root / 'facet-developer.ndjson'
        self.publisher_stats = self.root / 'facet-publisher-stats.ndjson'
        self.developer_stats = self.root / 'facet-developer-stats.ndjson'
        self.list_state = self.root / 'album-list.pages'
        self.out = self.root / 'library.json'
        self.index.write_text(json.dumps({
            'index_version': 'v1',
            'entries': {'Base Album': '/game-soundtracks/album/base-album'},
        }), encoding='utf-8')
        for path in (self.publishers, self.developers, self.publisher_stats,
                     self.developer_stats, self.list_state):
            path.write_text('', encoding='utf-8')

    def build_args(self, pretty=False):
        return [
            '--index', str(self.index),
            '--list', str(self.list_path),
            '--meta', str(self.meta),
            '--publishers', str(self.publishers),
            '--developers', str(self.developers),
            '--publisher-stats', str(self.publisher_stats),
            '--developer-stats', str(self.developer_stats),
            '--list-state', str(self.list_state),
            '--recent', str(self.recent),
            '--out', str(self.out),
            '--gzip',
        ] + (['--pretty'] if pretty else [])

    def test_recent_rows_extend_the_universe(self):
        self.list_path.write_text('', encoding='utf-8')
        self.meta.write_text('', encoding='utf-8')
        self.recent.write_text(json.dumps({
            'slug': 'brand-new-album',
            'title': 'Brand New Album',
            'platforms': ['Switch'],
            'album_type': 'Gamerip',
            'year': 2026,
            'listed_at': '2026-09-05',
            'discovered_at': '2026-09-05T00:00:00Z',
        }) + '\n', encoding='utf-8')

        build_library.main(self.build_args())
        data = json.loads(self.out.read_text(encoding='utf-8'))
        slugs = {album['slug'] for album in data['albums']}
        self.assertIn('brand-new-album', slugs)
        self.assertEqual(data['coverage']['added_from_list'], 1)

    def test_tracks_are_dropped_from_library_and_meta_cache(self):
        self.list_path.write_text(json.dumps({'slug': 'base-album', 'title': 'Base Album', 'crawled_at': '2026-09-05T00:00:00Z'}) + '\n', encoding='utf-8')
        self.recent.write_text('', encoding='utf-8')
        self.meta.write_text(json.dumps({
            'slug': 'base-album',
            'title': 'Base Album',
            'crawled_at': '2026-09-05T00:00:00Z',
            'tracks_complete': True,
            'tracks': [{'title': 'Huge payload'}],
            'formats': ['mp3'],
            'track_count': 1,
        }) + '\n', encoding='utf-8')

        build_library.main(self.build_args())
        album = json.loads(self.out.read_text(encoding='utf-8'))['albums'][0]
        self.assertNotIn('tracks', album)
        self.assertEqual(album['track_count'], 1)
        self.assertEqual(album['formats'], ['mp3'])

    def test_gzip_output_is_deterministic(self):
        payload = {'albums': [{'slug': 'base-album', 'title': 'Base Album', 'letter': 'B'}]}
        first = self.root / 'one.json.gz'
        second = self.root / 'two.json.gz'
        build_library.write_gzip(first, json.dumps(payload, ensure_ascii=False))
        build_library.write_gzip(second, json.dumps(payload, ensure_ascii=False))
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(json.loads(gzip.decompress(first.read_bytes()).decode('utf-8'))['albums'][0]['slug'], 'base-album')


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write_json(self, name, payload):
        path = self.root / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        return path

    def test_library_hash_ignores_generation_diagnostics(self):
        current = self.write_json('current.json', {
            'library_version': '2026-09-05',
            'generated_at': '2026-09-05T00:00:00Z',
            'index_version': 'v1',
            'coverage': {'albums': 1},
            'metadata_count': 1,
            'albums': [{'slug': 'a', 'title': 'A', 'letter': 'A'}],
        })
        previous = self.write_json('previous.json', {
            'library_version': '2026-09-06',
            'generated_at': '2026-09-06T00:00:00Z',
            'index_version': 'v2',
            'coverage': {'albums': 999},
            'metadata_count': 999,
            'albums': [{'slug': 'a', 'title': 'A', 'letter': 'A'}],
        })
        result = publication.compare_library(str(current), str(previous))
        self.assertFalse(result['changed'])

    def test_library_hash_changes_when_album_content_changes(self):
        current = self.write_json('current.json', {'albums': [{'slug': 'a', 'title': 'A', 'letter': 'A'}]})
        previous = self.write_json('previous.json', {'albums': [{'slug': 'a', 'title': 'B', 'letter': 'A'}]})
        result = publication.compare_library(str(current), str(previous))
        self.assertTrue(result['changed'])

    def test_song_manifest_compare_uses_content_keys(self):
        current = self.write_json('current-manifest.json', {
            'schema_version': 1,
            'content_sha256': 'abc',
            'sha256': 'def',
            'generated': 'now',
        })
        previous = self.write_json('previous-manifest.json', {
            'schema_version': 1,
            'content_sha256': 'abc',
            'sha256': 'def',
            'generated': 'later',
        })
        self.assertFalse(publication.compare_song_manifest(str(current), str(previous))['changed'])
        changed = self.write_json('changed-manifest.json', {
            'schema_version': 1,
            'content_sha256': 'zzz',
            'sha256': 'def',
        })
        self.assertTrue(publication.compare_song_manifest(str(changed), str(previous))['changed'])


if __name__ == '__main__':
    unittest.main()
