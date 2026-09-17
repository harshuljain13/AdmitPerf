.PHONY: diagrams diagrams-check test lint dashboard mock-engine

MMD := $(wildcard docs/architecture/*.mmd) $(wildcard reports/figures/*.mmd)
PNG := $(MMD:.mmd=.png)

## Render every docs/architecture/*.mmd to a matching .png
diagrams: $(PNG)

%.png: %.mmd
	mmdc -i $< -o $@ -b white -s 3

## Fail if any .png is older than its .mmd source (use in CI)
diagrams-check:
	@stale=""; \
	for f in $(MMD); do \
		p="$${f%.mmd}.png"; \
		if [ ! -f "$$p" ] || [ "$$f" -nt "$$p" ]; then stale="$$stale $$p"; fi; \
	done; \
	if [ -n "$$stale" ]; then \
		echo "Stale diagrams:$$stale"; \
		echo "Run 'make diagrams' and commit the result."; \
		exit 1; \
	fi; \
	echo "All diagrams current."

# Prefer the local venv if it exists, else fall back to whatever is on PATH.
PYTHON := $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m ruff check src tests dashboard
	$(PYTHON) -m ruff format --check src tests dashboard

# The app: configure an experiment, run the pipeline, read the results.
dashboard:
	$(PYTHON) -m streamlit run dashboard/app.py

# A mock engine so the dashboard has something to run against without a GPU.
# Start it in a second terminal; the Run page checks the port.
mock-engine:
	$(PYTHON) scripts/mock_vllm.py --port 8000 --capacity 4
