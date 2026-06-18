#!/usr/bin/env python3
"""NEAR post-quantum (ML-DSA-65) network simulation.

Simulates a NEAR-like network of 250 validator nodes and 8 shards at the
per-block level and evaluates the performance and economic impact of the
PostQuantumSignatures change across adoption scenarios:

  - user adoption: fraction of transactions signed with ML-DSA-65
  - validator adoption: validator (approval/endorsement) keys on ML-DSA-65
    (designed in docs/where-ml-dsa-enters.md surface 4; not shipped in WS1)

Grounding (see docs/ and the WS1 nearcore branch, all byte sizes verified
against gabehamilton/nearcore#1):

  - ML-DSA-65 pubkey 1952 B (borsh 1953), signature 3309 B (borsh 3310)
  - ed25519 pubkey 32 B (borsh 33), signature 64 B (borsh 65)
  - approval/endorsement slot: Option<Box<Signature>> = 1 + framed sig
  - verify mean: 80 us ML-DSA vs 32 us ed25519 (WS1 benches, single core)
  - verification surcharge: 100 Ggas per ML-DSA signature (85.yaml)
  - access keys are size-neutral in state (32 B hash-on-trie split)
  - validator pubkeys live in epoch state; approvals carry signatures only
    (docs/where-ml-dsa-enters.md surface-4 design decision)

Everything else (latencies, demand, witness overheads, prices) is an explicit
parameter with the assumption documented next to it.  Stdlib only.

Usage:
  python3 near_pq_sim.py [--blocks 2000] [--seed 7] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from collections import deque
from dataclasses import dataclass, field, asdict

# ---------------------------------------------------------------------------
# Crypto constants (verified against nearcore PR #1)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Scheme:
    name: str
    sig_framed: int       # borsh tag + raw signature, bytes
    pk_framed: int        # borsh tag + raw pubkey, bytes
    verify_us: float      # mean single-core verify, microseconds
    sign_us: float        # mean single-core sign, microseconds

ED25519 = Scheme("ed25519", sig_framed=65, pk_framed=33, verify_us=32.0, sign_us=25.0)
# Verify mean from WS1 benches (Intel Core Ultra 9 185H, flat across signature
# content).  Sign mean is NOT in WS1; ~3-4x verify is typical for hedged
# ML-DSA-65 (rejection sampling) on comparable AVX2 hardware - parameter.
MLDSA65 = Scheme("ml-dsa-65", sig_framed=3310, pk_framed=1953, verify_us=80.0, sign_us=300.0)

# Sanity-pin the WS1 facts this whole model rides on.
assert MLDSA65.sig_framed - 1 == 3309 and MLDSA65.pk_framed - 1 == 1952
APPROVAL_SLOT = {ED25519: 1 + ED25519.sig_framed, MLDSA65: 1 + MLDSA65.sig_framed}  # 66 / 3311

PQ_TX_OVERHEAD = (MLDSA65.pk_framed - ED25519.pk_framed) + (MLDSA65.sig_framed - ED25519.sig_framed)
assert PQ_TX_OVERHEAD == 5165  # extra wire bytes per ML-DSA transaction

# ---------------------------------------------------------------------------
# Simulation parameters (every number here is an assumption knob)
# ---------------------------------------------------------------------------

@dataclass
class Params:
    # --- topology / roles -------------------------------------------------
    nodes: int = 250                  # total validators
    shards: int = 8
    block_producers: int = 100        # block-producer seats (mainnet-like)
    approvers: int = 250              # all validators sign approvals each height
    chunk_validators_per_shard: int = 30   # stateless-validation mandate size
    gossip_fanout: int = 8
    cores_for_crypto: int = 8         # cores a node devotes to sig verification

    # --- timing / network --------------------------------------------------
    block_time_ms: float = 600.0      # mainnet target
    latency_median_ms: float = 40.0   # one-way inter-node latency, lognormal
    latency_sigma: float = 0.5
    bandwidth_bps: float = 1e9        # per-node, NEAR recommended 1 Gbps

    # --- per-chunk budgets (nearcore parameter analogues; assumptions) -----
    chunk_gas_limit_tgas: float = 1000.0      # gas budget per chunk
    chunk_tx_bytes_limit: int = 2 * 1024**2   # combined_transactions_size_limit analogue
    witness_soft_limit: int = 8 * 1024**2     # per-shard state-witness budget

    # --- transaction demand -------------------------------------------------
    offered_tps: float = 2000.0       # network-wide offered load
    tx_body_mean_b: float = 250.0     # classical signed-tx wire size, lognormal mean
    tx_body_sigma: float = 0.5
    tx_gas_mean_tgas: float = 5.0     # attached+burnt gas per tx, lognormal mean
    tx_gas_sigma: float = 0.6
    witness_fixed_b: float = 1.2e6    # state proof + receipts floor per witness
    witness_per_tx_factor: float = 1.5  # witness bytes carried per tx wire byte

    # --- economics ----------------------------------------------------------
    gas_price_near_per_tgas: float = 1e-4   # min gas price: 1e8 yocto/gas
    pq_surcharge_tgas: float = 0.1          # 100 Ggas (85.yaml, verified)
    near_usd: float = 2.50
    egress_usd_per_gb: float = 0.05         # blended cloud egress; bare metal ~0
    storage_usd_per_gb_month: float = 0.023
    total_staked_near: float = 590e6
    annual_inflation: float = 0.05          # max inflation
    validator_reward_share: float = 0.90    # rest to treasury
    total_supply_near: float = 1.26e9

@dataclass(frozen=True)
class Scenario:
    name: str
    user_pq: float        # fraction of txs signed with ML-DSA-65
    validator_pq: bool    # validator approval/endorsement keys on ML-DSA-65

SCENARIOS = [
    Scenario("baseline (all classical)",          0.00, False),
    Scenario("10% PQ transactions",               0.10, False),
    Scenario("50% PQ transactions",               0.50, False),
    Scenario("100% PQ transactions",              1.00, False),
    Scenario("validator keys PQ, classical txs",  0.00, True),
    Scenario("full PQ (txs + validators)",        1.00, True),
]

# ---------------------------------------------------------------------------
# Per-block mechanics
# ---------------------------------------------------------------------------

@dataclass
class Tx:
    bytes_wire: int
    gas_tgas: float
    pq: bool
    arrived_block: int

@dataclass
class BlockStats:
    block_time_ms: float
    txs_included: int
    pq_txs_included: int
    tx_bytes: int
    gas_used_tgas: float
    witness_bytes: list          # per shard
    approval_bytes: int          # header approvals payload
    endorsement_bytes: int       # block-body endorsement signatures
    bp_verify_ms: float          # producer-side sig verification, wall-clock
    backlog: int
    inclusion_delay_blocks: float
    bound: str                   # binding constraint of the fullest shard

def lognormal(rng: random.Random, median: float, sigma: float) -> float:
    return median * math.exp(rng.gauss(0.0, sigma))

def latency_ms(rng: random.Random, p: Params) -> float:
    return min(max(lognormal(rng, p.latency_median_ms, p.latency_sigma), 5.0), 400.0)

def send_ms(nbytes: float, p: Params) -> float:
    """Serialization time for nbytes on one node's link."""
    return nbytes * 8.0 / p.bandwidth_bps * 1000.0

def make_tx(rng: random.Random, p: Params, sc: Scenario, block: int) -> Tx:
    pq = rng.random() < sc.user_pq
    body = lognormal(rng, p.tx_body_mean_b, p.tx_body_sigma)
    gas = min(lognormal(rng, p.tx_gas_mean_tgas, p.tx_gas_sigma), 300.0)
    if pq:
        body += PQ_TX_OVERHEAD
        gas += p.pq_surcharge_tgas
    return Tx(int(body), gas, pq, block)

def fill_chunk(queue: deque, p: Params, block: int):
    """FIFO fill of one shard's chunk under gas, tx-bytes and witness budgets."""
    txs, tx_bytes, gas, witness = [], 0, 0.0, p.witness_fixed_b
    bound = "demand"
    while queue:
        tx = queue[0]
        w = tx.bytes_wire * p.witness_per_tx_factor
        if gas + tx.gas_tgas > p.chunk_gas_limit_tgas:
            bound = "gas"; break
        if tx_bytes + tx.bytes_wire > p.chunk_tx_bytes_limit:
            bound = "tx-bytes"; break
        if witness + tx.bytes_wire + w > p.witness_soft_limit:
            bound = "witness"; break
        queue.popleft()
        txs.append(tx)
        tx_bytes += tx.bytes_wire
        gas += tx.gas_tgas
        witness += tx.bytes_wire + w
    return txs, tx_bytes, gas, int(witness), bound

def simulate(p: Params, sc: Scenario, blocks: int, seed: int) -> dict:
    rng = random.Random(seed)
    vscheme = MLDSA65 if sc.validator_pq else ED25519
    queues = [deque() for _ in range(p.shards)]
    stats: list[BlockStats] = []
    hops = math.ceil(math.log(p.nodes) / math.log(p.gossip_fanout))

    n_endorse = p.shards * p.chunk_validators_per_shard      # endorsement sigs/block
    approval_payload = p.approvers * APPROVAL_SLOT[vscheme]  # header approvals bytes
    endorse_payload = n_endorse * APPROVAL_SLOT[vscheme]     # block-body endorsements

    last_block_time_ms = p.block_time_ms
    for b in range(blocks):
        # -- demand arrives (Poisson per block, split uniformly over shards) --
        # Arrivals scale with the ACTUAL duration of the previous block, not
        # the 600 ms target: when blocks stretch under congestion, real-time tx
        # arrivals keep coming, so more accumulate per (longer) block. Using the
        # constant target here would understate backlog exactly when it matters.
        lam = p.offered_tps * last_block_time_ms / 1000.0
        # Poisson via normal approximation (lam ~ 1200; fine and fast).
        n_arrivals = max(0, int(rng.gauss(lam, math.sqrt(lam)) + 0.5)) if lam > 0 else 0
        for _ in range(n_arrivals):
            queues[rng.randrange(p.shards)].append(make_tx(rng, p, sc, b))

        # -- chunk production per shard --------------------------------------
        included, pq_included, tx_bytes_total, gas_total = [], 0, 0, 0.0
        witness_bytes, bounds, shard_txs = [], [], []
        for s in range(p.shards):
            txs, tb, gas, wit, bound = fill_chunk(queues[s], p, b)
            included.extend(txs)
            pq_included += sum(1 for t in txs if t.pq)
            tx_bytes_total += tb
            gas_total += gas
            witness_bytes.append(wit)
            bounds.append(bound)
            shard_txs.append(txs)

        # -- critical path ----------------------------------------------------
        # Chunk side, computed PER SHARD: the block waits on the slowest chunk,
        # so each shard's witness distribution (parts model: ~2 link traversals
        # of the full witness; nearcore distributes erasure-coded parts which
        # validators re-forward) + chunk-validator verification of THAT shard's
        # own txs + endorsement back to the producer is computed independently,
        # then the max is taken. Verifying the bottleneck shard's actual tx
        # count (not the cross-shard average) keeps verify latency honest under
        # non-uniform Poisson load, where the fullest shard has both the largest
        # witness and the most signatures to check.
        t_chunks = []
        for s in range(p.shards):
            pq_count = sum(1 for t in shard_txs[s] if t.pq)
            classical_count = len(shard_txs[s]) - pq_count
            verify_us = pq_count * MLDSA65.verify_us + classical_count * ED25519.verify_us
            cv_verify_ms = verify_us / p.cores_for_crypto / 1000.0
            t_chunks.append(latency_ms(rng, p) + 2 * send_ms(witness_bytes[s], p)  # witness out
                            + cv_verify_ms + vscheme.sign_us / 1000.0
                            + latency_ms(rng, p) + send_ms(APPROVAL_SLOT[vscheme], p))  # endorse back
        t_chunk = max(t_chunks) if t_chunks else 0.0
        worst_shard = t_chunks.index(t_chunk) if t_chunks else 0

        # Approval side: every approver signs and sends to the next producer;
        # the producer ingests and verifies all of them.
        t_approve = (vscheme.sign_us / 1000.0
                     + latency_ms(rng, p)
                     + send_ms(approval_payload, p))                  # producer ingress
        bp_verify_ms = ((p.approvers + n_endorse) * vscheme.verify_us
                        / p.cores_for_crypto / 1000.0)

        # Block broadcast: header + approvals + endorsements + chunk headers.
        block_bytes = 2000 + approval_payload + endorse_payload + p.shards * 400
        t_block = hops * (latency_ms(rng, p) + send_ms(block_bytes * p.gossip_fanout, p))
        # Every receiving node re-verifies approvals + endorsements.
        recv_verify_ms = bp_verify_ms

        critical = max(t_chunk, t_approve) + bp_verify_ms + t_block + recv_verify_ms
        block_time = max(p.block_time_ms, critical)

        delays = [b - t.arrived_block for t in included]
        stats.append(BlockStats(
            block_time_ms=block_time,
            txs_included=len(included),
            pq_txs_included=pq_included,
            tx_bytes=tx_bytes_total,
            gas_used_tgas=gas_total,
            witness_bytes=witness_bytes,
            approval_bytes=approval_payload,
            endorsement_bytes=endorse_payload,
            bp_verify_ms=bp_verify_ms,
            backlog=sum(len(q) for q in queues),
            inclusion_delay_blocks=statistics.fmean(delays) if delays else 0.0,
            bound=bounds[worst_shard],
        ))
        last_block_time_ms = block_time

    return aggregate(p, sc, stats, vscheme)

# ---------------------------------------------------------------------------
# Aggregation: performance + economics
# ---------------------------------------------------------------------------

def aggregate(p: Params, sc: Scenario, stats: list[BlockStats], vscheme: Scheme) -> dict:
    n = len(stats)
    bt = [s.block_time_ms for s in stats]
    avg_bt_s = statistics.fmean(bt) / 1000.0
    blocks_per_day = 86400.0 / avg_bt_s
    tps = statistics.fmean(s.txs_included for s in stats) / avg_bt_s
    pq_tx_day = statistics.fmean(s.pq_txs_included for s in stats) * blocks_per_day

    # ---- bytes per block, by message class --------------------------------
    appr = stats[0].approval_bytes
    endo = stats[0].endorsement_bytes
    blockb = 2000 + appr + endo + p.shards * 400
    txb = statistics.fmean(s.tx_bytes for s in stats)
    witb = statistics.fmean(sum(s.witness_bytes) for s in stats)

    # Network-wide bytes moved per block (link-level, both directions counted
    # once as sender egress):
    #   approvals: each approver -> next producer (1 traversal)
    #   block gossip: every node forwards to fanout peers once (flood,
    #     duplicate-suppressed): ~nodes traversals of block_bytes
    #   tx gossip: ~2 traversals to reach the right chunk producer
    #   witness: ~2 traversals per shard witness (parts + forwarding)
    #   endorsements: chunk validators -> producer (1 traversal)
    net_block = (appr
                 + blockb * p.nodes
                 + txb * 2
                 + witb * 2
                 + endo)
    egress_node_gb_day = net_block * blocks_per_day / p.nodes / 1e9
    # The block producer of a height eats the burst: approvals + endorsements
    # ingress plus fanout-out of the block.
    bp_burst_mb = (appr + endo + blockb * p.gossip_fanout) / 1e6

    # ---- archival growth ---------------------------------------------------
    archive_gb_day = (blockb + txb + witb * 0.0) * blocks_per_day / 1e9
    # (witnesses are not archived; headers+approvals+endorsements+txs are)

    # ---- economics ----------------------------------------------------------
    surcharge_near_day = pq_tx_day * p.pq_surcharge_tgas * p.gas_price_near_per_tgas
    user_extra_per_tx_near = p.pq_surcharge_tgas * p.gas_price_near_per_tgas

    egress_usd_mo = egress_node_gb_day * 30 * p.egress_usd_per_gb
    archive_usd_mo_added = 0.0  # filled in relative to baseline by caller
    rewards_near_yr = p.total_staked_near * 0  # informational only
    minted_to_validators = p.total_supply_near * p.annual_inflation * p.validator_reward_share
    avg_reward_node_usd_mo = minted_to_validators / p.nodes / 12 * p.near_usd

    epoch_state_b = int(sc.validator_pq) * p.nodes * (MLDSA65.pk_framed - ED25519.pk_framed)

    return {
        "scenario": sc.name,
        "user_pq": sc.user_pq,
        "validator_pq": sc.validator_pq,
        "perf": {
            "avg_block_time_ms": round(statistics.fmean(bt), 1),
            "p99_block_time_ms": round(sorted(bt)[int(0.99 * n) - 1], 1),
            "blocks_stretched_pct": round(100 * sum(1 for x in bt if x > p.block_time_ms + 1e-9) / n, 2),
            "throughput_tps": round(tps, 1),
            "avg_inclusion_delay_blocks": round(statistics.fmean(s.inclusion_delay_blocks for s in stats), 2),
            "end_backlog_txs": stats[-1].backlog,
            "binding_constraint": statistics.mode(s.bound for s in stats),
            "bp_sig_verify_ms_per_block": round(stats[0].bp_verify_ms, 2),
        },
        "bytes": {
            "approvals_per_block_kb": round(appr / 1e3, 1),
            "endorsements_per_block_kb": round(endo / 1e3, 1),
            "block_kb": round(blockb / 1e3, 1),
            "avg_tx_bytes_per_block_kb": round(txb / 1e3, 1),
            "avg_witness_per_block_kb": round(witb / 1e3, 1),
            "witness_p99_per_shard_mb": round(sorted(w for s in stats for w in s.witness_bytes)[int(0.99 * n * p.shards) - 1] / 1e6, 2),
            "node_egress_gb_day": round(egress_node_gb_day, 2),
            "bp_burst_mb_per_block": round(bp_burst_mb, 2),
            "archive_growth_gb_day": round(archive_gb_day, 1),
            "epoch_state_added_kb": round(epoch_state_b / 1e3, 1),
        },
        "econ": {
            "pq_surcharge_burn_near_day": round(surcharge_near_day, 2),
            "user_extra_fee_per_pq_tx_near": user_extra_per_tx_near,
            "node_egress_usd_month": round(egress_usd_mo, 2),
            "archive_growth_usd_month_added": archive_usd_mo_added,
            "avg_reward_per_node_usd_month": round(avg_reward_node_usd_mo),
        },
    }

# ---------------------------------------------------------------------------
# Capacity analysis (closed-form, no Monte Carlo): max sustainable tx/s
# ---------------------------------------------------------------------------

def capacity(p: Params, user_pq: float, gas_mean_tgas: float | None = None, mix: str = "mainnet mix") -> dict:
    gas_mean = gas_mean_tgas if gas_mean_tgas is not None else p.tx_gas_mean_tgas
    avg_bytes = p.tx_body_mean_b * math.exp(p.tx_body_sigma**2 / 2) + user_pq * PQ_TX_OVERHEAD
    avg_gas = gas_mean * math.exp(p.tx_gas_sigma**2 / 2) + user_pq * p.pq_surcharge_tgas
    per_shard_per_block = {
        "gas": p.chunk_gas_limit_tgas / avg_gas,
        "tx-bytes": p.chunk_tx_bytes_limit / avg_bytes,
        "witness": (p.witness_soft_limit - p.witness_fixed_b) / (avg_bytes * (1 + p.witness_per_tx_factor)),
    }
    bind = min(per_shard_per_block, key=per_shard_per_block.get)
    cap = per_shard_per_block[bind] * p.shards / (p.block_time_ms / 1000.0)
    return {"mix": mix, "user_pq": user_pq, "capacity_tps": round(cap), "bound": bind,
            "per_constraint_tps": {k: round(v * p.shards / (p.block_time_ms / 1000.0)) for k, v in per_shard_per_block.items()}}

# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def relative_costs(results: list[dict], p: Params) -> None:
    """Fill deltas vs the baseline scenario in-place."""
    base = results[0]
    for r in results:
        r["econ"]["archive_growth_usd_month_added"] = round(
            (r["bytes"]["archive_growth_gb_day"] - base["bytes"]["archive_growth_gb_day"])
            * 30 * p.storage_usd_per_gb_month, 2)
        extra_egress = r["econ"]["node_egress_usd_month"] - base["econ"]["node_egress_usd_month"]
        r["econ"]["node_extra_cost_usd_month_vs_baseline"] = round(extra_egress, 2)
        r["econ"]["extra_cost_pct_of_avg_reward"] = round(
            100 * extra_egress / r["econ"]["avg_reward_per_node_usd_month"], 3)

def fmt_table(rows: list[list[str]]) -> str:
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    out = []
    for j, r in enumerate(rows):
        out.append("  ".join(c.ljust(w) for c, w in zip(r, widths)))
        if j == 0:
            out.append("  ".join("-" * w for w in widths))
    return "\n".join(out)

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--blocks", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--json", type=str, default=None)
    ap.add_argument("--tps", type=float, default=None, help="offered load override")
    ap.add_argument("--gas-mean", type=float, default=None, help="mean Tgas per tx override")
    args = ap.parse_args()

    p = Params()
    if args.tps is not None:
        p.offered_tps = args.tps
    if args.gas_mean is not None:
        p.tx_gas_mean_tgas = args.gas_mean
    results = [simulate(p, sc, args.blocks, args.seed) for sc in SCENARIOS]
    relative_costs(results, p)
    caps = [capacity(p, f) for f in (0.0, 0.1, 0.5, 1.0)]
    # Minimal-transfer mix (~0.5 Tgas/tx): the byte-bound regime described in
    # docs/size-impact-analysis.md section 2.
    caps += [capacity(p, f, gas_mean_tgas=0.5, mix="minimal transfers") for f in (0.0, 1.0)]

    print(f"NEAR PQ simulation - {p.nodes} nodes, {p.shards} shards, "
          f"{args.blocks} blocks/scenario, offered load {p.offered_tps:.0f} tps\n")

    rows = [["scenario", "block ms (avg/p99)", "tps", "incl delay", "bound",
             "BP verify ms", "approvals KB", "egress GB/d", "archive GB/d"]]
    for r in results:
        rows.append([
            r["scenario"],
            f'{r["perf"]["avg_block_time_ms"]}/{r["perf"]["p99_block_time_ms"]}',
            f'{r["perf"]["throughput_tps"]}',
            f'{r["perf"]["avg_inclusion_delay_blocks"]}',
            r["perf"]["binding_constraint"],
            f'{r["perf"]["bp_sig_verify_ms_per_block"]}',
            f'{r["bytes"]["approvals_per_block_kb"]}',
            f'{r["bytes"]["node_egress_gb_day"]}',
            f'{r["bytes"]["archive_growth_gb_day"]}',
        ])
    print(fmt_table(rows))

    print("\nCapacity (closed-form max sustainable tps, by binding constraint):")
    rows = [["tx mix", "user PQ fraction", "capacity tps", "bound", "gas-cap", "byte-cap", "witness-cap"]]
    for c in caps:
        rows.append([c["mix"], f'{c["user_pq"]:.0%}', f'{c["capacity_tps"]}', c["bound"],
                     f'{c["per_constraint_tps"]["gas"]}',
                     f'{c["per_constraint_tps"]["tx-bytes"]}',
                     f'{c["per_constraint_tps"]["witness"]}'])
    print(fmt_table(rows))

    print("\nEconomics (deltas vs baseline where marked):")
    rows = [["scenario", "PQ burn NEAR/d", "user fee/PQ-tx", "node egress $/mo",
             "extra $/mo vs base", "% of avg reward", "archive +$/mo"]]
    for r in results:
        e = r["econ"]
        rows.append([
            r["scenario"],
            f'{e["pq_surcharge_burn_near_day"]}',
            f'{e["user_extra_fee_per_pq_tx_near"]:.0e} N',
            f'{e["node_egress_usd_month"]}',
            f'{e["node_extra_cost_usd_month_vs_baseline"]}',
            f'{e["extra_cost_pct_of_avg_reward"]}',
            f'{e["archive_growth_usd_month_added"]}',
        ])
    print(fmt_table(rows))

    if args.json:
        with open(args.json, "w") as f:
            json.dump({"params": asdict(p), "results": results, "capacity": caps}, f, indent=2)
        print(f"\nwrote {args.json}")

if __name__ == "__main__":
    main()
