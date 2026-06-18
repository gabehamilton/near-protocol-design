# Simulation Findings — Performance & Economic Impact of ML-DSA-65

What a 250-node / 8-shard NEAR network does under post-quantum adoption.
Five findings, then the numbers behind each. Methodology, full tables, and
assumptions live in [`results.md`](results.md) and [`README.md`](README.md);
this file is the conclusions.

Two recorded runs back every number: a **mainnet-like** load (2000 tps,
~5 Tgas/tx function calls) and a **payments stress** (6000 tps, ~0.5 Tgas/tx).

---

## 1. User-side PQ is a storage/bandwidth problem, not a latency problem

At up to 100% PQ transactions under mainnet-like load, block time never
leaves the 600 ms target — the **gas budget binds long before the byte
budgets**, so a 21×-larger transaction changes nothing on the critical path.
The cost lands entirely on bytes at rest and in flight:

| user PQ | block time | archival growth | node egress |
|---|---|---|---|
| 0% | 601.2 ms | 54.2 GB/day | 17.8 GB/day |
| 100% | 600.9 ms | 947 GB/day (**17×**) | 42.8 GB/day (**2.4×**) |

**Implication:** PQ adoption by users is safe for consensus latency. Plan for
it as a disk-and-bandwidth growth curve, not a throughput regression.

## 2. Validator-key PQ is the consensus-path hit — and the design already blunts it

Moving validator approval/endorsement keys to ML-DSA is the one change that
touches the hot path. Approval payload grows **16.5 KB → 827.8 KB per block**
(250 approvers × 3311 B), blocks reach ~1.6 MB:

| | block time avg/p99 | throughput | inclusion delay | node egress |
|---|---|---|---|---|
| classical validators | 601.2 / 600.0 ms | 1998.7 tps | 0.01 blk | 17.8 GB/day |
| PQ validators | 643.6 / 854.3 ms (+7% / +42%) | ~offered (2001.7 tps) | 0.2 blk | 231.0 GB/day (**13×**) |

Throughput holds at the offered load — the stretch shows up as latency, not
dropped transactions — but the consensus path now carries ~21 Mbit/s-per-node
of pure approval/endorsement traffic, and once the full-PQ case is pushed it
begins to queue (16.8-block inclusion delay at 2000 tps offered). That egress
would be ~37% higher still if approvals carried public keys. They don't: the
surface-4 design keeps validator pubkeys in epoch state and puts only
signatures on the wire. **The single most important performance lever is
already pulled.**

## 3. Capacity loss depends entirely on the traffic mix

Whether PQ costs throughput is decided by what binds the chunk:

| traffic mix | classical | full PQ | change | binding constraint |
|---|---|---|---|---|
| function calls (~5 Tgas) | 2,227 tps | 2,191 tps | **−1.6%** | gas (unchanged) |
| payments (~0.5 Tgas) | 22,274 tps | 5,132 tps | **−77%** | flips gas → **tx-bytes** |

The stress run makes it concrete: classical clears 6000 tps of payments with
zero queueing; 100% PQ saturates at ~5,100 tps with an unbounded, growing
backlog (149-block inclusion delay and climbing), and full PQ falls to
~4,300 tps with a 280-block backlog as block-time stretch compounds the
arrival rate. **Gas-heavy chains barely notice; payment rails are byte-bound
and lose three-quarters of their headroom.**

## 4. Direct fees are negligible; the unpriced byte axis is the real subsidy

The 100 Ggas verify surcharge is **1e-5 NEAR (~$0.000025) per PQ
transaction** — invisible to users. Full-adoption burn is ~1,727 NEAR/day,
roughly 1% of daily issuance. But a PQ transaction consumes **~21× the wire
bytes** of a classical one while paying only for the extra *verification*.
The byte axis is unpriced.

**Implication:** the per-wire-byte surcharge that WS1 deferred
([`size-impact-analysis.md`](../docs/size-impact-analysis.md) §4) should land
**before** PQ adoption is meaningful — otherwise byte-bound congestion
(finding 3) does the pricing through fee-market escalation instead of design.

## 5. Validators absorb it; archival operators bear the largest unfunded cost

| party | extra cost at full PQ | context |
|---|---|---|
| validator (egress) | ~$320–345/mo | ~0.7% of avg per-node reward (~$47k/mo) — absorbable |
| archival operator (storage) | +$616–748/mo | **earns no protocol rewards** |
| user (state staking) | $0 | hash-on-trie split keeps ML-DSA keys at ed25519 cost |

**Implication:** the strongest economic argument for the approval-pruning /
header-commitment options in
[`size-impact-analysis.md`](../docs/size-impact-analysis.md) §1.2 is archival
economics, not validator economics.

---

## Bottom line for rollout

| if you are deciding… | the simulation says… |
|---|---|
| let users adopt PQ keys | safe now — latency-neutral; budget for 17× archival growth |
| migrate validator keys to PQ | feasible (+7% block time) **because** approvals are signature-only; revisit if p99 latency tightens |
| run a payments-heavy chain on PQ | raise byte budgets or expect a 77% capacity cut |
| price the change | ship per-byte tx pricing first; the verify surcharge alone under-charges PQ by ~21× on bytes |
| fund the costs | archival operators, not validators, are the strained party |

None of this is a blocker. The performance risk concentrates in one surface
(validator keys) whose chief mitigation is already in the design, and the
economic risk concentrates in one deferred decision (per-byte pricing) with a
clear deadline (before adoption scales).
