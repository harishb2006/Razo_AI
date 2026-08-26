.PHONY: bootstrap run seed dev test verify-audit eval

bootstrap:
	cd backend && python3 -m venv venv && ./venv/bin/pip install -q -r requirements.txt

seed:
	cd backend && ./venv/bin/python -m scripts.seed_catalog

run:
	cd backend && ./venv/bin/uvicorn app.main:app --reload --port 8000

dev: run

# Runs fully offline: no Mongo, no LLM key, no network.
test:
	cd backend && OFFLINE_MODE=True ./venv/bin/python -m pytest tests/ -q

verify-audit:
	cd backend && ./venv/bin/python -m scripts.verify_audit

eval:
	cd backend && OFFLINE_MODE=True ./venv/bin/python -m scripts.run_eval
