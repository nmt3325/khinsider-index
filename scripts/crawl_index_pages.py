#!/usr/bin/env python3
"""Sweep the flat khinsider album list for platform, album type and year.

/game-soundtracks?page=N is a paginated table of every album on the site,
500 rows per page, with the Platform, Type and Year columns already filled
in. That is ~210 requests for the whole archive instead of ~104,000 album
page visits, so this replaces the per-album crawl for those three fields.

Output is one NDJSON record per album:

    {"slug": "mario-kart-wii", "title": "Mario Kart Wii: ...",
     "platforms": ["Wii"], "album_type": "Gamerip", "year": 2008,
     "page": 121, "crawled_at": "2026-09-03T11:00:00Z"}

A page is only checkpointed after all of its rows are written, so an
interrupted run resumes at page granularity and never loses or duplicates a
page. --fresh builds into a staging snapshot first, and only replaces the
live list once the full sweep completes without failures.

Examples:
    python crawl_index_pages.py --fresh                 # full snapshot
    python crawl_index_pages.py --max-pages 3           # smoke test
    python crawl_index_pages.py                         # resume a partial run
"""
import argparse
import json
import os
import sys
import time

import album_list
import live_data


STAGE_SUFFIX = '-staging'
STAGE_MARKER = '.context.json'
COPY_CHUNK_SIZE = 1024 * 1024


def parse_pages(spec, last):
    """'', '7', '2-9' or '1,4,9' -> a list of page numbers."""
    if not spec:
        return list(range(1, last + 1))
    pages = []
    for chunk in spec.split(','):
        chunk = chunk.strip()
        if not chunk:
            continue
        if '-' in chunk:
            lo, hi = chunk.split('-', 1)
            pages.extend(range(int(lo), int(hi) + 1))
        else:
            pages.append(int(chunk))
    return [p for p in pages if p >= 1]


def staging_paths(out_path):
    base = os.path.dirname(os.path.abspath(out_path))
    name = os.path.basename(out_path)
    prefix = 'album-list-staging' if name == 'album-list.ndjson' else name + STAGE_SUFFIX
    return (
        os.path.join(base, prefix + '.ndjson'),
        os.path.join(base, prefix + '.pages'),
        os.path.join(base, prefix + '-failures.log'),
        os.path.join(base, prefix + STAGE_MARKER),
    )


def replace_file(src, dst):
    tmp = dst + '.tmp'
    with open(src, 'rb') as r, open(tmp, 'wb') as w:
        while True:
            chunk = r.read(COPY_CHUNK_SIZE)
            if not chunk:
                break
            w.write(chunk)
    os.replace(tmp, dst)


def load_marker(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def write_marker(path, marker):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(marker, f, ensure_ascii=False, sort_keys=True)
        f.write('\n')
    os.replace(tmp, path)


def validate_staging(out_path, state_path):
    """A restored completion ledger must have corresponding staged rows."""
    if not os.path.isfile(out_path) or not os.path.isfile(state_path):
        raise SystemExit('incomplete staging checkpoint; keep it aside or use a new --out')
    done = album_list.load_state(state_path)
    present = set()
    try:
        with open(out_path, encoding='utf-8') as f:
            for line in f:
                if not line.strip():
                    continue
                row = json.loads(line)
                page = row.get('page') if isinstance(row, dict) else None
                if not isinstance(page, int) or isinstance(page, bool) or page < 1:
                    raise ValueError('invalid staged page')
                present.add(str(page))
    except (OSError, ValueError) as exc:
        raise SystemExit('invalid staging checkpoint: %s' % exc) from exc
    if not done.issubset(present):
        raise SystemExit('incomplete staging checkpoint; completed pages have no rows')


def reset_paths(paths):
    for path in paths:
        if os.path.exists(path):
            os.remove(path)


def has_complete_snapshot(state_path, last_page):
    done = album_list.load_state(state_path)
    return all(str(page) in done for page in range(1, last_page + 1))


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', default=os.path.join(here, '..', 'album-list.ndjson'))
    ap.add_argument('--state', default=os.path.join(here, '..', 'album-list.pages'))
    ap.add_argument('--catalogue', default='', help='certify a complete live-v2 catalogue')
    ap.add_argument('--failures', default=os.path.join(here, '..', 'album-list-failures.log'))
    ap.add_argument('--path', default=album_list.LIST_PATH,
                    help='listing path to sweep (default %s)' % album_list.LIST_PATH)
    ap.add_argument('--pages', default='', help="page selection, e.g. 1-20 or 3,7 (default: all)")
    ap.add_argument('--max-pages', type=int, default=0, help='stop after N pages (0 = no limit)')
    ap.add_argument('--delay', type=float, default=0.7, help='delay before each request')
    ap.add_argument('--jitter', type=float, default=0.6)
    ap.add_argument('--retries', type=int, default=5)
    ap.add_argument('--deadline-minutes', type=float, default=0.0,
                    help='stop cleanly after N minutes (0 = no deadline)')
    ap.add_argument('--fresh', action='store_true',
                    help='write a staged full snapshot before replacing the live files')
    args = ap.parse_args()

    marker_path = None
    if args.fresh:
        out_path, state_path, fail_path, marker_path = staging_paths(args.out)
        marker = {'path': args.path}
        saved_marker = load_marker(marker_path)
        if saved_marker != marker:
            has_data = any(os.path.isfile(path) and os.path.getsize(path)
                           for path in (out_path, state_path, fail_path))
            if os.path.exists(marker_path) or has_data:
                raise SystemExit('staging context is missing or different; keep it aside or use a new --out')
        else:
            validate_staging(out_path, state_path)
        for path in (out_path, state_path, fail_path):
            if not os.path.exists(path):
                open(path, 'a', encoding='utf-8').close()
        if saved_marker != marker:
            write_marker(marker_path, marker)
    else:
        out_path, state_path, fail_path = args.out, args.state, args.failures

    fetch_kw = dict(retries=args.retries, delay=args.delay, jitter=args.jitter)
    started = time.time()
    observed_start = album_list.utc_now()

    first_url = album_list.list_page_url(1, args.path)
    html, note = album_list.fetch(first_url, **fetch_kw)
    if html is None:
        raise SystemExit('could not read %s: %s' % (first_url, note))
    if not album_list.has_album_table(html):
        raise SystemExit('could not parse %s: no-list-table' % first_url)
    first_rows, last_page, total = album_list.parse_album_list(html)
    if not first_rows:
        raise SystemExit('could not parse %s: empty-list-table' % first_url)
    print('%s: %d pages, %s albums advertised, %d rows on page 1'
          % (args.path, last_page, total, len(first_rows)), flush=True)

    wanted = parse_pages(args.pages, last_page)
    done = album_list.load_state(state_path)
    todo = [p for p in wanted if str(p) not in done]
    capped = False
    if args.max_pages and len(todo) > args.max_pages:
        todo = todo[:args.max_pages]
        capped = True
    print('%d pages selected, %d already done, %d to fetch'
          % (len(wanted), len(done), len(todo)), flush=True)

    out = open(out_path, 'a', encoding='utf-8')
    state = open(state_path, 'a', encoding='utf-8')
    failures = open(fail_path, 'a', encoding='utf-8')
    written = 0
    failed = []
    stopped_early = capped

    for i, page in enumerate(todo, 1):
        if args.deadline_minutes and (time.time() - started) / 60.0 >= args.deadline_minutes:
            print('deadline reached after %d pages; rerun to resume' % (i - 1), flush=True)
            stopped_early = True
            break
        if page == 1:
            page_html, rows, note = html, first_rows, 'ok'
        else:
            page_html, note = album_list.fetch(
                album_list.list_page_url(page, args.path), **fetch_kw)
            rows = album_list.parse_album_list(page_html)[0] if page_html is not None else None
        if page_html is not None:
            if not album_list.has_album_table(page_html):
                rows = None
                note = 'no-list-table'
            elif not rows:
                rows = None
                note = 'empty-list-table'
        if rows is None:
            failures.write('%d	%s\n' % (page, note))
            failures.flush()
            failed.append(page)
            print('page %d failed: %s' % (page, note), flush=True)
            continue
        stamp = album_list.utc_now()
        for row in rows:
            row = dict(row)
            row['page'] = page
            row['crawled_at'] = stamp
            album_list.append_json(out, row)
        written += len(rows)
        album_list.mark_state(state, page)
        if i % 10 == 0 or i == len(todo):
            elapsed = time.time() - started
            rate = i / elapsed if elapsed else 0
            print('%d/%d pages, %d rows, %.2f pages/s, eta %.1fmin'
                  % (i, len(todo), written, rate,
                     (len(todo) - i) / rate / 60 if rate else 0), flush=True)

    out.close()
    state.close()
    failures.close()
    print('done: %d rows from %d pages in %.1fmin (%d page failures)'
          % (written, len(todo) - len(failed), (time.time() - started) / 60, len(failed)),
          flush=True)

    complete = (not failed) and has_complete_snapshot(state_path, last_page)
    if args.fresh:
        if not complete:
            return 1
        if args.catalogue:
            if args.path != album_list.LIST_PATH:
                raise SystemExit('only the full album listing can certify a catalogue')
            try:
                live_data.write_catalogue(out_path, args.catalogue, last_page, total, observed_start)
            except live_data.IncompleteData as exc:
                # A finished-but-incoherent page set cannot make progress by
                # resuming its ledger. Keep one rejected sample, then rescan.
                replace_file(out_path, args.out + '.rejected')
                reset_paths((out_path, state_path, fail_path, marker_path))
                raise SystemExit(str(exc)) from exc
        replace_file(out_path, args.out)
        replace_file(state_path, args.state)
        replace_file(fail_path, args.failures)
        reset_paths((out_path, state_path, fail_path, marker_path))
        return 0
    if failed and not stopped_early:
        print('failed pages: %s' % ','.join(str(p) for p in failed), flush=True)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
