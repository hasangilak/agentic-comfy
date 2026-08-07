# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Papercut Studio: a local-only web UI for generating paper-cutout stop-motion keyframes. A scene is a duration split into 2–9 frames; each frame gets a derived timestamp, hold, and staged "beat", and is rendered by `mflux` with `flux2-klein-4b` on the local GPU. See `PRODUCT.md` for durable product truth (local-only is a hard promise, not a default).

## Commands

```sh
npm run dev          # both processes via concurrently
npm run dev:server   # tsx watch server/index.ts  -> 127.0.0.1:8791
npm run dev:web      # vite                       -> localhost:5173
npm run build        # tsc -b (typecheck both projects) + vite build
npm run preview
PORT=9000 npm run dev:server   # override server port
```

`npm run build` is the only typecheck — there is no lint config and no test framework installed. `tsc -b` covers both TS projects, so it type-checks the server too even though the server is never compiled (tsx runs the TypeScript directly).

Requires an Apple Silicon Mac with `mflux` on PATH and `flux2-klein-4b` weights cached; Node 20+. The `zip` binary is shelled out to for scene download.

## Architecture

Two processes, one shared type module.

**`shared/types.ts` is imported by both sides** and is the single source of truth for `Scene`/`Frame`/`SceneSettings`, the aspect presets, the default style suffix, `beatHint`/`defaultBeat` (the staged-motion phrasing seeded into each frame), and `frameTimings`. Both `tsconfig.app.json` (src + shared, DOM libs) and `tsconfig.node.json` (server + shared + vite.config) include it. A change here lands on client and server at once.

Imports carry explicit `.ts`/`.tsx` extensions — `allowImportingTsExtensions` is on in both projects. Match that.

Vite proxies `/api` and `/files` to `127.0.0.1:8791`, so the client always uses same-origin relative paths (`src/api.ts`). Nothing in the client knows the server port.

### Server: `server/store.ts` owns all state

`store.ts` is the authority; `index.ts` is a thin Express wrapper over its exported functions. State lives in an in-memory `Map<string, Scene>` mirrored to `out/scenes/<id>/scene.json`. `loadFromDisk()` runs at boot and demotes any `running`/`pending` status to `cancelled` — nothing survives a restart mid-render.

Three things in `store.ts` are load-bearing and easy to break:

- **The global render lock.** `queue()` chains every render through one promise because mflux holds ~18 GB of weights and two concurrent renders would thrash. Never parallelize renders.
- **`update(scene, persistToDisk)`.** The progress hot path calls `update(scene, false)` — tqdm ticks arrive several times a second and must not each write `scene.json`. Only terminal transitions persist.
- **`referenceFor()` + `composePrompt()`.** These implement the consistency modes. `chain` walks backward to the newest rendered frame (falling back to the uploaded reference); `anchor` uses the reference (falling back to frame 1); `none` returns nothing. When a reference exists, `CONTINUITY_CLAUSE` is prepended — it locks the look *and* explicitly licenses the pose to change, because without that second half chain mode reproduces the previous frame almost exactly. Final prompt order is: continuity clause (conditional) → frame beat → scene style suffix.

`updateScene()` throws while a scene is rendering. Changing `frameCount` reshapes the frame array in place, preserving existing frames by index; timings and seeds are always recomputed from `duration`/`varySeeds`.

### Render subprocess: `server/mflux.ts`

Spawns `mflux-generate-flux2` (text-to-image) or `mflux-generate-flux2-edit` (when `referencePaths` is set), model `flux2-klein-4b`, 8-bit quantized. Progress is scraped from tqdm output on **both stdout and stderr**, split on `\r` as well as `\n` because tqdm overwrites its line. Cancel is `SIGTERM` plus a `cancelled` flag that turns the close event into a rejection.

### Realtime: EventEmitter → SSE

`store.ts` emits on a module-level `bus` with two per-scene channels: `scene:<id>` (full scene object) and `log:<id>` (raw subprocess line). `GET /api/scenes/:id/events` subscribes both and streams them as SSE with a 15 s keep-alive ping. Client-side, `useSceneStream` (`src/hooks.ts`) applies scene payloads immediately but buffers log lines and flushes on a 250 ms timer so React does not re-render per tqdm tick.

Rendered frame URLs get a `?v=<timestamp>` suffix on completion — the file path is stable across re-rolls, so without it the browser serves the stale image.

### Client

`src/App.tsx` holds all state and passes it down; components in `src/components/` are presentational. Selected scene id is persisted to localStorage via `useStored`.

`src/estimate.ts` computes a time estimate from measured per-step constants (1.97 s/step text-to-image, ~3.2 s/step edit, scaled by pixel count), then `observedFrameSeconds()` takes over once real `elapsed` values exist — the UI prefers the measurement over the model.

## Model behaviour to keep in mind

Documented in the README and worth preserving in any UX work: chain mode drifts around frame 3+ (background elements appear/disappear); re-rendering a middle frame in chain mode leaves later frames conditioned on the old version, so the tail must be re-rendered too; hands are `flux2-klein-4b`'s consistent weak point.

## Repo notes

`out/` is gitignored and is the durable store — deleting it deletes scene history. `.claude/skills/impeccable/` is a design skill; `PRODUCT.md` and `.impeccable/` are its artifacts, not application code.
