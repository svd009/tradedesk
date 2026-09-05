"""
build_screener_snapshot.py
─────────────────────────────
Pre-builds the daily Screener snapshot, meant to run on a schedule
(see .github/workflows/screener-snapshot.yml), not by whoever happens
to visit the app first each day.

Why this exists:
  Building the full ~5,786-company snapshot takes a genuinely long
  time (a conservative, deliberately gentle thread pool over that many
  tickers can run 40-90+ minutes). Without this script, that entire
  wait falls on whichever real visitor happens to load the Screener
  first each day after the 6 AM ET reset. Running this on a schedule
  instead means the snapshot is already sitting in DynamoDB, ready
  instantly, by the time any real person actually opens the app.

This writes directly to the same DynamoDB table the live app reads
from — no changes needed to the app itself. It just needs to run
before anyone visits.

Usage: python scripts/build_screener_snapshot.py
Requires the same AWS credentials as the app itself (AWS_ACCESS_KEY_ID,
AWS_SECRET_ACCESS_KEY, AWS_REGION), set as environment variables.
"""

import sys
import os
import time

# Allow running this script directly from the scripts/ folder.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.orchestrator import screener


def _progress(done, total):
    if done % 250 == 0 or done == total:
        print(f"  {done}/{total} tickers processed...")


def main():
    print("Starting scheduled Screener snapshot build...")
    start = time.time()

    rows = screener.get_or_build_snapshot(force_refresh=True, progress_callback=_progress,
                                          allow_live_build=True)

    elapsed = round(time.time() - start, 1)
    print(f"\nDone. {len(rows)} tickers successfully fetched in {elapsed}s.")
    if not rows:
        print("WARNING: zero rows fetched — something is likely wrong "
              "(check AWS credentials, Yahoo Finance status, or logs above).")
        sys.exit(1)


if __name__ == "__main__":
    main()
