# khinsider-index
A static file containing a complete index of the content on downloads.khinsider.com

This repo runs the same index functionality built into my [khinsider](https://github.com/marcus-crane/khinsider/tree/v2) tool, just on a timer and uploaded as a file.

While any user can generate their own index if they choose, by default they'll download a tar gzipped version of the index that is generated and released against this repo, whenever any changes are detected.

Shout outs to [trackiam](https://github.com/glassechidna/trackiam) as inspiration. I've taken the idea a little further with automatic releasing of the files being watched though so feel free to poke around this repo too. I ran into a few gotchas that I've briefly documented.

I intend to write a fuller README in future

## Live index rebuild (2026-09, this fork)

The original indexer (`khinsider --debug index`) no longer works:
downloads.khinsider.com is now behind Cloudflare bot protection which
hard-blocks datacenter clients and headless browsers.

`scripts/crawl_live.py` rebuilds the index from the **live site** using
[curl_cffi](https://github.com/lexiforest/curl_cffi) with a Chrome TLS (JA3)
fingerprint, which passes the protection without a browser:

    pip install curl_cffi
    python3 scripts/crawl_live.py

Result of the 2026-09-01 rebuild: **104,431 albums** (vs 49,536 in
v0.0.2888 from 2024-01-30; +56,052 added / -1,157 removed). See the
Releases page for `index.tar.gz` and the diff lists.
