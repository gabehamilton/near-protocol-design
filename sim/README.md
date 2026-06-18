# NEAR PQ Network Simulation — 250 nodes, 8 shards

A per-block Monte-Carlo simulation of a NEAR-like network evaluating the
performance and economic impact of the ML-DSA-65 (PostQuantumSignatures)
change. Companion to the analytical model in
[`../docs/size-impact-analysis.md`](../docs/size-impact-analysis.md);
results and interpretation in [`results.md`](results.md).

```sh
python3 near_pq_sim.py                          # 2000 blocks/scenario, 2000 tps offered
python3 near_pq_sim.py --json results.json      # + machine-readable output
python3 near_pq_sim.py --tps 6000 --gas-mean 0.5  # payments-style stress run
```

Stdlib only, deterministic per `--seed`. Runs all six scenarios in ~5 s.

## What is modeled

- **Topology/roles:** 250 validators, 8 shards, 100 block-producer seats, all
  250 sign approvals each height, 30 chunk validators per shard (stateless
  validation), gossip fanout 8, 1 Gbps links, lognormal one-way latency
  (median 40 ms).
- **Per height:** Poisson tx arrivals → per-shard FIFO queues → chunk fill
  under three budgets (gas 1000 Tgas, tx bytes 2 MiB, witness 8 MiB) →
  witness distribution to chunk validators (parts model ≈ 2 link traversals)
  → chunk-validator signature verification + endorsement → approvals from
  all 250 to the next producer → block assembly (header + approval slots +
  endorsement signatures) → flood gossip (3 hops) with re-verification at
  every node. Block time = max(600 ms target, critical path).
- **Scenarios:** user PQ fraction ∈ {0, 10%, 50%, 100%} × validator keys
  classical/PQ.
- **Crypto numbers** are the WS1-verified facts (sizes asserted in code
  against the nearcore PR #1 constants): sig 3310 B framed vs 65 B, pk
  1953 B vs 33 B, approval slot 3311 B vs 66 B, verify 80 µs vs 32 µs,
  100 Ggas verify surcharge. Approvals carry signatures only — validator
  pubkeys live in epoch state (the surface-4 design decision).
- **Economics:** min gas price (1e-4 NEAR/Tgas), PQ surcharge burn, user fee
  delta, per-node egress priced at $0.05/GB (cloud blended; bare metal ~0),
  archival growth at $0.023/GB-mo, rewards from 5% inflation × 90% to
  validators over 250 nodes, NEAR at $2.50.

A closed-form **capacity** analysis (no Monte Carlo) computes max
sustainable tps per binding constraint (gas / tx-bytes / witness) for two
traffic mixes: "mainnet mix" (~5 Tgas/tx, function calls) and "minimal
transfers" (~0.5 Tgas/tx, payments).

## Deliberate simplifications

This is a systems-level model, not a packet simulator. Known abstractions,
all chosen to bias **against** hiding PQ costs:

- All 250 validators approve every height (worst case; if only the 100 BP
  seats approve, divide approval numbers by 2.5).
- Flood gossip with duplicate suppression ≈ every node forwards the block
  once; nearcore's partial-encoding distribution is cheaper.
- Witness distribution ≈ 2 full-witness link traversals (parts +
  forwarding), not per-part scheduling.
- No Doomslug skips/forks; block time stretches instead of skipping.
- ML-DSA sign time (300 µs) is a literature-typical estimate, not
  WS1-measured (WS1 measured verify only). Sign time is off the critical
  path here (one sign per node per height) so the result is insensitive to it.
- Demand is open-loop: no fee-market feedback throttling offered load when
  queues grow (saturation therefore shows as backlog, which is the honest
  signal).
- Latency is one global distribution; no geographic clustering.

## Files

- `near_pq_sim.py` — the simulation (parameters documented inline)
- `results.json`, `results_stress.json` — outputs of the recorded runs
- `results.md` — results tables + interpretation
