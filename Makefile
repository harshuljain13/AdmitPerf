# AdmitPerf. `make help` lists everything.
#
# Two halves, deliberately separate:
#
#   the package    src/admitperf — what teams pip install. Decides whether to admit a
#                  request. Standard library only, no sockets, no subprocesses, because
#                  it runs inside someone's request path.
#   the cluster    infra/ — one Helm chart and one worked example of a host to test
#                  against. Shells out to ssh and helm, so it cannot live in the package.
#
# Cluster targets forward to infra/Makefile, so exactly one Makefile knows about helm,
# ssh and values files. `make -C infra help` lists its own.

.DEFAULT_GOAL := help
.PHONY: help test lint fmt plan up down tunnel urls status smoke logs dashboards

PYTHON := $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)

# Which topology to act on: infra/values/<VALUES>.yaml
VALUES ?= single

help:  ## this list
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[1m%-10s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "  VALUES=$(VALUES)   topologies: $$(ls infra/values/*.yaml | xargs -n1 basename | sed 's/.yaml//' | tr '\n' ' ')"
	@echo "  cluster targets:   make -C infra help"

# -- the cluster -----------------------------------------------------------

plan:  ## what would deploy, and whether a run on it can mean anything
	@$(MAKE) --no-print-directory -C infra plan VALUES=$(VALUES)

up:  ## deploy it: engines, gateway, observability, tunnel
	@$(MAKE) --no-print-directory -C infra up VALUES=$(VALUES)

down:  ## uninstall the release and close the tunnel (instance keeps billing)
	@$(MAKE) --no-print-directory -C infra down VALUES=$(VALUES)

tunnel:  ## reopen the port-forwards and verify what answers
	@$(MAKE) --no-print-directory -C infra tunnel VALUES=$(VALUES)

urls: tunnel  ## alias for tunnel

status:  ## pods on the box
	@$(MAKE) --no-print-directory -C infra status VALUES=$(VALUES)

smoke:  ## prove the deployed cluster serves, through the gateway
	@$(MAKE) --no-print-directory -C infra smoke VALUES=$(VALUES)

logs:  ## follow the gateway (WHAT=engine|ui for others)
	@$(MAKE) --no-print-directory -C infra logs VALUES=$(VALUES) WHAT=$(WHAT)

dashboards:  ## regenerate the Grafana JSON from infra/lib/observability
	@$(MAKE) --no-print-directory -C infra dashboards

# -- the package -----------------------------------------------------------

test:  ## run the test suite
	$(PYTHON) -m pytest -q

lint:  ## ruff, plus every chart refusal
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .
	@$(MAKE) --no-print-directory -C infra lint

fmt:  ## apply ruff formatting and autofixes
	$(PYTHON) -m ruff format .
	$(PYTHON) -m ruff check --fix .
