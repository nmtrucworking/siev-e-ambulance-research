# SIEV E-Ambulance Research Operations

Canonical research repository for the e-ambulance simulation pipeline. The active source tree is this repository root; older exported packages are retained locally under `archive/` and are intentionally excluded from Git.

## Current research status

- Synthetic dataset and QA: implemented; dataset validator passes 16/16 checks.
- Rolling DES and policies B0/B3/B4/B6: implemented.
- Engineering smoke tests: 1-rep and 3-rep outputs are available under `experiments/`.
- OSMnx builder: implemented in `src/build_osmnx_network.py`; local artifacts have been generated and integration-smoke verified (artifacts are Git-ignored).
- 30 paired-replication experiment: not run yet.

Smoke-test metrics use the explicitly labelled Haversine fallback and must not be treated as final scientific evidence.

## Repository layout

```text
configs/          Experiment and runtime configuration
src/              Simulation, policies, validation and analysis code
scripts/          Reproducible command-line entry points
notebooks/        Research workflow notebooks 01-08
data/             Canonical synthetic input data
eda_pre_osmnx/    Pre-OSMnx EDA outputs
experiments/      Versionable smoke-test outputs; large final runs are ignored
validation/       Dataset QA and audit results
provenance/       Data-generation provenance and hashes
plots/            Research figures
results/          Small derived results
archive/          Local legacy/reference material, ignored by Git
```

## Environment

The validated project runtime is Python 3.13.15.

```bash
./scripts/bootstrap.sh
source .venv/bin/activate
```

`requirements.txt` is the canonical installation entry point and includes the complete experiment stack. Purpose-specific requirement files remain available for narrower environments.

## Validation

```bash
./scripts/check.sh
```

This compiles the Python sources, validates the synthetic dataset, and re-validates existing smoke-test outputs.

## Experiment workflow

1. Run `notebooks/01_EDA_Pre_OSMnx.ipynb`.
2. Run `.venv/bin/python src/build_osmnx_network.py` (or Notebook 02 with `RUN_NETWORK=True`) to generate `data/osmnx/`.
3. Complete traffic and energy/sensitivity work in Notebooks 03-06.
4. Run `scripts/run_30_paired_osmnx.sh` only after OSMnx artifacts exist.
5. Validate runtime outputs and run paired analysis.

See `RUNBOOK_TO_30_PAIRED.md` and `EXPERIMENT_ROADMAP_TO_30_PAIRED.md` for protocol details.
