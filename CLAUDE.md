# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Two apps that make one thing. **paperreel** (this directory) turns a concept into a vertical
1080×1920 Instagram Reel in paper-cutout stop-motion, rendering MiniMax-H3 on a single GPU on
Modal via ComfyUI. **Papercut Studio** (`image/`, a separate project with its own CLAUDE.md)
renders the opening stills locally with mflux. paperreel calls it over HTTP; see
"The two projects" below.

Two front ends drive paperreel: `storyboard.py` / `reel.py` (CLI) and a local FastAPI + React
node canvas ("the studio").

`README.md` is the design document — it explains *why* most of the constants and joins are
what they are. Read it before changing render behaviour, prompt scaffolding, or costs.

## Commands

```bash
make install      # npm deps + resolve the uv environment (PEP 723 inline deps in studio.py)
make run          # backend :8787 + Vite :5173 — use the Vite URL, it proxies /api and /media
make studio       # the above plus the image server on :8791 — the everyday target
make images       # just Papercut Studio's render server (make -C image dev-server)
make serve        # build the frontend, serve everything from :8787
make backend      # studio.py only
make frontend     # vite only
make stop         # kill whatever the Makefile started, in both projects
make build        # npm --prefix studio run build  (tsc -b && vite build — this is the typecheck)

make login        # uvx modal setup                        one-time, touches Modal
make models       # ~59 GiB of weights into a Modal Volume one-time, never needs re-running
make deploy       # deploy the GPU app (free until a request arrives)
make stop-app     # stop the GPU app now
```

There is no test suite and no linter config. `npm --prefix studio run build` is the only
static check (TypeScript); Python has none. The image project has its own `make typecheck`.

Python is always run through `uv run` — `studio.py`, `storyboard.py` and `reel.py` each
declare their dependencies inline (PEP 723). Modal is always `uvx modal ...`.

### Money

**Nothing in the Makefile can start a paid render.** That is deliberate and must stay true.
Rendering is only reachable from `storyboard.py --render` / `--all`, `reel.py`, or the
studio's render button. Planning, asset generation, uploads and compositing are free.

Never run a render to "verify" a change. A 4×10 s reel costs ~$1.13.

`agy` (Antigravity CLI) calls bill against a Google plan quota, not an API key, and refresh on
a ~5 hour window. **Image generation is roughly five per window.** That number is why the whole
design prefers chained beats over per-beat stills — but it only applies when agy is the stills
backend. With the image server up, stills are free and unmetered, so don't write new UI copy or
docs that state the quota unconditionally.

## Architecture

```
paperreel/config.py     every tunable + the measurement behind it; the prompt scaffold
paperreel/board.py      the board document and ALL derived state
paperreel/comfy.py      ComfyUI HTTP/WS client + the H3 graph builder
paperreel/pipeline.py   Modal app lifecycle + chained batch rendering (CLI path)
paperreel/render.py     the same, with per-beat telemetry and cancellation (studio path)
paperreel/papercut.py   HTTP client for image/ — the local mflux stills backend
paperreel/planner.py    agy: storyboard generation and still generation
paperreel/script.py     adopting a script written outside the studio
paperreel/agent.py      agy conversation -> board operations
paperreel/jobs.py       one worker thread, one job queue, one event stream
paperreel/api.py        FastAPI routes + SSE
paperreel/media.py      ffmpeg: cutout, compose, fit, last-frame, tail, stitch
comfyui_minimax_h3.py   the Modal GPU app (ComfyUI on RTX-PRO-6000)
studio/src/             React 19 + @xyflow/react canvas, Tailwind 4, Vite
```

### The two projects

```
image/  Papercut Studio        paperreel  this repo            Modal
  mflux · flux2-klein-4b   ──▶   storyboard.json      ──▶   MiniMax-H3
  local, free, no quota          board + joins              the paid stage
  Express :8791                  FastAPI :8787
```

**paperreel is the orchestrator; image is a renderer.** The seam is HTTP on loopback, not
shared code — neither project imports the other, and paperreel runs unchanged when the image
server is down. Only two things cross the boundary:

- `paperreel/papercut.py` → the image server's REST + SSE API (`POST /api/scenes`,
  `POST /api/scenes/:id/render`, `GET /api/scenes/:id/events`, `GET /files/...`).
- `config.PAPERCUT_ASPECT` names the `9:16-reel` preset (768×1344) in
  `image/shared/types.ts`. It exists *only* so stills match `GEN_WIDTH × GEN_HEIGHT` and
  never get cover-cropped by `media.fit_frame`. **If you change `GEN_WIDTH`/`GEN_HEIGHT`,
  change that preset too** — nothing enforces the match, it just silently starts cropping.

`config.ASSET_BACKEND` (`auto` | `papercut` | `agy`) picks the stills generator;
`api.stills_backend()` resolves it by probing `/api/health` per job, never cached. Both
backends write the same `beat<n>_asset.png`, so nothing downstream can tell them apart, and
both compose the prompt from `config.ASSET_STYLE_SUFFIX` for the same reason.

Beat runs are grouped by conditioning image in `papercut._runs`, lazily — a beat with no
reference yet renders alone first because its still *becomes* the reference for the rest
(`Board.reference_for`). Grouping up front would put a fresh board's whole cast in one
unconditioned scene, which is the cross-scene inconsistency the reference was added to fix.

paperreel maps onto Papercut's model as: `style_bible` + suffix → scene `style`, per-beat
`asset_prompt` → frame `beat`, cast reference → `anchor` consistency mode. Never `chain`:
these are hard cuts, and chaining drifts by frame three and serialises for no gain.

**Product tension worth knowing:** `image/PRODUCT.md` principle 3 says nothing leaves the
machine. That still holds of the image project itself — but a still it renders *is* uploaded
to Modal when paperreel renders the video. The promise is about the image tool, not about
what the user does with its output; don't "fix" it by adding uploads to `image/`.

### `reels/<slug>/storyboard.json` is the only database

There is no other store. Everything else — beat state, staleness, cost, what needs
rendering — is **derived** from the JSON plus what is on disk (`board.py`, "Derived state").
Nothing derived is ever persisted. That is what lets the CLI, a hand edit, and the canvas
coexist without drifting.

Consequence: if you add something a render depends on, it must go into
`Board.own_fingerprint` / `render_fingerprint`, or the canvas will show `rendered` on a beat
whose inputs changed.

`.gitignore` keeps `reels/*/storyboard.json` and nothing else under `reels/`.

### The four joins

A beat's `source` says where its frames come from. This is the central concept; `board.py`
constants, `config.build_prompt`, `render._frames` and the canvas edge styling must agree.

| source | first frame | last frame | checkpoint | image quota |
| --- | --- | --- | --- | --- |
| `asset` (cut) | its own still | — | fl2va | 1 |
| `chain` (continuation) | previous clip's last frame | — | fl2va | 0 |
| `bridge` | previous clip's last frame | its own still | fl2va | 1 |
| `reference` | none — composes its own | — | **ref2va** | 0 (uploads) |

`chains()`, `uses_asset()`, `uses_refs()` in `board.py` are the predicates; use them rather
than comparing strings.

A `reference` beat is a different checkpoint (`config.UNET_REF`), takes up to
`config.MAX_REF_IMAGES` (9) pictures referred to in the prompt as `<Picture 1>`…`<Picture 9>`
**1-based in connection order** while graph sockets are 0-based, and can optionally carry the
tail of the previous clip (`config.REF_VIDEO_SECONDS`) as `<Video 1>` instead of a keyframe
handoff. It cannot be generated into: the server answers a still-generation request for one
with a 409.

The join is also *told to the model in words* — `OPEN_CUT` vs `OPEN_CONTINUATION` vs
`OPEN_REFERENCE`/`CARRY_VIDEO`, plus `ARRIVE_ON_LAST` for a bridge. Getting the wording wrong
does not fail; it produces a visible restart jolt at the seam. `config.py` documents each one.

### Staleness cascades

`Board.states()` runs one downstream pass, not per-beat checks, because a *pending* upstream
edit does not change a downstream beat's own fingerprint. `STALE` means "you edited this";
`INVALIDATED` means "something it follows changed". `cascade()` expands a manual render
selection downstream; a cut (or a reference beat not carrying motion) breaks the run.

### Jobs and events

`jobs.Runner` is a single daemon worker thread with one queue — serial on purpose: one GPU
container, ComfyUI runs one graph at a time, chaining is serial anyway. `api.py` registers
handlers (`plan`, `chat`, `asset`, `caption`, `render`) and never blocks the request.

The worker must never die. It catches `(Exception, SystemExit)` — `comfy.py` was written for
CLI use and raises `SystemExit` for things like a 401.

The browser gets everything over one SSE stream at `/api/events`. Board changes are
**announced, not pushed** (`{"type":"board","slug":…}`); the client refetches, so derived state
is computed in exactly one place.

The container clock starts at deploy and stops at teardown, because that is what Modal bills.

### Auth

The Modal endpoint is **authenticated by default** (`unauthenticated=PUBLIC_ENDPOINT`).
Proxy-auth tokens are `wk-`/`ws-` (`MODAL_PROXY_TOKEN_ID` / `MODAL_PROXY_TOKEN_SECRET`),
a *different* credential type from the `ak-` CLI token in `~/.modal.toml` — the latter earns a
401. `config.load_env_file()` reads `.env` at import with `setdefault`, so a shell export wins.

The studio server runs on loopback only and holds those tokens; the browser never talks to
Modal. Don't move `agy` or `modal` shell-outs anywhere else.

## Constraints that are not negotiable without new measurements

- **Beats are 5 s or 10 s, nothing else** (`config.BEAT_LENGTHS`). 5 s is the model's
  124-frame floor; 10 s is 243 frames, the longest render that has ever completed on this
  card. 15 s (362 frames) failed. Render time is superlinear in frame count.
- **Generation is 768×1344**, the closest multiple-of-32 vertical to ~1 MP; delivery scales to
  1080×1920 in `media.py`.
- **8 steps** is the measured sweet spot; 20 costs ~70% more.
- **B200 is not worth it** — 1.19× faster, 2.06× the rate. Only revisit above 96 GB.
- **Parallelism saves nothing** — billing is per container-second and each container repays
  the ~22 s model load. Batch into one warm container.
- Cost estimates are wall-clock × `config.RATE_PER_SEC`, not Modal's billing API, and exclude
  the scale-down tail. They read slightly low.

## Conventions

Comments in this codebase explain *why*, usually with the measurement or the failure that
motivated the line. Match that — a change that drops the reasoning behind a constant loses the
only record of it. Prose comments and docstrings are full sentences; keep that register.

Frontend: `useStudio()` for shared state, `useDraft()` for any text input (the board refetches
on every server event, so a plain controlled input gets its value yanked mid-typing).

`prompts/40s-paper-cutout-script.md` is the authoring prompt handed to an outside AI to write
an importable script; `script.py` is the importer. Keep the two in sync.

## Known gaps

Listed in `README.md` under "Known gaps" — the CLI can still request 15 s beats, cross-scene
consistency is unmeasured, the container may be over-provisioned at 8 cores / 64 GiB, and the
studio's per-step WebSocket progress has never been exercised against a live render.
