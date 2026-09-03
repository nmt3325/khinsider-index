import os, sys, time, glob
import requests

AK = os.environ['ARCHIVE_ORG_S3_ACCESS']
SK = os.environ['ARCHIVE_ORG_S3_SECRET']
H = {'Authorization': 'LOW %s:%s' % (AK, SK), 'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}

QUEUE_GLOB = os.environ.get('QUEUE_GLOB', 'work/wayback_queue*.txt')
DONE = os.environ.get('DONE_FILE', 'work/wayback_done.txt')
FAIL = os.environ.get('FAIL_FILE', 'work/wayback_failed.txt')
INTERVAL = float(os.environ.get('SPN2_INTERVAL', '3'))
MAX_SECONDS = int(os.environ.get('SPN2_MAX_SECONDS', '0') or 0)
deadline = time.time() + MAX_SECONDS if MAX_SECONDS > 0 else None

done = set()
if os.path.exists(DONE):
    with open(DONE) as f:
        done = set(x.rstrip('\n') for x in f)
print('resuming; already done:', len(done), flush=True)

s = requests.Session()
dn = open(DONE, 'a', buffering=1)
fl = open(FAIL, 'a', buffering=1)

def submit(u):
    for attempt in range(6):
        if deadline and time.time() > deadline:
            return None
        try:
            r = s.post('https://web.archive.org/save', headers=H, data={'url': u}, timeout=60)
            if r.status_code == 200 and '"job_id"' in r.text:
                return True
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(min(600, 30 * (attempt + 1)))
                continue
            print('no job_id:', r.status_code, r.text[:120].replace('\n', ' '), flush=True)
            return False
        except Exception as e:
            print('net err', type(e).__name__, flush=True)
            time.sleep(20)
    return False

n = 0
fails = 0
stop = False
while True:
    progressed = False
    for qf in sorted(glob.glob(QUEUE_GLOB)):
        if stop:
            break
        with open(qf) as f:
            for line in f:
                if deadline and time.time() > deadline:
                    print('SPN2 time box reached', flush=True)
                    stop = True
                    break
                u = line.strip()
                if not u or u in done:
                    continue
                progressed = True
                r = submit(u)
                if r is True:
                    dn.write(u + '\n'); done.add(u); fails = 0
                elif r is False:
                    fl.write(u + '\n'); fails += 1
                else:
                    stop = True
                    break
                n += 1
                if n % 25 == 0:
                    print('submitted', n, '| total done', len(done), flush=True)
                if fails >= 15:
                    print('15 consecutive failures; sleeping 20 min', flush=True)
                    time.sleep(1200); fails = 0
                time.sleep(INTERVAL)
    if stop:
        break
    if not progressed:
        print('queue exhausted; sleeping 30 min', flush=True)
        time.sleep(1800)
print('SPN2 EXIT; done total', len(done), flush=True)
