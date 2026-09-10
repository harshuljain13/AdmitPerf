# experiments/

- `configs/` — YAML configs per run (policy × trace × model × engine × seed).
- `notebooks/` — analysis + figure generation.
- `fidelity/` — per-policy fidelity check against the original paper. One markdown file per policy.
- `results/` — small summary Parquet files. Raw run outputs live in `../data/results/` (gitignored).
