SHELL := bash
.DEFAULT_GOAL := help

MISE_BIN ?= mise
PYTHON_PATHS := packages scripts tests
UV_RUN = "$(MISE_BIN)" x -- uv run --locked --all-packages --group dev
args ?=

.PHONY: help setup check test unit ruff ruff-fix format format-check fix mypy lint compile contracts package audit policy license governance bundle dist-smoke

help:
	@printf '%s\n' \
		'make setup         Install locked tools/dependencies and the local commit hook.' \
		'make unit          Run the local unit suite.' \
		'make lint          Check Ruff formatting, lint and strict mypy.' \
		'make fix           Apply safe Ruff fixes and formatting.' \
		'make check         Check public content, contracts, lint, types and units.' \
		'make audit         Audit all locked runtime, build and development dependencies.' \
		'make package       Build and inspect all component wheels.' \
		'make policy        Check component, interface and state contracts.' \
		'make governance    Audit live GitHub controls without changing them.' \
		'make bundle        Prepare a hash-locked offline scaffold bundle.' \
		'make dist-smoke    Exercise every independent installed distribution.'

setup:
	"$(MISE_BIN)" install --locked
	"$(MISE_BIN)" x -- uv sync --locked --all-packages --group dev
	git config --local core.hooksPath .githooks

ruff:
	$(UV_RUN) python -m ruff check $(PYTHON_PATHS) $(args)

ruff-fix:
	$(UV_RUN) python -m ruff check --fix $(PYTHON_PATHS) $(args)

format:
	$(UV_RUN) python -m ruff format $(PYTHON_PATHS) $(args)

format-check:
	$(UV_RUN) python -m ruff format --check $(PYTHON_PATHS) $(args)

fix: ruff-fix format

mypy:
	$(UV_RUN) python -m mypy $(args)

lint: format-check ruff mypy license

compile:
	$(UV_RUN) python -m compileall -q $(PYTHON_PATHS)

unit:
	$(UV_RUN) python -m pytest -q $(args)

test: lint unit

contracts:
	$(UV_RUN) python -m scripts.check_contracts

check: lint contracts policy unit
	$(UV_RUN) python scripts/check_public.py

package:
	"$(MISE_BIN)" x -- uv build --all-packages --wheel
	$(UV_RUN) python -m scripts.check_wheel dist

audit:
	"$(MISE_BIN)" x -- uv export --quiet --locked --all-packages --all-groups --no-emit-workspace --no-hashes --format requirements-txt --output-file .venv/audit-requirements.txt
	$(UV_RUN) pip-audit --strict --no-deps --disable-pip -r .venv/audit-requirements.txt

policy:
	$(UV_RUN) python -m scripts.project_policy --history

license:
	$(UV_RUN) python -m reuse lint

governance:
	$(UV_RUN) python -m scripts.check_repository --scope full

bundle:
	$(UV_RUN) python -m scripts.distributions prepare --bundle artifacts/scaffold-bundle

dist-smoke:
	$(UV_RUN) python -m scripts.distributions smoke --bundle artifacts/scaffold-bundle
