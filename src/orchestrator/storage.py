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
                    # On-demand mode bills per request from the first call,
                    # with no free allowance on requests at all, just
                    # storage. 1/1 capacity comfortably covers this app's
                    # actual traffic (roughly one write per fresh analysis,
                    # a handful of reads per lookup); raise this later only
                    # if you ever see ProvisionedThroughputExceededException
                    # in the logs.
                    BillingMode="PROVISIONED",
                    ProvisionedThroughput={
                        "ReadCapacityUnits": 1,
                        "WriteCapacityUnits": 1,
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
    Same full-table-Scan caveat as get_usage_stats() — fine at this
    app's scale, not the approach for a much bigger table.
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
            ProjectionExpression="ticker, recommendation, confidence, baseline_price, cached_at"
        )
        return response.get("Items", [])
    except Exception as e:
        print(f"  [storage] DynamoDB scan failed ({e})")
        return []


def get_usage_stats() -> dict:
    """
    Basic usage numbers. Uses a full table Scan — fine at this app's
    realistic scale (hundreds to low thousands of rows), but worth
    knowing this wouldn't be the right approach at a much larger scale,
    a real analytics need at that point would call for a separate
    aggregation table updated incrementally rather than scanning
    everything on each stats request.
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
        response = table.scan(ProjectionExpression="ticker")
        tickers = [item["ticker"] for item in response.get("Items", [])]
        counts = {}
        for t in tickers:
            counts[t] = counts.get(t, 0) + 1
        popular = sorted(counts.items(), key=lambda x: -x[1])[:10]
        return {"total_analyses": len(tickers), "most_popular": popular}
    except Exception as e:
        print(f"  [storage] DynamoDB scan failed ({e})")
        return {"total_analyses": 0, "most_popular": []}
