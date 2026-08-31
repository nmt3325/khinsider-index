"""Shared parser for the khinsider album info block.

The album page carries a small info paragraph that looks like this:

    Platforms: 3DS
    Year: 2011
    Published by: Nintendo
    Number of Files: 106
    Total Filesize: 298 MB (MP3), 757 MB (FLAC)
    Date Added: Apr 7th, 2026
    Album type: Gamerip
    Uploaded by: someone

khinsider-subsonic-relay maps the interesting parts onto Subsonic tags:

    Year         -> year / releaseDate / originalReleaseDate
    Published by -> artist / albumArtist  (falls back to Developed by)
    Platforms    -> genre / genres[]
    Album type   -> genre / genres[]
    Date Added   -> created

Keep this module in sync with parse_album_info() in the relay's server.py.
"""
import os
import re
import urllib.parse

from bs4.element import Comment, Tag

AUDIO_EXT_RE = re.compile(r'\.(mp3|flac|ogg|m4a|opus|wma|wav)$', re.I)
SIZE_RE = re.compile(r'^\s*([\d.]+)\s*(B|KB|MB|GB)\s*$', re.I)
DURATION_RE = re.compile(r'^\s*(\d{1,3}):([0-5]\d)(?::([0-5]\d))?\s*$')
NUM_RE = re.compile(r'^\s*(\d{1,4})\.?\s*$')
AUDIO_FORMATS = ('mp3', 'flac', 'ogg', 'm4a', 'opus', 'wma', 'wav')
MONTHS = {'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
          'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12}
INFO_LABELS = {
    'platforms': 'platforms',
    'platform': 'platforms',
    'year': 'year',
    'album type': 'album_type',
    'developed by': 'developers',
    'published by': 'publishers',
    'catalog number': 'catalog_number',
    'date added': 'date_added',
    'number of files': 'file_count',
    'total filesize': 'total_filesize',
    'uploaded by': 'uploaders',
}


def dedupe(seq):
    out, seen = [], set()
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def as_year(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 1000 <= value <= 2999 else None
    m = re.search(r'(?:19|20)\d{2}', str(value))
    return int(m.group(0)) if m else None


def human2bytes(text):
    m = SIZE_RE.match(str(text or ''))
    if not m:
        return None
    mult = {'b': 1, 'kb': 1024, 'mb': 1024 ** 2, 'gb': 1024 ** 3}[m.group(2).lower()]
    return int(float(m.group(1)) * mult)


def duration_seconds(text):
    m = DURATION_RE.match(str(text or ''))
    if not m:
        return None
    a, b, c = m.group(1), m.group(2), m.group(3)
    return int(a) * 60 + int(b) if c is None else int(a) * 3600 + int(b) * 60 + int(c)


def parse_khdate(text):
    """'Apr 7th, 2026' -> '2026-04-07'."""
    if not text:
        return None
    text = str(text)
    m = re.search(r'([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})', text)
    if m:
        mon = MONTHS.get(m.group(1)[:3].lower())
        if mon:
            return '%04d-%02d-%02d' % (int(m.group(3)), mon, int(m.group(2)))
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', text)
    return m.group(0) if m else None


def derive_letter(title):
    """khinsider browse section for an album title ('0-9' or 'A'-'Z')."""
    ch = (title or '').strip()[:1].upper()
    return ch if 'A' <= ch <= 'Z' else '0-9'


def _tags(nodes, name):
    out = []
    for n in nodes:
        if not isinstance(n, Tag):
            continue
        if n.name == name:
            out.append(n)
        out += n.find_all(name)
    return out


def _info_lines(paragraph):
    """Split the info paragraph into logical lines on <br>."""
    lines, cur = [], []
    for node in paragraph.children:
        name = getattr(node, 'name', None)
        if name == 'table':
            break
        if name == 'br':
            lines.append(cur)
            cur = []
            continue
        cur.append(node)
    lines.append(cur)
    return [ln for ln in lines if ln]


def _line_text(nodes):
    parts = []
    for n in nodes:
        if isinstance(n, Comment):
            continue
        parts.append(n.get_text(' ', strip=True) if isinstance(n, Tag) else str(n))
    return re.sub(r'\s+', ' ', ' '.join(parts)).strip()


def parse_album_info(soup):
    """Return {year, publishers, developers, platforms, album_type, ...}."""
    info = {}
    target = None
    for cand in soup.select('#pageContent p[align=left]'):
        low = cand.get_text(' ', strip=True).lower()
        if any(k in low for k in ('year:', 'platforms:', 'platform:', 'album type:',
                                  'published by:', 'developed by:', 'date added:')):
            target = cand
            break
    if target is None:
        return info
    for nodes in _info_lines(target):
        text = _line_text(nodes)
        m = re.match(r'^([A-Za-z][A-Za-z0-9 /]{1,30}?)\s*:\s*(.*)$', text)
        if not m:
            continue
        key = INFO_LABELS.get(m.group(1).strip().lower())
        if not key:
            continue
        rest = m.group(2).strip()
        links = dedupe([a.get_text(' ', strip=True) for a in _tags(nodes, 'a')])
        links = [x for x in links if x and 'change log' not in x.lower()]
        bolds = dedupe([b.get_text(' ', strip=True) for b in _tags(nodes, 'b')])
        if key in ('platforms', 'developers', 'publishers', 'uploaders'):
            info[key] = dedupe(links or [x.strip() for x in rest.split(',') if x.strip()])
        elif key == 'album_type':
            info[key] = (links[0] if links else (bolds[0] if bolds else rest)) or None
        elif key == 'year':
            info[key] = as_year(bolds[0] if bolds else rest)
        elif key == 'date_added':
            info[key] = parse_khdate(bolds[0] if bolds else rest)
        elif key == 'catalog_number':
            info[key] = ((bolds[0] if bolds else rest) or '').strip() or None
        elif key == 'file_count':
            m2 = re.search(r'\d+', (bolds[0] if bolds else rest) or '')
            info[key] = int(m2.group(0)) if m2 else None
        elif key == 'total_filesize':
            info[key] = rest or None
    return info


def _songlist_roles(table):
    """Map <td> index -> role using the songlist header row.

    'Song Name' is followed by an unlabelled track-length column, so every
    header after it is shifted by one cell.
    """
    roles = {}
    hdr = table.find('tr', id='songlist_header') or table.find('tr')
    if not hdr:
        return roles
    cells = [c.get_text(' ', strip=True).strip().lower() for c in hdr.find_all(['th', 'td'])]
    shift = 0
    for i, label in enumerate(cells):
        idx = i + shift
        if label == '#':
            roles[idx] = 'num'
        elif label == 'cd':
            roles[idx] = 'disc'
        elif label in ('song name', 'song title', 'title'):
            roles[idx] = 'title'
            roles[idx + 1] = 'duration'
            shift = 1
        elif label.replace(' ', '') in AUDIO_FORMATS:
            roles[idx] = 'size_' + label.replace(' ', '')
    return roles


def parse_songlist(soup, slug):
    """Return track dicts parsed from table#songlist."""
    table = soup.select_one('table#songlist')
    if table is None:
        return []
    roles = _songlist_roles(table)
    prefix = '/game-soundtracks/album/%s/' % slug
    tracks = []
    for tr in table.find_all('tr'):
        if tr.get('id') in ('songlist_header', 'songlist_footer'):
            continue
        cells = tr.find_all('td')
        if not cells:
            continue
        basename = None
        for a in tr.find_all('a', href=True):
            path = urllib.parse.urlparse(a['href']).path
            if prefix in path and AUDIO_EXT_RE.search(path):
                basename = urllib.parse.unquote(path.rsplit('/', 1)[-1])
                break
        if not basename:
            continue
        t = {'basename': basename, 'num': None, 'disc': None, 'title': None,
             'duration': None, 'sizes': {}}
        for idx, cell in enumerate(cells):
            role = roles.get(idx)
            text = cell.get_text(' ', strip=True)
            if role == 'num':
                m = NUM_RE.match(text)
                t['num'] = int(m.group(1)) if m else None
            elif role == 'disc':
                m = NUM_RE.match(text)
                t['disc'] = int(m.group(1)) if m else None
            elif role == 'title':
                t['title'] = text or None
            elif role == 'duration':
                t['duration'] = duration_seconds(text)
            elif role and role.startswith('size_'):
                size = human2bytes(text)
                if size:
                    t['sizes'][role[5:]] = size
        if not t['title']:
            t['title'] = os.path.splitext(basename)[0]
        t['num'] = t['num'] or len(tracks) + 1
        t['formats'] = [f for f in AUDIO_FORMATS if f in t['sizes']] or ['mp3']
        tracks.append(t)
    return tracks


def album_record(slug, soup):
    """Album page -> one flat metadata record for the index."""
    h2 = soup.select_one('#pageContent h2')
    title = h2.get_text(' ', strip=True) if h2 else None
    if not title or title.lower().startswith('ooops'):
        return None
    info = parse_album_info(soup)
    tracks = parse_songlist(soup, slug)
    covers = dedupe([a['href'] for a in soup.select('div.albumImage a[href]')])
    formats = dedupe([f for t in tracks for f in t['formats']])
    durations = [t['duration'] for t in tracks if t.get('duration')]
    rec = {
        'slug': slug,
        'title': title,
        'letter': derive_letter(title),
        'year': info.get('year'),
        'publishers': info.get('publishers') or [],
        'developers': info.get('developers') or [],
        'platforms': info.get('platforms') or [],
        'album_type': info.get('album_type'),
        'catalog_number': info.get('catalog_number'),
        'date_added': info.get('date_added'),
        'uploaders': info.get('uploaders') or [],
        'total_filesize': info.get('total_filesize'),
        'track_count': len(tracks),
        'duration': sum(durations) if durations else None,
        'formats': formats,
        'cover': covers[0] if covers else None,
    }
    return rec
