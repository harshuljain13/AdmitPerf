# Tasks: AdmitPerf

Legend: `[ ]` not started · `[x]` in progress · `[✓]` done

Ordered by what the paper needs first. Each task is one sitting.
Cross-references `survey/.spec-dev/tasks.md`, which tracks the writing.

---

## Phase 1 — Close the contract gaps (free, no GPU)

Two contract items have no mechanism, and the paper will claim this repo
enforces all of them.

- [ ] **1.1** `parameter_provenance` in the manifest: per policy parameter,
      `fitted` | `inherited` | `default`. Chronos is the test case, since its
      alpha and beta are fitted at run time while gamma is inherited from the
      source paper, and that single inherited number swings the admit rate from
      1.8% to 100%.
- [ ] **1.2** `bench fairness`: tenant SLO fairness F, computed as Jain's index
      over per-tenant offered attainment. Undefined rather than 1.0 for a single
      tenant. Tests, including two policies with equal aggregate attainment and
      different F.
- [ ] **1.3** README section mapping each contract item to its mechanism, so a
      reader can check the claim in D1 without reading code.

## Phase 2 — Finish the evidence (about $2 of GPU, one session)

Every row in `survey/notes/sensitivity-table.md` that is not final.

- [ ] **2.1** `infra calibrate` on a fresh deployment. Gives row 6 its
      denominator: the deadline is currently compared against loaded latency,
      which is nearly circular. Two minutes.
- [ ] **2.2** Re-run the 2048-context configuration, one repeat, and keep the
      bundle. Recreates the destroyed HTTP 400 evidence. Three minutes.
- [ ] **2.3** Run `which-policy-when`: capacity probe, then four situations
      against one deployment, then `bench decide`. Fills the load row. Seventy
      five minutes.
- [ ] **2.4** Update the sensitivity table from the new bundles. No row keeps a
      "lost" or "mock" label.
- [ ] **2.5** Record both sessions in `docs/results.md` with commit and
      hardware.

## Phase 3 — The second port (about $0.50)

- [ ] **3.1** Port FluidWait-inspired (arXiv:2504.11320): policy, registry
      entry, unit tests at the decision boundaries.
- [ ] **3.2** Its deviation list, written while porting rather than after.
- [ ] **3.3** Add to `which-policy-when`, confirm from `signal_range` that its
      signal moved.
- [ ] **3.4** Reproduce one sensitivity knob with it, cheapest first, and report
      whether the effect holds outside Chronos. A negative result here is
      publishable and must be reported as such.

## Phase 4 — Make the paper reproducible from this repo

- [ ] **4.1** `docs/reproduce.md`: one command per paper table or figure, with
      the commit and the hardware, and the cost where a GPU is needed.
- [ ] **4.2** README: state that this is the reference implementation of the
      contract in the survey, cite the paper, link it.
- [ ] **4.3** Check every command from a clean clone. A reproduction appendix
      that has never been run from scratch is a promise, not a mechanism.

## Phase 5 — Legibility

- [ ] **5.1** Dashboard vocabulary pass. The charts and terms are opaque to
      anyone who did not build them. Pick the three terms that carry the
      argument, explain those, cut the rest.
- [ ] **5.2** `bench decide` output as the paper's decision table, so the figure
      and the command agree.

## Later

- [ ] **6.1** Scorpio-inspired port, including the length predictor.
- [ ] **6.2** QLM-inspired port.
- [ ] **6.3** Agent-session workload, then CONCUR-inspired.
- [ ] **6.4** The head-to-head study, once the contract exists.

---

## Blocked by

- 2.4 needs 2.1, 2.2 and 2.3.
- 3.4 needs 3.1.
- 4.1 needs Phase 2, since half the tables do not exist yet.

## Notes

- **Phase 1 is free and unblocks a paper claim.** Do it first.
- **Phase 2 is the only real spend in the plan**, and it is one session.
- **A failed port is a finding.** If FluidWait cannot be implemented from its
  text, that goes in the appendix beside the deviation lists.
