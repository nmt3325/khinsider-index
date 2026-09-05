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

from curl_cffi import requests as creq
from lxml import html as lxml_html

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
PERMANENT_NOTES = ('gone',)

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


def _doc(text):
    if hasattr(text, 'xpath'):
        return text
    return lxml_html.fromstring(text)


def _classes(node):
    return set((node.get('class') or '').split())


def _text(node, sep=' '):
    parts = [part.strip() for part in node.xpath('.//text()') if part.strip()]
    return sep.join(parts)


def _table_nodes(doc):
    return doc.xpath('//table[contains(concat(" ", normalize-space(@class), " "), " albumList ")]')


def _table_rows(table):
    return table.xpath('./tr | ./thead/tr | ./tbody/tr | ./tfoot/tr')


def has_album_table(text):
    """True when the page contains an albumList table, even if it has 0 rows."""
    return bool(_table_nodes(_doc(text)))


def _link_list(td):
    values = []
    for a in td.xpath('.//a'):
        value = _text(a)
        if value:
            values.append(value)
    if not values:
        text = _text(td)
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
        for a in td.xpath('.//a[@href]'):
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
        'title': _text(album_td, '') if album_td is not None else '',
    }
    if 'platform' in cells:
        record['platforms'] = _link_list(cells['platform'])
    if 'type' in cells:
        record['album_type'] = _text(cells['type']) or None
    if 'year' in cells:
        record['year'] = _to_year(_text(cells['year']))
    return record


def page_count(doc):
    """Highest ?page=N linked from the pager (1 when there is no pager)."""
    best = 1
    for href in doc.xpath('//a[@href]/@href'):
        if 'page=' not in href:
            continue
        value = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get('page')
        if value and value[0].isdigit():
            best = max(best, int(value[0]))
    return best


def total_count(doc):
    """The 'Found 5765 albums!' headline, when the page has one."""
    for tag in doc.xpath('//h2|//p|//div|//span'):
        text = _text(tag)
        if not text.startswith('Found '):
            continue
        if 'album' not in text.lower():
            continue
        parts = text.split()
        token = parts[1].replace(',', '') if len(parts) > 1 else ''
        if token.isdigit():
            return int(token)
    return None


def parse_album_list(text):
    """html -> (rows, pages, total).

    rows is a list of {slug, title, platforms, album_type, year} dicts, with
    the platform/type/year keys present only when the table has that column.
    A missing table (a facet with no albums, or an error page) yields ([], 1,
    total) so callers can tell it apart from a fetch failure.
    """
    doc = _doc(text)
    tables = _table_nodes(doc)
    pages, total = page_count(doc), total_count(doc)
    if not tables:
        return [], pages, total
    table = tables[0]
    rows, header = [], []
    for tr in _table_rows(table):
        ths = tr.xpath('./th')
        if ths:
            header = [_text(th).lower() for th in ths]
            continue
        tds = tr.xpath('./td')
        if not tds:
            continue
        record = _row_record(tds, header)
        if record is not None:
            rows.append(record)
    return rows, pages, total


def parse_facet_index(text, kind):
    """/album-publishers or /album-developers -> [{key, name, count, href}].

    khinsider only lists entities with 11 or more albums here, so the counts
    also tell us up front how much of the archive a facet sweep can cover.
    """
    prefix = FACET_PREFIX[kind]
    doc = _doc(text)
    out, seen = [], set()
    for a in doc.xpath('//a[@href]'):
        href = a.get('href') or ''
        path = urllib.parse.urlparse(href).path
        if prefix not in path:
            continue
        key = path.split(prefix, 1)[1].strip('/')
        if not key or key in seen:
            continue
        seen.add(key)
        count = None
        tail = a.tail.strip() if a.tail else ''
        if tail.startswith('(') and ')' in tail:
            digits = tail[1:tail.index(')')].replace(',', '')
            if digits.isdigit():
                count = int(digits)
        out.append({
            'key': key,
            'name': _text(a, ''),
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
