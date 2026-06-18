# Verification Consistency for ML-DSA-65 in NEAR

**Status:** design / normative draft (Workstream 2, core document)
**Scope:** the *acceptance rule* for ML-DSA-65 signatures anywhere they enter
the NEAR protocol, and the consistency property all conformant verifiers must
satisfy. Companion surface map: [`where-ml-dsa-enters.md`](where-ml-dsa-enters.md).
The formal model of the property is Workstream 3: [`../spec/`](../spec/).

The keywords MUST, MUST NOT, SHOULD, MAY are used per RFC 2119 in §3–§5.

---

## 1. Why this is a first-class protocol property

A blockchain's signature verifier is not "a cryptographic detail" — it is a
**consensus rule**. Every honest node must compute the *same* accept/reject
bit for the same `(public key, message, signature)` triple, or the chain can
be split without any party committing a slashable offense.

### 1.1 Prior art: the Ed25519 divergence class

Widely deployed Ed25519 implementations are documented to disagree on edge
cases: non-canonical point and scalar encodings, small-order components,
cofactored vs. cofactorless verification equations, and batch vs. single
verification (see Chalkias–Garillot–Nikolaenko, *Taming the many EdDSAs*,
2020, and Zcash's ZIP-215, which exists precisely because consensus required
pinning one rule). The attack pattern this enables:

1. An attacker crafts a signature in the *disagreement region* of two
   implementations (or two versions, or two configurations of one library).
2. Node A accepts the carrying object (transaction, approval); node B rejects
   it. Their states — or their views of finality — diverge.
3. Crucially, **no equivocation occurred**. There is one signature on one
   message. Nothing is slashable; the split is silent.

A variant targets light clients: a validator submits an approval that client
C1's verifier accepts and client C2's rejects. C1 and C2 can be driven to
different finality views while the on-chain adjudicator — which would need to
deem *two conflicting valid signatures* to slash — sees at most one valid
signature. The split is **unaccountable**.

### 1.2 Why ML-DSA multiplies the surface

- **More implementations.** WS1 already wires two (aws-lc-rs active, libcrux
  for differential testing); wallets, SDKs, light clients, and alternative
  node implementations will add more.
- **Bigger encodings, more non-canonical space.** A 3309-byte signature has
  far more malformed/borderline encodings than a 64-byte one. The ML-DSA hint
  vector in particular (§3.4) has a canonicity requirement that early
  Dilithium-era implementations got wrong in divergent ways.
- **Mode and context ambiguity.** FIPS 204 defines pure ML-DSA vs. HashML-DSA
  (pre-hash), an "internal" signing interface, and a context string of 0–255
  bytes. Two implementations that are each "FIPS 204 conformant" but choose
  different modes/contexts produce mutually unverifiable signatures — or
  worse, partially overlapping acceptance sets.
- **Signature non-uniqueness.** Default ML-DSA signing is *hedged*
  (randomized): one `(sk, M)` pair yields many distinct valid signatures.
  Any protocol logic that treats signature bytes as an identity is wrong
  (§3.6).
- **A future hybrid path** (classical + PQ composite acceptance) squares the
  matrix: every divergence in either component, plus divergence in the
  combinator, becomes a consensus risk. The rule template in §3 is written so
  a hybrid rule can be layered on top without reopening it.

WS1's differential-testing result — byte-for-byte KeyGen agreement from a
seed and two-way signature interoperation including agreement on *rejection*
between aws-lc-rs and libcrux — is the empirical seed of this document. §3
turns it into a normative rule; §4 turns it into a conformance obligation.

---

## 2. Definitions

- **Acceptance rule** `Accept(pv, surface, pk, msg, sig) → {accept, reject}`:
  the total, deterministic predicate a NEAR verifier evaluates, where `pv` is
  the protocol version, `surface` is the protocol entry point (transaction,
  AddKey, delegate action, …; see the surface map), `pk` the full public key,
  `msg` the surface-defined message bytes, `sig` the signature bytes.
- **Conformant implementation:** any code path, in any codebase, whose
  accept/reject decisions are claimed to implement NEAR's ML-DSA-65 rule.
  This includes nearcore's runtime, alternative full nodes, light clients,
  bridge contracts, and the verifier used when judging slashing evidence.
- **Judge:** any party evaluating the acceptance rule (full node, light
  client, on-chain adjudicator).
- **Divergence:** two conformant judges returning different bits for the same
  `(pv, surface, pk, msg, sig)`.

---

## 3. The canonical acceptance rule (normative)

### 3.1 Algorithm, parameter set, and mode

1. The scheme is **ML-DSA-65** as specified in **FIPS 204 (final, August
   2024)**. No other parameter set (44, 87) is accepted under borsh tag 2.
2. The mode is **pure ML-DSA** — `ML-DSA.Verify` (FIPS 204 Algorithm 3) —
   with the **empty context string** (`ctx = ""`, length 0).
   - Equivalently: `Verify_internal` (Algorithm 8) applied to the framed
     message `M′ = IntegerToBytes(0,1) ‖ IntegerToBytes(0,1) ‖ M`, i.e. the
     two-byte prefix `0x00 0x00` (domain byte 0 = pure mode, context length
     0) followed by the protocol message `M`.
   - **HashML-DSA (pre-hash mode) MUST NOT be accepted.** A HashML-DSA
     signature frames the message with domain byte `0x01` and an OID, so it
     fails pure-mode verification by construction; implementations MUST NOT
     "helpfully" try both modes.
   - An "ExternalMu" or raw `Verify_internal` interface MAY be used
     internally only if it is invoked with exactly the framing above.
3. The message `M` is the exact byte string defined by the protocol for the
   surface (e.g. for transactions, the 32-byte SHA-256 digest of the
   borsh-serialized `Transaction`). `M` construction is surface-specific and
   normative per surface; see the surface map. No implementation may hash,
   trim, or re-encode `M` beyond what the surface definition states.

### 3.2 Exact encoding lengths

4. The public key MUST be exactly **1952 bytes** (`ρ` 32 B ‖ packed `t1`,
   6 polynomials × 256 coefficients × 10 bits = 1920 B). Borsh framing: tag
   `2` then a fixed 1952-byte array — total 1953 bytes. Any other length
   MUST fail *deserialization* (never reach the verifier).
5. The signature MUST be exactly **3309 bytes** (`c̃` 48 B ‖ packed `z`,
   5 × 256 × 20 bits = 3200 B ‖ packed hint, ω + k = 55 + 6 = 61 B). Borsh
   framing: tag `2` then a fixed 3309-byte array — total 3310 bytes. Any
   other length MUST fail deserialization.
6. There is no length ambiguity left to the verifier: borsh fixed-size arrays
   make over- and under-length encodings unrepresentable. Implementations
   that parse these types outside borsh (e.g. JSON-RPC base58 fields) MUST
   enforce the same exact lengths before invoking verification.

### 3.3 Public-key decoding

7. `pkDecode` of a 1952-byte string cannot fail (every 10-bit `t1`
   coefficient value is in range by construction). Therefore key *decoding*
   contributes no accept/reject divergence surface — but key *binding* does:
   see §3.5.

### 3.4 Signature decoding and canonicity — the sharp edge

8. `sigDecode` MUST implement the FIPS 204 rejection rules exactly. In
   particular `HintBitUnpack` (FIPS 204 Algorithm 21) MUST return ⊥ — and
   the verifier MUST reject — when the hint encoding is non-canonical:
   - more than ω = 55 total hint indices;
   - hint indices within a polynomial not strictly increasing;
   - nonzero bytes in the hint region after the last used index.
   These are the classic divergence bugs of the Dilithium era: a permissive
   `HintBitUnpack` yields a verifier that accepts signatures a strict one
   rejects, which is exactly a consensus split. **Hint canonicity vectors are
   mandatory conformance vectors** (§4).
9. The verifier MUST enforce `‖z‖∞ < γ1 − β` (γ1 = 2¹⁹, β = 196 for
   ML-DSA-65) and the `c̃` recomputation equality, per Algorithm 8. No
   "early accept" shortcuts.
10. Verification MUST be a **pure function** of `(pk, M, sig)`: no
    dependence on platform, build flags, time, or RNG. (Variable *running
    time* is acceptable and expected — WS1 benchmarked it — but the
    *decision* must be bit-identical everywhere.)

### 3.5 Key binding to on-trie handles

11. An ML-DSA-65 `PublicKeyHandle` (borsh tag 3) is
    `SHA3-256(b"near:ml-dsa-65-pubkey-hash:v1" ‖ pk)` — 32 bytes. A handle
    **cannot verify anything**. A verifier resolving an access key from
    state MUST obtain the full 1952-byte `pk` from the carrying object on
    the wire and MUST check `HandleOf(pk) = stored handle` (byte equality)
    before treating `pk` as the registered key.
12. Byte equality of a SHA3-256 digest has no divergence surface; the
    binding inherits collision resistance of SHA3-256 with the domain tag
    separating it from every other SHA-3 use in the protocol. The WS3 model
    abstracts `HandleOf` as an injective function for exactly this reason.

### 3.6 Non-uniqueness of signatures (normative consequence)

13. ML-DSA signing is hedged: many distinct valid signatures exist per
    `(sk, M)`. Implementations and protocol logic MUST NOT:
    - use signature bytes as an identity, dedup key, or map key for a signed
      object (NEAR transaction hashes already exclude the signature — this
      MUST remain true for every PQ-bearing object, including approvals and
      slashing-evidence records);
    - assume that re-signing produces the same bytes;
    - treat "two different signatures by the same key on the same message"
      as equivocation. **Equivocation is two valid signatures on
      *conflicting messages*, never two signatures on one message.**
14. Third-party malleability is excluded by ML-DSA's strong-unforgeability
    (SUF-CMA) claim, but the protocol MUST NOT depend on it for consensus:
    the rule above (identity excludes signature bytes) makes malleability
    consensus-irrelevant by construction.

### 3.7 Protocol-version gating is part of the rule

15. `Accept` takes the protocol version: below the `PostQuantumSignatures`
    activation, every ML-DSA-65 signature MUST be rejected at action/
    transaction validation on every gated surface, regardless of
    cryptographic validity. Borsh *deserialization* is not gated (state must
    always parse), but no gated surface may accept. A node evaluating the
    gate at the wrong version is, for the purposes of this document, a
    non-conformant implementation — it diverges exactly like a buggy
    verifier, and the WS3 model treats the two identically.

### 3.8 The consistency requirement

16. **All conformant implementations MUST compute identical accept/reject
    decisions for every input** — not just "valid signatures verify," but
    *agreement on rejection* across the entire input space, including
    malformed, non-canonical, wrong-mode, and wrong-context inputs. This is
    the property WS1 demonstrated empirically for aws-lc-rs × libcrux and
    the property WS3 proves is what blocks the attacks of §1.1.

---

## 4. Conformance vectors (normative requirement)

A NEAR ML-DSA-65 conformance suite MUST exist and MUST be passed by every
conformant implementation before it ships in any judge role. Required
categories:

| # | Category | Expected | Why |
|---|----------|----------|-----|
| V1 | KeyGen from 32-byte seed ξ → exact 1952 B pk / 4032 B sk bytes | byte equality | FIPS 204 KeyGen is a pure function of ξ; WS1 proved aws-lc-rs ≡ libcrux byte-for-byte |
| V2 | Well-formed signatures over protocol messages (per surface framing) | accept | baseline interop |
| V3 | Cross-implementation: signatures produced by each known implementation | accept by all others | hedged signing differs per run; only the decision is compared |
| V4 | Length violations: 1951/1953-byte keys, 3308/3310-byte sigs, truncations | reject (at parse) | §3.2 |
| V5 | Hint canonicity: count > ω; non-increasing indices; nonzero trailing hint bytes | reject | §3.4 — the historical divergence hotspot |
| V6 | `z` out of range (coefficient ≥ γ1 − β); corrupted `c̃` | reject | §3.4 |
| V7 | Mode/context confusion: HashML-DSA signatures; pure-mode signatures with ctx ≠ "" | reject | §3.1 |
| V8 | Bit-flip fuzz corpus over valid signatures and keys | identical decisions across all implementations | catches disagreement regions no hand-written vector anticipates |
| V9 | Tampered messages under valid signatures | reject by all | WS1 differential test, promoted to a vector |

Sourcing: NIST ACVP ML-DSA `sigVer` vectors (which include malformed-hint
cases) SHOULD be incorporated wholesale; V2/V3/V8 are NEAR-specific and
generated from the WS1 differential harness
(`cargo test -p near-crypto --features ml-dsa-libcrux`), which becomes the
reference generator. The vectors are protocol artifacts: versioned in-repo,
frozen per protocol version, and extended (never mutated) by future changes.

**Rule for future implementations:** an implementation that passes V1–V9 may
still diverge on inputs outside the suite; the suite is a ratchet, not a
proof. The normative requirement remains §3.8 (total agreement), and any
discovered disagreement is a consensus bug in at least one implementation,
to be fixed *and* captured as a new vector.

---

## 5. Hybrid-path note (design-for, don't build)

If NEAR later adopts a hybrid classical+PQ acceptance rule, it MUST be
specified as a *combinator over this rule*, not a modification of it:
`AcceptHybrid = AcceptClassical(…) ∧ AcceptMLDSA(…)` (AND-composition is the
conservative choice; OR-composition reintroduces downgrade divergence).
Conformance categories V1–V9 then apply per component, plus combinator
vectors (each component individually failing). The WS3 model already covers
the hybrid case abstractly: a hybrid verifier is just another implementation
identifier whose oracle must equal the canonical one.

---

## 6. Protocol boundary handed to WS3

This section defines exactly what the TLA+ specification
([`../spec/MLDSAAcceptance.tla`](../spec/MLDSAAcceptance.tla)) models. The
spec covers the *acceptance logic and its consensus consequences* —
nothing inside the lattice math, nothing about networking or gas.

**Inside the boundary (modeled):**

- A set of **judges** — full nodes, light clients, and the on-chain
  **adjudicator** (the verifier consulted when slashing evidence is
  submitted) — each running some implementation `i`.
- Each implementation's acceptance rule, abstracted as an **oracle
  predicate** `Valid_i(sig, key, msg)`. The crypto is opaque; only the
  decision bit is modeled. The §3.7 feature gate and any §3.4-class
  implementation bug are both expressible as `Valid_i ≠ Valid_canonical`.
- The **conformance assumption** (§3.8) as a switchable hypothesis:
  `Conformant ⇒ ∀ i, j : Valid_i = Valid_j`.
- A **Byzantine signer** that may emit arbitrarily many `(msg, sig)` pairs:
  multiple distinct valid signatures on one message (hedged signing, §3.6)
  and valid signatures on **conflicting** messages (equivocation).
- Honest signers, which never equivocate and only emit canonically valid
  signatures.
- Key→handle binding abstracted as an injective `HandleOf` (justified by
  §3.5); because injectivity makes the handle check equivalent to key
  equality, the spec folds it into the oracle's `key` argument.

**Target properties (the WS3 obligations):**

- **P1 — Verification agreement (non-divergence):** no reachable state in
  which two honest judges have evaluated the same `(key, msg, sig)` and
  disagree on its validity.
- **P2 — Accountable equivocation:** no reachable state in which two light
  clients have accepted conflicting approvals from the same key *without*
  the adjudicator deeming two conflicting signatures valid — i.e. any
  light-client split leaves slashable evidence on the table. (The silent
  split of §1.1 is exactly a P2 violation.)

**Expected results, which the spec demonstrates by model checking:**

| Hypothesis | P1 | P2 |
|---|---|---|
| Conformant verifiers, honest signer | holds | holds (vacuously — no split at all) |
| Conformant verifiers, Byzantine signer | holds | holds — splits exist but are always slashable |
| Divergent verifiers (any §3-violation) | **fails** | **fails** — unaccountable split, the §1.1 attack |

The third row is the punchline: P1/P2 are not free properties of the
consensus design — they are purchased entirely by §3.8. That is why
verification consistency is a first-class protocol property and why the
conformance suite of §4 is a protocol obligation, not a testing nicety.

**Outside the boundary (not modeled):** lattice mathematics (inherited from
libcrux's hax→F\* proof of the primitive), hash collisions (standard-model
assumption), network timing/availability, Doomslug liveness, gas, and
rollout/migration mechanics.
