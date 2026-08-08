# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Papercut Studio: a web UI for generating paper-cutout stop-motion keyframes. A scene is a duration split into 2–9 frames; each frame gets a derived timestamp, hold, and staged "beat", and is rendered by Google's Gemini Nano Banana image models. See `PRODUCT.md` for durable product truth.

## Commands

The `Makefile` is the front door — `make help` lists every target. It wraps the npm scripts and adds project ops.

```sh
make dev             # both processes via concurrently
make dev-server      # tsx watch server/index.ts  -> 127.0.0.1:8791
make dev-web         # vite                       -> localhost:5173
make typecheck       # tsc -b, both TS projects
make build           # typecheck + vite build
make doctor          # node, zip, Gemini model, API key configuration
make health          # GET /api/health on the running server
make scenes          # list scenes on disk with frame counts
make stop            # stop both processes
make ports           # what is listening on 5173 and 8791
make clean           # dist + tsbuildinfo    (distclean also drops node_modules)
PORT=9000 make dev-server
```

`make nuke-scenes` deletes `out/scenes` and `out/uploads`. It refuses without `CONFIRM=yes`, and `out/` is gitignored — there is no recovery.

The underlying npm scripts (`dev`, `dev:server`, `dev:web`, `build`, `preview`) still work directly. The dev/build targets depend on `install`, which is timestamp-driven off `package.json`/`package-lock.json`, so `npm install` reruns only when those change.

Typechecking is the only automated check — there is no lint config and no test framework installed. `tsc -b` covers both TS projects, so it type-checks the server too even though the server is never compiled (tsx runs the TypeScript directly).

Requires Node 20+, a Google AI API key in `.env` as `X-GOOG-API-KEY`, and the `zip` binary for scene download.

## Architecture

Two processes, one shared type module.

**`shared/types.ts` is imported by both sides** and is the single source of truth for `Scene`/`Frame`/`SceneSettings`, the aspect presets, the default style suffix, `beatHint`/`defaultBeat` (the staged-motion phrasing seeded into each frame), and `frameTimings`. Both `tsconfig.app.json` (src + shared, DOM libs) and `tsconfig.node.json` (server + shared + vite.config) include it. A change here lands on client and server at once.

Imports carry explicit `.ts`/`.tsx` extensions — `allowImportingTsExtensions` is on in both projects. Match that.

Vite proxies `/api` and `/files` to `127.0.0.1:8791`, so the client always uses same-origin relative paths (`src/api.ts`). Nothing in the client knows the server port.

### Server: `server/store.ts` owns all state

`store.ts` is the authority; `index.ts` is a thin Express wrapper over its exported functions. State lives in an in-memory `Map<string, Scene>` mirrored to `out/scenes/<id>/scene.json`. `loadFromDisk()` runs at boot and demotes any `running`/`pending` status to `cancelled` — nothing survives a restart mid-render.

Three things in `store.ts` are load-bearing and easy to break:

- **The global render lock.** `queue()` chains every render through one promise so scene ordering, progress, and cancellation remain predictable. Never parallelize renders.
- **`update(scene, persistToDisk)`.** The progress hot path calls `update(scene, false)` — tqdm ticks arrive several times a second and must not each write `scene.json`. Only terminal transitions persist.
- **`referenceFor()` + `composePrompt()`.** These implement the consistency modes. `chain` walks backward to the newest rendered frame (falling back to the uploaded references); `anchor` uses the references (falling back to frame 1); `edit` uses the references and nothing else; `none` returns nothing. When a reference exists, `CONTINUITY_CLAUSE` is prepended — it locks the look *and* explicitly licenses the pose to change, because without that second half chain mode reproduces the previous frame almost exactly. Final prompt order is: continuity clause (conditional) → frame beat → scene style suffix.
- **`edit` is the one conditioned mode that omits the clause**, and that omission is the whole mode. "…but move the subject into a clearly different pose and position" is right when the reference is the previous frame of a moving sequence and exactly wrong when the reference *is* the picture being changed — the reel next door redraws its own reference pictures ("make the club longer"), and under `anchor` the club came back re-posed. `edit` also has no fallback: given nothing to condition on it renders from the text rather than silently editing frame 1, because editing a picture the caller did not name is worse than not editing at all. `composePrompt` branches on the mode, not on the reference count, so a future mode that also holds its reference lands in the same arm.
- **One image or several.** `uploadedReferences()` reads `referencePaths` when a caller sent it and `referencePath` otherwise, so a scene from the local UI (one slot, one upload) conditions on exactly the image it was given, and a scene from a caller that predates the list keeps working. The cap is `MAX_REFERENCES` in `shared/types.ts` — a request-size and consistency cap across the supported Gemini models. Chain mode conditions on the previous frame alone and ignores the uploads, which is the point of the mode. `CONTINUITY_CLAUSE_MULTI` is the same sentence pluralised and exists as its own string so a single-reference render still composes the byte-identical prompt it always did.

`updateScene()` throws while a scene is rendering. Changing `frameCount` reshapes the frame array in place, preserving existing frames by index; timings and seeds are always recomputed from `duration`/`varySeeds`.

### Gemini renderer: `server/gemini.ts`

POSTs to Google's `/v1beta/interactions` endpoint with `x-goog-api-key`. Text and each `referencePaths` file become inline inputs; the response's `output_image.data` is decoded to the requested PNG path. The scene's `geminiModel` and `geminiImageSize` are selected in the UI; `.env` values remain fallbacks for API-created or older scenes. Lite is forced to 1K. Progress is coarse because the API is request/response rather than a local sampling loop. Cancellation aborts the request and preserves the existing scene status behavior.

### Realtime: EventEmitter → SSE

`store.ts` emits on a module-level `bus` with two per-scene channels: `scene:<id>` (full scene object) and `log:<id>` (raw subprocess line). `GET /api/scenes/:id/events` subscribes both and streams them as SSE with a 15 s keep-alive ping. Client-side, `useSceneStream` (`src/hooks.ts`) applies scene payloads immediately but buffers log lines and flushes on a 250 ms timer so React does not re-render per tqdm tick.

Rendered frame URLs get a `?v=<timestamp>` suffix on completion — the file path is stable across re-rolls, so without it the browser serves the stale image.

### Client

`src/App.tsx` holds all state and passes it down; components in `src/components/` are presentational. Selected scene id is persisted to localStorage via `useStored`.

`src/estimate.ts` computes a time estimate from measured per-step constants (1.97 s/step text-to-image, ~3.2 s/step edit, scaled by pixel count), then `observedFrameSeconds()` takes over once real `elapsed` values exist — the UI prefers the measurement over the model.

## Model behaviour to keep in mind

Documented in the README and worth preserving in any UX work: chain mode can drift around frame 3+; re-rendering a middle frame in chain mode leaves later frames conditioned on the old version, so the tail must be re-rendered too.

## The reel pipeline next door

This project sits inside the **paperreel** repo (`../`), which turns a storyboard into a video by rendering MiniMax-H3 on a GPU on Modal. paperreel calls this server to produce the opening still each of its shots starts from — `../paperreel/papercut.py` is the client, over plain HTTP on `127.0.0.1:8791`. It replaces a Google-quota image tool capped at roughly five images per five hours, which is the whole reason the seam exists.

Nothing here imports anything from paperreel, and this server has no idea it is being driven. What it does have are two commitments to keep:

- **`GET /api/health` is a contract.** `service`, `limits.minFrames`, `limits.maxFrames`, `limits.maxReferences`, `modes`. paperreel probes it to decide whether stills can be rendered locally at all and reads both caps off it rather than hardcoding them. Keep the shape — and note that paperreel reads a *missing* `maxReferences` as "this build only conditions on one image", so removing it silently costs the reel its per-beat reference pictures. `modes` is there so a caller can tell whether this build knows `edit`: handed an unknown mode, `referenceFor` matches no arm and falls through to chain's backward walk, which on a one-frame scene finds nothing and renders from the text — the picture is dropped and nothing says so.
- **The `9:16-reel` preset (768×1344) is paperreel's generation grid**, not a taste choice. A still handed to H3 at any other size is cover-cropped and rescaled on the way in, which loses the framing the user just approved. Don't retune those numbers to make renders faster.

A scene created by paperreel looks unusual from this side and is meant to: `duration` is meaningless (opening frames of separate shots, not a sequence to play back), `consistency` is `anchor` or `none` for a shot's opening still and `edit` when the reel is redrawing one of its own reference pictures, and `referencePaths` point outside `out/` into the reel's directory — the reel's cast reference first, then the director's pictures for that one shot. All of that already works — `referenceFor()` only checks the files exist. It sends `referencePath` as well, set to the first of the list, so this side conditioning on one image is never a total loss of the cast.

## Repo notes

`out/` is gitignored and is the durable store — deleting it deletes scene history. `.claude/skills/impeccable/` is a design skill; `PRODUCT.md` and `.impeccable/` are its artifacts, not application code.

This directory is its own git repository nested inside paperreel's, with no remote and no `.gitmodules` entry. Commits here do not reach the outer repo.
