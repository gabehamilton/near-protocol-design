# Size Impact Analysis — ML-DSA-65 in NEAR

**Status:** design (Workstream 2)
**Scope:** quantify where multi-KB signatures bite: state, block
propagation, light clients, and gas/fee ripple points. **We note pricing
implications; we do not reprice** — gas/cost-model reparameterization is
deferred rollout work.

Inputs (WS1 / FIPS 204 ML-DSA-65, borsh-framed sizes in parentheses):
pubkey 1952 B (1953), signature 3309 B (3310), on-trie handle 32 B (33);
ed25519 pubkey 32 B (33), signature 64 B (65). Verify mean ≈ 80 µs vs
≈ 32 µs ed25519, flat across signature content (WS1 benches; tail is
scheduler noise, not attacker-controllable).

Model parameters used below — adjust to taste, formulas are linear:

- `V` = block approvers per block, worked example **V = 100** (≈ mainnet
  block-producer seats).
- `f` = fraction of validators using ML-DSA keys, worked examples f = 1
  (full migration) and f = 0.1.
- block time **600 ms** (mainnet target).
- mainnet epoch length **12 h** (~730 epochs/year).

---

## 1. State size

### 1.1 Access keys — mitigated by the hash-on-trie split

The trie stores a 33-byte handle for an ML-DSA access key — byte-identical
footprint to ed25519. Storage stake per full-access key ≈ **0.00082 NEAR**,
same as ed25519, versus ≈ 0.0200 NEAR had the raw 1952 B key been stored
(~24× saved, WS1 §4). The access-key population — the only key set that
scales with *users* — is therefore size-neutral. No action needed.

### 1.2 What still stores or carries full material

| Place | What | Size | Persistence |
|---|---|---|---|
| Epoch validator info | full validator pubkeys (surface 4 design: approvals don't carry keys, so state must) | 1952 B × V·f ≈ **195 KB/epoch record** at V=100, f=1 | per epoch; negligible |
| Delayed/buffered receipts | `AddKey` actions (1953 B pk), `SignedDelegateAction` inner pk+sig (~5.3 KB) | per pending receipt | transient state, but counts toward witness and congestion-control byte budgets |
| Block store: headers | approvals, 3310 B × V·f per block | §2 — this is the big one | forever on archival nodes |
| Block store: chunks | transactions incl. 3310 B sigs + 1953 B pks | proportional to PQ tx volume | forever on archival nodes |

**Archival growth is approval-dominated.** At V=100, f=1, 600 ms blocks,
headers grow by ≈ 331 KB/block ⇒ ≈ **48 GB/day ≈ 17 TB/year** of approval
bytes alone (vs ≈ 0.35 TB/year for ed25519). This does not affect consensus
or validator working set (recent blocks only) but dominates archival
economics. Mitigation options to evaluate in rollout design (deferred, note
only): prune approvals from non-epoch-boundary headers after finality
(light clients only need epoch-boundary approval sets), or move approvals
behind a header commitment into a pruneable column.

---

## 2. Block propagation bandwidth — the multiplier

Per-block approval payload in the header (`Vec<Option<Box<Signature>>>`,
66 B classical slot, 3311 B PQ slot):

```
approval_bytes(V, f) = V · (66 + f · 3245)
```

| scenario | per block | per second (600 ms) | per day | vs all-ed25519 |
|---|---|---|---|---|
| V=100, f=0 | 6.6 KB | 11 KB/s | 0.95 GB | 1× |
| V=100, f=0.1 | 39 KB | 65 KB/s | 5.6 GB | ~6× |
| V=100, f=1 | 331 KB | 552 KB/s ≈ **4.4 Mbit/s** | 47.7 GB | **~50×** |

Notes:

- This is per *propagation link*; gossip fan-out multiplies the node's
  egress accordingly. A validator with 8–16 active block-forwarding peers
  is looking at tens of Mbit/s sustained for approvals alone at f=1.
- Approvals are also sent point-to-point to the next block producer before
  inclusion — the producer's ingress sees the same V·f·3310 per block
  ahead of everyone else; at 600 ms blocks the producer must absorb
  ≈ 331 KB and verify it inside the slot.
- **Keys are not in this number** by design (surface-4 decision: pubkeys
  live in epoch state, approvals carry signatures only). Carrying keys
  per-approval would add 1953 B × V·f ≈ +195 KB/block (+59%) — this is
  what the design decision buys.
- Chunk/endorsement-style messages that carry validator signatures scale
  the same way; any new consensus message should be byte-budgeted against
  this table.

**Verify CPU on the hot path:** every node verifies V·f approvals per
block: 100 × 80 µs = **8 ms** single-core at f=1 (1.3% of the 600 ms slot)
vs 3.2 ms ed25519 — and ed25519 has practical batch verification (~2×)
while **ML-DSA has none**, so the gap in practice is closer to 5×. Bounded
and affordable, but it consumes slot-time budget that block-time reductions
would otherwise claim.

**Transactions per chunk:** a minimal transfer is ≈ 185 B signed with
ed25519 and ≈ 5.4 KB with ML-DSA (~29×). Under fixed byte budgets
(`max_transaction_size`, `combined_transactions_size_limit` — which counts
full `wire_size()` including the signature from `PostQuantumSignatures`
onward, WS1) a chunk/witness holds ~25–30× fewer minimal PQ transactions.
Throughput for PQ-heavy traffic is byte-bound, not gas-bound.

---

## 3. Light-client verification cost

Per epoch-boundary `LightClientBlockView` at V=100:

| component | ed25519 | ML-DSA (f=1) |
|---|---|---|
| `approvals_after_next` | 6.6 KB | **331 KB** |
| `next_bps` pubkeys | 3.3 KB | **195 KB** |
| signatures verified (≥⅔ stake, 67–100 sigs) | 2.1–3.2 ms | **5.4–8 ms** (mean, single core) |
| per year (730 epochs), bandwidth | ≈ 7 MB | ≈ **384 MB** |

- For a phone or desktop client this is acceptable: ~0.5 MB and <10 ms per
  12-hour epoch.
- For an **on-chain bridge light client** (NEAR client as a contract on a
  foreign chain) it is the binding constraint: ~331 KB of calldata and
  67–100 lattice verifications per epoch in a foreign VM. This likely
  forces succinct-proof wrapping (zk-proof of the approval set) or a
  dedicated precompile on the host chain — flagged as rollout-adjacent
  design work, out of scope here, but the acceptance rule already
  guarantees the statement such a proof must encode is exactly the
  canonical rule of `verification-consistency.md` §3.
- Memory floor: holding an epoch's validator keys is 195 KB (vs 3.3 KB) —
  irrelevant on phones, relevant in contract storage.

---

## 4. Gas / fee ripple points — note, don't reprice

Already priced or accounted (WS1, listed for completeness):

- **Verify CPU:** `ml_dsa_65_verification_cost` = 100 Ggas added to burnt
  gas at tx conversion, per signature including delegate inner signers.
- **Storage staking:** uses `trie_id_len()` (33 B), so access-key staking
  is already correct; any new storage-cost call site must use
  `trie_id_len()`, not `len()` (WS1 caveat 2).
- **Size accounting:** `wire_size()` (signature included) feeds
  `max_transaction_size` / `combined_transactions_size_limit` gates and
  the mempool — accounted, not priced.

Ripple points where pricing pressure exists but repricing is deferred:

1. **Per-wire-byte surcharge at conversion** for the ~5.2 KB tx overhead —
   the natural mirror of the verify-cost mechanism (WS1 explicitly defers
   this).
2. **`AddKey`/`DeleteKey` per-byte component** for the 1953 B pubkey the
   action carries on the wire and through receipts.
3. **Receipt size costs** where `SignedDelegateAction` nests inner pk+sig
   (~5.3 KB) into a receipt that transits congestion-control byte budgets.
4. **Consensus overhead is not gas-denominated:** approval bandwidth and
   verify CPU (§2) are paid by validators outside the gas market entirely;
   if f grows large, this is a validator-economics input (hardware/network
   requirements), not a fee-schedule input.
5. **Future host function** (`ml_dsa_verify`, if ever added) needs its own
   gas cost derived from the same benches; the 100 Ggas conversion charge
   is not reusable as-is.

The unifying observation: WS1 priced the *CPU* axis and accounted the
*byte* axis; every deferred item above is the byte axis acquiring a price.
Nothing here blocks the protocol design — but rollout must not flip
`PostQuantumSignatures` on a fee schedule that assumes 65-byte signatures
are the marginal case.
