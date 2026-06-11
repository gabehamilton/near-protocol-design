--------------------------- MODULE MLDSAAcceptance ---------------------------
(***************************************************************************)
(* Workstream 3: formal model of the ML-DSA signature acceptance path in   *)
(* NEAR and its consensus-relevant approval logic.                         *)
(*                                                                         *)
(* The protocol boundary modeled here is defined in                        *)
(* docs/verification-consistency.md section 6.  The cryptography is        *)
(* abstracted as an oracle predicate: each implementation i has an         *)
(* acceptance set oracle[i] (subsets of Validators \X Msgs \X Sigs); a     *)
(* judge running implementation i accepts approval a iff a \in oracle[i].  *)
(* Lattice math is NOT modeled (inherited from libcrux's hax->F* proof).   *)
(* The key->handle binding is folded into the validator identity, which is *)
(* sound because the SHA3-256 handle check is injective byte equality      *)
(* (docs/verification-consistency.md section 3.5).                         *)
(*                                                                         *)
(* The conformance requirement of the design doc (section 3.8: all         *)
(* conformant implementations decide identically on EVERY input) appears   *)
(* here as the constant Conformant.  With Conformant = TRUE the target     *)
(* properties hold; with Conformant = FALSE, TLC exhibits the silent       *)
(* divergence and unaccountable light-client-split attacks, showing the    *)
(* properties are purchased entirely by verification consistency.         *)
(***************************************************************************)
EXTENDS Naturals, FiniteSets

CONSTANTS
  Validators,     \* signer identities (validator keys, post handle-binding)
  ByzValidators,  \* subset of Validators that may equivocate / craft sigs
  JudgesA,        \* judges running implementation "implA"
  JudgesB,        \* judges running implementation "implB"
  LightClients,   \* subset of judges tracking finality via approvals
  Adjudicator,    \* the judge consulted when slashing evidence is submitted
  Msgs,           \* approval payloads; distinct elements are CONFLICTING
                  \* (think: two different blocks at the same height)
  Sigs,           \* abstract signature tokens (>= 2: hedged signing yields
                  \* many distinct valid signatures per message)
  Conformant      \* the section 3.8 hypothesis: all impls share one rule

Judges == JudgesA \cup JudgesB
Impls  == { "implA", "implB" }

ASSUME ByzValidators \subseteq Validators
ASSUME JudgesA \cap JudgesB = {}
ASSUME LightClients \subseteq Judges
ASSUME Adjudicator \in Judges
ASSUME Conformant \in BOOLEAN

ImplOf(j) == IF j \in JudgesA THEN "implA" ELSE "implB"

\* An approval is <<signer, message, signature>>.
Approvals == Validators \X Msgs \X Sigs

VARIABLES
  oracle,    \* [Impls -> SUBSET Approvals]: the acceptance rule per impl,
             \* chosen adversarially at Init, constant thereafter
  pool,      \* approvals emitted onto the (adversarial) network
  evaluated  \* [Judges -> SUBSET Approvals]: approvals each judge has
             \* received and run through its verifier

vars == <<oracle, pool, evaluated>>

(***************************************************************************)
(* Oracle choice.  Conformant => both implementations decide identically   *)
(* on every input -- the normative requirement of the design doc.  With    *)
(* Conformant = FALSE the two acceptance sets are unconstrained, which     *)
(* covers buggy HintBitUnpack, wrong context framing, a node evaluating    *)
(* the protocol-feature gate at the wrong version, etc.                    *)
(***************************************************************************)
Oracles ==
  { f \in [Impls -> SUBSET Approvals] :
      Conformant => f["implA"] = f["implB"] }

\* The decision bit judge j computes for approval a (deterministic, pure).
ValidFor(j, a) == a \in oracle[ImplOf(j)]

\* "Honestly generated": valid under every implementation's rule.  A real
\* signature produced by ML-DSA.Sign verifies under any conformant verifier.
CanonicalValid(a) == \A i \in Impls : a \in oracle[i]

Init ==
  /\ oracle \in Oracles
  /\ pool = {}
  /\ evaluated = [ j \in Judges |-> {} ]

(***************************************************************************)
(* A Byzantine signer emits anything: multiple distinct signatures on one  *)
(* message (hedged signing, doc section 3.6), signatures on conflicting    *)
(* messages (equivocation), and crafted edge-case encodings that land in   *)
(* the disagreement region of divergent verifiers.                         *)
(***************************************************************************)
EmitByz(v, m, s) ==
  /\ v \in ByzValidators
  /\ pool' = pool \cup { <<v, m, s>> }
  /\ UNCHANGED <<oracle, evaluated>>

(***************************************************************************)
(* An honest signer only emits honestly generated signatures and never     *)
(* signs two conflicting messages (it may re-sign the same message --      *)
(* hedged signing makes that normal and non-slashable).                    *)
(***************************************************************************)
EmitHonest(v, m, s) ==
  /\ v \in Validators \ ByzValidators
  /\ CanonicalValid(<<v, m, s>>)
  /\ \A a \in pool : a[1] = v => a[2] = m
  /\ pool' = pool \cup { <<v, m, s>> }
  /\ UNCHANGED <<oracle, evaluated>>

(***************************************************************************)
(* The adversarial network delivers any emitted approval to any judge, in  *)
(* any order; the judge runs its verifier (the decision itself is the      *)
(* derived predicate ValidFor, so a judge cannot "mis-evaluate" -- a buggy *)
(* verifier is modeled by its oracle, not by the judge's behavior).        *)
(***************************************************************************)
Observe(j, a) ==
  /\ a \in pool
  /\ a \notin evaluated[j]
  /\ evaluated' = [ evaluated EXCEPT ![j] = @ \cup {a} ]
  /\ UNCHANGED <<oracle, pool>>

Next ==
  \/ \E v \in Validators, m \in Msgs, s \in Sigs :
       EmitByz(v, m, s) \/ EmitHonest(v, m, s)
  \/ \E j \in Judges, a \in Approvals : Observe(j, a)

Spec == Init /\ [][Next]_vars

-------------------------------------------------------------------------------
(***************************************************************************)
(* Derived notions                                                         *)
(***************************************************************************)

\* Judge j has evaluated approval a and its verifier accepted it.
Accepts(j, a) == a \in evaluated[j] /\ ValidFor(j, a)

\* Light client lc holds an accepted approval by v for message m.
AcceptedByLC(lc, v, m) == \E s \in Sigs : Accepts(lc, <<v, m, s>>)

(***************************************************************************)
(* Slashable evidence against v: two emitted approvals on CONFLICTING      *)
(* messages, both of which the on-chain adjudicator's verifier deems       *)
(* valid.  Evidence is identified by (signer, message) pairs, never by     *)
(* signature bytes (doc section 3.6) -- hence s1 and s2 are existential.   *)
(* The adjudicator judges evidence on submission, so ValidFor (not         *)
(* Accepts) is the right notion: it need not have observed the approvals   *)
(* during normal operation.                                                *)
(***************************************************************************)
SlashableEvidence(v) ==
  \E m1, m2 \in Msgs : \E s1, s2 \in Sigs :
    /\ m1 # m2
    /\ <<v, m1, s1>> \in pool
    /\ <<v, m2, s2>> \in pool
    /\ ValidFor(Adjudicator, <<v, m1, s1>>)
    /\ ValidFor(Adjudicator, <<v, m2, s2>>)

\* Validator v has split the light clients: two clients accepted
\* conflicting approvals from v.
LightClientSplit(v) ==
  \E lc1, lc2 \in LightClients : \E m1, m2 \in Msgs :
    /\ m1 # m2
    /\ AcceptedByLC(lc1, v, m1)
    /\ AcceptedByLC(lc2, v, m2)

-------------------------------------------------------------------------------
(***************************************************************************)
(* Invariants                                                              *)
(***************************************************************************)

TypeOK ==
  /\ oracle \in [Impls -> SUBSET Approvals]
  /\ pool \subseteq Approvals
  /\ evaluated \in [Judges -> SUBSET Approvals]
  /\ \A j \in Judges : evaluated[j] \subseteq pool

(***************************************************************************)
(* P1 -- Verification agreement (non-divergence).  No reachable state in   *)
(* which two honest judges have both evaluated the same approval and       *)
(* disagree on its validity.  This is the "honest nodes never disagree on  *)
(* signature validity" property.                                           *)
(***************************************************************************)
VerificationAgreement ==
  \A j1, j2 \in Judges :
    \A a \in evaluated[j1] \cap evaluated[j2] :
      ValidFor(j1, a) <=> ValidFor(j2, a)

(***************************************************************************)
(* P2 -- Accountable equivocation.  Any light-client split leaves          *)
(* slashable evidence: there is no way to present conflicting but          *)
(* individually-valid approvals to light clients without the adjudicator   *)
(* being able to convict.  A violation is the SILENT split: clients        *)
(* diverge, yet no submittable evidence convicts anyone.                   *)
(***************************************************************************)
AccountableEquivocation ==
  \A v \in Validators : LightClientSplit(v) => SlashableEvidence(v)

(***************************************************************************)
(* P0 -- No split at all.  Holds only when every validator is honest       *)
(* (checked in the honest-signer configuration): non-equivocation plus     *)
(* conformant verification means light clients cannot be split, period.    *)
(***************************************************************************)
NoSplit ==
  \A v \in Validators : ~LightClientSplit(v)

===============================================================================
