# =============================================================================
# RUNECLAW Makefile v2 -- Developer convenience targets
# =============================================================================

.PHONY: install lock lock-check lint format run-cli run-telegram run-scan run-api \
        test test-core test-security test-cov deploy health logs \
        logs-api logs-bot cert db-init db-users db-shell clean help

PYTHON   ?= python3
COMPOSE  := docker compose
API_URL  ?= http://localhost:8000

# -- Colours -----------------------------------------------------------------
CYAN  := \033[36m
RESET := \033[0m

help: ## Show all available targets
	@echo ""
	@echo "  RUNECLAW v2 -- make targets"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  $(CYAN)%-18s$(RESET) %s\n", $$1, $$2}'
	@echo ""

# -- Setup -------------------------------------------------------------------
# Two installs and no third. The pip wheel carries its own hash. Everything
# else comes from requirements.lock under --require-hashes: pip checks every
# file against the hashes in the lock and refuses a package the lock does not
# name. A third line used to name fastapi and uvicorn[standard] here, with no
# hash, and brought in the extra's four packages from wherever pip found them;
# the lock pins all of that now (requirements.in).
install: ## Install all Python deps from the hashed requirements.lock (bot + api_bridge)
	$(PYTHON) -m pip install "pip @ https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70"
	$(PYTHON) -m pip install --require-hashes -r requirements.lock

# Both need pip-tools at the pinned version; requirements-piptools.in says how
# to build that environment. Run them with its python: make lock PYTHON=...
lock: ## Regenerate every hashed lock from its *.in file
	$(PYTHON) scripts/compile_locks.py

lock-check: ## Compile again and compare with the committed locks (what CI runs)
	$(PYTHON) scripts/compile_locks.py --check

# -- Code quality ------------------------------------------------------------
lint: ## ruff + mypy
	ruff check bot/ api_bridge.py
	ruff format bot/ api_bridge.py --check
	mypy bot/ --ignore-missing-imports

format: ## Auto-format with ruff
	ruff format bot/ api_bridge.py
	ruff check bot/ api_bridge.py --fix

# -- Local run ---------------------------------------------------------------
run-cli: ## Start interactive CLI
	$(PYTHON) -m bot.main --mode cli

run-telegram: ## Start Telegram bot
	$(PYTHON) -m bot.main --mode telegram

run-scan: ## One-shot market scan
	$(PYTHON) -m bot.main --mode scan

run-api: ## Start API bridge on :8000
	$(PYTHON) -m uvicorn api_bridge:app --host 0.0.0.0 --port 8000 --reload

# -- Tests -------------------------------------------------------------------
test: ## Run full test suite
	$(PYTHON) -m pytest tests/ -v --tb=short

test-core: ## Core tests only
	$(PYTHON) -m pytest tests/test_core.py -v --tb=short

test-security: ## Security tests only
	$(PYTHON) -m pytest tests/test_security.py -v --tb=short

test-cov: ## Tests with coverage report
	$(PYTHON) -m pytest tests/ -v --tb=short \
		--cov=bot --cov-report=html --cov-report=term-missing

# -- Docker / production -----------------------------------------------------
deploy: ## Build image + start all services (bot, api_bridge, redis, nginx)
	BUILD_SHA=$$(git rev-parse --short HEAD) \
		$(COMPOSE) up -d --build --wait

health: ## Check API bridge health endpoint
	@awk -F= '/^DASHBOARD_TOKEN=/{print "Authorization: Bearer " substr($$0, index($$0, "=")+1); exit}' .env \
		| curl -sf --header @- $(API_URL)/health \
		| python3 -m json.tool \
		|| echo "API not reachable at $(API_URL)"

logs: ## Follow all service logs
	$(COMPOSE) logs -f --tail=100

logs-api: ## Follow api_bridge logs only
	$(COMPOSE) logs -f --tail=100 api_bridge

logs-bot: ## Follow bot logs only
	$(COMPOSE) logs -f --tail=100 bot

# -- Database ----------------------------------------------------------------
db-init: ## Initialise SQLite user database
	$(PYTHON) -c "from bot.db.models import init_db; init_db(); print('DB ready')"

db-users: ## List all registered users
	$(PYTHON) -c "from bot.db.models import list_users, user_count; \
	  print(user_count()); \
	  [print(u['email'], '|', u['plan'], '|', 'linked' if u['chat_id'] else 'unlinked') \
	   for u in list_users()]"

db-shell: ## Open SQLite shell on data/runeclaw.db
	sqlite3 data/runeclaw.db

# -- Cert helper (run once on VPS) -------------------------------------------
cert: ## Get Let's Encrypt cert (set DOMAIN= e.g. make cert DOMAIN=runeclaw.example.com)
	certbot certonly --standalone -d $(DOMAIN)

# -- Cleanup -----------------------------------------------------------------
clean: ## Remove caches and build artefacts
	find . -type d -name __pycache__  -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache  -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache  -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf dist/ build/ htmlcov/
