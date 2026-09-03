#!/usr/bin/env python3
"""Sweep khinsider publisher/developer facets to learn album -> company.

/album-publishers lists every publisher with 11 or more albums (1,625 of
them, 79,305 album slots) and /album-developers does the same for developers
(961 / 34,172). Each entity has a /game-soundtracks/publisher/<key> page that
is the same 500-row albumList table as the main browse, so ~1.7k requests
assign a publisher to roughly three quarters of the archive - versus 104k
requests to read the same field off individual album pages.

Output is one NDJSON record per (album, entity) pair:

    {"slug": "mario-kart-wii", "name": "Nintendo", "key": "nintendo"}

An entity is checkpointed only after every one of its pages succeeded, so a
resumed run re-fetches whole entities rather than trusting a half-read one.
Entities are visited largest first, which means a run that is cut short still
covers the most albums possible.

Examples:
    python crawl_facets.py --kind publisher --fresh
    python crawl_facets.py --kind developer --fresh
    python crawl_facets.py --kind publisher --limit 5 --max-pages 1   # smoke test
"""
import argparse
import os
import sys
import time

import album_list


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--kind', choices=sorted(album_list.FACET_INDEX_PATH), default='publisher')
    ap.add_argument('--out', default='', help='default ../facet-<kind>.ndjson')
    ap.add_argument('--state', default='', help='default ../facet-<kind>.entities')
    ap.add_argument('--stats', default='', help='default ../facet-<kind>-stats.ndjson')
    ap.add_argument('--failures', default='', help='default ../facet-<kind>-failures.log')
    ap.add_argument('--delay', type=float, default=0.7, help='delay before each request')
    ap.add_argument('--jitter', type=float, default=0.6)
    ap.add_argument('--retries', type=int, default=5)
    ap.add_argument('--limit', type=int, default=0, help='stop after N entities (0 = all)')
    ap.add_argument('--max-pages', type=int, default=0,
                    help='cap pages per entity (0 = follow the pager)')
    ap.add_argument('--min-count', type=int, default=0,
                    help='skip entities with fewer than N albums')
    ap.add_argument('--deadline-minutes', type=float, default=0.0,
                    help='stop cleanly after N minutes (0 = no deadline)')
    ap.add_argument('--fresh', action='store_true',
                    help='discard any previous output and checkpoint first')
    ap.add_argument('--progress-every', type=int, default=25)
    args = ap.parse_args()

    base = os.path.join(here, '..', 'facet-%s' % args.kind)
    out_path = args.out or base + '.ndjson'
    state_path = args.state or base + '.entities'
    stats_path = args.stats or base + '-stats.ndjson'
    fail_path = args.failures or base + '-failures.log'
    if args.fresh:
        for path in (out_path, state_path, stats_path, fail_path):
            if os.path.exists(path):
                os.remove(path)

    fetch_kw = dict(retries=args.retries, delay=args.delay, jitter=args.jitter)
    started = time.time()

    index_url = album_list.facet_index_url(args.kind)
    html, note = album_list.fetch(index_url, **fetch_kw)
    if html is None:
        raise SystemExit('could not read %s: %s' % (index_url, note))
    entities = album_list.parse_facet_index(html, args.kind)
    advertised = sum(e['count'] or 0 for e in entities)
    print('%s: %d entities, %d album slots advertised'
          % (args.kind, len(entities), advertised), flush=True)
    if not entities:
        raise SystemExit('no %s entities found - did the page layout change?' % args.kind)

    if args.min_count:
        entities = [e for e in entities if (e['count'] or 0) >= args.min_count]
    entities.sort(key=lambda e: (-(e['count'] or 0), e['key']))
    done = album_list.load_state(state_path)
    todo = [e for e in entities if e['key'] not in done]
    if args.limit:
        todo = todo[:args.limit]
    print('%d entities selected, %d already done, %d to sweep'
          % (len(entities), len(done), len(todo)), flush=True)

    out = open(out_path, 'a', encoding='utf-8')
    state = open(state_path, 'a', encoding='utf-8')
    stats = open(stats_path, 'a', encoding='utf-8')
    failures = open(fail_path, 'a', encoding='utf-8')
    pairs = requests = 0
    broken = []
    stopped_early = False

    for i, entity in enumerate(todo, 1):
        if args.deadline_minutes and (time.time() - started) / 60.0 >= args.deadline_minutes:
            print('deadline reached after %d entities; rerun to resume' % (i - 1), flush=True)
            stopped_early = True
            break
        rows, page, last_page, ok = [], 1, 1, True
        while page <= last_page:
            html, note = album_list.fetch(album_list.facet_page_url(entity, page), **fetch_kw)
            requests += 1
            if html is None:
                failures.write('%s\t%d\t%s\n' % (entity['key'], page, note))
                failures.flush()
                ok = album_list.is_permanent(note)
                break
            page_rows, pager, _ = album_list.parse_album_list(html)
            rows.extend(page_rows)
            if page == 1:
                last_page = pager if not args.max_pages else min(pager, args.max_pages)
            page += 1
        seen = set()
        for row in rows:
            if row['slug'] in seen:
                continue
            seen.add(row['slug'])
            album_list.append_json(out, {
                'slug': row['slug'],
                'name': entity['name'],
                'key': entity['key'],
            })
        pairs += len(seen)
        album_list.append_json(stats, {
            'key': entity['key'],
            'name': entity['name'],
            'expected': entity['count'],
            'found': len(seen),
            'pages': last_page,
            'complete': ok,
            'at': album_list.utc_now(),
        })
        if ok:
            album_list.mark_state(state, entity['key'])
        else:
            broken.append(entity['key'])
        if i % args.progress_every == 0 or i == len(todo):
            elapsed = time.time() - started
            rate = i / elapsed if elapsed else 0
            print('%d/%d entities, %d pairs, %d requests, %.2f ent/s, eta %.1fmin'
                  % (i, len(todo), pairs, requests, rate,
                     (len(todo) - i) / rate / 60 if rate else 0), flush=True)

    for handle in (out, state, stats, failures):
        handle.close()
    print('done: %d pairs from %d requests in %.1fmin (%d entities incomplete)'
          % (pairs, requests, (time.time() - started) / 60, len(broken)), flush=True)
    if broken and not stopped_early:
        print('incomplete: %s' % ','.join(broken[:20]), flush=True)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
