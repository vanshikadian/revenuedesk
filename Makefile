PY := .venv/bin/python

# Load local secrets from .env if present (gitignored). docker compose
# reads the same file on its own.
-include .env
export

.PHONY: setup seed demo serve agent test lint fmt docker-up clean

.venv/bin/activate: requirements.txt requirements-dev.txt
	python3.11 -m venv .venv || python3 -m venv .venv
	.venv/bin/pip install -q -r requirements-dev.txt
	@touch .venv/bin/activate

setup: .venv/bin/activate  ## create venv + install pinned deps

seed: setup  ## generate vendor CSVs and load the DuckDB warehouse
	$(PY) -m revops.seed

demo: setup seed  ## one command: seed + serve on http://localhost:8000
	$(PY) -m uvicorn revops.api:app --port 8000

serve: setup  ## serve with auto-reload (dev)
	$(PY) -m uvicorn revops.api:app --port 8000 --reload

agent: setup  ## run one agent pass from the CLI
	$(PY) -m revops.agent

test: setup  ## pytest (offline, no keys or network needed)
	$(PY) -m pytest tests/ -q

lint: setup  ## ruff + black, check only
	.venv/bin/ruff check revops tests
	.venv/bin/black --check revops tests

fmt: setup  ## auto-fix lint + format
	.venv/bin/ruff check --fix revops tests
	.venv/bin/black revops tests

docker-up:  ## build + run in Docker
	docker compose up --build

clean:
	rm -rf data .pytest_cache .ruff_cache
