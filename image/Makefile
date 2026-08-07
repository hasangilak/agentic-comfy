SHELL := /bin/bash
.DEFAULT_GOAL := help

# Server port; must match the vite proxy target in vite.config.ts if changed there too.
PORT ?= 8791
WEB_PORT ?= 5173

.PHONY: help install dev dev-server dev-web build typecheck preview health scenes doctor ports kill clean distclean nuke-scenes

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

doctor: ## Check node, mflux binaries, and model weights
	@echo "node        $$(node -v 2>/dev/null || echo MISSING)"
	@echo "npm         $$(npm -v 2>/dev/null || echo MISSING)"
	@echo "zip         $$(command -v zip || echo 'MISSING — make download will fail')"
	@echo "mflux t2i   $$(command -v mflux-generate-flux2 || echo 'MISSING — renders will fail')"
	@echo "mflux edit  $$(command -v mflux-generate-flux2-edit || echo 'MISSING — chain/anchor modes will fail')"
	@echo "arch        $$(uname -m) (arm64 required)"
	@hf=$${HF_HOME:-$${HUGGINGFACE_HUB_CACHE:-$$HOME/.cache/huggingface}}; \
		w=$$(find "$$hf" -maxdepth 3 -iname '*klein*' 2>/dev/null | head -1); \
		echo "weights     $${w:-not found under $$hf (mflux downloads on first run)}"

ports: ## Show what is listening on the app ports
	@lsof -nP -iTCP:$(PORT) -iTCP:$(WEB_PORT) -sTCP:LISTEN || echo "nothing listening on $(PORT) or $(WEB_PORT)"

kill: ## Free the app ports (does not touch running mflux renders)
	@pids=$$(lsof -ti:$(PORT) -ti:$(WEB_PORT) 2>/dev/null); \
	if [ -z "$$pids" ]; then echo "nothing to kill"; else echo "$$pids" | xargs kill && echo "killed: $$pids"; fi

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
