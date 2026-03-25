# Latency Expected Outcome

## Baseline Snapshot (pre-change estimate)
- Source: `archive/legacy/logs/20260212_133940/SessionRawresponse.json`
- Method: compare log write time (`ts`) to newest Discord message timestamp in each response payload.
- Note: this is an approximation of detection lag because legacy logs did not store explicit send/ack timings.

| Metric | Baseline (approx) |
|---|---|
| Core roll fetch detect lag p95 (absolute) | ~1431 ms |
| Core `/tu` detect lag p95 (absolute) | ~1896 ms |
| Core `/wl` detect lag p95 (absolute) | ~1564 ms |
| Core kakera reaction detect lag p95 (absolute) | ~1571 ms |

## Expected Outcome After This Refactor

| Flow / action | Before (expected range) | After (expected range) |
|---|---|---|
| Core `roll` message detect lag p95 | ~1200-2200 ms | ~350-900 ms |
| Core `claim` action ack p95 | ~1500-2600 ms | ~350-850 ms |
| Core `kakera react` ack p95 | ~900-1800 ms | ~250-700 ms |
| External roll detection p95 | ~800-3000 ms | ~150-900 ms |
| `OH` click ack p95 | ~400-1000 ms | ~120-500 ms |
| `OC` click ack p95 | ~400-1000 ms | ~120-500 ms |
| `OQ` click ack p95 | ~400-1000 ms | ~120-500 ms |

## Acceptance Target
- Global target: `p95 response_ms < 900 ms` for `action_ack`.
- Auto-degrade safety:
  - `aggressive -> balanced` on sustained 429/failure pressure.
  - `balanced -> legacy` on severe pressure.
  - step-up recovery after healthy windows.

## How to Measure Post-Change
1. Run normal sessions for core + `OH/OC/OQ`.
2. Inspect `logs/LatencyMetrics.jsonl`.
3. Execute:

```bash
python scripts/summarize_latency_metrics.py
```

This reports `p50`/`p95` by flow/action/tier from persisted telemetry.

