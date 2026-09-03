import os, time, glob
import requests

DONE_FILE = 'work/wayback_done.txt'
FAIL_FILE = 'work/wayback_failed.txt'
INTERVAL = 5.0

done = set()
if os.path.exists(DONE_FILE):
    with open(DONE_FILE) as f:
        done = set(x.rstrip('\n') for x in f)

s = requests.Session()
s.headers.update({'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'})
# Optional: route via proxy (HTTPS_PROXY env var is honored automatically by requests)
# Optional: authenticated SPN2 (higher quota, per-account): set ARCHIVE_ORG_S3_ACCESS / ARCHIVE_ORG_S3_SECRET
S3_ACCESS = os.environ.get('ARCHIVE_ORG_S3_ACCESS')
S3_SECRET = os.environ.get('ARCHIVE_ORG_S3_SECRET')
USE_SPN2 = bool(S3_ACCESS and S3_SECRET)
if USE_SPN2:
    s.headers.update({'Authorization': f'LOW {S3_ACCESS}:{S3_SECRET}'})
    print('using authenticated SPN2 API', flush=True)
dn = open(DONE_FILE, 'a', buffering=1)
fl = open(FAIL_FILE, 'a', buffering=1)

def submit(url):
    for attempt in range(6):
        try:
            if USE_SPN2:
                r = s.post('https://web.archive.org/save', data={'url': url}, timeout=90)
            else:
                r = s.get('https://web.archive.org/save/' + url, timeout=90, allow_redirects=True)
            if r.status_code == 200:
                return True
            if r.status_code >= 500 or r.status_code == 429:
                time.sleep(min(600, 45 * (attempt + 1)))
                continue
            return False
        except Exception:
            time.sleep(30)
    return False

def iter_pending():
    for qf in sorted(glob.glob('work/wayback_queue*.txt')):
        with open(qf) as f:
            for line in f:
                u = line.strip()
                if u and u not in done:
                    yield u

print(f'starting; already done: {len(done)}', flush=True)
n = 0
consec_fail = 0
while True:
    attempted = False
    for u in iter_pending():
        attempted = True
        if submit(u):
            dn.write(u + '\n'); done.add(u); consec_fail = 0
        else:
            fl.write(u + '\n'); consec_fail += 1
        n += 1
        if n % 10 == 0:
            print(f'attempted {n}, saved total {len(done)}, consec_fail {consec_fail}', flush=True)
        time.sleep(INTERVAL)
        if consec_fail >= 10:
            print('10 consecutive failures; sleeping 30 min then restarting scan', flush=True)
            time.sleep(1800); consec_fail = 0
            break
    if not attempted:
        print('queue exhausted; sleeping 30 min', flush=True)
        time.sleep(1800)
