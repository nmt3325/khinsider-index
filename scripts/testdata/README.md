# Frozen parser fixtures

Captured from public KHInsider HTML on 2026-09-05:
- https://downloads.khinsider.com/game-soundtracks?albumListSize=large
- https://downloads.khinsider.com/search?search=&type=album&sort=timestamp
- https://downloads.khinsider.com/game-soundtracks/album/seed-2016

Listing excerpts retain representative rows, original headers, pagination and
totals; page chrome and images are omitted. Expected rows came from the
pre-change parser. Full 500-row comparisons also passed before these portable
fixtures were trimmed. The album excerpt retains its main content and player
data. Tests parse script text; they do not execute JavaScript or fetch audio.
