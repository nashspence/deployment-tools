SHELL := bash
.DEFAULT_GOAL := help

MISE_BIN ?= mise
PYTHON_PATHS := src scripts tests
UV_RUN = "$(MISE_BIN)" x -- uv run --locked --group dev
args ?=

.PHONY: help setup check test unit ruff ruff-fix format format-check fix mypy lint compile contracts package audit

help:
	@printf '%s\n' \
		'make setup         Install locked tools/dependencies and the local commit hook.' \
		'make unit          Run the local unit suite.' \
		'make lint          Check Ruff formatting, lint and strict mypy.' \
		'make fix           Apply safe Ruff fixes and formatting.' \
		'make check         Check public content, contracts, lint, types and units.' \
		'make audit         Audit all locked runtime, build and development dependencies.' \
		'make package       Build the wheel.'

setup:
	"$(MISE_BIN)" install --locked
	"$(MISE_BIN)" x -- uv sync --locked --group dev
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

lint: format-check ruff mypy

compile:
	$(UV_RUN) python -m compileall -q $(PYTHON_PATHS)

unit:
	$(UV_RUN) python -m pytest -q $(args)

test: lint unit

contracts:
	$(UV_RUN) python -m scripts.check_contracts

check: lint contracts unit
	$(UV_RUN) python scripts/check_public.py

package:
	"$(MISE_BIN)" x -- uv build --wheel

audit:
	"$(MISE_BIN)" x -- uv export --quiet --locked --all-groups --no-emit-project --no-hashes --format requirements-txt --output-file .venv/audit-requirements.txt
	$(UV_RUN) pip-audit --strict --no-deps --disable-pip -r .venv/audit-requirements.txt
