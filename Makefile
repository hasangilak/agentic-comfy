# Paper-cutout Reels -- install and run the studio.
#
# Nothing in here spends GPU money. Rendering stays a button in the studio and an explicit
# flag on storyboard.py, which is the whole point of separating the stages: a mistyped make
# target must not be able to start a paid render.
#
#   make install     dependencies (npm + the uv environment)
#   make qwen        pull the local language model (one-time, ~23 GiB)
#   make run         all three servers: stills on :8791, backend on :8787, Vite on :5173
#   make serve       build the frontend and serve everything from :8787
#
# One-time, and the only targets that touch Modal: `make login`, `make models`, `make deploy`.

SHELL := /bin/bash
STUDIO := studio
IMAGE := image
BACKEND_PORT ?= 8787
IMAGE_PORT ?= 8791
STAMP := .make/uv.stamp
# The model that writes the scripts, edits the board and looks at the stills. Keep in step
# with config.QWEN_MODEL, or override both together (PAPERREEL_QWEN_MODEL / QWEN_MODEL=...).
QWEN_MODEL ?= qwen3.6

.DEFAULT_GOAL := help
.PHONY: help install build run backend frontend serve stop stop-mflux images studio qwen \
        login models deploy stop-app clean

help:
	@echo "make install   npm dependencies + resolve the uv environment"
	@echo "make run       stills :$(IMAGE_PORT) + backend :$(BACKEND_PORT) + Vite dev server :5173  (use the Vite URL)"
	@echo "make serve     build the frontend, then serve it and the API from :$(BACKEND_PORT)"
	@echo "make backend   just the studio server"
	@echo "make frontend  just the Vite dev server"
	@echo "make images    Papercut Studio's render server on :$(IMAGE_PORT) -- where stills come from"
	@echo "make studio    an alias for make run"
	@echo "make qwen      pull $(QWEN_MODEL) into Ollama -- the script writer and still reviewer"
	@echo "make stop      kill every server either project has running (an mflux render survives)"
	@echo "make stop-mflux  end an in-flight mflux render -- that frame is lost"
	@echo
	@echo "one-time, touches Modal:"
	@echo "make login     uvx modal setup"
	@echo "make models    download ~59 GiB of weights into a Modal Volume (needed once)"
	@echo "make deploy    deploy the GPU app (free until a request arrives)"
	@echo "make stop-app  stop the GPU app now"

install: $(STUDIO)/node_modules $(STAMP)

# Only reinstalls when the manifests actually change.
$(STUDIO)/node_modules: $(STUDIO)/package.json $(STUDIO)/package-lock.json
	npm --prefix $(STUDIO) install
	@touch $@

# studio.py declares its dependencies inline (PEP 723), so `uv run` builds the environment
# on first use. Doing it here means `make run` starts immediately instead of resolving.
$(STAMP): studio.py
	uv run studio.py --help > /dev/null
	@mkdir -p $(dir $@) && touch $@

build: $(STUDIO)/node_modules
	npm --prefix $(STUDIO) run build

# All three servers, the two background ones first. `kill 0` on the way out takes the whole
# process group with it -- without it, Ctrl-C leaves uv's python child holding port
# $(BACKEND_PORT) and the next `make run` fails with an address already in use, and the image
# server holding :$(IMAGE_PORT) with an mflux render attached to it.
#
# npm directly rather than a recursive `$(MAKE) -C $(IMAGE)`: a recipe line containing
# $(MAKE) is executed even under `make -n`, and this whole trap is one continued line -- so
# a dry run would start all three servers for real. It also keeps every child in this
# process group, which is what `kill 0` relies on.
run: install $(IMAGE)/node_modules
	@echo "stills   http://127.0.0.1:$(IMAGE_PORT)"
	@echo "backend  http://127.0.0.1:$(BACKEND_PORT)"
	@echo "frontend http://127.0.0.1:5173   <- use this one, it proxies the API through"
	@# Said before the servers start rather than discovered as a failed job three clicks in:
	@# with no model there is no script, no conversation and no caption.
	@if ollama list 2>/dev/null | grep -q '^$(QWEN_MODEL)'; then \
		echo "model    $(QWEN_MODEL) via ollama"; \
	else \
		echo "model    $(QWEN_MODEL) NOT AVAILABLE -- run 'make qwen', and start Ollama"; \
	fi
	@trap 'kill 0' EXIT INT TERM; \
	PORT=$(IMAGE_PORT) npm --prefix $(IMAGE) run dev:server & \
	uv run studio.py --port $(BACKEND_PORT) & \
	npm --prefix $(STUDIO) run dev; \
	wait

backend: $(STAMP)
	uv run studio.py --port $(BACKEND_PORT)

frontend: $(STUDIO)/node_modules
	npm --prefix $(STUDIO) run dev

# One URL, no Vite: the backend mounts studio/dist itself.
serve: build $(STAMP)
	uv run studio.py --port $(BACKEND_PORT)

$(IMAGE)/node_modules: $(IMAGE)/package.json $(IMAGE)/package-lock.json
	npm --prefix $(IMAGE) install
	@touch $@

# Where opening stills come from, and the only place they come from: mflux on this machine, so
# it spends no money. With this not listening a beat's still has to be an upload, which the
# studio says on the node rather than failing a click. Separate target because it holds ~18 GB
# of weights while rendering and is not wanted on a session that is only editing a script.
images: $(IMAGE)/node_modules
	PORT=$(IMAGE_PORT) npm --prefix $(IMAGE) run dev:server

# Everything the studio needs from Ollama, in one place. The weights are ~23 GiB, so this is
# a one-time download; Ollama itself has to already be running (`ollama serve`, or the app).
#
# It does NOT start Ollama: it is a system service on most installs and a desktop app on the
# rest, and a Makefile that starts one of those has to know which -- and then owns stopping it.
# `make studio` reports whether it is answering instead, which is the part that is useful.
qwen:
	ollama pull $(QWEN_MODEL)
	@ollama show $(QWEN_MODEL) | sed -n '/Capabilities/,/^$$/p'

# `make studio` was the three-server target before `run` became it. Kept as an alias because
# it is what the README, the docs and a year of muscle memory say.
studio: run

# Everything either project can leave behind: the studio server, both Vite dev servers, the
# image project's tsx watcher and the `concurrently` wrapper `make -C image dev` starts them
# under. Matched by path under each project's node_modules rather than by binary name, so a
# vite belonging to some other checkout is never a candidate -- and so a tool added to either
# project later is covered without editing this list.
#
# The patterns are split across a quote for the same reason image/Makefile splits its own:
# pgrep -f sees this recipe's shell too, and an unsplit pattern matches the shell that is
# running it.
#
# An in-flight mflux render survives this on purpose -- it holds ~18 GB and killing it loses
# the frame. `make stop-mflux` is the one that ends it, and the note below fires only when
# there is one to end.
stop:
	@pids=$$( { pgrep -f "studio""\.py"; \
	            pgrep -f "$(CURDIR)/$(STUDIO)/node_""modules/"; \
	            pgrep -f "$(CURDIR)/$(IMAGE)/node_""modules/"; } 2>/dev/null | sort -un); \
	if [ -z "$$pids" ]; then echo "nothing running"; else \
		kill $$pids 2>/dev/null || true; \
		echo "stopped: $$(echo $$pids | tr '\n' ' ')"; \
	fi; \
	m=$$(pgrep -f "mflux-generate-""flux2" || true); \
	if [ -n "$$m" ]; then \
		echo "note: an mflux render is still running (pid $$(echo $$m | tr '\n' ' ')) and holds ~18 GB"; \
		echo "      'make stop-mflux' ends it -- the frame is lost, the scene on disk is not"; \
	fi

# Plain `make` rather than $(MAKE): a recipe line containing $(MAKE) runs even under
# `make -n`, and a dry run must not kill a render.
stop-mflux:
	@make -C $(IMAGE) stop-mflux

login:
	uvx modal setup

# Lands in a persistent Volume, so cold starts never re-pay for it. Never needs re-running.
models:
	uvx modal run comfyui_minimax_h3.py::download_models

deploy:
	uvx modal deploy comfyui_minimax_h3.py

stop-app:
	uvx modal app stop comfyui-minimax-h3 --yes

clean:
	rm -rf $(STUDIO)/dist $(STUDIO)/.vite $(STUDIO)/tsconfig.tsbuildinfo .make
