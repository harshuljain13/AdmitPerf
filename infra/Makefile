# infra-2 — the cluster. `make help` lists everything.
#
# A topology is a VALUES FILE, not a script. Switch with VALUES=:
#
#     make up                    values/single.yaml
#     make up VALUES=disagg
#     make plan VALUES=sliced
#
# Nothing here generates a manifest. Helm templates do the looping and the
# conditionals, so `make show` prints exactly what `make up` would apply.

.DEFAULT_GOAL := help
.PHONY: help plan show lint up down tunnel urls logs status smoke dashboards deps clean

VALUES  ?= single
RELEASE ?= lab
# Subcharts refuse `default`, and a release spread across it leaves stragglers.
NS      ?= admitperf
CHART   := ./chart
VFILE   := ./values/$(VALUES).yaml
PYTHON  := $(shell [ -x ../.venv/bin/python ] && echo ../.venv/bin/python || echo python3)

help:  ## this list
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[1m%-10s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "  VALUES=$(VALUES)   topologies: $$(ls values/*.yaml | xargs -n1 basename | sed 's/.yaml//' | tr '\n' ' ')"

# -- before you rent anything ----------------------------------------------

plan:  ## what deploys, and whether a run on it can mean anything
	@$(PYTHON) plan.py $(VFILE) $(if $(HBM),--hbm $(HBM)) $(if $(GPU),--gpu $(GPU))

show:  ## the full rendered manifests (what `make up` applies)
	@helm template $(RELEASE) $(CHART) -n $(NS) -f $(VFILE)

show-summary:  ## one line per resource
	@helm template $(RELEASE) $(CHART) -n $(NS) -f $(VFILE) \
	  | awk '/^kind:/{k=$$2} /^  name:/{if(k){printf "  %-22s %s\n", k, $$2; k=""}}'

lint:  ## chart syntax, plus every topology renders and every refusal fires
	@helm lint $(CHART)
	@for v in values/*.yaml; do \
	  printf '  %-24s ' "$$v"; \
	  helm template $(RELEASE) $(CHART) -n $(NS) -f $$v >/dev/null 2>&1 \
	    && echo renders || { echo FAILED; exit 1; }; \
	done
	@bash scripts/check-refusals.sh

deps:  ## fetch the subcharts (HAMi, KEDA, kube-prometheus-stack)
	@helm dependency build $(CHART)

# -- the cluster -----------------------------------------------------------

up:  ## bootstrap, sync, secrets, helm install, tunnel
	@VALUES=$(VALUES) RELEASE=$(RELEASE) bash scripts/up.sh

down:  ## uninstall the release and close the tunnel (instance keeps billing)
	@VALUES=$(VALUES) RELEASE=$(RELEASE) bash scripts/down.sh

tunnel:  ## reopen the port-forwards and verify what answers
	@VALUES=$(VALUES) RELEASE=$(RELEASE) bash scripts/tunnel.sh

urls: tunnel  ## alias for tunnel

status:  ## pods on the box
	@VALUES=$(VALUES) bash -c 'source scripts/_env.sh && "$${SSH[@]}" "$$LAMBDA" "$$KUBECTL -n $$NS get pods -o wide"'

smoke:  ## prove the deployed cluster serves, through the gateway
	@VALUES=$(VALUES) bash scripts/smoke.sh

logs:  ## follow the gateway (WHAT=engine|ui for others)
	@VALUES=$(VALUES) WHAT=$(WHAT) bash scripts/logs.sh

# -- assets ----------------------------------------------------------------

dashboards:  ## regenerate the Grafana JSON from lib/observability/dashboards.py
	@cd lib && $(PYTHON) -m observability.dashboards ../chart/files/dashboards
	@ls chart/files/dashboards/*.json | wc -l | xargs printf '  %s dashboards written\n'

clean:  ## drop fetched subcharts and the tunnel pidfile
	@rm -rf $(CHART)/charts $(CHART)/Chart.lock .tunnel.pid
	@echo "cleaned"
