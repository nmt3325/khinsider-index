import glob
import os
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

import requests

AK = os.environ["ARCHIVE_ORG_S3_ACCESS"]
SK = os.environ["ARCHIVE_ORG_S3_SECRET"]
H = {
    "Authorization": "LOW %s:%s" % (AK, SK),
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}

QUEUE_GLOB = os.environ.get("QUEUE_GLOB", "work/wayback_queue*.txt")
DONE = os.environ.get("DONE_FILE", "work/wayback_done.txt")
FAIL = os.environ.get("FAIL_FILE", "work/wayback_failed.txt")
INTERVAL = float(os.environ.get("SPN2_INTERVAL", "3"))
STATUS_INTERVAL = float(os.environ.get("SPN2_STATUS_INTERVAL", "6"))
STATUS_MAX_SECONDS = int(os.environ.get("SPN2_STATUS_MAX_SECONDS", "900"))
MY_ARCHIVE_WORKERS = max(1, int(os.environ.get("MY_ARCHIVE_WORKERS", "16")))
MAX_IN_FLIGHT = max(
    MY_ARCHIVE_WORKERS,
    int(os.environ.get("MY_ARCHIVE_MAX_IN_FLIGHT", str(MY_ARCHIVE_WORKERS * 4))),
)
MAX_SECONDS = int(os.environ.get("SPN2_MAX_SECONDS", "0") or 0)
deadline = time.time() + MAX_SECONDS if MAX_SECONDS > 0 else None

SAVE_URL = "https://web.archive.org/save"
STATUS_URL = "https://web.archive.org/save/status/"
MY_ARCHIVE_URL = "https://web.archive.org/__wb/web-archive/"


done = set()
if os.path.exists(DONE):
    with open(DONE) as f:
        done = set(x.rstrip("\n") for x in f)
print("resuming; already done:", len(done), flush=True)

s = requests.Session()
s.headers.update(H)
dn = open(DONE, "a", buffering=1)
fl = open(FAIL, "a", buffering=1)


def out_of_time(limit=None):
    now = time.time()
    return (deadline is not None and now > deadline) or (
        limit is not None and now > limit
    )


def sleep_with_deadline(seconds, limit=None):
    limits = [x for x in (deadline, limit) if x is not None]
    if limits:
        remaining = min(limits) - time.time()
        if remaining <= 0:
            return False
        time.sleep(min(seconds, remaining))
        return not out_of_time(limit)
    time.sleep(seconds)
    return True


def submit(u):
    for attempt in range(6):
        if out_of_time():
            return None
        try:
            r = s.post(SAVE_URL, data={"url": u}, timeout=60)
            if r.status_code == 200:
                try:
                    job_id = r.json().get("job_id")
                except ValueError:
                    job_id = None
                if job_id:
                    return job_id
            if r.status_code == 429 or r.status_code >= 500:
                if not sleep_with_deadline(min(600, 30 * (attempt + 1))):
                    return None
                continue
            print(
                "no job_id:",
                r.status_code,
                r.text[:120].replace("\n", " "),
                flush=True,
            )
            return False
        except Exception as e:
            print("net err", type(e).__name__, flush=True)
            if not sleep_with_deadline(20):
                return None
    return False


def save_to_my_archive(session, u, timestamp, limit):
    payload = {"url": u, "snapshot": timestamp, "tags": []}
    for attempt in range(6):
        if out_of_time(limit):
            return None
        try:
            r = session.post(MY_ARCHIVE_URL, json=payload, timeout=60)
            try:
                result = r.json()
            except ValueError:
                result = {}
            if r.status_code == 200 and result.get("success") is True:
                return True
            if r.status_code == 429 or r.status_code >= 500:
                if not sleep_with_deadline(min(300, 30 * (attempt + 1)), limit):
                    return None
                continue
            print(
                "my archive failed:",
                r.status_code,
                str(result.get("error") or r.text[:120]).replace("\n", " "),
                flush=True,
            )
            return False
        except Exception as e:
            print("my archive net err", type(e).__name__, flush=True)
            if not sleep_with_deadline(20, limit):
                return None
    return False


def wait_for_capture_and_save(u, job_id):
    session = requests.Session()
    session.headers.update(H)
    limit = time.time() + STATUS_MAX_SECONDS
    if deadline is not None:
        limit = min(limit, deadline)

    while not out_of_time(limit):
        try:
            r = session.get(STATUS_URL + job_id, timeout=60)
            if r.status_code == 200:
                try:
                    result = r.json()
                except ValueError:
                    result = {}
                status = result.get("status")
                if status == "success":
                    timestamp = result.get("timestamp")
                    if not timestamp:
                        print("status missing timestamp:", job_id, flush=True)
                        return False
                    return save_to_my_archive(session, u, timestamp, limit)
                if status == "error":
                    print(
                        "capture failed:",
                        str(result.get("message") or job_id).replace("\n", " "),
                        flush=True,
                    )
                    return False
                if status not in (None, "pending"):
                    print("unknown capture status:", status, job_id, flush=True)
            elif r.status_code != 429 and r.status_code < 500:
                print("status failed:", r.status_code, job_id, flush=True)
                return False
        except Exception as e:
            print("status net err", type(e).__name__, flush=True)

        if not sleep_with_deadline(STATUS_INTERVAL, limit):
            return None

    return None


submitted = 0
archived = 0
fails = 0
stop = False
pending = {}
in_flight_urls = set()
executor = ThreadPoolExecutor(max_workers=MY_ARCHIVE_WORKERS)


def collect_completed(block=False):
    global archived, fails
    if not pending:
        return
    if block:
        completed, _ = wait(pending, return_when=FIRST_COMPLETED)
    else:
        completed = {future for future in pending if future.done()}

    for future in completed:
        u = pending.pop(future)
        in_flight_urls.discard(u)
        try:
            result = future.result()
        except Exception as e:
            print("my archive worker err", type(e).__name__, flush=True)
            result = False

        if result is True:
            dn.write(u + "\n")
            done.add(u)
            archived += 1
            fails = 0
            if archived % 25 == 0:
                print(
                    "saved to my archive",
                    archived,
                    "| total done",
                    len(done),
                    flush=True,
                )
        elif result is False:
            fl.write(u + "\n")
            fails += 1


while True:
    progressed = False
    collect_completed()
    for qf in sorted(glob.glob(QUEUE_GLOB)):
        if stop:
            break
        with open(qf) as f:
            for line in f:
                if out_of_time():
                    print("SPN2 time box reached", flush=True)
                    stop = True
                    break
                u = line.strip()
                if not u or u in done or u in in_flight_urls:
                    continue

                while len(pending) >= MAX_IN_FLIGHT:
                    collect_completed(block=True)
                    if out_of_time():
                        print("SPN2 time box reached", flush=True)
                        stop = True
                        break
                if stop:
                    break

                progressed = True
                job_id = submit(u)
                if isinstance(job_id, str):
                    future = executor.submit(wait_for_capture_and_save, u, job_id)
                    pending[future] = u
                    in_flight_urls.add(u)
                elif job_id is False:
                    fl.write(u + "\n")
                    fails += 1
                else:
                    stop = True
                    break

                submitted += 1
                if submitted % 25 == 0:
                    print(
                        "submitted",
                        submitted,
                        "| awaiting my archive",
                        len(pending),
                        "| total done",
                        len(done),
                        flush=True,
                    )
                if fails >= 15:
                    print("15 consecutive failures; sleeping 20 min", flush=True)
                    if not sleep_with_deadline(1200):
                        stop = True
                        break
                    fails = 0
                if not sleep_with_deadline(INTERVAL):
                    stop = True
                    break
    if stop:
        break
    if pending:
        collect_completed(block=True)
        progressed = True
    if not progressed:
        print("queue exhausted; sleeping 30 min", flush=True)
        if not sleep_with_deadline(1800):
            break

while pending:
    collect_completed(block=True)
executor.shutdown(wait=True, cancel_futures=True)
dn.close()
fl.close()
print(
    "SPN2 EXIT; submitted",
    submitted,
    "| saved to my archive",
    archived,
    "| done total",
    len(done),
    flush=True,
)
