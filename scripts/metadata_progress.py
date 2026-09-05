#!/usr/bin/env python3
"""Measure individual-page coverage, independently of which fields are populated.

A publisher/year obtained from a listing is NOT evidence of an album-page
visit. Only a record in album-meta.ndjson counts as fetched. Confirmed HTTP 404
results are reported separately; malformed/no-content pages stay pending.
Slug comparisons deliberately match crawl_album_meta.py's resume semantics.
"""
import argparse
import json
from pathlib import Path

# Legacy HTTP-200 parse failures must re-enter the retry queue.
PERMANENT_NOTES = {'gone'}


def metadata_slugs(path):
    result = set()
    path = Path(path)
    if not path.exists():
        return result
    with path.open(encoding='utf-8') as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                slug = record['slug']
                if not isinstance(slug, str) or not slug:
                    raise ValueError('missing slug')
            except (ValueError, KeyError, TypeError) as exc:
                raise ValueError(f'{path}:{number}: invalid metadata record') from exc
            result.add(slug)
    return result


def unavailable_slugs(path):
    result = set()
    path = Path(path)
    if path.exists():
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                parts = line.rstrip('\n').split('\t')
                if len(parts) >= 2 and parts[1].strip() in PERMANENT_NOTES:
                    result.add(parts[0].strip())
    return result


def measure(library_path, meta_path, failures_path):
    library = json.loads(Path(library_path).read_text(encoding='utf-8'))
    albums = library.get('albums')
    if not isinstance(albums, list) or not albums:
        raise ValueError('library must contain a non-empty albums list')
    catalog = set()
    for album in albums:
        slug = album.get('slug') if isinstance(album, dict) else None
        if not isinstance(slug, str) or not slug:
            raise ValueError('library contains an album without a valid slug')
        catalog.add(slug)
    fetched = catalog & metadata_slugs(meta_path)
    unavailable = (catalog & unavailable_slugs(failures_path)) - fetched
    pending = catalog - fetched - unavailable
    published = library.get('coverage', {}).get('sources', {}).get('album_page', {}).get('albums', 0)
    return {
        'total': len(catalog),
        'fetched': len(fetched),
        'unavailable': len(unavailable),
        'pending': len(pending),
        'fetched_percent': round(100 * len(fetched) / len(catalog), 2),
        'published_fetched': int(published or 0),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', default='library.json')
    parser.add_argument('--meta', default='album-meta.ndjson')
    parser.add_argument('--failures', default='album-meta-failures.log')
    parser.add_argument('--summary', default='')
    parser.add_argument('--github-output', default='')
    parser.add_argument('--markdown', default='')
    args = parser.parse_args()
    result = measure(args.library, args.meta, args.failures)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.summary:
        Path(args.summary).write_text(text + '\n', encoding='utf-8')
    if args.github_output:
        with open(args.github_output, 'a', encoding='utf-8') as stream:
            for key, value in result.items():
                stream.write(f'{key}={value}\n')
    if args.markdown:
        with open(args.markdown, 'a', encoding='utf-8') as stream:
            stream.write('### Full album-page metadata coverage\n\n')
            for key in ('total', 'fetched', 'unavailable', 'pending', 'fetched_percent'):
                stream.write(f'- {key}: **{result[key]}**\n')
            stream.write('\nFetched means an individual page was parsed; legitimate empty fields remain empty. '
                         'Unavailable means a confirmed 404, not a successful fetch. '
                         'Transient failures remain pending for retry.\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
