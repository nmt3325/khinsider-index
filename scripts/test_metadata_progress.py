import json
from pathlib import Path
import tempfile
import unittest

from metadata_progress import measure


class MetadataProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = self.root / 'library.json'
        self.meta = self.root / 'album-meta.ndjson'
        self.failures = self.root / 'album-meta-failures.log'

    def prepare(self, albums, records=(), failures=''):
        self.library.write_text(json.dumps({'albums': albums}), encoding='utf-8')
        self.meta.write_text(''.join(json.dumps(r) + '\n' for r in records), encoding='utf-8')
        self.failures.write_text(failures, encoding='utf-8')

    def read(self):
        result = measure(self.library, self.meta, self.failures)
        self.assertEqual(result['total'], result['fetched'] + result['unavailable'] + result['pending'])
        return result

    def test_listing_metadata_does_not_count_as_page_visit(self):
        self.prepare([{'slug': 'known', 'publishers': ['Nintendo'], 'year': 2011},
                      {'slug': 'empty', 'publishers': [], 'date_added': None},
                      {'slug': 'missing'}])
        self.assertEqual(self.read()['pending'], 3)

    def test_existing_page_with_empty_fields_is_finished(self):
        self.prepare([{'slug': 'a'}, {'slug': 'b'}], [{'slug': 'a', 'publishers': [], 'date_added': None}])
        result = self.read()
        self.assertEqual((result['fetched'], result['pending']), (1, 1))

    def test_only_permanent_failures_are_excluded(self):
        self.prepare([{'slug': s} for s in ('a', 'b', 'c', 'd')], failures=
                     'a\tgone\tA\nb\tno album content\tB\nc\tcloudflare\tC\nd\t500\tD\n')
        result = self.read()
        self.assertEqual((result['unavailable'], result['pending']), (1, 3))

    def test_legacy_no_content_is_retryable(self):
        self.prepare([{'slug': 'a'}], failures='a\tno album content\tA\n')
        result = self.read()
        self.assertEqual((result['unavailable'], result['pending']), (0, 1))

    def test_success_wins_over_old_failure(self):
        self.prepare([{'slug': 'a'}], [{'slug': 'a'}], 'a\tgone\tA\n')
        result = self.read()
        self.assertEqual((result['fetched'], result['unavailable']), (1, 0))

    def test_deduplicates_and_ignores_out_of_catalog_records(self):
        self.prepare([{'slug': 'a'}, {'slug': 'a'}, {'slug': 'b'}],
                     [{'slug': 'a'}, {'slug': 'a'}, {'slug': 'outside'}], 'outside\tgone\n')
        result = self.read()
        self.assertEqual((result['total'], result['fetched'], result['pending']), (2, 1, 1))

    def test_malformed_metadata_fails_instead_of_under_counting(self):
        self.prepare([{'slug': 'a'}])
        self.meta.write_text('{broken\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'invalid metadata record'):
            self.read()

    def test_empty_library_is_not_false_completion(self):
        self.prepare([])
        with self.assertRaisesRegex(ValueError, 'non-empty'):
            self.read()

    def test_absent_metadata_file_is_a_fresh_crawl(self):
        self.prepare([{'slug': 'a'}])
        self.meta.unlink()
        self.failures.unlink()
        self.assertEqual(self.read()['pending'], 1)

    def test_published_coverage_is_reported_separately(self):
        self.prepare([{'slug': 'a'}, {'slug': 'b'}], [{'slug': 'a'}, {'slug': 'b'}])
        data = json.loads(self.library.read_text())
        data['coverage'] = {'sources': {'album_page': {'albums': 1}}}
        self.library.write_text(json.dumps(data))
        result = self.read()
        self.assertEqual((result['fetched'], result['published_fetched']), (2, 1))


if __name__ == '__main__':
    unittest.main()
