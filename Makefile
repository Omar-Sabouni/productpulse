PYTHON ?= python3
VENV := .venv
VENV_PYTHON := $(VENV)/bin/python
VENV_STREAMLIT := $(VENV)/bin/streamlit
RUNTIME_STAMP := $(VENV)/.productpulse-runtime-installed
DEV_STAMP := $(VENV)/.productpulse-dev-installed

.PHONY: start install dev-install rebuild test lint check clean

start: rebuild
	$(VENV_STREAMLIT) run src/productpulse/dashboard.py

install: $(RUNTIME_STAMP)

$(RUNTIME_STAMP): pyproject.toml $(VENV_PYTHON)
	$(PYTHON) -m pip --python $(VENV_PYTHON) install -e .
	touch $(RUNTIME_STAMP)

dev-install: $(DEV_STAMP)

$(DEV_STAMP): pyproject.toml $(VENV_PYTHON)
	$(PYTHON) -m pip --python $(VENV_PYTHON) install -e ".[dev]"
	touch $(RUNTIME_STAMP) $(DEV_STAMP)

rebuild: install
	$(VENV_PYTHON) -m productpulse.pipeline --rebuild

test: dev-install
	$(VENV_PYTHON) -m pytest

lint: dev-install
	$(VENV_PYTHON) -m ruff check .
	$(VENV_PYTHON) -m ruff format --check .

check: lint test

$(VENV_PYTHON):
	$(PYTHON) -m venv --without-pip $(VENV)

clean:
	rm -rf build dist .pytest_cache .ruff_cache src/productpulse.egg-info
