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
page. --fresh starts a clean snapshot, which is what the scheduled sweep
does because the whole pass costs only a few minutes.

Examples:
    python crawl_index_pages.py --fresh                 # full snapshot
    python crawl_index_pages.py --max-pages 3           # smoke test
    python crawl_index_pages.py                         # resume a partial run
"""
import argparse
import os
import sys
import time

import album_list


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


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', default=os.path.join(here, '..', 'album-list.ndjson'))
    ap.add_argument('--state', default=os.path.join(here, '..', 'album-list.pages'))
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
                    help='discard any previous output and checkpoint first')
    args = ap.parse_args()

    if args.fresh:
        for path in (args.out, args.state, args.failures):
            if os.path.exists(path):
                os.remove(path)

    fetch_kw = dict(retries=args.retries, delay=args.delay, jitter=args.jitter)
    started = time.time()

    # page 1 also tells us how many pages there are, so it is always fetched
    first_url = album_list.list_page_url(1, args.path)
    html, note = album_list.fetch(first_url, **fetch_kw)
    if html is None:
        raise SystemExit('could not read %s: %s' % (first_url, note))
    first_rows, last_page, total = album_list.parse_album_list(html)
    print('%s: %d pages, %s albums advertised, %d rows on page 1'
          % (args.path, last_page, total, len(first_rows)), flush=True)

    wanted = parse_pages(args.pages, last_page)
    done = album_list.load_state(args.state)
    todo = [p for p in wanted if str(p) not in done]
    if args.max_pages:
        todo = todo[:args.max_pages]
    print('%d pages selected, %d already done, %d to fetch'
          % (len(wanted), len(done), len(todo)), flush=True)

    out = open(args.out, 'a', encoding='utf-8')
    state = open(args.state, 'a', encoding='utf-8')
    failures = open(args.failures, 'a', encoding='utf-8')
    written = 0
    failed = []
    stopped_early = False

    for i, page in enumerate(todo, 1):
        if args.deadline_minutes and (time.time() - started) / 60.0 >= args.deadline_minutes:
            print('deadline reached after %d pages; rerun to resume' % (i - 1), flush=True)
            stopped_early = True
            break
        if page == 1:
            rows, note = first_rows, 'ok'
        else:
            html, note = album_list.fetch(
                album_list.list_page_url(page, args.path), **fetch_kw)
            rows = album_list.parse_album_list(html)[0] if html is not None else None
        if rows is None:
            failures.write('%d\t%s\n' % (page, note))
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
    if failed and not stopped_early:
        print('failed pages: %s' % ','.join(str(p) for p in failed), flush=True)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
