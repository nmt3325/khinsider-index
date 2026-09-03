#!/usr/bin/env python3
"""Turn the coverage block of a built library.json into release notes."""
import argparse
import gzip
import json
import sys


def read_json(path):
    opener = gzip.open if path.endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8') as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--library', default='library.json')
    ap.add_argument('--out', default='release-notes.md')
    args = ap.parse_args()

    data = read_json(args.library)
    coverage = data.get('coverage') or {}
    sources = coverage.get('sources') or {}
    fields = coverage.get('fields') or {}
    total = data.get('album_count') or 1

    lines = [
        'Metadata library for [khinsider-subsonic-relay](https://github.com/nmt3325/khinsider-subsonic-relay).',
        '',
        'Point the relay at this release and it stays current:',
        '',
        '```',
        'LIBRARY_URL=https://github.com/nmt3325/khinsider-index/releases/latest/download/library.json',
        '```',
        '',
        '- albums: **%d**' % data.get('album_count', 0),
        '- albums with some metadata: **%d**' % data.get('metadata_count', 0),
        '- index version: `%s`' % data.get('index_version'),
        '- generated: `%s`' % data.get('generated_at'),
        '',
        '### Field coverage',
        '',
        '| field | known | empty | not looked at | known |',
        '| --- | --- | --- | --- | --- |',
    ]
    for name, row in fields.items():
        lines.append('| `%s` | %d | %d | %d | %.1f%% |'
                     % (name, row.get('known', 0), row.get('empty', 0),
                        row.get('unknown', 0), 100.0 * row.get('known', 0) / total))
    lines += [
        '',
        '### Where it came from',
        '',
        '| source | requests it costs | albums reached |',
        '| --- | --- | --- |',
    ]
    album_list = sources.get('album_list') or {}
    pub = sources.get('facet_publisher') or {}
    dev = sources.get('facet_developer') or {}
    page = sources.get('album_page') or {}
    lines.append('| flat album list | %d pages | %d |'
                 % (album_list.get('pages_swept', 0), album_list.get('albums', 0)))
    lines.append('| publisher facets | %d entities | %d |'
                 % (pub.get('entities_swept', 0), pub.get('albums_assigned', 0)))
    lines.append('| developer facets | %d entities | %d |'
                 % (dev.get('entities_swept', 0), dev.get('albums_assigned', 0)))
    lines.append('| individual album pages | 1 per album | %d |' % page.get('albums', 0))

    text = '\n'.join(lines) + '\n'
    with open(args.out, 'w', encoding='utf-8') as f:
        f.write(text)
    sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
