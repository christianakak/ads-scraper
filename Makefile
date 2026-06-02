.PHONY: install test lint local smoke

install:
	pip install -e ".[dev]"

test:
	PYTHONPATH=. pytest tests/unit/ -v

lint:
	ruff check . --fix

# Run the API locally (hot-reload)
local:
	PYTHONPATH=. python3 -m uvicorn api.app:app --reload --port 8000

# Quick CLI smoke test against a real domain (dummy modes on by default)
smoke:
	PYTHONPATH=. python3 -m cli.audit scan barratthomes.co.uk --geography uk
