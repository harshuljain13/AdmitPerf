<!--
  Keep the title in conventional-commit form — release notes are assembled from these:
  feat(policies): …   fix(bench): …   docs: …   refactor: …   chore: …
-->

## What changed, and why

<!--
  The *why* is the part worth writing. A change that explains its reasoning is
  understandable in six months; one that does not is archaeology. If you fixed something,
  say what the broken behaviour looked like from outside — the symptom someone would
  have reported.
-->

## How it was verified

<!--
  Not "tests pass" — what you actually drove. A run you watched, a bundle you read, the
  decision log you checked. This project has already shipped a figure its bundles did not
  support, and a run where every signal was flat because the engine was never saturated.
  Both passed their tests.
-->

## Checklist

- [ ] `pytest -q` passes locally.
- [ ] `ruff check .` and `ruff format --check .` are clean.
  <!-- Check the exit code, not the output: piping either into `tail` hides the failure. -->
- [ ] If this touches a policy: it is Class A — a pure function of `(request, state)`,
      no engine changes. Class B does not fit behind this API.
- [ ] If this adds or changes a number that appears in a doc, the bundle it came from is
      named, and the commit that produced the bundle is recorded.
- [ ] `docs/status.md` still matches what the software can actually demonstrate.
  <!-- A claim the code cannot back in one command is the review risk that matters here. -->
- [ ] Version untouched — releases are cut by tag, never by hand-editing a version.

## Anything deliberately left undone

<!--
  Known gaps, follow-ups, things that look wrong but are intentional. A limitation stated
  here is a known limitation; the same limitation unstated is a bug waiting to be
  rediscovered.
-->

Closes #
