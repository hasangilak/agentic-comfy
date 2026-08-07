# Paper-cutout Reels -- install and run the studio.
#
# Nothing in here spends GPU money. Rendering stays a button in the studio and an explicit
# flag on storyboard.py, which is the whole point of separating the stages: a mistyped make
# target must not be able to start a paid render.
#
#   make install     dependencies (npm + the uv environment)
#   make run         backend on :8787 and the Vite dev server on :5173
#   make serve       build the frontend and serve everything from :8787
#
# One-time, and the only targets that touch Modal: `make login`, `make models`, `make deploy`.

SHELL := /bin/bash
STUDIO := studio
IMAGE := image
BACKEND_PORT ?= 8787
IMAGE_PORT ?= 8791
STAMP := .make/uv.stamp

.DEFAULT_GOAL := help
.PHONY: help install build run backend frontend serve stop images studio login models deploy \
        stop-app clean

help:
	@echo "make install   npm dependencies + resolve the uv environment"
	@echo "make run       backend :$(BACKEND_PORT) + Vite dev server :5173  (hot reload; use the Vite URL)"
	@echo "make serve     build the frontend, then serve it and the API from :$(BACKEND_PORT)"
	@echo "make backend   just the studio server"
	@echo "make frontend  just the Vite dev server"
	@echo "make images    Papercut Studio's render server on :$(IMAGE_PORT) -- where stills come from"
	@echo "make studio    make images + make run, together"
	@echo "make stop      kill anything this Makefile started"
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

# Both servers, backend in the background. `kill 0` on the way out takes the whole process
# group with it -- without it, Ctrl-C leaves uv's python child holding port $(BACKEND_PORT)
# and the next `make run` fails with an address already in use.
run: install
	@echo "backend  http://127.0.0.1:$(BACKEND_PORT)"
	@echo "frontend http://127.0.0.1:5173   <- use this one, it proxies the API through"
	@trap 'kill 0' EXIT INT TERM; \
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

# Where opening stills come from. mflux on this machine, so it spends no money and no image
# quota -- the studio prefers it over agy whenever this is listening, and says which one it
# used in the job log. Separate target because it holds ~18 GB of weights while rendering and
# is not wanted on a session that is only editing a script.
images: $(IMAGE)/node_modules
	PORT=$(IMAGE_PORT) npm --prefix $(IMAGE) run dev:server

# All three processes. Same `kill 0` trap as `run`: Ctrl-C must not leave the image server
# holding :$(IMAGE_PORT) with an mflux render attached to it.
#
# npm directly rather than a recursive `$(MAKE) -C $(IMAGE)`: a recipe line containing
# $(MAKE) is executed even under `make -n`, and this whole trap is one continued line -- so
# a dry run would start all three servers for real. It also keeps every child in this
# process group, which is what `kill 0` relies on.
studio: install $(IMAGE)/node_modules
	@echo "stills   http://127.0.0.1:$(IMAGE_PORT)"
	@echo "backend  http://127.0.0.1:$(BACKEND_PORT)"
	@echo "frontend http://127.0.0.1:5173   <- use this one, it proxies the API through"
	@trap 'kill 0' EXIT INT TERM; \
	PORT=$(IMAGE_PORT) npm --prefix $(IMAGE) run dev:server & \
	uv run studio.py --port $(BACKEND_PORT) & \
	npm --prefix $(STUDIO) run dev; \
	wait

# An in-flight mflux render survives this on purpose -- it holds ~18 GB and killing it loses
# the frame. `make -C image stop-mflux` is the one that ends it.
stop:
	-@pkill -f "studio.py" 2>/dev/null; pkill -f "$(STUDIO)/node_modules/.bin/vite" 2>/dev/null; \
	pkill -f "$(IMAGE)/node_modules/.bin/tsx" 2>/dev/null; \
	echo "stopped"

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
