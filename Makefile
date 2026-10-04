.PHONY: test lint fmt

# Prefer the local venv if it exists, else whatever is on PATH.
PYTHON := $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)

test:
	$(PYTHON) -m pytest -q

# `.` rather than a list of directories. Naming them meant `make lint` broke the
# moment one was removed, and silently skipped anything added outside the list.
lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

fmt:
	$(PYTHON) -m ruff format .
	$(PYTHON) -m ruff check --fix .
