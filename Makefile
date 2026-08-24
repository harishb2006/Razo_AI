.PHONY: bootstrap run seed dev

bootstrap:
	cd backend && python3 -m venv venv && ./venv/bin/pip install -q -r requirements.txt

seed:
	cd backend && ./venv/bin/python -m scripts.seed_catalog

run:
	cd backend && ./venv/bin/uvicorn app.main:app --reload --port 8000

dev: run
