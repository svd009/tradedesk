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

from config import DYNAMODB_TABLE_NAME, BEDROCK_REGION

# A reserved partition key value, never a real ticker, used to hold small
# aggregate counter rows (see _increment_counters / get_usage_stats).
# Living in the same table under its own partition means a Query for
# "give me all the counters" only ever reads that one small partition —
# genuinely cheap — rather than scanning every analysis ever stored.
_STATS_PARTITION = "__STATS__"
_TOTAL_COUNTER_SIG = "TOTAL"

_dynamo_lock = threading.Lock()
_table = None
_dynamo_available = False

# Fallback used only if DynamoDB itself is unreachable (e.g. IAM
# permissions not yet attached) — keeps the app running, just without
# the cross-restart durability DynamoDB provides.
_memory_fallback: dict = {}


def _get_table():
    """
    Lazily connect to DynamoDB and ensure the table exists, creating it
    on first run if needed. Returns None (triggering the in-memory
    fallback everywhere else in this file) if DynamoDB can't be reached
    at all, rather than raising and crashing the app.
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
            # Table doesn't exist yet — create it. This only needs to
            # happen once, ever, per AWS account.
            try:
                dynamodb = boto3.resource("dynamodb", region_name=BEDROCK_REGION)
                table = dynamodb.create_table(
                    TableName=DYNAMODB_TABLE_NAME,
                    KeySchema=[
                        {"AttributeName": "ticker", "KeyType": "HASH"},
                        {"AttributeName": "window_sig", "KeyType": "RANGE"},
                    ],
                    AttributeDefinitions=[
                        {"AttributeName": "ticker", "AttributeType": "S"},
                        {"AttributeName": "window_sig", "AttributeType": "S"},
                    ],
                    # Provisioned mode, not on-demand (PAY_PER_REQUEST) —
                    # DynamoDB's Always Free tier (25 RCU/25 WCU, permanent,
                    # never expires) only applies to provisioned capacity.
                    # 5 RCU / 3 WCU — bumped up from an initial 1/1, which
                    # turned out to be too low: DynamoDB Scan operations
                    # (used for usage stats and accuracy tracking) charge
                    # capacity based on the SIZE of data read, not row
                    # count, and each stored analysis can be 5-20KB, so
                    # even a modest history would have exceeded 1 RCU on
                    # a single Scan. Still comfortably within the free tier.
                    BillingMode="PROVISIONED",
                    ProvisionedThroughput={
                        "ReadCapacityUnits": 5,
                        "WriteCapacityUnits": 3,
                    },
                )
                table.wait_until_exists()
                _table = table
                _dynamo_available = True
                return _table
            except ClientError as create_error:
                if create_error.response["Error"]["Code"] == "ResourceInUseException":
                    # Another concurrent request won the race and created
                    # the table a moment before us — that's success, not
                    # failure, just connect to what's now there instead
                    # of falling back.
                    dynamodb = boto3.resource("dynamodb", region_name=BEDROCK_REGION)
                    table = dynamodb.Table(DYNAMODB_TABLE_NAME)
                    table.load()
                    _table = table
                    _dynamo_available = True
                    return _table
                print(f"  [storage] Could not create DynamoDB table — "
                      f"falling back to in-memory (not persistent): {create_error}")
                return None
            except Exception as create_error:
                print(f"  [storage] Could not create DynamoDB table — "
                      f"falling back to in-memory (not persistent): {create_error}")
                return None
        else:
            print(f"  [storage] DynamoDB unreachable ({e}) — "
                  f"falling back to in-memory (not persistent). "
                  f"Likely cause: the IAM user needs DynamoDB permissions attached.")
            return None
    except Exception as e:
        print(f"  [storage] DynamoDB unreachable ({e}) — "
              f"falling back to in-memory (not persistent).")
        return None


def init_db():
    """Kept for interface parity with the previous SQLite version —
    connecting/creating the table happens lazily in _get_table() on
    first real use instead, so this is just a no-op trigger."""
    _get_table()


def _increment_counters(table, ticker: str):
    """
    Atomically bump two small counter rows: one global total, one for
    this specific ticker. DynamoDB's ADD on a numeric attribute creates
    the item with that starting value if it doesn't exist yet — no
    separate initialization step needed. These rows are tiny (a ticker
    name and a number), completely unlike the 5-20KB analysis rows, so
    reading all of them back later is cheap.
    """
    try:
        table.update_item(
            Key={"ticker": _STATS_PARTITION, "window_sig": _TOTAL_COUNTER_SIG},
            UpdateExpression="ADD analysis_count :incr",
            ExpressionAttributeValues={":incr": 1},
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
        if baseline_price is not None:
            item["baseline_price"] = str(baseline_price)  # stored as a
                                                            # string to sidestep
                                                            # DynamoDB's Decimal-
                                                            # only Number typing
        table.put_item(Item=item)
        _increment_counters(table, ticker)
    except Exception as e:
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
        response = table.scan(
            ProjectionExpression="ticker, recommendation, confidence, baseline_price, cached_at",
            FilterExpression="ticker <> :stats",
            ExpressionAttributeValues={":stats": _STATS_PARTITION},
        )
        return response.get("Items", [])
    except Exception as e:
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
        ticker_counts = []
        for item in items:
            if item["window_sig"] == _TOTAL_COUNTER_SIG:
                total = int(item.get("analysis_count", 0))
            elif item["window_sig"].startswith("TICKER#"):
                ticker_counts.append((item.get("ticker_name", "?"), int(item.get("analysis_count", 0))))

        popular = sorted(ticker_counts, key=lambda x: -x[1])[:10]
        return {"total_analyses": total, "most_popular": popular}
    except Exception as e:
        print(f"  [storage] DynamoDB query failed ({e})")
        return {"total_analyses": 0, "most_popular": []}
