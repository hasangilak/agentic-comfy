# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Two apps that make one thing. **paperreel** (this directory) turns a concept into a vertical
1080×1920 Instagram Reel in paper-cutout stop-motion, rendering MiniMax-H3 on a single GPU on
Modal via ComfyUI. **Papercut Studio** (`image/`, a separate project with its own CLAUDE.md)
renders the opening stills locally with mflux. paperreel calls it over HTTP; see
"The two projects" below.

Everything that is words runs on **one local model** — `qwen3.6` on Ollama — and everything
that is a still runs on mflux next door. Nothing but the video render is metered. See "The
language model" below; there is no hosted LLM anywhere in this repo.

Two front ends drive paperreel: `storyboard.py` / `reel.py` (CLI) and a local FastAPI + React
node canvas ("the studio").

`README.md` is the design document — it explains *why* most of the constants and joins are
what they are. Read it before changing render behaviour, prompt scaffolding, or costs.

## Commands

```bash
make install      # npm deps + resolve the uv environment (PEP 723 inline deps in studio.py)
make run          # all three: stills :8791, backend :8787, Vite :5173 — use the Vite URL,
                  # it proxies /api and /media. The everyday target.
make studio       # an alias for make run
make images       # just Papercut Studio's render server (make -C image dev-server)
make qwen         # ollama pull qwen3.6 — the script writer and still reviewer, one-time ~23 GiB
make serve        # build the frontend, serve everything from :8787
make backend      # studio.py only
make frontend     # vite only
make stop         # kill every server running in either project (mflux render survives)
make stop-mflux   # end an in-flight mflux render next door — that frame is lost
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

**Nothing outside the GPU render is metered.** The language model is local (Ollama) and the
stills are local (mflux), so there is no quota, no key and no per-token charge anywhere. The
Antigravity CLI (`agy`) is gone; its ~five-images-per-five-hours window is the reason chaining
is the default and a reel was designed to need one image rather than one per beat. Both are
still good filmmaking. Neither is forced. **Do not write new copy or docs that state an image
quota** — that constraint no longer exists, and saying it trains the user to ration something
free.

## Architecture

```
paperreel/config.py     every tunable + the measurement behind it; the prompt scaffold
paperreel/board.py      the board document and ALL derived state
paperreel/comfy.py      ComfyUI HTTP/WS client + the H3 graph builder
paperreel/pipeline.py   Modal app lifecycle + chained batch rendering (CLI path)
paperreel/render.py     the same, with per-beat telemetry and cancellation (studio path)
paperreel/papercut.py   HTTP client for image/ — the transport to the mflux stills server
paperreel/qwen.py       Ollama transport: structured output, tool calls, vision
paperreel/stills.py     rendering stills, then LOOKING at them; the still-job rules
paperreel/planner.py    the authoring brief -> a script, then its own self-check
paperreel/script.py     adopting a script written outside the studio
paperreel/agent.py      the tool loop -> board operations
paperreel/jobs.py       one worker thread, one job queue, one event stream
paperreel/api.py        FastAPI routes + SSE
paperreel/media.py      ffmpeg: cutout, compose, fit, last-frame, tail, stitch
comfyui_minimax_h3.py   the Modal GPU app (ComfyUI on RTX-PRO-6000)
studio/src/             React 19 + @xyflow/react canvas, Tailwind 4, Vite
```

### The two projects

```
Ollama :11434            paperreel  this repo            Modal
  qwen3.6 · 36B MoE      ──▶   script, board ops,   ──▶   MiniMax-H3
  vision · tools · think        caption, still review      the paid stage
                                     │        ▲
image/  Papercut Studio               ▼        │ the still, looked at
  mflux · flux2-klein-4b   ──▶   storyboard.json
  local, free, unmetered         board + joins
  Express :8791                  FastAPI :8787
```

**paperreel is the orchestrator; image is a renderer.** The seam is HTTP on loopback, not
shared code — neither project imports the other, and paperreel still loads and edits boards
when the image server is down (it just cannot generate a still). Only two things cross the
boundary:

- `paperreel/papercut.py` → the image server's REST + SSE API (`POST /api/scenes`,
  `POST /api/scenes/:id/render`, `GET /api/scenes/:id/events`, `GET /files/...`).
- `config.PAPERCUT_ASPECT` names the `9:16-reel` preset (768×1344) in
  `image/shared/types.ts`. It exists *only* so stills match `GEN_WIDTH × GEN_HEIGHT` and
  never get cover-cropped by `media.fit_frame`. **If you change `GEN_WIDTH`/`GEN_HEIGHT`,
  change that preset too** — nothing enforces the match, it just silently starts cropping.
- `limits.maxReferences` on `/api/health`, read by `papercut.max_references` and floored
  against `config.MAX_STILL_REFS`. A build that does not report it is treated as the older
  single-reference one, so the cast reference still lands and only the uploads are dropped.

**What a still is drawn from is `Board.still_pictures`** — the reel's locked cast reference, then
the director's uploads on that beat, sent as `referencePaths` with the first also in the legacy
`referencePath`. Deliberately *not* the beat's own still, which is the thing being generated. It
is a different list from `pictures_for` and a much shorter one (4 against 9): mflux encodes every
conditioning image through every sampling step. The uploads are there because the still is what
the clip's opening sampling steps are anchored to — a picture the clip is held to and the frame it
opens on never saw is two answers to the same puppet. Both methods guard on `uses_refs`, so the
join decides whether a picture counts, in one place.

Papercut is the **only** stills generator; there is no backend switch and no fallback. With
`/api/health` not answering, a beat's still is an upload — that is what `manual_stills` is for.
`api.status()` reports `stills.backend` as `"papercut"` or `"none"`, probed per request.

Beat runs are grouped by conditioning image in `papercut._runs`, lazily — a beat with no
reference yet renders alone first because its still *becomes* the reference for the rest
(`Board.reference_for`). Grouping up front would put a fresh board's whole cast in one
unconditioned scene, which is the cross-scene inconsistency the reference was added to fix.
`stills.generate` mirrors that split one level up, and for a sharper reason: it renders **and
reviews** the reference-defining beat alone before anything else starts, because rejecting that
still after the batch would replace the reference every other still was just matched against.

Three modules, three jobs, and keeping them apart is what makes the review possible at all:
`papercut.py` is transport, `stills.py` is judgement (which beats may get a still, and what
happens to one that comes back wrong), `qwen.py` is the model.

`stills.converse` is the per-still conversation behind the node's **✎ talk about this still**
(job kind `still_chat`, `POST /api/reels/{slug}/beats/{n}/asset/chat`). A structured vision call,
not a tool loop — there are only two outcomes of looking at a picture with someone, the prompt it
should say instead and whether to draw it again. **The automatic review deliberately does not run
on what it renders**: the reviewer holds a still to the cast reference, and half of what a
director asks for here is a departure from it, so it would rewrite the requested change back out.
The review posts its own verdicts into the same transcript (`beat["asset_chat"]`, trimmed to
`config.ASSET_CHAT_MEMORY`), which is what makes a prompt rewritten between the render you asked
for and the one you got readable rather than baffling. `papercut.generate(seed=...)` exists for
one caller: a re-render with the prompt unchanged, which would otherwise come back byte-identical
because Papercut derives a frame's seed as `scene.seed + index`.

paperreel maps onto Papercut's model as: `style_bible` + suffix → scene `style`, per-beat
`asset_prompt` (plus its pictures' notes, appended by `papercut._beat_text`) → frame `beat`,
`still_pictures` → `referencePaths` in `anchor` consistency mode. Never `chain`: these are hard
cuts, chaining drifts by frame three and serialises for no gain, and Papercut conditions a
chained frame on the previous frame *alone* — the uploads would be silently dropped.

**Product tension worth knowing:** `image/PRODUCT.md` principle 3 says nothing leaves the
machine. That still holds of the image project itself — but a still it renders *is* uploaded
to Modal when paperreel renders the video. The promise is about the image tool, not about
what the user does with its output; don't "fix" it by adding uploads to `image/`.

### The language model

`qwen.py` is the only place that talks to Ollama, over plain httpx — no SDK, no LangChain, no
new dependency (httpx is already in every entry point's PEP 723 block). Three shapes of call,
all `/api/chat`:

- `structured(messages, schema)` — Ollama constrains the decode to the JSON Schema, so the
  script and every verdict come back validated rather than parsed hopefully.
- `chat(messages, tools=...)` — one round of a tool loop. `calls_of` / `answered` handle the
  round-trip; `agent.turn` drives it, capped at `config.AGENT_MAX_ROUNDS`.
- `text(messages)` — prose, for the caption.

Vision is the same call with base64 `images` on a message (`qwen.encode`). Used only by
`stills.review`.

Things that are the way they are because they were measured, not assumed:

- **`think` is passed explicitly on every call, and defaults to off.** The default for a
  reasoning model is on, and the same unambiguous board edit took 0.9 s with it off and 14 s
  with it on. Only the planning pair asks for it (`config.PLAN_THINK`).
- **A structured field the model writes *first* becomes its scratchpad.** Ollama decodes in
  schema-property order, so `changes` declared before `beats` in `REVIEW_SCHEMA` produced 40
  log lines of stream-of-consciousness before a single beat was rewritten. It is declared last,
  and thinking is on for that call so reasoning has a channel of its own.
- **`num_ctx` must be set** (32768). Ollama defaults to a few thousand tokens and truncates
  silently — which does not fail, it answers confidently from a prompt whose end is missing.
  The review pass sizes this: the whole authoring brief (~6.7k tokens) plus a draft plus its
  correction.
- **Prompt order in `agent.turn` is history → board → question.** The board used to come first,
  which left a stale line of the model's own transcript nearer the question than the truth —
  and it answered from the nearer one, insisting a four-beat reel had five for the rest of the
  session. `transcript()` also labels itself as history for the same reason.
- **`board_digest` spells out the "waiting on" lists** rather than leaving them to be inferred
  from the joins. Asked which beats need a still, the model reasoned from the join names and got
  it wrong in both directions. Both lists are already derived in `board.py`.
- **Tool parameter descriptions are load-bearing.** Given a bare "Edit one beat" the model spent
  a whole turn reasoning about what a parameter called `action` wanted — reading the field name
  as a verb. `qwen.tool()` exists to make writing them the default.

**Both review passes are only affordable because nothing is metered.** Neither would have been
worth a quota slot; both are worth ten seconds. If you add a call, that is the test to apply.

**No LangGraph, deliberately.** The graph here is four nodes with one back-edge, and the two
things a graph framework would bring are already owned: durable serial execution is
`jobs.Runner`, and state is `storyboard.json`, which is the only database. A checkpointer would
be a *second* store of the same state, which is exactly the drift the derived-state design
exists to prevent.

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

| source | first frame | last frame | checkpoint | needs a still |
| --- | --- | --- | --- | --- |
| `reference` (**the default cut**) | none — conditioned towards its own still | — | **ref2va** | yes, generated |
| `chain` (continuation) | previous clip's last frame | — | fl2va | no |
| `bridge` | previous clip's last frame | its own still | fl2va | yes, generated |
| `asset` (exact-keyframe cut) | its own still, exactly | — | fl2va | yes, generated |

`chains()`, `uses_asset()`, `uses_refs()` in `board.py` are the predicates; use them rather
than comparing strings. `uses_asset()` answers "does the still go into a KEYFRAME slot" and is
therefore false for `reference` — the question "must a still exist on disk" is
`Board.needs_still(beat)`, which is beat-level because a reference beat carrying motion answers
it differently. Same split as `chains()` vs `Board.follows_upstream()`.

**`reference` is the default cut, not an uploads-only special case.** It is a different
checkpoint (`config.UNET_REF`) taking up to `config.MAX_REF_IMAGES` (9) pictures referred to in
the prompt as `<Picture 1>`…`<Picture 9>` **1-based in connection order** while graph sockets are
0-based. Two of the nine wire themselves, and `Board.pictures_for(n)` is the single place that
order is decided:

1. the beat's own generated still — `config.REF_ROLE_OPENING`, and `config.OPEN_REFERENCE_STILL`
   tells the model to begin the clip on it;
2. the reel's locked cast reference (`Board.reference_for`, so `None` on the beat that *is* the
   reference) — `config.REF_ROLE_CAST`;
3. then the director's uploads, `beat<n>_ref1.png` upward.

It returns **(path, role) pairs**, deliberately: the prompt addresses each picture by position,
so a path list and a note list that could slip by one is a live bug. Keep it that shape.
`Board.ref_budget(n)` is the remaining upload count (7, not 9, on a beat that opens a shot) and
`next_ref_index` enforces it — an upload that `pictures_for` would truncate must be refused, not
stored. `to_json` publishes `auto_refs` / `ref_offset` / `ref_slots` / `opens_on` so the canvas
shows the numbers the model is actually told.

A reference beat can instead carry the tail of the previous clip (`config.REF_VIDEO_SECONDS`) as
`<Video 1>`. **Exactly one of three things may say where a reference shot opens** — `CARRY_VIDEO`,
`OPEN_REFERENCE_STILL`, or `COMPOSE_OPENING` — and `build_prompt` enforces that precedence. So a
carrying beat wires no still at all, needs none (`needs_still` is false), and the server answers a
still-generation request for one with a 409. Every other reference beat generates its still like
any other cut.

`asset` is now the deliberate choice rather than the default: a keyframe latent is re-injected at
every step and never denoised, so it is the join for a beat whose opening frame must land exactly.
Nothing silently moves a beat onto or off it — `source_for` honours an explicit `asset` even in
first position, which is what keeps every board written before this rendering on the checkpoint it
was rendered on.

Cost note: reference tokens ride through every sampling step where a keyframe is one VAE encode,
so `SECONDS_PER_FRAME` / `RENDER_INTERCEPT` — fitted on the keyframe path — read slightly low for
a reference cut, and a reel mixing cuts with continuations pays one checkpoint swap per shot
boundary.

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
handlers (`plan`, `chat`, `asset`, `still_chat`, `caption`, `render`) and never blocks the
request.

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
Modal. Don't move the `modal` shell-outs anywhere else, and don't give the browser Ollama's URL
either — the model is reached from the server so one place decides what it is allowed to do.

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

`prompts/40s-paper-cutout-script.md` is **the** specification of what a script for this pipeline
has to be, and both ways into a board are written against it: a human pastes it into an outside
AI, and `planner.brief()` hands the same file to the local model with only its opening interview
(section 0) spliced out for the beat count and length the studio already asked for. `script.py`
normalises whatever comes back, from either path.

So: change the brief, not a copy of it. There is deliberately no summary of those rules inside
`planner.py` — there was one, and a summary that drifts from the document has the two paths
quietly writing to different specifications. Same for the review pass: it hands the whole brief
back and asks the model to run section 11's self-check, rather than restating the checks.

## Known gaps

Listed in `README.md` under "Known gaps" — the CLI can still request 15 s beats, cross-scene
consistency is unmeasured, the container may be over-provisioned at 8 cores / 64 GiB, and the
studio's per-step WebSocket progress has never been exercised against a live render.

**A still drawn from more than one picture is timed, not judged.** The cap, the notes clause and
the join guard are exercised on a real board, and the cost is measured — 18.6 s for one
reference against 31.4 s for two, same prompt and seed at 768×1344, which is the number in
`config.MAX_STILL_REFS` and `image/src/estimate.ts`. What has *not* been compared is whether more
pictures hold the cast better than one, or whether the *reference images show: …* clause helps
rather than giving `flux2-klein-4b` one more thing to draw into the frame. Do not quote a quality
claim; `PAPERREEL_MAX_STILL_REFS` is how to explore it, and it is free.

Added with the local model, and unmeasured: the still review's false-accept rate on *subtle*
cast drift (it is verified to reject an obvious mismatch and to pass a good still, which is not
the same thing), and how a board behaves when Ollama is stopped mid-turn rather than absent from
the start. Planning is also slow — draft plus self-check is about five minutes.

The per-still conversation (`stills.converse`) has been exercised against stubs, not against a
live qwen and mflux: what is unverified is how often the model sets `regenerate` correctly (a
question about the picture that redraws it anyway costs 10–18 s of nothing) and whether it really
carries the untouched half of a prompt through a rewrite rather than paraphrasing the style bible.
Both show up in the transcript on the node, which is where to look first.

**Moving the default cut to ref2va is reasoned, not measured.** No A/B render exists for how
much exactness the opening frame loses, how much the extra reference tokens cost per beat, or
whether the per-shot-boundary checkpoint swap is material. Do not quote a number for any of the
three. `asset` is the same cut on the keyframe path, so the comparison is one join click apart if
you are asked for it — and it is a paid render, so only on request.
