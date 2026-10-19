SHELL := bash
.DEFAULT_GOAL := help

MISE_BIN ?= mise
FILES ?= .
TESTS ?= tests
PYTHON_PATHS ?= packages scripts tests
BUNDLE_OUTPUT ?= artifacts/scaffold-bundle
UV_RUN = "$(MISE_BIN)" x -- uv run --locked --all-packages --group dev
VALIDATE = $(UV_RUN) python -m scripts.validation run --target $@ --
args ?=

.PHONY: help setup check test unit ruff ruff-fix format format-check fix mypy lint compile contracts dist package audit policy license governance bundle dist-smoke operation-qualification

help:
	@printf '%s\n' \
		'Targets:' \
		'  make setup         Install locked tools/dependencies and the local commit hook.' \
		'  make unit          Run the local unit suite.' \
		'  make ruff          Check Python lint.' \
		'  make ruff-fix      Apply safe Ruff fixes.' \
		'  make format        Format Python files.' \
		'  make format-check  Check Python formatting.' \
		'  make fix           Run ruff-fix, then format.' \
		'  make mypy          Run strict type checks.' \
		'  make license       Verify Apache-2.0 REUSE provenance.' \
		'  make lint          Run formatting, lint, types and licensing checks.' \
		'  make compile       Byte-compile repository Python files.' \
		'  make test          Run lint and units.' \
		'  make contracts     Validate schemas and synthetic fixtures.' \
		'  make policy        Check component, interface and state contracts.' \
		'  make check         Run lint, contracts, policy, operation coverage and units; check public content.' \
		'  make audit         Audit all locked runtime, build and development dependencies.' \
		'  make dist          Rebuild and inspect every component wheel.' \
		'  make package       Alias for dist.' \
		'  make dist-smoke    Rebuild wheels; prepare and exercise a temporary offline bundle.' \
		'  make bundle        Build a persistent offline scaffold bundle in a new output directory.' \
		'  make governance    Audit live GitHub controls without changing them.' \
		'  make operation-qualification  Check the source-derived operation coverage map.' \
		'' \
		'Variables:' \
		"  args='...'         Forward tool flags to unit, ruff, ruff-fix, format, format-check or mypy." \
		"                     For operation-qualification: check (default), generate or report [--require-current-proofs]." \
		"  TESTS='...'        Select unit test paths or node IDs; default: tests." \
		"  FILES='...'        Select Ruff/format paths; default: ." \
		"  PYTHON_PATHS='...' Select compile paths; default: packages scripts tests." \
		'  BUNDLE_OUTPUT=...  Persistent bundle destination; it must not already exist.' \
		'  MISE_BIN=...       Use a particular mise executable.' \
		'' \
		'Validation receipts: artifacts/validation; includes source identity, selection, timing and results.' \
		'Use TESTS for unit paths and args for pytest flags; test/check also invoke other tools.'

setup:
	"$(MISE_BIN)" install --locked
	"$(MISE_BIN)" x -- uv sync --locked --all-packages --group dev
	git config --local core.hooksPath .githooks

ruff:
	$(VALIDATE) python -m ruff check $(FILES) $(args)

ruff-fix:
	$(VALIDATE) python -m ruff check --fix $(FILES) $(args)

format:
	$(VALIDATE) python -m ruff format $(FILES) $(args)

format-check:
	$(VALIDATE) python -m ruff format --check $(FILES) $(args)

fix: ruff-fix format

mypy:
	$(VALIDATE) python -m mypy $(args)

lint: format-check ruff mypy license

compile:
	$(VALIDATE) python -m compileall -q $(PYTHON_PATHS)

unit:
	$(VALIDATE) python -m pytest -q $(TESTS) $(args)

test: lint unit

contracts:
	$(VALIDATE) python -m scripts.check_contracts

check: lint contracts policy operation-qualification unit
	$(UV_RUN) python scripts/check_public.py

package: dist

dist:
	$(VALIDATE) python -m scripts.distributions build

audit:
	"$(MISE_BIN)" x -- uv export --quiet --locked --all-packages --all-groups --no-emit-workspace --no-hashes --format requirements-txt --output-file .venv/audit-requirements.txt
	$(VALIDATE) python -m pip_audit --strict --no-deps --disable-pip -r .venv/audit-requirements.txt

policy:
	$(VALIDATE) python -m scripts.project_policy --history

license:
	$(VALIDATE) python -m reuse lint

governance:
	$(VALIDATE) python -m scripts.check_repository --scope full

bundle: dist
	$(VALIDATE) python -m scripts.distributions prepare --bundle "$(BUNDLE_OUTPUT)"

dist-smoke: dist
	$(VALIDATE) python -m scripts.distributions smoke-fresh

operation-qualification:
	$(VALIDATE) python -m scripts.operation_qualification $(if $(strip $(args)),$(args),check)
