# GTM Intelligence Engine — Build & Deploy
# Requires: AWS CLI, AWS SAM CLI, Python 3.12

.PHONY: install test lint build deploy local smoke

install:
	pip install -e ".[dev]"

test:
	PYTHONPATH=. pytest tests/unit/ -v

lint:
	ruff check . --fix

# Run the API locally (uvicorn, hot-reload)
local:
	PYTHONPATH=. uvicorn api.app:app --reload --port 8000

# Test a single domain audit locally
smoke:
	PYTHONPATH=. python -m cli.audit run example.co.uk --geography uk --pretty

# SAM build (packages Lambda deployment)
build:
	sam build --use-container

# Guided first deploy (saves samconfig.toml)
deploy-guided:
	sam build && sam deploy --guided

# Subsequent deploys (uses saved samconfig.toml)
deploy:
	sam build && sam deploy

# Tail Lambda logs
logs:
	sam logs -n GTMIntelligenceFunction --tail

# Invoke Lambda locally with test payload
invoke-local:
	sam local invoke GTMIntelligenceFunction \
		--event infrastructure/events/audit_test.json \
		--env-vars infrastructure/env.json

# Run Supabase schema (run in Supabase SQL editor, not locally)
schema:
	@echo "Run infrastructure/supabase_schema.sql in your Supabase SQL editor"
	@echo "https://supabase.com/dashboard/project/_/sql"
