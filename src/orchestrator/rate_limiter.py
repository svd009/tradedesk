"""
rate_limiter.py
─────────────────
A token bucket rate limiter, scoped per browser session.

The concept:
  Each user gets a "bucket" holding up to N tokens (RATE_LIMIT_BUCKET_CAPACITY).
  Every analysis request costs 1 token. The bucket slowly refills over time,
  1 token every RATE_LIMIT_REFILL_SECONDS_PER_TOKEN seconds, up to the cap.
  If the bucket's empty, the request is denied until it refills.

Why token bucket specifically (over fixed window, sliding window log, etc.):
  It allows short bursts — someone can run several tickers back-to-back if
  they haven't used the app recently — while still capping sustained
  hammering once the bucket empties. That burst-tolerance is the practical
  reason it's the industry default (Stripe, AWS API Gateway both use it),
  and it's a cheap, simple thing to reason about: two numbers (capacity,
  refill rate) and one rule.

Why per-session, not per-IP:
  A "real" production rate limiter usually keys on IP address or an
  authenticated user ID, so someone can't dodge the limit by opening a
  new incognito tab. This app has no login system, and reliably getting a
  real client IP from inside Streamlit (behind Streamlit Community Cloud's
  own proxy) isn't straightforward. Session-scoping is the honest, correct
  scope for what's actually enforceable here — worth stating plainly if
  asked "how would you make this more robust," rather than pretending this
  is airtight IP-level protection it isn't.
"""

import time


class TokenBucket:
    """A single user's token bucket. One instance per browser session."""

    def __init__(self, capacity: int, refill_seconds_per_token: float):
        self.capacity = capacity
        self.refill_seconds_per_token = refill_seconds_per_token
        self.tokens = float(capacity)  # start full — don't punish a first-time visitor
        self.last_refill = time.time()

    def _refill(self):
        now = time.time()
        elapsed = now - self.last_refill
        if elapsed <= 0:
            return
        refill_amount = elapsed / self.refill_seconds_per_token
        self.tokens = min(self.capacity, self.tokens + refill_amount)
        self.last_refill = now

    def try_consume(self, amount: float = 1.0) -> bool:
        """Attempt to spend `amount` tokens. Returns True if allowed."""
        self._refill()
        if self.tokens >= amount:
            self.tokens -= amount
            return True
        return False

    def seconds_until_next_token(self) -> float:
        """How long until at least 1 token is available, for a helpful wait message."""
        self._refill()
        if self.tokens >= 1:
            return 0.0
        return (1.0 - self.tokens) * self.refill_seconds_per_token

    def tokens_remaining(self) -> float:
        self._refill()
        return self.tokens


def get_user_bucket(session_state, capacity: int, refill_seconds_per_token: float) -> TokenBucket:
    """
    Get (or create) this browser session's bucket. Storing it in
    st.session_state is what makes this per-user: Streamlit gives each
    browser tab/session its own independent session_state dict.
    """
    if "_rate_limit_bucket" not in session_state:
        session_state["_rate_limit_bucket"] = TokenBucket(capacity, refill_seconds_per_token)
    return session_state["_rate_limit_bucket"]
