# reports/

Written studies, each backed by a run bundle under `results/`.

| Report | Subject |
|---|---|
| [`chronos-reproduction.md`](chronos-reproduction.md) | Chronos (Frontiers CS 8, 2026) — background, port, and a real-engine comparison against an uncontrolled baseline |

Figures live in `figures/` as Mermaid sources; `make diagrams` renders them and
`make diagrams-check` fails if a PNG is older than its source.

Conventions: every number is traceable to a bundle, every bundle records the
resolved config that produced it, and anything not measured is named as such
rather than estimated.
