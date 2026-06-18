# Simulation Results — Performance & Economic Impact of ML-DSA-65

250 nodes, 8 shards, 2000 blocks per scenario, seed 7. Two recorded runs:
**mainnet-like load** (2000 tps offered, ~5 Tgas/tx → `results.json`) and a
**payments stress** (6000 tps offered, ~0.5 Tgas/tx → `results_stress.json`).
Model and assumptions: [`README.md`](README.md).

## Headline findings

1. **User-side PQ adoption is a storage/bandwidth problem, not a latency
   problem.** Even at 100% PQ transactions, block times stay at the 600 ms
   target under mainnet-like load — the gas budget binds long before the
   byte budgets. The cost surfaces as bytes: archival growth 54 → 947
   GB/day (17×) and per-node egress 18 → 43 GB/day.
2. **Validator-key PQ is the consensus-path hit.** Approval payload grows
   16.5 KB → 827.8 KB per block (250 × 3311 B) and endorsements similarly,
   making a ~1.6 MB block. Average block time stretches 601 → 644 ms
   (+7%), p99 ≈ 854 ms, and per-node egress jumps ~13× (18 → 231 GB/day) —
   sustained ~21 Mbit/s per node just for consensus messages. Throughput
   holds at the offered load (stretch appears as latency, not dropped txs),
   but the full-PQ case begins to queue under load (16.8-block inclusion
   delay at 2000 tps offered). This is the surface the
   where-ml-dsa-enters.md design keeps off the wire wherever possible
   (signature-only approvals).
3. **Capacity depends entirely on the traffic mix.** Function-call traffic
   (~5 Tgas) is gas-bound: full PQ costs only 1.6% capacity (2227 → 2191
   tps). Payments traffic (~0.5 Tgas) is byte-bound under PQ: capacity
   falls 22,274 → 5,132 tps (−77%) and the binding constraint flips from
   gas to `tx-bytes` — confirming the size-impact doc's analysis. In the
   stress run, classical clears 6000 tps with zero queueing while 100% PQ
   saturates at ~5,100 tps with an unbounded backlog (149-block average
   inclusion delay and climbing); full PQ falls to ~4,300 tps with a
   280-block backlog as block-time stretch compounds the arrival rate.
4. **Direct economic effects on users and the protocol are negligible; the
   indirect ones are not.** The 100 Ggas surcharge is 1e-5 NEAR
   (~$0.000025) per PQ transaction; full-adoption extra burn is ~1,728
   NEAR/day (~1% of daily issuance). But a PQ transaction consumes ~21× the
   wire bytes of a classical one while paying only the verify surcharge —
   the unpriced byte axis is a real subsidy, and the deferred per-byte
   pricing in size-impact-analysis.md §4 is where it must be recovered.
5. **Validator economics hold; archival economics strain.** Extra egress at
   full PQ is ~$320–345/mo per validator on cloud pricing — ~0.7% of the
   average per-node reward (~$47k/mo), absorbable. Archival operators,
   who earn no protocol rewards, absorb +$616–748/mo of storage growth at
   full adoption — the strongest economic argument for the approval-pruning
   options flagged in size-impact-analysis.md §1.2.

## Mainnet-like load (2000 tps offered, ~5 Tgas/tx)

| scenario | block ms avg/p99 | tps | incl delay (blk) | bound | BP verify ms | approvals KB | egress GB/d | archive GB/d |
|---|---|---|---|---|---|---|---|---|
| baseline (all classical) | 601.2 / 600.0 | 1998.7 | 0.01 | demand | 1.96 | 16.5 | 17.8 | 54 |
| 10% PQ transactions | 600.6 / 600.0 | 1999.5 | 0.01 | demand | 1.96 | 16.5 | 20.3 | 144 |
| 50% PQ transactions | 600.4 / 600.0 | 1998.5 | 0.01 | demand | 1.96 | 16.5 | 30.3 | 501 |
| 100% PQ transactions | 600.9 / 620.1 | 2000.5 | 0.02 | demand | 1.96 | 16.5 | 42.8 | 947 |
| validator keys PQ, classical txs | 643.6 / 854.3 | 2001.7 | 0.2 | gas | 4.90 | 827.8 | 231.0 | 267 |
| full PQ (txs + validators) | 665.0 / 894.0 | 1967.6 | 16.8 | gas | 4.90 | 827.8 | 248.2 | 1138 |

Producer-side burst with PQ validators: ~14 MB ingress+egress per block
(approvals + endorsements in, block × fanout out) inside a 600 ms slot —
within a 1 Gbps link's envelope (~75 MB/600 ms) but now a first-order term.
The inclusion-delay column reflects the arrival-rate feedback added after
review: when a block stretches past 600 ms, real-time transactions keep
arriving, so the backlog under stress is no longer hidden by a constant
arrival assumption.

## Capacity (closed-form, max sustainable tps)

| tx mix | user PQ | capacity tps | bound | gas-cap | byte-cap | witness-cap |
|---|---|---|---|---|---|---|
| mainnet mix (~5 Tgas) | 0% | **2,227** | gas | 2,227 | 98,706 | 135,337 |
| mainnet mix | 100% | **2,191** (−1.6%) | gas | 2,191 | 5,132 | 7,037 |
| minimal transfers (~0.5 Tgas) | 0% | **22,274** | gas | 22,274 | 98,706 | 135,337 |
| minimal transfers | 100% | **5,132** (−77%) | **tx-bytes** | 19,086 | 5,132 | 7,037 |

## Payments stress (6000 tps offered, ~0.5 Tgas/tx)

| scenario | tps achieved | inclusion delay (blocks) | bound |
|---|---|---|---|
| baseline (all classical) | 6,001 | 0.0 | demand |
| 100% PQ transactions | 5,108 | 149.4 (growing) | tx-bytes |
| full PQ (txs + validators) | 4,312 | 280.5 (growing) | tx-bytes |

The PQ scenarios are past saturation: the queue grows without bound, which
in a real network means fee-market escalation rather than infinite delay —
i.e., PQ payments traffic above ~5,100 tps prices itself out unless the
byte budgets (`combined_transactions_size_limit`, witness limits) are
raised, which in turn raises witness-distribution bandwidth.

## Economics

| scenario | PQ burn NEAR/d | user fee per PQ tx | node egress $/mo | Δ vs base $/mo | % of avg reward | archive Δ $/mo |
|---|---|---|---|---|---|---|
| baseline | 0 | — | 26.73 | 0 | 0 | 0 |
| 10% PQ txs | 173 | 1e-5 N | 30.51 | +3.78 | 0.008% | +62 |
| 50% PQ txs | 864 | 1e-5 N | 45.50 | +18.77 | 0.04% | +308 |
| 100% PQ txs | 1,728 | 1e-5 N | 64.24 | +37.51 | 0.08% | +616 |
| validator keys PQ | 0 | — | 346.57 | +319.84 | 0.68% | +147 |
| full PQ | 1,700 | 1e-5 N | 372.34 | +345.61 | 0.73% | +748 |

Other ledger items:

- **Storage staking: zero user-side delta.** The hash-on-trie split keeps an
  ML-DSA access key at the same 0.00082 NEAR stake as ed25519 (verified in
  WS1) — the simulation carries no per-user state inflation at all.
- **Epoch state:** PQ validator keys add 250 × 1920 B = 480 KB per epoch
  record. Negligible.
- **Burn vs issuance:** full-adoption surcharge burn (~1.7k NEAR/day) offsets
  ~1% of daily validator issuance (~173k NEAR/day) — a real but small
  deflationary nudge.

## What this changes in the design docs

- Confirms size-impact-analysis.md §2 quantitatively, and sharpens it: the
  often-quoted "~50× approval bandwidth" understates the archival effect at
  250 approvers (16.5 → 828 KB/block is the right number for this topology).
- The dominant *performance* risk is validator-key migration, and the
  dominant lever is already in the design (signature-only approvals; pubkeys
  in epoch state). The next lever, if p99 block time matters, is approval
  pruning / header-commitment restructuring (size doc §1.2).
- The dominant *economic* risk is unpriced transaction bytes under PQ
  payments traffic — the per-wire-byte surcharge deferred in WS1 should land
  **before** PQ adoption is meaningful, or byte-bound saturation does the
  pricing via congestion instead.
