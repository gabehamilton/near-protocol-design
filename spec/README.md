# WS3 — TLA+ Spec of ML-DSA Acceptance & Non-Divergence

Formal model of the ML-DSA signature **acceptance path** and the
consensus-relevant **approval logic** in NEAR — not nearcore, not the
lattice primitive. The protocol boundary it covers is defined in
[`../docs/verification-consistency.md`](../docs/verification-consistency.md) §6;
the crypto is abstracted as an oracle predicate, per the design brief.

## Files

| file | purpose |
|---|---|
| `MLDSAAcceptance.tla` | the spec: oracle, signers, judges, properties |
| `MLDSAAcceptance.cfg` | conformant verifiers + Byzantine signer — **passes** |
| `MLDSAHonest.cfg` | conformant verifiers + honest signer — **passes**, incl. `NoSplit` |
| `MLDSADivergent.cfg` | divergent verifiers — **expected violation** of P1 (silent divergence) |
| `MLDSADivergentSplit.cfg` | divergent verifiers — **expected violation** of P2 (unaccountable split) |

## Running

```sh
java -cp tla2tools.jar tlc2.TLC -deadlock -config MLDSAAcceptance.cfg      MLDSAAcceptance
java -cp tla2tools.jar tlc2.TLC -deadlock -config MLDSAHonest.cfg         MLDSAAcceptance
java -cp tla2tools.jar tlc2.TLC -deadlock -config MLDSADivergent.cfg      MLDSAAcceptance
java -cp tla2tools.jar tlc2.TLC -deadlock -config MLDSADivergentSplit.cfg MLDSAAcceptance
```

`-deadlock` disables deadlock reporting: runs terminate when every approval
has been emitted and evaluated, which is completion, not error. The model is
finite and TLC explores it exhaustively in ~1 s per config.

## Model ↔ protocol mapping

| TLA+ element | Protocol meaning (WS2 boundary reference) |
|---|---|
| `oracle[i] ⊆ Validators × Msgs × Sigs` | implementation *i*'s acceptance rule `Accept(...)` — the entire §3 rule, including FIPS 204 verify, hint canonicity, mode/context framing, and the protocol-feature gate, collapsed to its decision bit |
| `Conformant` | the §3.8 normative requirement: all conformant implementations decide identically on *every* input. `FALSE` models any §3 violation — permissive `HintBitUnpack` (§3.4), wrong context framing (§3.1), or a node evaluating the `PostQuantumSignatures` gate at the wrong version (§3.7) — all are the same failure shape |
| `Validators` element | a validator key *after* handle binding; the SHA3-256 key↔handle check (§3.5) is injective byte equality, so folding it into the signer identity loses nothing |
| `Msgs`, distinct elements conflicting | approval payloads, e.g. two different blocks at one height (`ApprovalInner` + target height) |
| `Sigs`, ≥ 2 tokens | distinct signature strings; hedged signing yields many valid signatures per message (§3.6), so signature bytes are never an identity |
| `EmitByz` | a Byzantine validator emitting any approval: equivocation, re-signing, or crafted edge-case encodings landing in a disagreement region |
| `EmitHonest` | an honest validator: only canonically valid signatures, never two conflicting messages (re-signing one message is allowed and non-slashable) |
| `Observe(j, a)` | adversarial network delivery + the judge running its verifier; judges are honest-but-divergent — bugs live in oracles, not behavior |
| `Adjudicator` + `ValidFor` (not `Accepts`) | the on-chain verifier judging *submitted* slashing evidence — it rules on evidence whether or not it observed the approvals live |
| `SlashableEvidence(v)` | two emitted approvals on conflicting messages, both valid per the adjudicator; identified by `(signer, message)`, never signature bytes (§3.6) |
| `LightClientSplit(v)` | conflicting approvals from `v` accepted by light clients. Deliberately does **not** require two *distinct* clients: a single client holding conflicting valid approvals is an equal failure and must be equally slashable — the checked property is the stronger one |

## Properties

- **P1 `VerificationAgreement`** — no reachable state where two judges have
  evaluated the same `(signer, msg, sig)` and disagree. ("Every honest node
  evaluates the acceptance rule identically.")
- **P2 `AccountableEquivocation`** — `LightClientSplit(v) ⇒
  SlashableEvidence(v)`: no path lets a validator present conflicting but
  individually-valid approvals to light clients *without* leaving evidence
  the adjudicator convicts on. A violation is the silent, unslashable split.
- **P0 `NoSplit`** — with only honest signers, no split exists at all
  (checked in the honest config; it obviously fails under equivocation, so
  it is not asserted in the Byzantine configs — there the guarantee is
  accountability, not prevention).

## Results (TLC 2.20, exhaustive, 2026-06-11)

| config | hypothesis | result | states (distinct) |
|---|---|---|---|
| `MLDSAAcceptance.cfg` | conformant, Byzantine signer | **P1 ∧ P2 hold** | 104,976 |
| `MLDSAHonest.cfg` | conformant, honest signer | **P1 ∧ P2 ∧ P0 hold** | 784 |
| `MLDSADivergent.cfg` | divergent | **P1 violated** (by design) | trace depth 4 |
| `MLDSADivergentSplit.cfg` | divergent | **P2 violated** (by design) | trace depth 5 |

The two counterexample traces, in protocol terms:

- **P1 violation (silent divergence):** the oracle differs on one input
  (`implA` rejects `⟨v1, mA, s1⟩`, `implB` accepts). The Byzantine signer
  emits that single approval; judge `lc1` (implA) and judge `lc2` (implB)
  both evaluate it and disagree. One signature, one message, zero
  equivocation — and the network has forked its view of validity. This is
  the Ed25519-era attack transplanted to ML-DSA.
- **P2 violation (unaccountable split):** the signer emits approvals for
  *conflicting* messages `mA` and `mB` that are valid under `implB` but
  rejected by `implA`. A light client on implB accepts both conflicting
  approvals, while the adjudicator — running implA — deems *neither* valid:
  conflicting finality views exist and **no submittable evidence convicts
  anyone**. Under conformance this is impossible: whatever a client
  accepted, the adjudicator also accepts, so any split self-generates its
  own slashing evidence (the 104,976-state pass of the first config).

Together the four runs are the punchline of WS2 §6: P1/P2 are not free
properties of NEAR's consensus design — they are purchased entirely by the
verification-consistency rule, which is why the canonical acceptance rule
and its conformance vectors are protocol obligations.

## Abstraction-fidelity notes

- The oracle is chosen once at `Init` and is constant per behavior:
  acceptance rules are deterministic, pure functions (§3.4 rule 10).
  Nondeterminism across *behaviors* lets TLC quantify over every possible
  divergence, so the divergent configs cover all bug shapes at this
  granularity, not one hand-picked bug.
- Honest emission requires validity under **all** implementations
  (`CanonicalValid`): a real `ML-DSA.Sign` output verifies under any
  correct verifier. A divergent implementation that *rejects* honest
  signatures is also covered — that is just another oracle in the
  `Conformant = FALSE` space.
- Quorums and stake weights are not modeled: P1/P2 are per-signature and
  per-validator properties, and aggregating conformant decisions cannot
  reintroduce divergence. A ≥⅔-stake light-client rule sits strictly above
  this boundary.
- Not modeled (out of boundary, WS2 §6): lattice math, hash collisions,
  network liveness/timing, Doomslug liveness, gas, migration.
