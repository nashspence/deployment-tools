.PHONY: check test package
check:
	python scripts/check_public.py
	python scripts/check_contracts.py
	python -m pytest

test:
	python -m pytest

package:
	python -c "from setuptools.build_meta import build_wheel; build_wheel('dist')"
