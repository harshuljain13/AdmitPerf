# Requirements: what the survey needs from AdmitPerf

*2026-09-17. The survey names AdmitPerf as the framework it contributes:
taxonomy, contract, reference implementation. This file is the third one's job
description. The paper's needs are the requirements; nothing else is.*

## Problem Statement

The survey argues that admission-control papers cannot be compared, and proposes
a contract of things every paper should report. Two claims in that argument
cannot be made by reading papers:

1. **Releasing code is not sufficient.** A faithful implementation of a
   published policy still reports whatever the unreported parameters say it
   should.
2. **The contract is the right minimum.** Each item must be shown to matter by
   watching a result move when it is absent.

AdmitPerf exists to make those two claims true, and then to make following the
contract cheap enough that other people do it.

## Goals

- **G1 Evidence.** Produce the six sensitivity rows in `survey/notes/
  sensitivity-table.md` to a standard a reviewer will accept: real hardware
  where the claim says hardware, analysis clearly labelled where it is analysis.
- **G2 Enforcement.** Every contract item is checked by something executable, so
  "we followed the contract" is verifiable rather than asserted.
- **G3 Generality.** Show the sensitivity is not specific to one ported policy,
  by reproducing at least one knob on a second published algorithm.
- **G4 Metrics the paper defines.** Implement offered attainment and tenant SLO
  fairness exactly as the paper defines them, so the definitions are executable.
- **G5 Reproduction.** Any table or figure in the paper can be regenerated from
  this repo with one command, at a named commit.

## Non-Goals

- **Being a benchmark suite.** The paper proposes a contract, not a
  leaderboard. No rankings ship.
- **Winning a head-to-head.** A clean winner would undercut the survey's own
  finding. That study comes after the contract exists.
- **Breadth of ports.** Two published policies is enough for G3. QLM and CONCUR
  are follow-up work.
- **Multi-hardware claims.** One model, one GPU. The claim is about methodology.

## Success Criteria

| # | Criterion | Test |
|---|---|---|
| S1 | Six sensitivity rows, each with an honest provenance label | `sensitivity-table.md` has no row marked "lost" or "mock" |
| S2 | Contract items are enforced in code | each of the six maps to a check, a field, or a command |
| S3 | A second published policy reproduces at least one knob | its deviation list and result are in the repo |
| S4 | The paper's metrics run | offered attainment and tenant SLO fairness, with tests |
| S5 | One command per paper table | reproduction appendix lists them, and they work from a clean checkout |
| S6 | Every cited bundle names its commit | `manifest.environment.local.code.commit` present |

## Constraints

- **Adapter API v0 is frozen.** Evidence must be collectable without changing
  `Decision` or `AdmissionPolicy`.
- **Money.** Under $5 of GPU for everything outstanding.
- **Honesty over completeness.** A gap stated plainly beats a number that cannot
  be traced. We have already published one figure that the bundles did not
  support.

## Open Questions

- **Q1** Which second policy: FluidWait-inspired (cheap, its signal moves on our
  hardware) or Scorpio-inspired (peer-reviewed, deadline-aware, but needs a
  learned length predictor)?
- **Q2** Can tenant SLO fairness be measured on the current workload, which
  assigns tenants but does not differentiate their SLOs?
- **Q3** Does the reproduction appendix pin a commit per table, or one commit
  for the whole paper?
