.PHONY: init run dev dev-backend dev-frontend test lint format clean

init:
	@echo "==> Initializing Backend..."
	cd backend && uv venv && uv sync
	@echo "==> Initializing Frontend..."
	cd frontend && npm install
	@echo "==> Setup complete."

dev-backend:
	cd backend && uv run uvicorn src.narravant.main:app --reload --port 8000 --host 0.0.0.0

dev-frontend:
	cd frontend && npm run dev

# Two-process startup: backend (uvicorn:8000) and frontend (Vite:5173, /api proxy).
# Use `make run` by default; `make dev-backend` / `make dev-frontend` are available for running each separately.
run:
	cd backend && uv run uvicorn src.narravant.main:app --reload --port 8000 --host 0.0.0.0 &
	cd frontend && npm run dev

# Backward-compatible alias. New setup instructions use `make run`.
dev: run

test:
	cd backend && uv run pytest
	cd frontend && npm test -- --run

lint:
	cd backend && uv run ruff check src tests scripts
	cd frontend && npm run lint

format:
	cd backend && uv run ruff format .
	cd frontend && npm run format 2>/dev/null || true

clean:
	rm -rf backend/.venv frontend/node_modules frontend/dist runtime/*.sqlite3*
