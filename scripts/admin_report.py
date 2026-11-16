"""
admin_report.py
────────────────
A private report for the developer: how much the app is used, and what the
automatic checks and users have flagged. Reads DynamoDB only; changes nothing.

Run from the project folder:

    python scripts/admin_report.py

Needs the same AWS settings as the app (.env or environment variables).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.orchestrator import storage  # noqa: E402


def main():
    stats = storage.get_usage_stats()

    print("\n== Usage ==")
    print(f"Fresh analyses run : {stats.get('total_analyses', 0)}")
    if stats.get("most_popular"):
        print("Most analysed      : " + ", ".join(f"{t} ({n})" for t, n in stats["most_popular"][:5]))

    flagged = [a for a in storage.get_flagged_analyses()]
    print(f"\n== Flagged by automatic checks ({len(flagged)}) ==")
    for row in flagged[:25]:
        print(f"{row.get('cached_at', '')[:16]}  {row.get('ticker', ''):<12} {row.get('recommendation', '')}")

    feedback = storage.get_feedback(limit=50)
    print(f"\n== User reports ({len(feedback)} most recent) ==")
    for row in feedback:
        print(f"{row.get('created_at', '')[:16]}  {row.get('stock_ticker', ''):<12} "
              f"[{row.get('recommendation', '')}] {row.get('reason', '')}")
        if row.get("comment"):
            # repr() so nothing typed by a user can affect the terminal.
            print(f"    comment: {row['comment']!r}")
    print()


if __name__ == "__main__":
    main()
