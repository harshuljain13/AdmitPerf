# Submission — design the cluster and serve an app

Answers to the project instructions, part by part. Each part is filled in only
once it has been decided and, where it is a number, measured.

Every figure is labelled **predicted** or **measured**. Predictions are written
before the run and checked after. They are not edited to match.

---

## Part 0 — The application

*Not answered yet.*

## Part 1 — Capacity on paper

*Not answered yet.*

## Part 2 — Design the cluster

*Not answered yet.*

## Part 3 — Guardrails, admit, stay vs leave

*Not answered yet.*

## Part 4 — Place

*Not answered yet.*

## Part 5 — Queue

*Not answered yet. Notebook.*

## Part 6 — Hop and warmth

*Not answered yet.*

## Part 7 — Wire the app to the cluster

*Not answered yet.*

## Part 8 — Proof

*Not answered yet.*

---

## The questions to be answered at Part 8

Kept here from the start so the build aims at them rather than discovering them
at the end.

- What is the app, and which tokens are shared vs unique?
- What dies at guardrails vs admit vs place vs queue?
- Where do I prevent work that will time out?
- Where do I protect KV?
- Where do I prioritise interactive traffic?
- Where do I stop one tenant from owning the GPU?
- Where do I hop, and what is not copied?
- Where do I evict, and what becomes a ghost if I skip it?
- Where does the engine scheduler sit versus my admit / place / queue?
- What limited concurrency on this GPU for this app?
- Four production alerts I would set?
- If I scale, which pool — prefill tokens or decode slots?
- What would I change at 10× traffic, and which three knobs are the wrong next move?
