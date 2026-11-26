#!/usr/bin/env python3
"""Long-running loop for the rubies tracker (one GitHub Actions job lasts ~5h45m, then starts the next).

- every hour (at :00-:04 Stockholm): save a snapshot of the Rubies board
- every POST_EVERY_H hours (00, 04, 08, 12, 16, 20 by default): post the picture
- PREVIEW=true: one snapshot + picture right now, then stop
"""
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

import rubies

TZ = ZoneInfo("Europe/Stockholm")
POLL = 60
RUN_FOR = int(float(os.environ.get("RUN_FOR_SECONDS", str(345 * 60))))
POST_EVERY_H = int(os.environ.get("POST_EVERY_H", "4"))


def sh(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=120).returncode
    except Exception as e:
        print("cmd failed:", cmd[:2], e)
        return 1


def save_state(msg):
    if not os.path.exists(rubies.STATE):
        return
    sh("git", "config", "user.name", "github-actions[bot]")
    sh("git", "config", "user.email", "41898283+github-actions[bot]@users.noreply.github.com")
    sh("git", "add", rubies.STATE)
    if sh("git", "diff", "--cached", "--quiet") != 0:
        sh("git", "commit", "-m", msg)
        if sh("git", "pull", "--rebase", "-X", "theirs") != 0:
            sh("git", "rebase", "--abort")
        sh("git", "push")


def safe(label, fn):
    try:
        print(f"[{datetime.now(TZ):%H:%M:%S}] {label}")
        return fn()
    except Exception:
        print(f"{label} crashed:")
        traceback.print_exc()
        return 1


def main():
    if os.environ.get("PREVIEW", "").lower() == "true":
        rc = safe("rubies preview", rubies.main)
        save_state("rubies snapshot (preview)")
        return rc
    st = rubies.load()
    last_at = st["snaps"][-1]["at"] if st["snaps"] else 0
    done_hour = None
    if time.time() - last_at > 50 * 60:  # nothing recent (first start / long gap): snapshot now, no post
        safe("rubies snapshot (catch-up)", rubies.snapshot)
        save_state("rubies snapshot")
        done_hour = datetime.now(TZ).strftime("%Y%m%d%H")
    start = time.time()
    while time.time() - start < RUN_FOR:
        t0 = time.time()
        now = datetime.now(TZ)
        key = now.strftime("%Y%m%d%H")
        if now.minute < 5 and key != done_hour:
            done_hour = key
            if now.hour % POST_EVERY_H == 0:
                safe("rubies snapshot + picture", rubies.main)
            else:
                safe("rubies snapshot", rubies.snapshot)
            save_state("rubies snapshot")
        time.sleep(max(5, POLL - (time.time() - t0)))
    save_state("final state")
    return 0


if __name__ == "__main__":
    sys.exit(main())
