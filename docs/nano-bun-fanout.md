# Ten-worker private-cache fanout experiment

Run the dispatch-only **Nano Bun private-cache fanout experiment** on the reviewed branch.
This adds no push/PR workload, browser binary, model run, or deployment.

The original fixed Node 26.0.0/npm 11.9.0/Bun 1.4.4 comparison first creates and verifies
a warm seed cache. Ten workers each receive a real independent copy, without hardlinks
to the seed or each other. Existing adapter locks, quarantine markers, input hashes,
exact two-test gates, and output hash checks are unchanged. Each worker runs offline.
This is NOT concurrent access to a shared writable Bun cache. That remains unsupported.

Ten workers run at concurrency 1, 2, and 4, each three times with rotated order.
Copies are provisioned sequentially before the execution timer; copy time is separately
reported and INCLUDED in total batch time. Every batch starts with fresh private copies.
Seed preparation cost is recorded separately, not silently amortized into a speed claim.
Artifacts contain seed evidence, batch reports, worker logs/receipts, and failure details.
Require fanout-summary.status=passed AND all 90 worker receipts with exact matching outputs.

Use total_seconds to compare end-to-end batch wall, execution_seconds for worker scheduling,
and cache_logical_bytes for duplicated logical cache bytes. These are not allocated disk,
RSS, PSS, CPU-time or throughput under CPU/memory quotas. The host has no new resource caps.
Cache verification and runtime probes are included in worker time. Test cases are repeated
for the benchmark; these are not ten distinct test-suite shards. No general CI speedup is
claimed until the real experiment has run. No shared-cache security or race-free claim.
