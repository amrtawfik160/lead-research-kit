.PHONY: help setup dev api web start build test layout-check layout-test release-check check run-daily run-daily-execute status providers demo team-list filters schema

PYTHON ?= python3
WORKSPACE ?= $(abspath dashboard/.data/sample-workspace)
TARGET ?= 20
EXECUTE ?= 0
CLI = PYTHONPATH=src $(PYTHON) -m lead_research

help:
	@echo "Veinore — AI Agent & Operator Entrypoints"
	@echo ""
	@echo "Setup & Run:"
	@echo "  make setup              Install dashboard dependencies and seed the synthetic sample workspace"
	@echo "  make dev                Start both loopback API (127.0.0.1:8787) and Next.js dev UI (127.0.0.1:3000)"
	@echo "  make api                Start only the loopback Python API server on 127.0.0.1:8787"
	@echo "  make web                Start only the Next.js dev UI server on 127.0.0.1:3000"
	@echo "  make build              Build the Next.js dashboard for production"
	@echo "  make start              Start production API (:8787) and Next.js server (:3000)"
	@echo ""
	@echo "Daily Lead Runs & CLI (JSON output, works with zero API keys):"
	@echo "  make run-daily          Dry-run daily lead preview (WORKSPACE=..., TARGET=20, EXECUTE=1 for live)"
	@echo "  make run-daily-execute  Execute daily lead run (WORKSPACE=..., TARGET=20)"
	@echo "  make status             Print workspace status as JSON (WORKSPACE=...)"
	@echo "  make providers          List all 41 providers and required/optional env vars as JSON"
	@echo "  make demo               Run offline synthetic demo into a temporary directory"
	@echo "  make team-list          Print the conjoined team list as JSON (WORKSPACE=...)"
	@echo "  make filters            Print saved filters as JSON (WORKSPACE=...)"
	@echo "  make schema             Print the CLI command catalog as JSON"
	@echo ""
	@echo "Checks & Verification:"
	@echo "  make test               Run all Python unit tests (offline)"
	@echo "  make layout-check       Run static dashboard layout & primitive guard (check_dashboard_layout.py)"
	@echo "  make release-check      Run secret & public-release guardrail scan (release_check.py)"
	@echo "  make layout-test        Run Playwright layout contract across all 12 routes at 1280px & 1440px"
	@echo "  make check              Run unit tests, layout-check, release-check, and dashboard build"

setup:
	cd dashboard && npm install
	PYTHONPATH=src:dashboard $(PYTHON) -c "from pathlib import Path; from api.sample import seed_sample; p = Path('dashboard/.data/sample-workspace').resolve(); seed_sample(p); print('{\"ok\": true, \"sample_workspace\": \"' + str(p) + '\"}')"

dev:
	cd dashboard && npm run dev

api:
	cd dashboard && $(PYTHON) server.py --host 127.0.0.1 --port 8787

web:
	cd dashboard && npx next dev --port 3000

build:
	cd dashboard && npm run build

start:
	cd dashboard && npm start

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

layout-check:
	$(PYTHON) scripts/check_dashboard_layout.py

release-check:
	$(PYTHON) scripts/release_check.py .

layout-test:
	cd dashboard && npm run layout:test

check: test layout-check release-check build

run-daily:
	@PYTHONPATH=src:dashboard $(PYTHON) -c "import os, sys; from pathlib import Path; from lead_research.configuration import default_config, save_config; from lead_research.store import Store; ws = Path(sys.argv[1]).expanduser().resolve(); sample = Path('dashboard/.data/sample-workspace').resolve(); \
from api.sample import seed_sample; \
seed_sample(ws) if ws == sample and not (ws / 'providers.json').exists() else None; \
ws.mkdir(mode=0o700, parents=True, exist_ok=True); \
save_config(ws / 'providers.json', default_config()) if not (ws / 'providers.json').exists() else None; \
Store(ws / 'research.sqlite3').close()" "$(WORKSPACE)"
	$(CLI) --workspace "$(WORKSPACE)" daily --target "$(TARGET)" $(if $(filter 1 true yes,$(EXECUTE)),--execute,) --json

run-daily-execute:
	$(MAKE) run-daily WORKSPACE="$(WORKSPACE)" TARGET="$(TARGET)" EXECUTE=1

status:
	@PYTHONPATH=src:dashboard $(PYTHON) -c "import sys; from pathlib import Path; from api.sample import seed_sample; ws = Path(sys.argv[1]).expanduser().resolve(); sample = Path('dashboard/.data/sample-workspace').resolve(); seed_sample(ws) if ws == sample and not (ws / 'providers.json').exists() else None" "$(WORKSPACE)"
	$(CLI) --workspace "$(WORKSPACE)" status --json

providers:
	@PYTHONPATH=src:dashboard $(PYTHON) -c "import sys; from pathlib import Path; from api.sample import seed_sample; ws = Path(sys.argv[1]).expanduser().resolve(); sample = Path('dashboard/.data/sample-workspace').resolve(); seed_sample(ws) if ws == sample and not (ws / 'providers.json').exists() else None" "$(WORKSPACE)"
	$(CLI) --workspace "$(WORKSPACE)" provider list --json

demo:
	@TMP_DIR="$$(mktemp -d)"; \
	trap 'rm -rf "$$TMP_DIR"' EXIT; \
	$(CLI) --workspace "$$TMP_DIR/ws" demo --output "$$TMP_DIR/out" --json

team-list:
	$(CLI) --workspace "$(WORKSPACE)" team list --json

filters:
	$(CLI) --workspace "$(WORKSPACE)" filter list --json

schema:
	$(CLI) schema --json
