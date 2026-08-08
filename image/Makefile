SHELL := /bin/bash
.DEFAULT_GOAL := help

# Server port; must match the vite proxy target in vite.config.ts if changed there too.
PORT ?= 8791
WEB_PORT ?= 5173

.PHONY: help install dev dev-server dev-web build typecheck preview health scenes doctor ports stop clean distclean nuke-scenes

help: ## Show this help
	@awk 'BEGIN{FS=":.*##"} /^[a-zA-Z_-]+:.*##/{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: node_modules ## Install dependencies

node_modules: package.json package-lock.json
	npm install
	@touch node_modules

dev: install ## Run render server + web app together
	npm run dev

dev-server: install ## Run only the render server (override with PORT=…)
	PORT=$(PORT) npm run dev:server

dev-web: install ## Run only the vite web app
	npm run dev:web

build: install ## Typecheck both TS projects and build the web client
	npm run build

typecheck: install ## Typecheck only (src, shared, server, vite.config)
	npx tsc -b

preview: ## Serve the built web client
	npm run preview

health: ## Hit the render server health endpoint
	@curl -sf http://127.0.0.1:$(PORT)/api/health | python3 -m json.tool || \
		{ echo "server not responding on 127.0.0.1:$(PORT) — run 'make dev-server'"; exit 1; }

scenes: ## List scenes on disk with their frame counts
	@if [ ! -d out/scenes ]; then echo "no scenes yet"; exit 0; fi; \
	for d in out/scenes/*/; do \
		[ -f "$$d/scene.json" ] || continue; \
		id=$$(basename "$$d"); \
		title=$$(python3 -c "import json,sys;s=json.load(open('$$d/scene.json'));print(s.get('title') or '(untitled)', '·', s.get('status'))"); \
		frames=$$(ls "$$d" | grep -c '^frame-.*\.png$$' || true); \
		printf "  %-10s %-40s %s frames\n" "$$id" "$$title" "$$frames"; \
	done

doctor: ## Check Node, Gemini model selection, and API key configuration
	@echo "node        $$(node -v 2>/dev/null || echo MISSING)"
	@echo "npm         $$(npm -v 2>/dev/null || echo MISSING)"
	@echo "zip         $$(command -v zip || echo 'MISSING — make download will fail')"
	@echo "provider    Google Gemini Nano Banana"
	@echo "model       $${GEMINI_IMAGE_MODEL:-gemini-3-pro-image}"
	@if rg -q '^X-GOOG-API-KEY[[:space:]]*=' ../.env .env 2>/dev/null; then \
		echo "api key     configured"; \
	else \
		echo "api key     MISSING — set X-GOOG-API-KEY in .env"; \
	fi

ports: ## Show what is listening on the app ports
	@lsof -nP -iTCP:$(PORT) -iTCP:$(WEB_PORT) -sTCP:LISTEN || echo "nothing listening on $(PORT) or $(WEB_PORT)"

# The patterns below are split across a quote so pgrep cannot match this recipe's own shell.
stop: ## Stop the render server and web app
	@pat="$(CURDIR)/node_""modules/"; \
	pids=$$(pgrep -f "$$pat" || true); \
	if [ -z "$$pids" ]; then echo "nothing running"; else \
		srv=0; web=0; \
		for p in $$pids; do \
			case "$$(ps -o command= -p $$p 2>/dev/null)" in \
				*concurrently*) srv=1; web=1 ;; \
				*tsx*) srv=1 ;; \
				*vite*|*esbuild*) web=1 ;; \
			esac; \
		done; \
		kill $$pids 2>/dev/null || true; \
		if [ $$srv = 1 ]; then echo "stopped render server on 127.0.0.1:$(PORT)"; else echo "render server was not running"; fi; \
		if [ $$web = 1 ]; then echo "stopped web app on localhost:$(WEB_PORT)"; else echo "web app was not running"; fi; \
		echo "pids: $$(echo $$pids | tr '\n' ' ')"; \
	fi

clean: ## Remove build output and TS build info
	rm -rf dist node_modules/.tmp

distclean: clean ## Also remove node_modules
	rm -rf node_modules

nuke-scenes: ## DESTRUCTIVE: delete every rendered scene in out/ (CONFIRM=yes required)
	@if [ "$(CONFIRM)" != "yes" ]; then \
		echo "This permanently deletes out/scenes and out/uploads — all rendered frames and scene history."; \
		echo "out/ is gitignored, so there is no way to recover it."; \
		echo "Re-run with: make nuke-scenes CONFIRM=yes"; \
		exit 1; \
	fi
	rm -rf out/scenes out/uploads
	@echo "scenes and uploads deleted"
