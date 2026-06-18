# near-protocol-design — Post-Quantum (ML-DSA) Protocol Design & TLA+ Spec

Design documents (**Workstream 2**) and a model-checked TLA+ specification
(**Workstream 3**) for integrating **ML-DSA-65 (FIPS 204)** signatures into
the NEAR protocol. Signatures, not encryption: the quantum exposure is the
Shor-broken signature schemes (Ed25519/secp256k1) used for account keys,
transaction signing, validator approvals, and light-client verification.

Companion implementation (**Workstream 1**, the grounding baseline):
[`gabehamilton/nearcore`](https://github.com/gabehamilton/nearcore), branch
`pq-libcrux-variation` — ML-DSA-65 already integrated in `near-crypto`
(additive; classical schemes untouched), with the pubkey-hash-on-trie split,
protocol gating, and dual-backend (aws-lc-rs × libcrux) differential testing.
This repo holds **no implementation code**.

> **📊 [Simulation findings](sim/findings.md)** — performance & economic impact
> of the PQ change on a 250-node / 8-shard network: user-side PQ is
> latency-neutral but grows archival storage 17×; validator-key PQ stretches
> block time +3.4%; payments-mix capacity drops 77%; the unpriced transaction
> byte axis (~21×) is the real subsidy, and archival operators bear the
> largest unfunded cost.

## Contents

| | document | what it is |
|---|---|---|
| WS2 | [`docs/verification-consistency.md`](docs/verification-consistency.md) | **The core.** Normative canonical acceptance rule for ML-DSA-65 in NEAR (mode, context, encoding lengths, hint canonicity, key↔handle binding, gating), the conformance-vector requirement, and the protocol boundary handed to WS3. |
| WS2 | [`docs/where-ml-dsa-enters.md`](docs/where-ml-dsa-enters.md) | Surface map: every point an ML-DSA key/signature enters the protocol — access keys, transactions, delegate actions, validator approvals, light clients — with producer/verifier/byte-location/critical-path per surface. |
| WS2 | [`docs/size-impact-analysis.md`](docs/size-impact-analysis.md) | The sharp edge, quantified: state size, approval bandwidth (~50× at full validator migration), light-client cost, and the gas/fee ripple points (noted, not repriced). |
| WS3 | [`spec/MLDSAAcceptance.tla`](spec/MLDSAAcceptance.tla) (+ 4 `.cfg`, [`spec/README.md`](spec/README.md)) | TLC-checked model of the acceptance path: crypto as an oracle predicate, Byzantine signer, divergent-verifier hypothesis. Proves **non-divergence** and **accountable equivocation** hold under the conformance rule — and exhibits the silent-divergence and unaccountable light-client-split attacks when it is dropped. |
| sim | [`sim/near_pq_sim.py`](sim/near_pq_sim.py) ([`results.md`](sim/results.md)) | 250-node / 8-shard network simulation quantifying PQ performance and economic impact across adoption scenarios: block time, capacity by binding constraint, bandwidth/archival growth, fees/burn, validator and archival-operator economics. |

## The one-paragraph result

Honest nodes agreeing on signature validity (P1) and every light-client
split leaving slashable evidence (P2) are **not** properties of NEAR's
consensus design — they are purchased entirely by the verification-
consistency rule: *all conformant implementations accept/reject identically
on every input*. TLC verifies P1 ∧ P2 exhaustively under that rule (104,976
states) and produces 4–5 step counterexamples for both the moment any
implementation diverges (buggy hint decoding, wrong mode/context, a
mis-evaluated feature gate — all the same failure shape). Hence the
canonical acceptance rule and its conformance vectors are protocol
obligations, not testing niceties.

## Non-goals (design *for* them, don't build them)

Live-chain migration/rollout (hybrid transition, governance, gas
repricing), re-verifying the ML-DSA primitive (libcrux's hax→F\* proof is
inherited), and formally verifying nearcore (WS3 formalizes one protocol
property, with the crypto abstracted).
