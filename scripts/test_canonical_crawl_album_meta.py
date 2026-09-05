import json
import time

import crawl_album_meta


def test_dedupe_targets_preserves_first_file_order():
    rows = [('a', 'A1'), ('b', 'B'), ('a', 'A2'), ('c', 'C')]
    assert crawl_album_meta.dedupe_targets(rows) == [('a', 'A1'), ('b', 'B'), ('c', 'C')]


def test_filter_targets_with_refresh_days(tmp_path):
    out = tmp_path / 'album-meta.ndjson'
    now = time.time()
    old = time.strftime(crawl_album_meta.TIME_FMT, time.gmtime(now - 10 * 86400))
    recent = time.strftime(crawl_album_meta.TIME_FMT, time.gmtime(now - 86400))
    out.write_text('\n'.join([
        json.dumps({'slug': 'old', 'crawled_at': old}),
        json.dumps({'slug': 'recent', 'crawled_at': recent}),
    ]) + '\n', encoding='utf-8')
    done = crawl_album_meta.load_done(str(out))
    todo = crawl_album_meta.filter_targets([('old', 'old'), ('recent', 'recent'), ('new', 'new')],
                                           done, str(tmp_path / 'fail.log'), refresh_days=7, now=now)
    assert todo == [('old', 'old'), ('new', 'new')]


def test_transient_songlist_parse_errors_are_not_permanent(tmp_path):
    failures = tmp_path / 'fail.log'
    failures.write_text('slug\tsonglist parse error: missing songlist\ttitle\n', encoding='utf-8')
    assert crawl_album_meta.load_failed(str(failures), permanent_only=True) == set()
