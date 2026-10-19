.PHONY: setup check test package audit
setup:
	mise exec -- uv sync --locked --group dev
	git config --local core.hooksPath .githooks

check:
	mise exec -- uv run --locked --group dev python scripts/check_public.py
	mise exec -- uv run --locked --group dev python scripts/check_contracts.py
	$(MAKE) test

test:
	mise exec -- uv run --locked --group dev python -m pytest

package:
	mise exec -- uv build --wheel

audit:
	mise exec -- uv export --quiet --locked --all-groups --no-emit-project --no-hashes --format requirements-txt --output-file .venv/audit-requirements.txt
	mise exec -- uv run --locked --group dev pip-audit --strict --no-deps --disable-pip -r .venv/audit-requirements.txt
