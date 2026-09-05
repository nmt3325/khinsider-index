#!/usr/bin/env python3
"""Discover recently listed albums from the khinsider homepage.

The homepage groups latest soundtracks into per-day album tables. This script
tracks those discovery events separately from the album metadata crawl so a
seen homepage event is only acknowledged after a later successful metadata
crawl for the same slug.
"""
import argparse
import datetime as dt
import json
import os
import sys
import time

from lxml import html as lxml_html

import album_list
import live_data

STATE_VERSION = 1


MONTHS = {
    'january': 1,
    'february': 2,
    'march': 3,
    'april': 4,
    'may': 5,
    'june': 6,
    'july': 7,
    'august': 8,
    'september': 9,
    'october': 10,
    'november': 11,
    'december': 12,
}


def recent_page_url(page=1):
    return album_list.page_url(album_list.BASE + '/', page)


def atomic_write_text(path, text):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(text)
    os.replace(tmp, path)



def atomic_write_json(path, data):
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + '\n')



def load_state(path):
    base = {'version': STATE_VERSION, 'watermark': None, 'seen': {}, 'pending': {}}
    if not os.path.exists(path):
        return base
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return base
    if not isinstance(data, dict):
        return base
    base.update({
        'version': data.get('version', STATE_VERSION),
        'watermark': data.get('watermark'),
        'seen': data.get('seen') or {},
        'pending': data.get('pending') or {},
        'cursor': data.get('cursor'),
    })
    return base



def save_state(path, state):
    atomic_write_json(path, {
        'version': STATE_VERSION,
        'watermark': state.get('watermark'),
        'seen': state.get('seen', {}),
        'pending': state.get('pending', {}),
        'cursor': state.get('cursor'),
    })



def load_recent_rows(path):
    rows = {}
    if not os.path.exists(path):
        return rows
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except Exception:
                continue
            slug = record.get('slug')
            if not slug:
                continue
            key = album_list.norm_slug(slug)
            old = rows.get(key)
            if old is None:
                rows[key] = record
                continue
            newer = (record.get('listed_at') or '', record.get('discovered_at') or '')
            older = (old.get('listed_at') or '', old.get('discovered_at') or '')
            if newer >= older:
                rows[key] = record
    return rows



def save_recent_rows(path, rows):
    ordered = sorted(rows.values(), key=lambda r: ((r.get('listed_at') or ''), (r.get('slug') or '')))
    text = ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in ordered)
    atomic_write_text(path, text)



def load_meta_crawled(path):
    return {album_list.norm_slug(slug): record['crawled_at']
            for slug, record in live_data.latest_records(path).items()}


def event_key(slug, listed_at):
    return '%s\t%s' % (listed_at, album_list.norm_slug(slug))



def queue_slugs(state):
    ordered = sorted(
        state.get('pending', {}).values(),
        key=lambda item: (item.get('discovered_at') or '', item.get('listed_at') or '', item.get('slug') or ''),
    )
    out, seen = [], set()
    for item in ordered:
        slug = item.get('slug')
        if slug and slug not in seen:
            seen.add(slug)
            out.append(slug)
    return out



def save_queue(path, state):
    atomic_write_text(path, ''.join('%s\n' % slug for slug in queue_slugs(state)))



def parse_heading_date(text):
    text = ' '.join((text or '').split()).strip()
    if not text:
        return None
    parts = text.replace(',', '').split()
    if len(parts) != 3:
        return None
    month = MONTHS.get(parts[0].lower())
    if not month:
        return None
    day = ''.join(ch for ch in parts[1] if ch.isdigit())
    if not (day and day.isdigit() and parts[2].isdigit()):
        return None
    return '%04d-%02d-%02d' % (int(parts[2]), month, int(day))



def parse_recent_sections(text):
    doc = lxml_html.fromstring(text)
    roots = doc.xpath('//*[@id="pageContent"]') or [doc]
    root = roots[0]
    headings = root.xpath('.//h3[contains(concat(" ", normalize-space(@class), " "), " latestSoundtrackHeading ")]')
    out = []
    for heading in headings:
        listed_at = parse_heading_date(' '.join(heading.xpath('.//text()')))
        if not listed_at:
            continue
        node = heading.getnext()
        table_html = None
        while node is not None:
            classes = set((node.get('class') or '').split())
            if node.tag == 'h3' and 'latestSoundtrackHeading' in classes:
                break
            if 'pagination' in classes:
                break
            tables = []
            if node.tag == 'table' and 'albumList' in classes:
                tables = [node]
            else:
                tables = node.xpath('.//table[contains(concat(" ", normalize-space(@class), " "), " albumList ")]')
            if tables:
                table_html = lxml_html.tostring(tables[0], encoding='unicode')
                break
            node = node.getnext()
        if table_html is None:
            continue
        rows, _, _ = album_list.parse_album_list(table_html)
        out.append((listed_at, rows))
    return out



def subtract_days(iso_date, days):
    when = dt.date.fromisoformat(iso_date)
    return (when - dt.timedelta(days=days)).isoformat()



def acknowledge_pending(state, metadata_path):
    crawled = load_meta_crawled(metadata_path)
    if not crawled:
        return state
    seen = dict(state.get('seen') or {})
    pending = {}
    for key, item in (state.get('pending') or {}).items():
        slug = item.get('slug')
        discovered_at = item.get('discovered_at') or ''
        crawled_at = crawled.get(album_list.norm_slug(slug or ''))
        if crawled_at and crawled_at > discovered_at:
            seen[key] = {
                'slug': slug,
                'listed_at': item.get('listed_at'),
                'discovered_at': discovered_at,
                'crawled_at': crawled_at,
            }
        else:
            pending[key] = item
    state['seen'] = seen
    state['pending'] = pending
    return state



def upsert_recent_row(rows, row, listed_at, discovered_at):
    record = dict(row)
    record['listed_at'] = listed_at
    record['discovered_at'] = discovered_at
    key = album_list.norm_slug(record['slug'])
    old = rows.get(key)
    if old is None:
        rows[key] = record
        return
    newer = (record.get('listed_at') or '', record.get('discovered_at') or '')
    older = (old.get('listed_at') or '', old.get('discovered_at') or '')
    if newer >= older:
        rows[key] = record



def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--state', default=os.path.join(here, '..', 'recent-state.json'))
    ap.add_argument('--out', default=os.path.join(here, '..', 'recent-albums.ndjson'))
    ap.add_argument('--queue', default=os.path.join(here, '..', 'recent-slugs.txt'))
    ap.add_argument('--metadata', default=os.path.join(here, '..', 'album-meta.ndjson'))
    ap.add_argument('--overlap-days', type=int, default=3)
    ap.add_argument('--max-pages', type=int, default=10)
    ap.add_argument('--deadline-minutes', type=float, default=5.0)
    ap.add_argument('--ack-only', action='store_true')
    args = ap.parse_args()

    state = acknowledge_pending(load_state(args.state), args.metadata)
    rows = load_recent_rows(args.out)

    if args.ack_only:
        save_state(args.state, state)
        save_queue(args.queue, state)
        return 0

    cutoff = None
    if state.get('watermark'):
        cutoff = subtract_days(state['watermark'], max(0, args.overlap_days))

    started = time.time()
    cursor = state.get('cursor') or {}
    page = cursor.get('page', 1)
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise live_data.DataError('invalid recent-discovery cursor')
    if cursor:
        cutoff = cursor.get('cutoff')
    initial_page = page
    last_page = page
    newest_processed = cursor.get('newest')
    complete = True
    stop_reason = None
    reached_cutoff = False

    while page <= last_page:
        if args.max_pages and page - initial_page >= args.max_pages:
            complete = False
            stop_reason = 'max-pages'
            break
        if args.deadline_minutes and (time.time() - started) / 60.0 >= args.deadline_minutes:
            complete = False
            stop_reason = 'deadline'
            break
        html, note = album_list.fetch(recent_page_url(page))
        if html is None:
            complete = False
            stop_reason = str(note)
            break
        doc = lxml_html.fromstring(html)
        last_page = album_list.page_count(doc)
        sections = parse_recent_sections(html)
        if not sections:
            complete = False
            stop_reason = 'no recent sections'
            break
        for listed_at, page_rows in sections:
            if cutoff and listed_at < cutoff:
                reached_cutoff = True
                break
            newest_processed = max(newest_processed or listed_at, listed_at)
            discovered_at = album_list.utc_now()
            for row in page_rows:
                key = event_key(row['slug'], listed_at)
                if key in state['seen']:
                    continue
                item = state['pending'].get(key)
                if item is None:
                    item = {
                        'slug': row['slug'],
                        'listed_at': listed_at,
                        'discovered_at': discovered_at,
                        'row': dict(row),
                    }
                    state['pending'][key] = item
                else:
                    item['row'] = dict(row)
                upsert_recent_row(rows, item['row'], listed_at, item['discovered_at'])
            if reached_cutoff:
                break
        if reached_cutoff:
            break
        page += 1

    if complete and newest_processed:
        state['watermark'] = max(state.get('watermark') or newest_processed, newest_processed)

    state['cursor'] = None if complete else {
        'page': page, 'cutoff': cutoff, 'newest': newest_processed,
    }
    save_recent_rows(args.out, rows)
    save_state(args.state, state)
    save_queue(args.queue, state)

    if complete:
        return 0
    print('recent discovery stopped: %s' % (stop_reason or 'incomplete'), flush=True)
    return 1


if __name__ == '__main__':
    sys.exit(main())
