.PHONY: bootstrap run seed dev test verify-audit eval buyer client client-install client-build

bootstrap:
	cd backend && python3 -m venv venv && ./venv/bin/pip install -q -r requirements.txt
	cd client && npm install

seed:
	cd backend && ./venv/bin/python -m scripts.seed_catalog

run:
	cd backend && ./venv/bin/uvicorn app.main:app --reload --port 8000

dev: run

# The React app — run alongside `make run` in a second terminal.
client:
	cd client && npm run dev

client-build:
	cd client && npm run build

# Runs fully offline: no Mongo, no LLM key, no network.
test:
	cd backend && OFFLINE_MODE=True ./venv/bin/python -m pytest tests/ -q

verify-audit:
	cd backend && ./venv/bin/python -m scripts.verify_audit

eval:
	cd backend && OFFLINE_MODE=True ./venv/bin/python -m scripts.run_eval

# The AI buyer (Story 5) — a separate program that discovers this shop from
# its manifest and tries to buy. Needs `make run` in another terminal.
buyer:
	cd backend && ./venv/bin/python -m scripts.buyer_agent
