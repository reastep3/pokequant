.PHONY: setup dev api web worker seed test lint lock build-api build-web compose deploy-api deploy-web

PY := backend/.venv/bin/python

setup:            ## create venv + install backend + frontend deps
	python3 -m venv backend/.venv && backend/.venv/bin/pip install -q -r backend/requirements-dev.in
	cd frontend && npm ci

seed:             ## seed demo universe into DATABASE_URL (SQLite by default)
	cd backend && .venv/bin/python -m scripts.seed_demo

api:              ## run API with reload (SQLite + in-memory cache unless .env says otherwise)
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

worker:
	cd backend && .venv/bin/python -m app.workers

web:
	cd frontend && npm run dev

test:
	cd backend && .venv/bin/python -m pytest -q

lint:
	cd backend && .venv/bin/ruff check app tests scripts
	cd frontend && npm run lint

lock:             ## regenerate pinned backend/requirements.txt from requirements.in
	python3 -m venv /tmp/pq-lock && /tmp/pq-lock/bin/pip install -q -r backend/requirements.in \
	  && { echo "# Pinned runtime lock. Regenerate with: make lock"; /tmp/pq-lock/bin/pip freeze | grep -v '^pip=='; } > backend/requirements.txt \
	  && rm -rf /tmp/pq-lock

build-api:
	docker build --target api -t pokequant-api .
build-web:
	docker build --target web --build-arg NEXT_PUBLIC_API_URL=http://localhost:8000 -t pokequant-web .
compose:
	docker compose up --build

deploy-api:       ## (after review) fly deploy the API/worker app
	fly deploy -c fly.toml
deploy-web:
	fly deploy -c fly.web.toml
