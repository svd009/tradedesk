"""
storage.py
───────────
DynamoDB-backed persistence for TradeDesk's daily analysis cache.

Why DynamoDB instead of a local SQLite file:
  The first version of this file used SQLite, a single file on disk.
  That turned out to have a real problem: Streamlit Community Cloud's
  own documentation states plainly that local file storage is NOT
  guaranteed to survive across app restarts/redeploys, and it doesn't,
  multiple real reports confirm local files getting wiped exactly on
  redeploy, the exact scenario this was meant to solve. DynamoDB is
  genuinely durable, external storage — the data lives in AWS, not in
  the app's own filesystem, so it survives redeploys, reboots, and even
  deleting and recreating the app entirely.

Why DynamoDB specifically (over S3, the other AWS option):
  This is fundamentally a key-value access pattern — "get the cached
  result for this ticker+window" and "save this result" — which is
  exactly what DynamoDB is built for. But the decisive reason is cost
  permanence, not just access pattern fit: DynamoDB's free tier (25GB
  storage + 25 read/write capacity units, in PROVISIONED billing mode
  specifically) is "Always Free" — it never expires, on any AWS account,
  regardless of age. S3's free tier (5GB + 20,000 GET + 2,000 PUT
  requests) only lasts 12 months from account creation, then it quietly
  starts billing standard rates. For a project meant to keep running,
  that permanence is worth more than S3's marginal simplicity.

Schema (deliberately designed for more than just caching):
  Partition key:  ticker        (e.g. "NVDA")
  Sort key:       window_sig    (e.g. "2026-08-30#none")
  This isn't just "one row per cache entry" by accident — using ticker
  as the partition key means a future accuracy-tracking feature can run
  a single Query for "every analysis ever done on NVDA" and get its full
  history back, ordered by window, without redesigning anything. Same
  one-write-two-uses idea as the SQLite version had, just with a schema
  that scales to that specific future use case better.

Graceful degradation:
  If DynamoDB is unreachable — most likely because the IAM user hasn't
  been given DynamoDB permissions yet — every function here falls back
  to an in-memory dict rather than crashing the app. This matches the
  same resilience philosophy as the Yahoo Finance -> Twelve Data
  fallback: a missing/misconfigured secondary system degrades the
  feature (no persistence across restarts), it doesn't take down the
  primary one (the app still runs and still caches within its lifetime).
"""

import json
import threading
import boto3
from botocore.exceptions import ClientError
from datetime import datetime

from config import (
    DYNAMODB_TABLE_NAME,
    FAIL_ON_STORAGE_ERROR,
    BEDROCK_REGION,
)

# A reserved partition key value, never a real ticker, used to hold small
# aggregate counter rows (see _increment_counters / get_usage_stats).
# Living in the same table under its own partition means a Query for
# "give me all the counters" only ever reads that one small partition —
# genuinely cheap — rather than scanning every analysis ever stored.
_STATS_PARTITION = "__STATS__"
_TOTAL_COUNTER_SIG = "TOTAL"
# Another reserved partition, this one for daily screener snapshot rows —
# same table, same "reserve a partition key, Query instead of Scan"
# trick as the counters above, just for a batch of ~554 rows instead of
# a couple of counters.
_SCREENER_PARTITION = "__SCREENER__"
# Reserved partition for "this looks wrong" reports sent by users.
_FEEDBACK_PARTITION = "__FEEDBACK__"
# Every reserved partition starts with this, so real-analysis scans can
# exclude all of them with one filter.
_RESERVED_PREFIX = "__"

_dynamo_lock = threading.Lock()
_table = None
_dynamo_available = False

# Fallback used only if DynamoDB itself is unreachable (e.g. IAM
# permissions not yet attached) — keeps the app running, just without
# the cross-restart durability DynamoDB provides.
_memory_fallback: dict = {}


def _storage_error(message: str, error: Exception):
    """Raise in production; otherwise log and allow the local fallback."""
    if FAIL_ON_STORAGE_ERROR:
        raise RuntimeError(f"{message}: {error}") from error
    print(f"  [storage] {message} — falling back to in-memory: {error}")


def _get_table():
    """
    Lazily connect to the DynamoDB table provisioned by infrastructure.
    Returns None (triggering the in-memory fallback everywhere else in
    this file) when storage is unavailable and local fallback is enabled.
    """
    global _table, _dynamo_available
    if _table is not None:
        return _table

    try:
        dynamodb = boto3.resource("dynamodb", region_name=BEDROCK_REGION)
        table = dynamodb.Table(DYNAMODB_TABLE_NAME)
        table.load()  # raises if the table doesn't exist yet
        _table = table
        _dynamo_available = True
        return _table
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            _storage_error(
                f"DynamoDB table {DYNAMODB_TABLE_NAME!r} does not exist; "
                "deploy the infrastructure before starting the app",
                e,
            )
            return None
        _storage_error("DynamoDB is unreachable", e)
        return None
    except Exception as e:
        _storage_error("DynamoDB is unreachable", e)
        return None


def init_db():
    """Kept for interface parity with the previous SQLite version —
    connecting to the provisioned table happens lazily in _get_table()
    on first real use instead, so this is just a no-op trigger."""
    _get_table()


def _increment_counters(table, ticker: str, usage: dict = None):
    """
    Atomically bump two small counter rows: one global total, one for
    this specific ticker. DynamoDB's ADD on a numeric attribute creates
    the item with that starting value if it doesn't exist yet — no
    separate initialization step needed. These rows are tiny (a ticker
    name and a number), completely unlike the 5-20KB analysis rows, so
    reading all of them back later is cheap.
    """
    try:
        usage = usage or {}
        cost = usage.get("estimated_cost_usd")
        table.update_item(
            Key={"ticker": _STATS_PARTITION, "window_sig": _TOTAL_COUNTER_SIG},
            # Running totals of token usage and cost alongside the count.
            # Cost is kept in millionths of a dollar, because DynamoDB
            # counters must be whole numbers to add up exactly.
            UpdateExpression="ADD analysis_count :incr, tokens_in :ti, "
                             "tokens_out :to, cost_micro_usd :c, priced_count :p",
            ExpressionAttributeValues={
                ":incr": 1,
                ":ti": int(usage.get("input_tokens", 0) or 0),
                ":to": int(usage.get("output_tokens", 0) or 0),
                ":c": int(round((cost or 0) * 1_000_000)),
                ":p": 1 if cost is not None else 0,
            },
        )
        table.update_item(
            Key={"ticker": _STATS_PARTITION, "window_sig": f"TICKER#{ticker}"},
            UpdateExpression="ADD analysis_count :incr SET ticker_name = :t",
            ExpressionAttributeValues={":incr": 1, ":t": ticker},
        )
    except Exception as e:
        print(f"  [storage] Counter update failed ({e}) — "
              f"usage stats may undercount this entry")


def get_cached(ticker: str, window_key: str, portfolio_sig: str):
    """Look up a cached analysis. Returns (result_dict, cached_at) or None."""
    window_sig = f"{window_key}#{portfolio_sig}"
    table = _get_table()

    if table is None:
        with _dynamo_lock:
            hit = _memory_fallback.get((ticker, window_sig))
        if hit is None:
            return None
        return hit["result"], hit["cached_at"]

    try:
        response = table.get_item(Key={"ticker": ticker, "window_sig": window_sig})
        item = response.get("Item")
        if item is None:
            return None
        return json.loads(item["result_json"]), datetime.fromisoformat(item["cached_at"])
    except Exception as e:
        if FAIL_ON_STORAGE_ERROR:
            raise
        print(f"  [storage] DynamoDB read failed ({e}) — treating as cache miss")
        return None


def save_analysis(ticker: str, window_key: str, portfolio_sig: str,
                  result: dict, cached_at: datetime):
    """
    Save (or overwrite, on a forced refresh) a freshly-computed analysis.

    Also captures the stock's price at the moment of this recommendation
    (baseline_price) — without this, there's no fixed point to measure
    "did the price move the way the recommendation implied" against
    later. Pulled from the Technical subagent's own findings, since it
    already fetches current price as part of its normal work — no extra
    API call needed to get this.
    """
    window_sig = f"{window_key}#{portfolio_sig}"
    recommendation = None
    confidence = None
    try:
        rec_data = result.get("synthesis", {}).get("recommendation", {})
        recommendation = rec_data.get("recommendation")
        confidence = rec_data.get("confidence")
    except Exception:
        pass

    baseline_price = None
    try:
        technical = result.get("subagent_findings", {}).get("technical", {})
        baseline_price = technical.get("key_levels", {}).get("current_price")
    except Exception:
        pass  # Technical subagent may have failed/timed out — accuracy
              # tracking just can't check this particular entry later

    table = _get_table()
    result_json = json.dumps(result, default=str)

    if table is None:
        with _dynamo_lock:
            _memory_fallback[(ticker, window_sig)] = {
                "result": result, "cached_at": cached_at,
                "recommendation": recommendation, "confidence": confidence,
                "baseline_price": baseline_price,
            }
        return

    try:
        item = {
            "ticker": ticker,
            "window_sig": window_sig,
            "recommendation": recommendation or "UNKNOWN",
            "confidence": str(confidence) if confidence is not None else "0",
            "result_json": result_json,
            "cached_at": cached_at.isoformat(),
        }
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        quality = result.get("quality") if isinstance(result.get("quality"), dict) else {}
        item["tokens_in"] = int(usage.get("input_tokens", 0) or 0)
        item["tokens_out"] = int(usage.get("output_tokens", 0) or 0)
        if usage.get("estimated_cost_usd") is not None:
            item["cost_usd"] = str(usage["estimated_cost_usd"])
        item["needs_review"] = bool(quality.get("needs_review", False))
        if baseline_price is not None:
            item["baseline_price"] = str(baseline_price)  # stored as a
                                                            # string to sidestep
                                                            # DynamoDB's Decimal-
                                                            # only Number typing
        table.put_item(Item=item)
        _increment_counters(table, ticker, usage)
    except Exception as e:
        if FAIL_ON_STORAGE_ERROR:
            raise
        print(f"  [storage] DynamoDB write failed ({e}) — "
              f"caching in-memory for this session only")
        with _dynamo_lock:
            _memory_fallback[(ticker, window_sig)] = {
                "result": result, "cached_at": cached_at,
                "recommendation": recommendation, "confidence": confidence,
                "baseline_price": baseline_price,
            }


def get_all_analyses() -> list:
    """
    Every stored analysis, with the fields the accuracy tracker needs
    (ticker, recommendation, confidence, baseline_price, cached_at).

    This genuinely needs a Scan, not a Query — the accuracy tracker has
    to inspect every historical row's actual data (was this specific
    call right or wrong), which aggregate counters can't answer. Now
    protected by the bumped 5 RCU capacity (see _get_table), but still
    worth knowing this is the one place in this file that doesn't scale
    indefinitely — a much larger history would eventually want a
    different approach (e.g. a GSI on cached_at to page through only
    old-enough rows, or marking rows "checked" once judged so they're
    never re-scanned).
    """
    table = _get_table()
    if table is None:
        with _dynamo_lock:
            return [
                {
                    "ticker": k[0],
                    "recommendation": v.get("recommendation"),
                    "confidence": v.get("confidence"),
                    "baseline_price": v.get("baseline_price"),
                    "cached_at": v["cached_at"].isoformat(),
                }
                for k, v in _memory_fallback.items()
            ]

    try:
        # A Scan returns at most 1 MB per call, and the filter is applied
        # AFTER that limit. Without following LastEvaluatedKey, a table
        # holding many screener rows could return a first page with no
        # analyses in it at all, and the Track Record would look empty.
        items, kwargs = [], {
            "ProjectionExpression": "ticker, recommendation, confidence, baseline_price, cached_at",
            # Exclude every reserved partition (stats, screener, feedback).
            "FilterExpression": "NOT begins_with(ticker, :reserved)",
            "ExpressionAttributeValues": {":reserved": _RESERVED_PREFIX},
        }
        while True:
            response = table.scan(**kwargs)
            items.extend(response.get("Items", []))
            if "LastEvaluatedKey" not in response:
                break
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
        return items
    except Exception as e:
        if FAIL_ON_STORAGE_ERROR:
            raise
        print(f"  [storage] DynamoDB scan failed ({e})")
        return []


def get_usage_stats() -> dict:
    """
    Basic usage numbers, read from the small counter rows maintained by
    _increment_counters — a Query against one partition, not a Scan of
    every analysis ever stored. This is the fix for a real capacity
    problem the original Scan-based version had: Scan charges by the
    size of data read, and each analysis row can be 5-20KB, so even a
    modest history would exceed a small provisioned-capacity budget.
    Counter rows are tiny, reading all of them stays cheap indefinitely,
    regardless of how large the analyses table itself grows.
    """
    table = _get_table()
    if table is None:
        with _dynamo_lock:
            tickers = [k[0] for k in _memory_fallback.keys()]
        counts = {}
        for t in tickers:
            counts[t] = counts.get(t, 0) + 1
        popular = sorted(counts.items(), key=lambda x: -x[1])[:10]
        return {"total_analyses": len(tickers), "most_popular": popular}

    try:
        response = table.query(
            KeyConditionExpression="ticker = :stats",
            ExpressionAttributeValues={":stats": _STATS_PARTITION},
        )
        items = response.get("Items", [])

        total = 0
        totals_row = {}
        ticker_counts = []
        for item in items:
            if item["window_sig"] == _TOTAL_COUNTER_SIG:
                total = int(item.get("analysis_count", 0))
                totals_row = item
            elif item["window_sig"].startswith("TICKER#"):
                ticker_counts.append((item.get("ticker_name", "?"), int(item.get("analysis_count", 0))))

        popular = sorted(ticker_counts, key=lambda x: -x[1])[:10]
        return {
            "total_analyses": total,
            "most_popular": popular,
            # Running totals since token tracking was added.
            "tokens_in": int(totals_row.get("tokens_in", 0)),
            "tokens_out": int(totals_row.get("tokens_out", 0)),
            "cost_usd": int(totals_row.get("cost_micro_usd", 0)) / 1_000_000,
            "priced_analyses": int(totals_row.get("priced_count", 0)),
        }
    except Exception as e:
        if FAIL_ON_STORAGE_ERROR:
            raise
        print(f"  [storage] DynamoDB query failed ({e})")
        return {"total_analyses": 0, "most_popular": []}


# In-memory fallback for the screener snapshot, mirrors the same
# graceful-degradation pattern as the rest of this file.
_screener_memory_fallback: dict = {}


def save_screener_row(date_str: str, ticker: str, row: dict):
    """
    Save one ticker's screener metrics for a given date. Uses the
    reserved __SCREENER__ partition with a "date#ticker" sort key, so
    reading back a whole day's snapshot is one efficient Query (see
    get_screener_snapshot), not a Scan across the entire table.
    """
    table = _get_table()
    window_sig = f"{date_str}#{ticker}"
    # row's own "ticker" field (e.g. "NVDA") would silently overwrite the
    # DynamoDB partition key attribute — which is also named "ticker" but
    # always holds "__SCREENER__" for these rows — if unpacked directly.
    # Stored under "stock_ticker" instead, reconstructed on read.
    row_without_collision = {k: v for k, v in row.items() if k != "ticker"}

    if table is None:
        with _dynamo_lock:
            _screener_memory_fallback[(date_str, ticker)] = row
        return

    try:
        item = {
            "ticker": _SCREENER_PARTITION,
            "window_sig": window_sig,
            "stock_ticker": ticker,
            **row_without_collision,
        }
        # DynamoDB rejects native Python floats for Number attributes —
        # store numeric fields as strings, same convention as
        # baseline_price elsewhere in this file, parsed back on read.
        for key, value in item.items():
            if isinstance(value, float):
                item[key] = str(value)
        table.put_item(Item=item)
    except Exception as e:
        if FAIL_ON_STORAGE_ERROR:
            raise
        print(f"  [storage] Screener row write failed for {ticker} ({e})")
        with _dynamo_lock:
            _screener_memory_fallback[(date_str, ticker)] = row


def get_screener_snapshot(date_str: str) -> list:
    """
    All screener rows for a given date, via a Query scoped to the
    reserved partition (cheap, regardless of how large the main
    analyses table grows) rather than a Scan.
    """
    table = _get_table()

    if table is None:
        with _dynamo_lock:
            return [
                row for (d, t), row in _screener_memory_fallback.items()
                if d == date_str
            ]

    try:
        from boto3.dynamodb.conditions import Key
        response = table.query(
            KeyConditionExpression=Key("ticker").eq(_SCREENER_PARTITION)
                                   & Key("window_sig").begins_with(f"{date_str}#"),
        )
        rows = []
        for item in response.get("Items", []):
            row = {k: v for k, v in item.items() if k not in ("ticker", "window_sig")}
            if "stock_ticker" in row:
                row["ticker"] = row.pop("stock_ticker")
            # Convert the string-encoded numeric fields back to floats.
            for key, value in row.items():
                if key in ("ticker", "company_name", "market", "sector", "currency"):
                    continue  # these are genuinely strings, never numeric
                if isinstance(value, str):
                    try:
                        row[key] = float(value)
                    except ValueError:
                        pass
            rows.append(row)
        return rows
    except Exception as e:
        if FAIL_ON_STORAGE_ERROR:
            raise
        print(f"  [storage] Screener snapshot query failed ({e})")
        return []


# ── User feedback ("this looks wrong") ─────────────────────────────────────

_feedback_memory_fallback: list = []
_FEEDBACK_REASONS = ("Price or number looks wrong", "Recommendation doesn't make sense",
                     "News is outdated or irrelevant", "Something else")
_MAX_COMMENT_CHARS = 500


def save_feedback(ticker: str, reason: str, comment: str = "",
                  recommendation: str = "", analysis_time: str = "") -> bool:
    """
    Store one user report against an analysis. Returns True if saved.

    The comment is free text typed by a member of the public: it is
    length-capped here and must always be treated as untrusted text
    wherever it is displayed (never rendered as HTML, never fed to a
    model as instructions).
    """
    now = datetime.now().isoformat(timespec="seconds")
    row = {
        "ticker": _FEEDBACK_PARTITION,
        "window_sig": f"{now}#{ticker.upper()}",
        "stock_ticker": ticker.upper(),
        "reason": str(reason)[:80],
        "comment": str(comment or "").strip()[:_MAX_COMMENT_CHARS],
        "recommendation": str(recommendation or "")[:20],
        "analysis_time": str(analysis_time or "")[:40],
        "created_at": now,
    }
    table = _get_table()
    if table is None:
        with _dynamo_lock:
            _feedback_memory_fallback.append(row)
        return True
    try:
        table.put_item(Item=row)
        return True
    except Exception as e:
        print(f"  [storage] Feedback write failed ({e})")
        return False


def get_feedback(limit: int = 100) -> list:
    """Most recent user reports first. A Query on one partition, not a Scan."""
    table = _get_table()
    if table is None:
        with _dynamo_lock:
            return list(reversed(_feedback_memory_fallback))[:limit]
    try:
        response = table.query(
            KeyConditionExpression="ticker = :fb",
            ExpressionAttributeValues={":fb": _FEEDBACK_PARTITION},
            ScanIndexForward=False,  # newest first (sort key starts with the timestamp)
            Limit=limit,
        )
        return response.get("Items", [])
    except Exception as e:
        if FAIL_ON_STORAGE_ERROR:
            raise
        print(f"  [storage] Feedback query failed ({e})")
        return []


def get_flagged_analyses() -> list:
    """
    Analyses the automatic checks marked for review (bad data, an
    inconsistent answer, numbers not found in the inputs, failed agents).
    A Scan, so intended for the occasional admin report, not page loads.
    """
    table = _get_table()
    if table is None:
        with _dynamo_lock:
            return [
                {"ticker": k[0], "cached_at": v["cached_at"].isoformat(),
                 "recommendation": v.get("recommendation")}
                for k, v in _memory_fallback.items()
                if isinstance(v.get("result"), dict)
                and (v["result"].get("quality") or {}).get("needs_review")
            ]
    try:
        items, kwargs = [], {
            "ProjectionExpression": "ticker, recommendation, cached_at",
            "FilterExpression": "needs_review = :yes",
            "ExpressionAttributeValues": {":yes": True},
        }
        while True:
            response = table.scan(**kwargs)
            items.extend(response.get("Items", []))
            if "LastEvaluatedKey" not in response:
                break
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
        return sorted(items, key=lambda r: r.get("cached_at", ""), reverse=True)
    except Exception as e:
        print(f"  [storage] Flagged-analysis scan failed ({e})")
        return []
