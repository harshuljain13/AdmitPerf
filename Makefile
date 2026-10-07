# AdmitPerf — the package. `make help` lists everything.
#
# A library of admission-control policies, a standard way to log what they decided, and
# reports from those logs. Nothing here provisions, serves, or generates load.
#
# `admitperf.core` imports only the standard library, opens no socket and spawns no
# subprocess, because it runs inside someone else's request path. tests/test_layering.py
# enforces each of those.
#
# The cluster that exercises it lives in llm-inference-experiments/admitperf_testing.

.DEFAULT_GOAL := help
.PHONY: help test lint fmt demo dashboard

PYTHON := $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)

help:  ## this list
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[1m%-10s\033[0m %s\n", $$1, $$2}'

test:  ## run the test suite
	$(PYTHON) -m pytest -q

lint:  ## ruff check and format --check
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

fmt:  ## apply ruff formatting and autofixes
	$(PYTHON) -m ruff format .
	$(PYTHON) -m ruff check --fix .

demo:  ## three policies against a simulated engine, no GPU
	$(PYTHON) -m admitperf.cli demo

dashboard:  ## read the logs in a browser
	$(PYTHON) -m admitperf.cli dashboard
