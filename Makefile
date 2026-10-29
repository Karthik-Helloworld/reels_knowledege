# Phase 0 video pipeline.

.PHONY: edu-setup edu-render-hello edu-test

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

edu-setup:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r core/requirements.txt
	npm install --prefix core
	cd core && npx remotion browser ensure

edu-render-hello:
	cd core && npm run render:hello

edu-test:
	@python=$$(test -x $(PYTHON) && echo $(PYTHON) || echo python3); \
	$$python -m unittest discover -s core/tests -t . -v
	cd core && npx tsc --noEmit
