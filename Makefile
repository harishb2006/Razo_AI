.PHONY: bootstrap run seed dev test verify-audit eval buyer client client-install client-build \
        smoke preflight docker-build docker-run

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

# --- Submission & deploy -----------------------------------------------------

# End-to-end check that the guardrails actually fire. Works against a local
# server, a container, or the deployed URL:
#   make smoke BASE=https://razo-ai-api.onrender.com KEY=<api-key>
BASE ?= http://127.0.0.1:8000
KEY  ?= dev-local-key
smoke:
	./scripts/smoke.sh $(BASE) $(KEY)

# Everything that would sink the submission: unpushed work, a committed
# secret, a README claim that no longer holds.
preflight:
	./scripts/preflight.sh

# The exact image Render deploys.
docker-build:
	docker build -t razo-ai-api ./backend

docker-run: docker-build
	docker run --rm -p 8000:8000 -e OFFLINE_MODE=True -e PORT=8000 razo-ai-api
