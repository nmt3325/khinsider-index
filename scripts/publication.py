#!/usr/bin/env python3
"""Content-aware publication helpers for library and song-index releases."""
import argparse
import gzip
import hashlib
import json
import os
import sys

SONG_MANIFEST_KEYS = ('schema_version', 'content_sha256', 'sha256')


def read_json(path):
    opener = gzip.open if path.endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8') as f:
        return json.load(f)


def stable_json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def normalize_library(data):
    return {'albums': data.get('albums', [])}


def library_content_sha256(path):
    return sha256_bytes(stable_json_bytes(normalize_library(read_json(path))))


def compare_library(current_path, previous_path=None):
    current = library_content_sha256(current_path)
    previous = None
    if previous_path and os.path.exists(previous_path):
        previous = library_content_sha256(previous_path)
    return {
        'mode': 'library',
        'current_content_sha256': current,
        'previous_content_sha256': previous,
        'changed': previous is None or current != previous,
    }


def song_manifest_signature(path):
    data = read_json(path)
    return {key: data.get(key) for key in SONG_MANIFEST_KEYS}


def compare_song_manifest(current_path, previous_path=None):
    current = song_manifest_signature(current_path)
    previous = None
    if previous_path and os.path.exists(previous_path):
        previous = song_manifest_signature(previous_path)
    return {
        'mode': 'song_manifest',
        'current': current,
        'previous': previous,
        'changed': previous is None or current != previous,
    }


def write_github_output(path, result):
    if not path:
        return
    with open(path, 'a', encoding='utf-8') as fh:
        if result['mode'] == 'library':
            fh.write('changed=%s\n' % ('true' if result['changed'] else 'false'))
            fh.write('content_sha256=%s\n' % result['current_content_sha256'])
            fh.write('previous_content_sha256=%s\n' % (result['previous_content_sha256'] or ''))
        else:
            fh.write('changed=%s\n' % ('true' if result['changed'] else 'false'))
            for key in SONG_MANIFEST_KEYS:
                fh.write('%s=%s\n' % (key, result['current'].get(key) or ''))


def build_arg_parser():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='command', required=True)

    lib = sub.add_parser('library-changed')
    lib.add_argument('--current', required=True)
    lib.add_argument('--previous')
    lib.add_argument('--github-output')

    song = sub.add_parser('song-manifest-changed')
    song.add_argument('--current', required=True)
    song.add_argument('--previous')
    song.add_argument('--github-output')
    return ap


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    if args.command == 'library-changed':
        result = compare_library(args.current, args.previous)
    else:
        result = compare_song_manifest(args.current, args.previous)
    write_github_output(args.github_output, result)
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write('\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
