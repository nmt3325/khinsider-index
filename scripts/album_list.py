#!/usr/bin/env python3
"""Shared parser and fetcher for khinsider album-list pages.

Every listing page on khinsider - the flat browse at /game-soundtracks, and
the per-publisher, per-developer, per-platform, per-year and per-type facets -
renders the same table:

    <table class="albumList">
      <tr><th></th><th>Album</th><th>Platform</th><th>Type</th><th>Year</th></tr>
      <tr><td class="albumIcon"><a href="/game-soundtracks/album/SLUG"></a></td>
          <td><a href="/game-soundtracks/album/SLUG">Title</a></td>
          <td><a>PS4</a>,<a>PS5</a></td>
          <td>Soundtrack</td>
          <td>2021</td></tr>
    </table>

That is the whole point of this module: platform, album type and release year
for the entire archive are readable from ~210 listing pages of 500 rows each
instead of 104k individual album pages, and publisher/developer come from the
~1.6k facet pages. One HTTP request buys 500 albums instead of one.

parse_album_list() is the single parser used by crawl_index_pages.py,
crawl_facets.py and any future facet sweep.
"""
import json
import os
import random
import threading
import time
import urllib.parse

from bs4 import BeautifulSoup
from curl_cffi import requests as creq

BASE = 'https://downloads.khinsider.com'
ALBUM_PREFIX = '/game-soundtracks/album/'
LIST_PATH = '/game-soundtracks'

FACET_INDEX_PATH = {
    'publisher': '/album-publishers',
    'developer': '/album-developers',
}
FACET_PREFIX = {
    'publisher': '/game-soundtracks/publisher/',
    'developer': '/game-soundtracks/developer/',
}

CF_MARKERS = ('attention required', 'just a moment', 'cf-browser-verification',
              'enable javascript and cookies to continue')

# notes that mean "never ask again" rather than "try again later"
PERMANENT_NOTES = ('gone', 'no-list-table')

_local = threading.local()


def session():
    if not hasattr(_local, 'sess'):
        _local.sess = creq.Session(impersonate='chrome')
    return _local.sess


def safe_url(url):
    """Percent-encode non-ASCII path/query bytes, leaving existing escapes."""
    return urllib.parse.quote(url, safe=":/?&=%#~+,!$'()*;[]@-_.")


def is_permanent(note):
    return note in PERMANENT_NOTES


def fetch(url, retries=5, delay=0.6, jitter=0.6, timeout=45):
    """Fetch a listing page.

    Returns (html, note). html is None on failure; note is 'ok', 'gone'
    (permanent) or a transient reason such as 'cloudflare' / 'http 503'.
    Cloudflare challenges and 5xx are retried with exponential backoff.
    """
    target = safe_url(url)
    note = 'unknown'
    for attempt in range(max(1, retries)):
        time.sleep(delay + random.random() * jitter)
        try:
            r = session().get(target, timeout=timeout)
        except Exception as exc:
            note = 'exception: %s' % exc
        else:
            if r.status_code == 404:
                return None, 'gone'
            if r.status_code == 200:
                if any(m in r.text[:4000].lower() for m in CF_MARKERS):
                    note = 'cloudflare'
                else:
                    return r.text, 'ok'
            else:
                note = 'http %s' % r.status_code
        if attempt < retries - 1:
            time.sleep(min(60.0, (2 ** attempt) * 2.0) + random.random() * 3.0)
    return None, note


def slug_from_href(href):
    """/game-soundtracks/album/foo -> 'foo' (raw path segment, as in index.json)."""
    if not href:
        return None
    path = urllib.parse.urlparse(href).path
    if ALBUM_PREFIX not in path:
        return None
    return path.split(ALBUM_PREFIX, 1)[1].strip('/') or None


def norm_slug(slug):
    """Key used when joining facet rows to index.json entries.

    index.json stores raw path segments, which are percent-encoded for
    non-ASCII albums in some vintages and not in others. Unquoting both sides
    makes the join independent of that.
    """
    if not slug:
        return slug
    try:
        return urllib.parse.unquote(slug)
    except Exception:
        return slug


def _link_list(td):
    values = [a.get_text(strip=True) for a in td.find_all('a')]
    values = [v for v in values if v]
    if not values:
        text = td.get_text(' ', strip=True)
        values = [part.strip() for part in text.split(',') if part.strip()]
    out, seen = [], set()
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _to_year(text):
    text = (text or '').strip()
    if not text.isdigit():
        return None
    value = int(text)
    return value if value > 0 else None


def _row_record(tds, header):
    cells = {}
    for i, td in enumerate(tds):
        name = header[i] if i < len(header) else ''
        if name not in cells:
            cells[name] = td
    slug = None
    for td in tds:
        for a in td.find_all('a'):
            slug = slug_from_href(a.get('href'))
            if slug:
                break
        if slug:
            break
    if not slug:
        return None
    album_td = cells.get('album')
    record = {
        'slug': slug,
        'title': album_td.get_text(strip=True) if album_td is not None else '',
    }
    if 'platform' in cells:
        record['platforms'] = _link_list(cells['platform'])
    if 'type' in cells:
        record['album_type'] = cells['type'].get_text(strip=True) or None
    if 'year' in cells:
        record['year'] = _to_year(cells['year'].get_text(strip=True))
    return record


def page_count(soup):
    """Highest ?page=N linked from the pager (1 when there is no pager)."""
    best = 1
    for a in soup.find_all('a'):
        href = a.get('href') or ''
        if 'page=' not in href:
            continue
        value = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get('page')
        if value and value[0].isdigit():
            best = max(best, int(value[0]))
    return best


def total_count(soup):
    """The 'Found 5765 albums!' headline, when the page has one."""
    for tag in soup.find_all(['h2', 'p', 'div', 'span']):
        text = tag.get_text(' ', strip=True)
        if not text.startswith('Found ') or 'album' not in text:
            continue
        token = text.split()[1].replace(',', '') if len(text.split()) > 1 else ''
        if token.isdigit():
            return int(token)
    return None


def parse_album_list(html):
    """html -> (rows, pages, total).

    rows is a list of {slug, title, platforms, album_type, year} dicts, with
    the platform/type/year keys present only when the table has that column.
    A missing table (a facet with no albums, or an error page) yields ([], 1,
    total) so callers can tell it apart from a fetch failure.
    """
    soup = BeautifulSoup(html, 'lxml')
    table = soup.select_one('table.albumList')
    pages, total = page_count(soup), total_count(soup)
    if table is None:
        return [], pages, total
    rows, header = [], []
    for tr in table.find_all('tr'):
        ths = tr.find_all('th')
        if ths:
            header = [th.get_text(strip=True).lower() for th in ths]
            continue
        tds = tr.find_all('td')
        if not tds:
            continue
        record = _row_record(tds, header)
        if record is not None:
            rows.append(record)
    return rows, pages, total


def parse_facet_index(html, kind):
    """/album-publishers or /album-developers -> [{key, name, count, href}].

    khinsider only lists entities with 11 or more albums here, so the counts
    also tell us up front how much of the archive a facet sweep can cover.
    """
    prefix = FACET_PREFIX[kind]
    soup = BeautifulSoup(html, 'lxml')
    out, seen = [], set()
    for a in soup.find_all('a'):
        href = a.get('href') or ''
        path = urllib.parse.urlparse(href).path
        if prefix not in path:
            continue
        key = path.split(prefix, 1)[1].strip('/')
        if not key or key in seen:
            continue
        seen.add(key)
        count = None
        tail = a.next_sibling
        if isinstance(tail, str):
            token = tail.strip()
            if token.startswith('(') and ')' in token:
                digits = token[1:token.index(')')].replace(',', '')
                if digits.isdigit():
                    count = int(digits)
        out.append({
            'key': key,
            'name': a.get_text(strip=True),
            'count': count,
            'href': href,
        })
    return out


def page_url(base_url, page):
    """Add or replace ?page=N on a listing URL."""
    if page <= 1:
        return base_url
    joiner = '&' if '?' in base_url else '?'
    return '%s%spage=%d' % (base_url, joiner, page)


def list_page_url(page=1, path=LIST_PATH):
    return page_url(BASE + path, page)


def facet_page_url(entry, page=1):
    return page_url(urllib.parse.urljoin(BASE, entry['href']), page)


def facet_index_url(kind):
    return BASE + FACET_INDEX_PATH[kind]


def append_json(handle, record):
    handle.write(json.dumps(record, ensure_ascii=False) + '\n')
    handle.flush()


def load_state(path):
    """Read a checkpoint file of completed unit keys, one per line."""
    done = set()
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            for line in f:
                key = line.strip()
                if key:
                    done.add(key)
    return done


def mark_state(handle, key):
    handle.write('%s\n' % key)
    handle.flush()


def utc_now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
