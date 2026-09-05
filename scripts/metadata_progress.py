#!/usr/bin/env python3
"""Measure complete live album/track coverage, never published/legacy summaries."""
import argparse
import json

import live_data


def measure(catalogue, metadata, recent_state=None):
    return live_data.inspect(catalogue, metadata, recent_state)[-1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalogue', default='work/live-v2/catalogue.json')
    parser.add_argument('--meta', default='work/live-v2/album-meta.ndjson')
    parser.add_argument('--recent-state', default=None)
    parser.add_argument('--summary', default='')
    args = parser.parse_args(argv)
    result = measure(args.catalogue, args.meta, args.recent_state)
    if args.summary:
        live_data.atomic_json(args.summary, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
