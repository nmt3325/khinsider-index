#!/usr/bin/env python3
"""List the albums that still need an individual album-page visit.

The listing and facet sweeps fill in year, platform, album type and
publisher for most of the archive with ~1.9k requests. Whatever they cannot
reach - publishers with fewer than 11 albums, albums with no publisher at
all, and fields that only exist on the album page such as date added or
track count - is the residual, and only the residual is worth spending
per-album requests on.

In library.json a missing key means "never looked", while null or [] means
"looked, there is nothing there". This script emits exactly the albums whose
--field key is missing, newest first (albums absent from --previous) and then
in a deterministic hash order, so repeated capped runs sample the rest of the
archive evenly instead of grinding through the same alphabetical prefix.

Examples:
    python residual_slugs.py --out residual.txt
    python residual_slugs.py --field date_added --limit 5000
    python residual_slugs.py --previous previous-library.json --limit 5000
"""
import argparse
import gzip
import hashlib
import json
import os
import sys


def read_json(path):
    opener = gzip.open if path.endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8') as f:
        return json.load(f)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--library', default=os.path.join(here, '..', 'library.json'))
    ap.add_argument('--previous', default='',
                    help='an older library.json; albums missing from it go first')
    ap.add_argument('--field', default='publishers',
                    help='emit albums whose key is absent (default publishers)')
    ap.add_argument('--limit', type=int, default=0, help='cap the list (0 = all)')
    ap.add_argument('--out', default='', help='write here instead of stdout')
    args = ap.parse_args()

    albums = read_json(args.library).get('albums') or []
    known = set()
    if args.previous and os.path.exists(args.previous):
        known = {a.get('slug') for a in (read_json(args.previous).get('albums') or [])}

    residual = [a for a in albums if args.field not in a]
    residual.sort(key=lambda a: (
        a.get('slug') in known,
        hashlib.md5((a.get('slug') or '').encode('utf-8')).hexdigest(),
    ))
    fresh = sum(1 for a in residual if a.get('slug') not in known)
    if args.limit:
        residual = residual[:args.limit]

    lines = ''.join((a.get('slug') or '') + '\n' for a in residual if a.get('slug'))
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as f:
            f.write(lines)
    else:
        sys.stdout.write(lines)
    print('%d of %d albums have no %s (%d new since --previous); emitted %d'
          % (len(residual), len(albums), args.field, fresh, lines.count('\n')),
          file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
