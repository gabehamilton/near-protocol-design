# Where ML-DSA Enters the NEAR Protocol — Surface Map

**Status:** design (Workstream 2)
**Scope:** every point at which an ML-DSA-65 key or signature enters the
protocol, what changes there, and which points sit on a consensus- or
latency-critical path. Sizes and grounding facts come from WS1
(`gabehamilton/nearcore`, branch `pq-libcrux-variation`); the normative
acceptance rule for *all* of these surfaces is
[`verification-consistency.md`](verification-consistency.md).

Byte sizes used throughout (WS1, FIPS 204 ML-DSA-65):

| object | raw | borsh-framed | classical (ed25519) framed |
|---|---|---|---|
| public key | 1952 B | 1953 B (tag 2) | 33 B |
| signature | 3309 B | 3310 B (tag 2) | 65 B |
| on-trie key id (`PublicKeyHandle`) | 32 B hash | 33 B (tag 3) | 33 B |

## Summary table

| # | Surface | Producer | Verifier(s) | Bytes live | Critical path | Status |
|---|---------|----------|-------------|------------|---------------|--------|
| 1 | Account access keys (`AddKey`/`DeleteKey`, trie storage) | account owner | runtime (action validation) | wire: full pk in action; state: 33 B handle | chunk application / state witness | **shipped (WS1, pv 85)** |
| 2 | Transaction signing | account owner | every chunk producer & validator at tx conversion | wire: pk 1953 B in tx body + sig 3310 B | chunk production & validation, witness | **shipped (WS1, pv 85)** |
| 3 | DelegateActions / meta-transactions (inner signer) | end user (inner), relayer (outer) | runtime at receipt processing | wire: inner pk + inner sig inside action | chunk application | **shipped (WS1, pv 85)** |
| 4 | Validator block production & approvals | block/chunk producers, approvers | every node on block receipt; Doomslug | block header `approvals`, chunk header sig | **consensus hot path** | designed here, not shipped |
| 5 | Light-client proofs | validators (indirectly) | light clients off-chain | `LightClientBlockView.approvals_after_next` | client critical path (off-chain) | designed here, not shipped |
| 6 | Implicit accounts (PQ) | n/a | n/a | n/a | n/a | out of scope; precedent noted |
| 7 | Contract host functions | n/a | WASM runtime | n/a | n/a | out of scope (no `ml_dsa_verify` host fn) |

---

## 1. Account access keys

**What changes.** `AddKey` may register an ML-DSA-65 key once
`PostQuantumSignatures` is active. The trie never stores the pubkey: the
access-key trie entry is keyed by
`[tag 3] ‖ SHA3-256(b"near:ml-dsa-65-pubkey-hash:v1" ‖ pk)` — 33 bytes, the
same on-trie footprint as ed25519. The full key exists only on the wire.

- **Produces:** the account owner signs the `AddKey`-carrying transaction;
  the action body carries the full 1953 B pubkey.
- **Verifies:** the runtime's action validation (`validate_add_key_action`),
  gated by the protocol feature. No signature by the *new* key is verified
  at registration — only the registering transaction's signature.
- **Bytes:** wire + receipt only (a delayed receipt holds the 1953 B action
  in state transiently until applied); trie holds 33 B forever.
- **Consequences of the hash split:**
  - A `PublicKeyHandle` cannot verify. Any path that reads an access key
    from state and needs to verify must get the full pk from the wire and
    check the hash binding (acceptance rule §3.5).
  - `view_access_key_list` returns `ml-dsa-65-hash:<bs58>`; wallets must
    recompute their own handle (public helper exists in WS1) to reconcile.
  - Tooling that reconstructs keys from state (mirror, fork-network)
    cannot recover the pubkey from the handle; WS1 leaves
    `TODO(post-quantum)` markers where keys are currently dropped. Any
    future design needing state→pubkey must add an explicit pk registry —
    do not weaken the hash split to get it.

## 2. Transaction signing

**What changes.** `SignedTransaction` may carry an ML-DSA-65 signer: the
`Transaction` body's `public_key` field is the full 1953 B key and the outer
signature is 3310 B — ~5.2 KB of overhead versus ~100 B classical.

- **Produces:** the account owner. Message `M` = the 32-byte SHA-256 digest
  of the borsh-serialized `Transaction` (unchanged framing; ML-DSA pure
  mode, empty context, applied to that digest).
- **Verifies:** every chunk producer at transaction conversion, and every
  chunk validator re-applying the chunk from the state witness. The
  signature must verify *and* the pubkey must hash-match the on-trie access
  key handle.
- **Bytes:** wire and mempool (mempool counts full `wire_size()` per WS1);
  the tx, signature included, rides in chunks and therefore in the state
  witness from `PostQuantumSignatures` onward.
- **Critical path:** chunk production/validation throughput — both byte
  budgets (`max_transaction_size`, `combined_transactions_size_limit`) and
  verify CPU (~80 µs mean vs ~32 µs ed25519, WS1 benches; priced at
  100 Ggas via `ml_dsa_65_verification_cost`).

## 3. DelegateActions / meta-transactions

**What changes.** A `SignedDelegateAction` carries an *inner* signer — full
pk + signature — nested inside the relayer's outer transaction. With both
layers ML-DSA the object carries 2 × (1953 + 3310) ≈ 10.5 KB of key/sig
material.

- **Produces:** the end user signs the inner `DelegateAction`; the relayer
  signs the enclosing transaction.
- **Verifies:** the runtime verifies the inner signature at receipt
  processing (`validate_delegate_action`), gated; the outer signature is
  surface 2. The inner verification is charged to the transaction's burnt
  gas exactly like an outer signature (WS1 §6).
- **Divergence note:** the inner signature is verified by the runtime *on
  chain*, so it is consensus-critical in the same way as surface 2 —
  acceptance-rule conformance applies identically to the nested case.

## 4. Validator block production & approvals — the consensus-critical path

**Not shipped in WS1** (`is_valid_staking_key` still rejects ML-DSA-65);
this is the design for it.

**What changes.** Validator keys sign, per block: block-producer header
signatures, chunk-producer header signatures, and Doomslug approvals
(endorsements/skips) from every block approver. Approvals are sent to the
next producer and embedded in the next block header
(`approvals: Vec<Option<Signature>>`, one slot per approver in epoch order).

- **Produces:** every validator, every block. Highest-frequency signing
  surface in the protocol.
- **Verifies:** the next block producer (on approval receipt) and **every
  node** on block receipt — approvals are checked during header validation
  before the block is accepted. This is the consensus hot path: verify
  latency here is in the block-time budget, and any accept/reject
  divergence here is a chain split, not a stalled transaction.
- **Bytes:** block header grows by ~3310 B per PQ approver (vs 66 B); see
  [`size-impact-analysis.md`](size-impact-analysis.md) for the model.

**Design decisions:**

1. **Approvals carry signature only, never the pubkey.** The validator's
   ML-DSA pubkey is registered once via the staking transaction and stored
   in epoch validator info (`ValidatorStake`), exactly as ed25519 keys are
   today. Every verifier already holds the full key from epoch state, so
   the 1952 B key is paid once per epoch per validator, not once per
   approval. This is the single most important size decision on this
   surface (≈37% of naive per-approval bytes).
   - Consequence: validator keys are full keys *in state* — the access-key
     hash split (surface 1) deliberately does **not** apply here, because
     approval verification needs the key bytes and epoch info is tiny
     relative to the access-key population. State cost: 1952 B × validators
     per epoch record.
2. **Message framing unchanged:** approvals sign the borsh-encoded
   `ApprovalInner` + target height exactly as today; only the scheme
   changes. No pre-hash mode (acceptance rule §3.1).
3. **No aggregation assumed.** ML-DSA has no practical signature
   aggregation or batch verification (unlike ed25519 batch verify or BLS
   aggregation). The design must absorb per-approval verify cost linearly;
   any future aggregation scheme is a separate proposal.
4. **Slashing/challenge evidence** for equivocation is a pair of
   conflicting approvals by one key. Evidence records must identify
   approvals by `(approver, message)` — never by signature bytes, which are
   not unique under hedged signing (acceptance rule §3.6). The adjudicator
   verifying evidence is a judge in the WS3 model; its implementation must
   be conformant or accountability fails (WS3 property P2).

## 5. Light-client proofs

**What changes.** A light client tracking finality verifies
`approvals_after_next` in `LightClientBlockView` against ≥⅔ of the epoch's
stake, and validator pubkeys for the next epoch from `next_bps`. With PQ
validator keys: each approval is 3310 B, each `next_bps` entry carries a
1952 B pubkey.

- **Produces:** validators (surface 4); the full node serving the light
  client merely relays.
- **Verifies:** the light client, off the chain's critical path but on the
  *client's* critical path — bandwidth and CPU budgets are those of phones,
  embedded devices, and on-chain bridge contracts on foreign chains.
- **Bridge caveat:** a NEAR light client implemented *as a contract on
  another chain* (e.g. Rainbow-style bridges) must run ML-DSA verification
  in that chain's execution environment. This is the most
  resource-constrained judge in the entire system and the most likely to be
  implemented independently — i.e. the highest-risk surface for
  acceptance-rule divergence. Conformance vectors (acceptance rule §4) are
  mandatory for bridge verifiers, and the WS3 light-client-split property
  is aimed squarely here.
- **Divergence consequence:** a validator feeding
  conflicting-but-individually-valid approvals to two light clients with
  divergent verifiers splits their finality views without slashable
  evidence — the exact P2 attack modeled in WS3.

## 6. Implicit accounts — out of scope, precedent noted

PQ implicit accounts are deferred. The WS1 hash choice (SHA3-256,
domain-tagged, 32 B — short enough to be a NEAR account id) deliberately
keeps the door open: a future PQ implicit account can be the hash itself,
letting an ML-DSA access key be created from the account id alone. Any such
design must define its own domain tag and follow the acceptance rule's
binding pattern (§3.5).

## 7. Contract host functions — out of scope

No `ml_dsa_verify` host function exists; contracts cannot verify ML-DSA
signatures on-chain. If one is added later it becomes a judge surface: the
host function's acceptance rule MUST be the canonical rule of
[`verification-consistency.md`](verification-consistency.md) §3, vectors
included — a permissive host function would let contracts observe
divergence that the protocol layer excludes.
