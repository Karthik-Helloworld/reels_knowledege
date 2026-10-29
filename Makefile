# Local educational video pipeline.

.PHONY: edu-setup edu-render-hello edu-test edu-video review publish

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

edu-setup:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r core/requirements.txt
	$(PIP) install -r edu/requirements.txt
	npm install --prefix core
	cd core && npx remotion browser ensure

edu-render-hello:
	cd core && npm run render:hello

edu-video:
	@python=$$(test -x $(PYTHON) && echo $(PYTHON) || echo python3); \
	if [ -n "$(FACT_FIXTURE)" ]; then \
	  $$python -m edu.scripts.run_video --topic "$(TOPIC)" --fact-fixture "$(FACT_FIXTURE)"; \
	else \
	  $$python -m edu.scripts.run_video --topic "$(TOPIC)"; \
	fi

review:
	@python=$$(test -x $(PYTHON) && echo $(PYTHON) || echo python3); \
	$$python -m edu.scripts.review $(ARGS)

publish:
	@python=$$(test -x $(PYTHON) && echo $(PYTHON) || echo python3); \
	$$python -m edu.publish.run $(ARGS)

edu-test:
	@python=$$(test -x $(PYTHON) && echo $(PYTHON) || echo python3); \
	$$python -m unittest discover -s core/tests -t . -v; \
	$$python -m unittest discover -s edu/tests -t . -v; \
	$$python -m ruff check edu
	cd core && npx tsc --noEmit
