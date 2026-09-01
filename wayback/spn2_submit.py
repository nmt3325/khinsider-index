# SPN2 (authenticated Save Page Now) submitter for the song URL queue.
# Requires: ARCHIVE_ORG_S3_ACCESS / ARCHIVE_ORG_S3_SECRET (from https://archive.org/account/s3.php)
# NOTE: archive.org edge-blocks GitHub-hosted runner IPs even with auth; run from
# Google Colab or a residential connection. Verified working from Colab 2026-09-01:
# album page and track page captures confirmed via CDX (status 200).
# Track URLs serve HTML pages that embed the current direct MP3 link
# (jetta.vgmtreasurechest.com), so track-page captures preserve the file pointer.
import os, time
import requests

AK = os.environ["ARCHIVE_ORG_S3_ACCESS"]
SK = os.environ["ARCHIVE_ORG_S3_SECRET"]
H = {"Authorization": "LOW %s:%s" % (AK, SK), "User-Agent": "Mozilla/5.0", "Accept": "application/json"}

Q = os.environ.get("QUEUE", "work/wayback_queue.txt")
DONE = os.environ.get("DONE_FILE", "work/wayback_done.txt")
INTERVAL = float(os.environ.get("SPN2_INTERVAL", "3"))

done = set()
if os.path.exists(DONE):
    with open(DONE) as f:
        done = set(x.rstrip("\n") for x in f)
print("resuming; already done:", len(done), flush=True)

s = requests.Session()
dn = open(DONE, "a", buffering=1)

def submit(u):
    for attempt in range(6):
        try:
            r = s.post("https://web.archive.org/save", headers=H, data={"url": u}, timeout=60)
            if r.status_code == 200:
                return True
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(min(600, 30 * (attempt + 1)))
                continue
            print("perm fail", r.status_code, u[:110], flush=True)
            return False
        except Exception as e:
            print("net err", type(e).__name__, flush=True)
            time.sleep(20)
    return False

n = 0
fails = 0
while True:
    progressed = False
    with open(Q) as f:
        for line in f:
            u = line.strip()
            if not u or u in done:
                continue
            progressed = True
            if submit(u):
                dn.write(u + "\n"); done.add(u); fails = 0
            else:
                fails += 1
            n += 1
            if n % 25 == 0:
                print("submitted", n, "| total done", len(done), flush=True)
            if fails >= 15:
                print("15 consecutive failures; sleeping 20 min", flush=True)
                time.sleep(1200); fails = 0
                break
            time.sleep(INTERVAL)
    if not progressed:
        print("queue exhausted; sleeping 30 min", flush=True)
        time.sleep(1800)
