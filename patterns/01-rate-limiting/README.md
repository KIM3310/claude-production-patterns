# Pattern 01: Rate Limiting & Backoff

## Problem

Anthropic publishes per-model rate limits (requests/min, tokens/min). Hitting them returns 429 errors. Naive clients treat 429 as permanent failure and bubble up to users. Sophisticated clients back off and retry. Production clients implement layered defense.

## Pattern

Three layers of rate limiting work together:

1. **Server-side (Anthropic's)**: hard ceiling.
2. **Client-side global**: soft limit below server limit to leave headroom for retries.
3. **Per-tenant / per-user**: quota enforcement to prevent one tenant starving others.

```
User request
     │
     ▼
Per-tenant token bucket ──→ 429 if tenant exceeded their quota
     │ passes
     ▼
Global rate limiter ──→ queue + delay if approaching global cap
     │ passes
     ▼
Anthropic API call
     │
     ├─ 200 → success
     │
     └─ 429 from Anthropic → exponential backoff + retry
            (max 5 attempts, 1s → 2s → 4s → 8s → 16s)
```

## When to use

Every production deployment. Not optional.

## Implementation pieces

### `TokenBucket` — per-tenant quota

- Each tenant has a token bucket with configurable rate and burst.
- Every request consumes N tokens (estimated by input token count).
- If bucket is empty, return 429 immediately (don't queue).
- Bucket refills at configured rate.

### `GlobalRateLimiter` — cluster-wide throttle

- Tracks requests-per-minute and tokens-per-minute against Anthropic's limits.
- When at 80% of limit, starts queueing / delaying new requests.
- At 95%, returns 429 to clients.
- At 100%, client has failed. Circuit opens to prevent thundering herd.

### `RetryWithBackoff` — Anthropic 429 handler

- Catches `anthropic.RateLimitError`.
- Exponential backoff with jitter (prevents synchronized retry storms).
- Cap at 5 attempts; after that, raise to caller.

### Monitoring

- Emit metrics: `rate_limit_exceeded_count{tenant, reason}`.
- Alert on: global rate limit > 80% for 5 minutes.
- Dashboard: tenant-level quota utilization.

## Code

See `pattern.py` for the runnable implementation.

## Common mistakes

1. **No per-tenant limit**: one misbehaving tenant consumes global quota; others fail.
2. **Queuing instead of rejecting**: long queues hide the problem. Fail fast with a clear retry-after signal.
3. **No jitter in backoff**: synchronized retries hammer Anthropic. Always add jitter.
4. **Unbounded retries**: eventually fail and propagate the error. Infinite retries == infinite latency.
5. **Hiding rate-limit errors from caller**: caller should know they were rate-limited so they can handle gracefully.

## Measurement

Key metrics:

- Request success rate: should be 99%+ at steady state.
- 429 rate to users: should be <1% (tenant quota exceeded is a legitimate user error).
- 429 rate from Anthropic: should be ~0% (means our client-side limiter is tuned correctly).
- P99 retry latency: should be <30 seconds (5 retries × ~5s backoff average).

## Related patterns

- Pattern 09 (error classification) — deciding when 429 is retryable.
- Pattern 02 (cost control) — rate limits and cost limits are related.
- Pattern 08 (observability) — these metrics need to be visible.
