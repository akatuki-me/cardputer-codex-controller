PYTHON ?= python

.PHONY: install typecheck lint test build ci dev fixture publication-check

install:
	$(PYTHON) -m pip install -e ".[dev]"

typecheck:
	$(PYTHON) -m mypy bridge/cardputer_codex_bridge

lint:
	$(PYTHON) -m ruff check .

test:
	$(PYTHON) -m pytest

build:
	$(PYTHON) -m build

publication-check:
	$(PYTHON) scripts/check_publication.py

ci: publication-check typecheck lint test build

dev:
	@echo "M0 app-server client is not implemented yet."

fixture:
	@echo "No runtime fixture exists before M0."
