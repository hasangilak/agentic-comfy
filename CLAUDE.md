# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Two apps that make one thing. **paperreel** (this directory) turns a concept into a vertical
1080×1920 Instagram Reel in paper-cutout stop-motion, rendering MiniMax-H3 on a single GPU on
Modal via ComfyUI. **Papercut Studio** (`image/`, a separate project with its own CLAUDE.md)
renders the opening stills through Gemini. paperreel calls it over HTTP; see
"The two projects" below.

Everything that is words runs on **one Gemini model** — `gemini-3.7-flash` over Google's API
— and everything that is a still runs through the same API next door, on the same
`X-GOOG-API-KEY`. Words, stills and video are all metered; only the ffmpeg work is free. See
"The language model" below. There is no local model and no Ollama anywhere in this repo.

Two front ends drive paperreel: `storyboard.py` / `reel.py` (CLI) and a local FastAPI + React
node canvas ("the studio").

[ARCHITECTURE.md](ARCHITECTURE.md) is the design document — it explains *why* most of the
constants and joins are what they are. Read it before changing render behaviour, prompt
scaffolding, or costs. [README.md](README.md) is setup and how to run.

## Commands

```bash
make install      # npm deps + resolve the uv environment (PEP 723 inline deps in studio.py)
make run          # all three: stills :8791, backend :8787, Vite :5173 — use the Vite URL,
                  # it proxies /api and /media. The everyday target.
make studio       # an alias for make run
make images       # just Papercut Studio's render server (make -C image dev-server)
make serve        # build the frontend, serve everything from :8787
make backend      # studio.py only
make frontend     # vite only
make stop         # kill every server running in either project
make build        # npm --prefix studio run build  (tsc -b && vite build — this is the typecheck)
make harness      # golden-board eval: skills, next_stage, fingerprints, job restore. Calls no model.

make login        # uvx modal setup                        one-time, touches Modal
make models       # ~177 GiB of weights into a Modal Volume one-time, never needs re-running
make deploy       # deploy the GPU app (free until a request arrives)
make stop-app     # stop the GPU app now
```

There is no linter config. `npm --prefix studio run build` is the TypeScript check.
`make harness` (`uv run evals/harness.py`) is the Python one: it loads every skill, checks
`next_stage` on three golden boards, dry-runs the next phase unsent, and asserts that envelope /
acts / continuity notes leave fingerprints byte-identical. It calls no model and spends no GPU.

`uv run crew.py --list` is the cheaper subset: frontmatter, placeholders, tool names, schema
paths. `--dry-run` prints the exact prompt and tool declarations that would be sent; `--where`
prints which stage a board is waiting on.

Python is always run through `uv run` — `studio.py`, `storyboard.py` and `reel.py` each
declare their dependencies inline (PEP 723). Modal is always `uvx modal ...`.

### Money

**Nothing in the Makefile can start a paid render.** That is deliberate and must stay true.
Rendering is only reachable from `storyboard.py --render` / `--all`, `reel.py`, or the
studio's render button. Planning, asset generation, uploads and compositing are free.

Never run a render to "verify" a change. A 4×10 s reel on the old quantized card
cost ~$1.13; BF16 on B200 is unmeasured and higher.

**Words and images are both metered, on one key.** The language model and the stills both go
through Gemini with `X-GOOG-API-KEY` from `.env`. A turn is cents and a still is cents against
a reel's dollars, so neither is a thing to ration — but neither is free either, and a call
added to a loop that runs per beat has a price. The Antigravity CLI (`agy`) is gone; its
~five-images-per-five-hours window is the reason chaining is the default and a reel was
designed to need one image rather than one per beat. Both are still good filmmaking. Neither
is forced. **Do not write new copy or docs that state an image quota** — that constraint no
longer exists, and saying it trains the user to ration something that is not scarce.

## Architecture

```
paperreel/config.py     every tunable + the measurement behind it; the prompt scaffold
paperreel/board.py      the board document and ALL derived state (`picture_kinds` is the unhashed parallel to `pictures_for`)
paperreel/comfy.py      ComfyUI HTTP/WS client + the H3 graph builder
paperreel/pipeline.py   Modal app lifecycle + chained batch rendering (CLI path)
paperreel/render.py     the same, with per-beat telemetry and cancellation (studio path)
paperreel/papercut.py   HTTP client for image/ — the transport to the Gemini stills server
paperreel/gemini.py     Gemini transport: structured output, tool calls, vision
paperreel/stills.py     rendering stills, then LOOKING at them; the still-job rules
paperreel/pictures.py   reference pictures as drawable assets; NO review pass, deliberately
paperreel/staging.py    the reel's cast and sets; a sheet is held to its note, not the cast still
paperreel/panels.py     the storyboard: a rough sketch per shot; reaches NO renderer
paperreel/planner.py    the authoring brief -> a script, then its own self-check
paperreel/develop.py    the same brief WITH its interview: a script talked into existence
paperreel/script.py     adopting a script written outside the studio
paperreel/agent.py      the tool loop -> board operations; `revise` and `direct`
paperreel/llm.py        the LLM Protocol + provider registry; gemini.py is the one impl
paperreel/skills.py     SKILL.md -> a system prompt and its settings, read off disk
paperreel/runtime.py    the SECOND tool loop: prompt and toolbox handed in, not written above
paperreel/tools.py      what the agents can do, over the modules that already do it
paperreel/critique.py   one still, one lens, one verdict + a suggested fix; renders NOTHING
paperreel/crew.py       three stages, each a CAST; `next_stage`; NO fourth stage, deliberately
paperreel/skills/       one directory per agent, each holding its SKILL.md
paperreel/jobs.py       one worker thread, one job queue, one event stream
paperreel/api.py        FastAPI routes + SSE
paperreel/media.py      ffmpeg: cutout, compose, fit, last-frame, tail, stitch
comfyui_minimax_h3.py   the Modal GPU app (ComfyUI on B200)
studio/src/             React 19 + @xyflow/react canvas, Tailwind 4, Vite
studio/src/route.ts     the ONLY place that reads or writes window.location
studio/src/stages/      the four stage pages; the canvas is one of them
```

### The four stages

The studio is a sequence now, not one page with four features layered on it:

```
/                            no reel open — the three ways in, at page size
/reels/:slug/script          the interview, the style bible, the scenes as prose
/reels/:slug/storyboard      named roster, then a panel per shot, then the sheets
/reels/:slug/assets          the still each shot opens on, and what it is drawn from
/reels/:slug/studio          the canvas: the chain, the price, the render
/reels/:slug                 → resolved from the board, never from a stored "last visited"
```

**`resolveStage` derives the default** — no beats → script; no panel written → storyboard;
`assets_needed` → assets; otherwise studio. Four reads of fields `to_json` already publishes,
so a hand-edited `storyboard.json` cannot disagree with it and nothing new is persisted. Same
for `StageRail`'s readouts. **No stage is ever gated**: `storyboard.py` stops at any stage, an
imported script may arrive with its stills made, and a `manual_stills` board skips stage 3
entirely — a locked rail would be the studio lying about a design whose claim is that the
stages are separable. What replaces a gate is the "waiting on" strip at the top of each page.

**No router dependency**, but all location knowledge is in `route.ts` — `parseRoute`,
`buildRoute`, `resolveStage`, `STAGES`, `STAGE_JOBS`. The route space is two path params and
one search key against a three-dependency `package.json`; what a router would have bought is
not worth a second authority on `window.location`, and the extraction is what makes swapping
one in later a one-file change. `?shot=n` syncs to `studio.expanded` through `replaceState`,
not `pushState` — closing the modal must not need a Back press.

**Stage navigation is in the left rail, not a bar across the top.** The top bar was killed for
spending a row of the window on state; this is navigation rather than state, so `RailRow`'s
"ours has nothing to navigate to" no longer holds — but the Storyboard grid and the Assets
still both want that height, so it stays in the column.

Two guards moved when the canvas became one stage of four, and both would silently stop working
if moved back: the **structure-changed reset** (clear selection / render selection / open scene
when `beats.length` moves) is in `useStudioState`, because `BeatModal` is opened from three
stages and a guard that only fires while React Flow is mounted is no guard; and the SPA
fallback in `api.py` now answers `/reels/{slug}/{stage}` as well, because Vite's dev server
falls back for any path and the built app 404s.

`ChatPanel` is on **storyboard and studio only**. On Script the transcript IS the page; on
Assets it is deliberately absent, because the board agent cannot see a picture and
`stills.converse` can — two conversations about one still, one of them blind, is worse than
one. Its "N scenes without a still" strip now navigates to Assets rather than firing the batch:
a batch you cannot watch is the thing that stage exists to fix.

### The two projects

```
Google API               paperreel  this repo            Modal
  gemini-3.7-flash       ──▶   script, board ops,   ──▶   MiniMax-H3
  vision · tools · think        caption, still review      the expensive stage
                                     │        ▲
image/  Papercut Studio               ▼        │ the still, looked at
  Gemini image API          ──▶   storyboard.json
  API, configured in .env         board + joins
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
- `modes` on `/api/health`, read by `papercut.edits`. It says whether that build knows the
  `edit` consistency mode; one that does not gets `anchor` for a picture redraw instead. Sending
  an unknown mode would be the worst of the three outcomes — the image server matches no arm of
  its own `referenceFor`, falls through to chain's backward walk, finds nothing on a one-frame
  scene and renders from the text, dropping the picture silently.
- `config.PAPERCUT_REF_ASPECT` names the `1:1` preset. Unlike `PAPERCUT_ASPECT` this one carries
  no constraint — a reference picture is conditioning, never a frame handed to H3 — so it can be
  retuned freely.

**What a still is drawn from is `Board.still_pictures`** — bound character/prop sheets first
(the identity lock), then this beat's storyboard panel when the PNG exists and the cap is at
least two (reserved, never first — an older image server that reads only `referencePath[0]`
must not turn the still into a pencil drawing), then a set sheet if it still fits, then
director uploads on a reference join. Beat 1's composed still is in **only when this beat binds
no character sheet**: sending both locked every later still to that camera. Deliberately *not*
the beat's own still, which is the thing being generated, and deliberately *not* in
`pictures_for` — H3 never sees the sketch. It is a different list from `pictures_for` and a much
shorter one (4 against 9). Sheets are join-agnostic so an asset or bridge still still matches
the puppets; uploads stay gated on `uses_refs`, because a picture on a keyframe beat reaches
the clip never.

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

Four modules, four jobs, and keeping them apart is what makes the review possible at all:
`papercut.py` is transport, `stills.py` is judgement about stills (which beats may get one, and
what happens to one that comes back wrong), `pictures.py` is the same for reference pictures —
which is a *different* judgement, see below — and `gemini.py` is the model.

`pictures.py` exists because a reference picture is now drawn, not only uploaded: `pictures.draw`
renders one into `beat<n>_ref<i>.png`, `pictures.converse` is a vision turn about it that ends in
a redraw. It is **not** part of `stills.py`, and growing it there would break the split above.
`stills.review` holds a still to the character sheets (or the cast still when there are none)
and rejects it for drift, wrong shot size against this beat's prompt, and a set that was
handed as a picture and came back as a different place; a reference picture is *supposed* to
differ from the cast — it is a prop, a set with nobody in it, a colour chart — so the
reviewer would reject almost every one. **There is no review pass on a picture, ever.**

Three things about a drawn picture were measured on a live render and are all the same mistake in
different clothes — a model shown the cast will draw the cast:

- **Nothing conditions a first draw.** The obvious design anchors it on the cast reference so it
  is made of the same paper. Tried: "a single iron-grey club" against a fox reference came back
  as the fox, because Gemini reproduces the subject it is shown. `pictures.conditioning`
  therefore returns `[]` for a new picture and the picture *itself* for a redraw. The medium
  travels as words instead.
- **`papercut.draw` overrides the scene `style`.** Papercut composes every frame as
  `continuity clause + frame beat + scene style`, so the board's style bible reaches the model on
  every frame whatever the beat text says — and that bible *is* the cast description. `draw` sends
  `config.REF_DRAW_STYLE_SUFFIX` there instead: the medium, and a design sheet's framing (subject
  whole and centred on a plain ground), which is the opposite of `ASSET_STYLE_SUFFIX`'s vertical
  9:16 shot.
- **A redraw uses `consistency="edit"`**, the fourth mode, added to `image/` for this. Every other
  conditioned mode prepends a clause ending "but move the subject into a clearly different pose
  and position" — right for the next frame of a moving sequence, exactly wrong when the reference
  IS the picture being changed and the note said "make the club longer". `papercut.edits()` reads
  `modes` off `/api/health` and falls back to `anchor` on an older build, which keeps the picture
  and loses only the hold.

`PAPERCUT_REF_ASPECT` is `1:1`, not the still's `9:16-reel`. That preset exists *only* so a still
lands on H3's generation grid uncropped; a picture is conditioning, never a frame.

`stills.converse` is the per-still conversation behind the node's **✎ talk about this still**
(job kind `still_chat`, `POST /api/reels/{slug}/beats/{n}/asset/chat`). A structured vision call,
not a tool loop — there are only two outcomes of looking at a picture with someone, the prompt it
should say instead and whether to draw it again. The turn is shown `Board.still_pictures` — what
the still is actually drawn from — and then the still itself, numbered rather than tagged: the
`<Picture N>` vocabulary is the *video* model's, and "the first image / the second image" does
not scale past two.

That endpoint is **multipart**, because a note can arrive with pictures attached, and they go
through `api.store_refs` — the same function the reference tray posts to. Storing them is the
only thing that works: `Board.still_pictures` reads the beat, so an image held for one turn would
steer the model's words and never reach Gemini. It therefore carries the tray's consequence, the
join moving to `reference`, which `StillChat` warns about before the send. An attachment also
forces `regenerate` on: the conditioning changed, so the picture on screen was drawn from
something the beat no longer says, whatever the model makes of the words. **The automatic review deliberately does not run
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
chained frame on the previous frame *alone* — the uploads would be silently dropped. A *picture*
maps differently on every axis: `REF_DRAW_STYLE_SUFFIX` → `style`, `ref_draws[i]` → the one frame
`beat`, the picture itself → `referencePaths` in `edit` mode, and `1:1` → `aspectId`.

### Staging: the reel's cast and sets

`staging.py` is `pictures.py` one level up, and the level is the whole point. A picture belongs
to one beat, so a second character had nowhere to live and the same clearing was redrawn from the
same paragraph in every shot that used it. The two things that were reel-wide before this both
have the same ceiling: `style_bible` is one paragraph (words land differently on every
generation) and the cast reference is one image, and not a design sheet at all — it is beat 1's
own *still*, a composed shot whose framing and light every later still is then anchored to.

A staging entry lives in `board.data["staging"]` as `{id, kind, name, note, draw, chat}`, its
sheet at `stage_<id>.png` **directly in the reel directory** — `api.media_file` serves only files
whose parent IS that directory, so a sheet in a subfolder would render and never be visible. A
beat binds ids in `beat["staging"]`, and `Board.bind_stage` replaces rather than appends.

`config.STAGE_KINDS` is not decoration. It decides three things at once, and `staging.style_for`
/ `staging.aspect_for` / `Board.staging_pictures` are the three places:

| kind | style suffix | aspect | in the clip | in the still |
| --- | --- | --- | --- | --- |
| `character` | `CHAR_DRAW_STYLE_SUFFIX` (`look().model`) | `PAPERCUT_CHAR_ASPECT` (16:9) | a picture | a picture |
| `prop` | `REF_DRAW_STYLE_SUFFIX` (`look().sheet`) | `PAPERCUT_REF_ASPECT` | a picture | a picture |
| `environment` | `SET_DRAW_STYLE_SUFFIX` (`look().set`) | `PAPERCUT_SET_ASPECT` | a picture | a picture if it fits the cap, otherwise **words** |

The set suffix exists because `look().sheet` asks for "the subject complete and centred"
on a "plain neutral background" with "no scenery", and a set sheet is nothing but scenery with
the subject deliberately absent — handed the prop-sheet suffix, "a moonlit clearing ringed with
birches" is a single birch on grey, which is a faithful reading of what it was told. The
character suffix exists for the other half of that lesson: sharing the prop suffix packed one
centred portrait into the identity slot, and H3 never saw the turnaround. `CHAR_SHEET_LAYOUT`
is four labeled sections of one puppet (turnaround, expressions, head, palette), not the
nine-section reference that would shrink the figure below a lock. Small labels are required;
a lore paragraph is forbidden because lettering leaks into the clip.

**The one measured constraint is the still's cap**, and everything else falls out of it. The
video model takes `MAX_REF_IMAGES` (9); the still takes `MAX_STILL_REFS` (9, matching it).
Identity sheets still win over extra graphite. A set that does not fit after those is dropped
and `Board.staging_text` picks it up — **whatever a render is not handed as a picture, it is
told in words**, one rule applied by
`config.build_prompt(staging=...)` and `papercut._beat_text`, each computing it against *the very
list it is conditioning on* so a sheet is never both `<Picture 2>` and a sentence about a second
one of it. A set that **does** fit is a picture: dropping it unconditionally invented a new
web in every shot.

Bound sheets sit between `auto_pictures` and the uploads in `pictures_for`, which renumbers the
uploads — safe by construction rather than by luck, because that method returns (path, role)
pairs and `mentions` resolves by path. `ref_budget` subtracts them, so an upload the render would
truncate is refused rather than stored. Character and prop sheets also wire an **asset** cut:
fl2va has no socket for a turnaround, so that cut renders on ref2va with the still as
`<Picture 1>` and the sheets after it. Chain and bridge keep a keyframe latent and cannot mix;
those sheets stay words. The composed cast still is omitted once identity sheets exist — sending
both pulled H3 back to beat 1's wide, the stills lesson applied to video.

**Both fingerprints append `staging_digest` only when the beat binds something.** An
unconditional part would change every existing board's fingerprint, mark every rendered beat
stale at once and re-price a paid render for a feature nobody had used. It overlaps
`frame_ids.refs` on a beat that wires pictures; that is harmless (both move together) and it is
what carries staging onto chain and bridge, which take the same sheets as words.

`pictures.py`'s three drawing lessons apply here unchanged, and `staging.conditioning` records
them: nothing conditions a first draw unless the director names a sibling with `@stage:` (a model
shown the cast draws the cast), the board's style bible never reaches the render (`papercut.draw`
now takes `style` / `aspect` / `label`, defaulted so a reference-picture draw composes the
byte-identical scene it always did), and a redraw is `consistency="edit"`. `staging.review`
holds a sheet to **its own note** (eye counts, closed palette, extra parts) — that is a
different question from holding it to the cast still, which would reject almost every sheet
because a design is supposed to differ from a composed shot. `pictures.py` still has no
review pass, for that same reason.

`papercut.NO_BEAT` is how a reel-level render rides the beat-keyed scene path. `_gemini_settings`
answers "nothing stored" for a frame that is not a beat rather than raising, which is what lets
one scene body serve a still, a reference picture and a design sheet.

### Storyboard panels

A storyboard in the film sense is a sheet of rough panels — several drawings per shot (opening,
midpoint, landing), showing framing,
angle, and with arrows on the panel how the subject and camera move. `panels.py` is that pass.
Written by Gemini into a per-beat `panel` field (free, one turn for the whole reel), drawn by
`gemini-3.1-flash-lite-image` at 1K, and stitched into `reels/<slug>/storyboard_sheet.png`.

**A panel conditions the still, never the video.** It is in `Board.still_pictures` as a
composition reference (`config.REF_ROLE_PANEL`) and is absent from `Board.pictures_for`. H3
never sees the graphite sketch. It is in no fingerprint: the still *file* is what the clip
hashes, and putting the sketch in `own_fingerprint` / `render_fingerprint` would mark every
paid clip stale over a drawing the video model never saw. Four things fall out of that split:

- **It is not in `own_fingerprint`, `render_fingerprint` or `frame_ids_for`** — not conditionally,
  as `staging_digest` is, but *never*. An unconditional part would mark every beat of every existing
  board `stale` at once and re-price a paid render over a sketch. The absence is commented where the
  digest ends, because a later hand will otherwise "fix" the omission.
- **The sketch is deliberately not the film's medium.** `config.PANEL_STYLE_SUFFIX` asks for
  graphite and grey marker and explicitly negates paper cutout. A Lite 1K version of the real
  medium is a bad preview *of* that medium and reads on the canvas as a finished still, which is the
  one confusion this feature must not create. The still prompt says match the composition, not the
  pencil.
- **Nothing conditions a panel** (`pictures=[]`, so `_scene_body` composes `"none"`).
  `pictures.py`'s measured lesson one level further out: a model shown the cast reference draws the
  cast, in the cast's medium. The subject travels as words. The cost is that consistency across
  panels is nil — two panels of the same fox are two readings of one sentence — and that is
  acceptable in a storyboard. The still is then drawn from the sketch *and* the identity sheets,
  which is how the film's medium comes back.
- **There is no review pass and no conversation on the panel itself**, and for a third reason
  again: `stills.review` holds a still to the sheets (and now to the panel for shot size),
  `pictures.py` skips review because a beat-level picture is supposed to differ from the cast,
  `staging.review` holds a sheet to its note rather than to a still, and here there is nothing
  for a verdict to be *about*. A wrong panel is redrawn, or its one line is edited by hand.

`config.PANEL_MODEL` / `PANEL_IMAGE_SIZE` are passed explicitly and therefore beat the beat's own
`gemini_model` (`papercut.draw` does `gemini_model or beat_model`), so a board whose stills are Pro
at 2K still gets Lite 1K panels — a storyboard drawn on the expensive model is not a storyboard.
`PANEL_ASPECT` is `9:16` (640×1152), not `PAPERCUT_ASPECT`: that grid exists only so a *still* is
not cover-cropped by `media.fit_frame`, and a panel is never a frame. `gemini_options` is
deliberately **not** wired to any panel route.

`papercut._beat_text` is the one place the still prompt changes: when the panel is among the
pictures it asks Gemini to match that composition and not copy the other refs' framing; without
a panel the old "do not copy a reference's framing" sentence is unchanged.

`Board.panel_path` is in `media_makers()` even though it is not a video input, because `renumber()`
renames through that tuple: a panel left out would hand beat 2 the sketch of the beat that used to
be there, and the next still would be drawn from the wrong shot. `sheet()` is PIL rather than
ffmpeg's `tile` filter — `tile` cannot caption each cell without a font path and a `drawtext`
escape dance, and a panel with no beat number under it is not a storyboard sheet. Job kinds
`panel_write` and `panel_draw`; the controls are `RailRow`s in `panels/Sidebar.tsx`, **not**
`CanvasToolbar`, which is the money bar.

### `@`-mentions

A director can name one picture inside any prompt field — `@ref:a1b2c3` for an upload,
`@stage:a1b2c3` for a reel-level design, `@cast` — instead of describing it again. The two hex
token spaces are namespaced in the resolved dict (`config.STAGE_MENTION_PREFIX`) because the two
id lists are minted independently and a bare body could name either.
`config.MENTION_RE` / `expand_mentions` are the grammar,
`Board.mentions(n, pictures)` resolves it, and the token is expanded **at prompt-build time on the
server**, never substituted in the browser.

**The token carries the picture's id, not its number**, and that is the whole design. Three
reasons, any one sufficient:

1. The same stored string is read by two prompt builders with incompatible orderings. The video
   model gets `pictures_for` tagged `<Picture N>`; the still model gets `still_pictures`, which is
   identity sheets (or the cast still) then the storyboard panels, capped at nine, with no tags at all. One literal cannot be
   right in both, so `expand_mentions` takes a `prose=` flag and each consumer passes its own list.
2. `ref_offset` moves when beat 1's still lands, when a `character.png` is pinned, when carry is
   ticked, and when the join is cycled — four events that touch no text and would silently
   relabel every number typed into prose. A number in prose is persisted derived state, which is
   the one thing the board document never holds.
3. `remove_ref` compacts, so a positional token would need rewriting on every delete.

A token whose picture is not in *this* consumer's list degrades to the picture's role text, and to
nothing when it has none — never to a position the model was not given. `Board.remove_ref` rewrites
mentions of a departed picture into what it was for (`_drop_mentions`), and deliberately does not
touch the transcripts: those are history.

**Five prompts rewrite a field that can hold tokens**, and a dropped token does not fail — it
renders a shot conditioned on a picture nobody named. `config.MENTION_NOTE` is one copy of the
"copy it exactly" sentence, spliced into `agent.SYSTEM`, `agent.REVISE_SYSTEM`,
`stills.CHAT_SYSTEM`, `pictures.DRAW_SYSTEM` and the still review's prompt — the review being the
one that fires without anyone asking. `config.lost_mentions` is the post-check: it cannot repair,
but it puts the loss in the transcript the director already reads.

**Mentions add nothing to the fingerprint, and that falls out rather than being decided.**
Expansion is a pure function of (raw text, picture list); the text is hashed already and the
picture list is hashed as ordered `(file_hash, note)` pairs, so every event that changes an
expansion changes one of them. `ref_draws` is out of the fingerprint for the same reason
`asset_prompt` is: the image it produced is hashed instead, and fingerprinting both cause and
effect would mark a beat stale — and re-price a paid render — over a prompt nobody has pressed ✦ on.

**Product tension worth knowing:** `image/PRODUCT.md` principle 3 says nothing leaves the
machine. That still holds of the image project itself — but a still it renders *is* uploaded
to Modal when paperreel renders the video. The promise is about the image tool, not about
what the user does with its output; don't "fix" it by adding uploads to `image/`.

### The language model

`gemini.py` is the only place that talks to the language model, over plain httpx — no SDK, no
LangChain, no new dependency (httpx is already in every entry point's PEP 723 block). Three
shapes of call, all `models/<model>:generateContent`:

- `structured(messages, schema)` — the decode is constrained to the JSON Schema
  (`responseJsonSchema`), so the script and every verdict come back validated rather than
  parsed hopefully.
- `chat(messages, tools=...)` — one round of a tool loop. `calls_of` / `answered` handle the
  round-trip; `agent.turn` drives it, capped at `config.AGENT_MAX_ROUNDS`.
- `text(messages)` — prose, for the caption.

**Callers speak the Ollama message vocabulary and `gemini.py` translates it.** Every prompt in
`agent.py`, `planner.py`, `stills.py`, `pictures.py`, `staging.py` and `panels.py` is written
as `{"role": "system"|"user"|"assistant"|"tool", "content": str, "images": [base64]}`, and
`_contents` turns that into Gemini `contents` + `systemInstruction`. Keep new callers in that
vocabulary: the transport is the one place a change of provider should be visible.

Vision is the same call with base64 `images` on a message (`gemini.encode`, which downscales
to `config.LLM_IMAGE_EDGE`). Used by `stills.review` / `stills.converse`, `pictures.converse`
and `staging.converse`.

Things that are the way they are because they were measured, not assumed:

- **`think` is passed explicitly on every call, and defaults to off** — `low` rather than
  the model's own default (`medium` on 3.7-flash). Thought tokens are billed as output and an
  unambiguous board edit needs none; only the planning pair asks for `high` (`config.PLAN_THINK`).
  `minimal` 400s on gemini-3.7-flash, so it is not the floor.
- **An assistant turn goes back to the API as the parts the model returned**, kept on the
  message as `_parts`. Gemini 3 signs its reasoning (`thoughtSignature`) and checks that
  signature on the next turn, so a reconstructed text-only assistant turn breaks the tool loop
  `agent.turn` depends on. Same reason `answered()` returns one message holding every
  `functionResponse` rather than one per call.
- **A function declaration takes `enum` on strings only.** `{"type": "number", "enum": [5, 10]}`
  on the beat length — legal JSON Schema, and what Ollama was given — answers with a 400 naming
  the index and takes the whole tool loop with it. `gemini._declarable` moves a non-string enum
  into the parameter's description rather than dropping the constraint.
- **A structured field the model writes *first* becomes its scratchpad.** The decode follows
  schema-property order, so `changes` declared before `beats` in `REVIEW_SCHEMA` produced 40
  log lines of stream-of-consciousness before a single beat was rewritten. It is declared last,
  and thinking is on for that call so reasoning has a channel of its own.
- **Prompt order in `agent.turn` is history → board → question.** The board used to come first,
  which left a stale line of the model's own transcript nearer the question than the truth —
  and it answered from the nearer one, insisting a four-beat reel had five for the rest of the
  session. `transcript()` also labels itself as history for the same reason.
- **`board_digest` spells out the "waiting on" lists** rather than leaving them to be inferred
  from the joins. Asked which beats need a still, the model reasoned from the join names and got
  it wrong in both directions. Both lists are already derived in `board.py`.
- **Tool parameter descriptions are load-bearing.** Given a bare "Edit one beat" the model spent
  a whole turn reasoning about what a parameter called `action` wanted — reading the field name
  as a verb. `gemini.tool()` exists to make writing them the default.

**Both review passes are affordable, not free.** Neither would have been worth a slot in
`agy`'s five-per-five-hours window; both are worth a flash turn, which is a fraction of one of
the images this pipeline already spends without hesitating. That is the test to apply to a new
call — not "does it cost anything" but "is it worth less than the image it is checking".

**No LangGraph, deliberately.** The graph here is four nodes with one back-edge, and the two
things a graph framework would bring are already owned: durable serial execution is
`jobs.Runner`, and state is `storyboard.json`, which is the only database. A checkpointer would
be a *second* store of the same state, which is exactly the drift the derived-state design
exists to prevent. `crew.run` is what that decision looks like in code: a `while` loop over
four `if` statements.

### The medium: what the film is physically made of

`config.MEDIUMS` is a table of `Medium` bundles, one per material, and `board.data["medium"]`
picks one. Before it, every string that named paper was a module-level global — nine of them
reached a render and one of them, the vision review's, *rejected* a still for not being paper.
So a board whose style bible said clay was fighting its own reviewer.

A `Medium` carries fifteen fields, which is the honest count of how many places a medium is
named: `shot` (every video prompt's opening clause), `surface` (the material words in the
reference paragraph), `craft`, `audio`, `still` / `sheet` / `model` / `set` (the four image
suffixes — still, prop sheet, character model sheet, set sheet),
`judge` (what the review holds a still to), `essence` (the parenthetical four chat prompts use
for "not yours to overrule"), `negate` (what a storyboard panel must NOT look like), `avoid`
(what both renderers must not produce — H3 hears it as the closing `Avoid:` block, Gemini as
Papercut's `negativePrompt`; neither model has a real negative socket), `name`,
`opening`, `physics` and `construction` (the brief's two medium-bound sections).

**Three media ship: `paper-cutout`, `paper-craft` and `claymation`, and they are not
translations of each other.** Cutout is stacked flats: a shape is *swapped* for another on
a pin. Papercraft is the same rigid paper *folded into volume* — scored creases, faceted
forms, tabs. Clay's grammar is that a shape *becomes* another — squash and stretch is what
the medium is for. Writing any of them under another's physics produces the failure that
reads as a cheap 3D render. Each entry is written from the material outward rather than by
substituting words into another.

**Absent means the default, and the default is stored by being absent.** `Board.medium()` reads
a missing key as paper cutout; `Board.medium_digest()` returns `""` for it; `PATCH` *deletes*
the key when set back to paper. One representation, so "a board that never named a medium" and
"a board set back to paper" are the same board — and every reel written before the bundle keeps
the fingerprint it had. Verified: 9 boards on disk, 0 drift, and 384 prompt shapes byte-identical
to what `build_prompt` produced before.

**The film envelope is that rule applied to duration.** Absent means a reel. `envelope`, named
`acts`, `continuity_notes` and `render_budget` are digest-visible when set and never in a
fingerprint -- they change the brief, the chapter stitch and the quoted cap, not a beat's clip
hash. Putting any of them in `own_fingerprint` would re-price every existing reel. `make harness`
is the sensor for that promise. Queued GPU jobs survive a studio restart in `.jobs.json`; only
`render` is restored, and already-`RENDERED` beats are skipped.

`prompts/40s-stop-motion-script.md` (renamed from `40s-paper-cutout-script.md`) is forked at
five seams — `<<<OPENING>>>`, `<<<PHYSICS>>>`, `<<<CONSTRUCTION>>>`, plus `<<<LENGTH>>>` /
`<<<DURATION>>>` for the reel vs film envelope — resolved by
`planner.template(medium_key, envelope)`. The other ~88% is pipeline and is word-for-word correct in any
medium. **`agent.MEDIUM` is misnamed and needs no changes at all**: read it and it is entirely
the four joins, 5 s or 10 s, one thing moves, the camera never moves.

### `blocking`: where things stand in the frame

A new per-beat field, and the gap it fills was real. The style bible says what things look like;
a design sheet says it again precisely for one named thing; the `scene` line says where the shot
is and at what scale — and is deliberately *shared* by every beat of one continuous shot, so a
shot where the subject crosses left to right has one scene line for both halves. The `panel`
says shot size, angle and camera move, and conditions the still, never the clip. **Nobody said what is
standing where.**

`BLOCKING_PREFIX` ("In frame: ") sits between the staging and the scene line in `build_prompt`.
It is in both fingerprints, **conditionally** — the `staging_digest` rule, appended last, in the
same order in both methods, because `fingerprint()` is positional and the two disagreeing is how
a beat flips between `stale` and `invalidated`. Unlike `panel`, editing it marks the beat stale,
which is correct: it changes what the beat renders.

### The crew: three stages, each a cast

`crew.py` walks a reel from a concept to stills on disk and stops. Three entry points, one
implementation: `uv run crew.py`, job kinds `crew` / `agent` on the studio's queue, and
`from paperreel import crew`.

```
crew.py               --concept --medium | --name | --stage | --through | --agent
                      | --where | --list | --dry-run
paperreel/crew.py     STAGES, STAGE_CAST, CHECKERS, style_artist(), next_stage(), plan_of()
paperreel/runtime.py  Hooks, Context, Tool, Agent, Turn, build(), run(), preview()
paperreel/skills.py   SKILL.md -> Skill; placeholders; mtime cache
paperreel/tools.py    27 tools, every one a call into a module that already existed
paperreel/critique.py three lenses over one still; a verdict and a suggested fix
paperreel/llm.py      the Protocol; `gemini` is registered against it as a module
```

**A stage is a cast, not an agent.** A film crew is several specialists on one scene rather
than one generalist per phase, and the failure that prevents is specific: one agent asked to
write the story AND fix the material AND block the frame answers about whichever it noticed
first.

| stage | cast, in order |
| --- | --- |
| `script` | `script-writer`, then the style artist |
| `storyboard` | the style artist, then `mise-en-scene` (extract the roster), then `storyboarder`, then `character-sheet` and `set-designer`, then mise blocking, coherence, continuity, then mise again to lock |
| `assets` | `asset-maker`, then three checkers: style, blocking, story |

Order is load-bearing in every row. The writer goes first because there is nothing to style
until there are beats. On storyboard the style artist goes first, then `mise-en-scene` names
and binds the roster — `panels._digest` names the designs a beat binds, so a panel written
before the binding is a panel written about a cast it could not see. The panels go **before
the sheets**: a sheet before a panel is a puppet with no shot to be in. Character-sheet and
set-designer draw after the storyboard, then mise blocks against those sheets, then locks
the roster by looking at the panels next to them.

**Members do not pass messages; the board is the passing.** Each runs in turn and reads what the
one before left, which is why no agent is handed another's output and why the board is reloaded
between them. A member that fails is logged and stepped over — these are separate specialists,
and the storyboard is still worth having when the design pass fell over. The phase cursor does
not advance for a phase that failed: a 429 that stamped extract/panels/sheets/seams/lock `done`
is how a billing miss looked like the roster existed (measured 2026-08-17). `reopen_phase` on
the first failed phase is the sensor; later members still run.

**A role is not a job; a stage plus a role is** (`crew._is_check`). The style artist and the
script writer MAKE on their own stages and CHECK on the assets stage, so the same skill name
appears in three casts doing two different things.

**The style role resolves by medium.** `STAGE_CAST` names `crew.STYLE`, and `style_artist(board)`
turns it into `style-paper-cutout`, `style-paper-craft` or `style-claymation`. One skill
per medium, one tool set — what differs is the prompt, not what they can do. Each owns
`set_medium`, so the skill and the render ask for the same material by construction rather
than by the director remembering to set both.

**The cross-check reports; it never re-renders.** Three lenses (`critique.LENSES`) — craft,
staging, story — one structured vision call each, filed into the beat's own `asset_chat` beside
`stills.review`'s verdicts, carrying a concrete suggested fix. The bound is a money bound as
much as a design one: three lenses that could each reject and re-render would turn one
disagreeing panel into a run that spends its whole `CREW_STILL_BUDGET` on one beat. Three vision
calls per still, once. `POST /api/reels/{slug}/beats/{n}/inspect` runs one lens on demand;
`GET /api/reels/{slug}/crew` answers what the crew would do next, free.

**The agents wrap; they never reimplement.** Every tool is a call into `agent.py`, `board.py`,
`develop.py`, `panels.py`, `pictures.py`, `planner.py`, `staging.py` or `stills.py`, so the
measured prompt scaffolding, the fingerprint rules, the still review, the join guards and the
picture budget all keep one copy. Descriptions are borrowed too — `tools.borrowed` re-declares
an entry of `agent.TOOLS` through the provider rather than retyping a second wording of it, and
`narrowed`/`called` is how the asset stage gets a one-field `set_asset_prompt` off the same
`set_beat` literal. The toolbox is one flat namespace, which is *why* it is renamed: two tools
answering to `set_beat` silently overwrote each other the first time.

**`next_stage` mirrors `resolveStage` (`studio/src/route.ts:93`) line for line, and answers
`None` where that answers `"studio"`.** That is the whole money boundary: "the crew is
finished" and "only the paid stage is left" are literally the same value, `STAGES` has three
entries, and there is no cast a fourth could resolve through. It is two
implementations of one rule in two languages — the drift `planner.py`'s docstring warns about —
because the Python answer is not reachable from the browser without a route. `crew.py --where`
is what makes a disagreement observable in one command; it agrees on all nine boards on disk.

**Nothing in this layer can spend the GPU, structurally.** `tools.py` does not import `render`,
`pipeline`, `comfy` or `modal`; there is no tool that could reach them; `runtime.build`
validates every `tools:` name in a `SKILL.md` at load, so a skill file — which is data a user
can edit — cannot name one either. `crew.py` has no `--render` and no `--all`, and its PEP-723
block is `["pillow", "httpx", "numpy", "scipy"]`: numpy/scipy key a sheet for the local
still compositor, and there is still no `imageio-ffmpeg`, so this remains the first entry
point that cannot reach the video pipeline. The dependency list is where that is visible.
The invariant is one grep, written into
`tools.py`'s docstring so it survives.

The one guard this layer *adds* rather than inherits is `config.CREW_STILL_BUDGET` (72), counted
in `Context.state` across every stage of a run. `max_rounds` bounds turns and not money, and
`generate_stills` is the one metered tool in the toolbox. **That number is a guess, not a
measurement** — the first real run should replace it. `CREW_GEMINI_BUDGET` (200 rounds, 0 = no
cap) is the same idea for words: a storyboard cast is nine members, each with its own round cap.

**A skill is `paperreel/skills/<name>/SKILL.md`** — flat frontmatter plus a markdown body, read
per run and cached on `(path, mtime)` so a prompt can be edited against a running `studio.py`.
The frontmatter reader is 40 lines and is *not* YAML: the schema is closed and flat, and PyYAML
would be downloaded by four PEP-723 entry points to buy only the ability to write frontmatter
this schema then rejects. `schema:` therefore names a dotted path (`planner:PLAN_SCHEMA`) rather
than inlining one — those schemas carry a property-**order** lesson in their comments and a
second copy in frontmatter is the drift. `{{MEDIUM}}`, `{{BRIEF}}`, `{{SHOT_GRAMMAR}}`,
`{{MENTION_NOTE}}` splice in the one copy of each; an unknown placeholder is an error, because a
model handed a literal `{{CAST}}` answers about a variable.

**Nothing per-run is in a system prompt.** The board digest goes in the user turn through
`crew.prelude`, which is `agent.turn`'s measured order (the model answered from whichever
board-shaped text sat nearest the question) and is also what makes the mtime cache sound.

**There are two tool loops now, and that was accepted rather than overlooked.** `agent.turn`
stays frozen: moving the studio's chat panel onto `runtime.run` would buy nothing a user can see
and would put the most-exercised path in the product through untested code. `runtime.run` adds
two things `agent.turn` does not, both because a crew run is longer than a chat turn —
cancellation checked *between rounds*, and a `Context.state` that survives the whole run.

**`gemini.py` was not renamed, moved or wrapped.** A module satisfies a `Protocol` of plain
functions, so `llm.LLM` is written to `gemini.py`'s existing surface and `gemini` itself is the
implementation; the only edit was reparenting `GeminiError` onto `llm.LLMError` (still a
`RuntimeError`, so all nine `except gemini.GeminiError` sites are untouched). `tool()` is on the
Protocol on purpose: `_declarable` folding a numeric `enum` into the description is a
per-provider dialect fix, so the declaration builder belongs to whoever knows the dialect.

**Transcripts land in the board's own `chat` array under the skill name** — `ChatTurn.role` in
`studio/src/types.ts` was widened for it. Same reason `agent.revise` writes there: an agent that
rewrote five beats and left no trace is a board that changed for no reason the next
conversational turn can see. Per-round tool chatter goes to the job log instead.

### Two ways the model writes a script, one specification

`planner.plan` is the one-shot path: `planner.brief()` splices section 0 of
`prompts/40s-stop-motion-script.md` out and replaces it with `ANSWERS`, because the studio's
form had already settled the beat count and one length for every beat.

`develop.py` is the conversational one, and it exists because section 0 **is a four-question
interview** — "STOP, interview the director first… Only after you have answers do you write the
script." So this path splices out nothing: `develop.brief()` is `planner.template()` whole,
with the concept filled in. There is still exactly one copy of the specification, and nothing in
`develop.py` restates a rule of the medium. Differences worth knowing:

- **The board exists from the first message**, with `beats: []` — `POST /api/reels/develop`
  answers synchronously so the browser can stand on the conversation before the model speaks.
  Verified representable end to end: `states()` is `{}`, `pending()` is `[]`, `cost_of([])` is
  zero, and `summaries()` reads `beats: 0`, which the rail shows as `draft`. The payoff is that
  `data["chat"]` is the transcript, so the interview and every later board conversation are one
  history — the thing `agent.create` fakes by writing two synthetic turns after the fact.
- **One tool, `write_script`,** derived from `PLAN_SCHEMA` plus a per-beat `seconds`. Required,
  not optional: section 0's first question is about *mixed* lengths, which the one-shot path
  cannot express. `seconds` is a `number` with the two legal values in its **description** — a
  numeric `enum` on a function declaration answers 400 and takes the call with it.
- **The self-check is `planner.review`, unchanged**, with one parameter: `settled`. Its
  paragraph about what the review may not touch is the only thing that differs between the two
  paths (`SETTLED_BY_FORM` / `develop.SETTLED_BY_INTERVIEW`), and both are about *scope*, not
  about the medium. `planner._as_json` does not show the review `seconds`, so `develop`
  re-attaches them by position afterwards — safe because `review` rejects a result whose beat
  count moved.
- **`develop.adopt` merges onto the board rather than creating one.** `script.adopt` would mint
  a new slug and move the page out from under a director mid-sentence.
- **One guard, and it guards money**: a board with any `render` record refuses the rewrite (409),
  because a new script would orphan paid clips. Scene-by-scene editing is still open.
- One model call per turn, not a loop: the tool's effect is the board, and the board is what the
  page is showing.

### `reels/<slug>/storyboard.json` is the only database

There is no other store. Everything else — beat state, staleness, cost, what needs
rendering — is **derived** from the JSON plus what is on disk (`board.py`, "Derived state").
Nothing derived is ever persisted. That is what lets the CLI, a hand edit, and the canvas
coexist without drifting. `Board.script_notes` is the newest of them: `script.notes` answering
for a board at any age, with the joins resolved through `source_for` because a beat on disk may
predate the field.

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

**A long take stays on `reference`.** Chain and bridge cannot mix a keyframe latent with
the nine image sockets, so `reference, chain, chain, bridge` is how later beats lose the
sheets and the pose sequence. Successive `reference` beats of the same shot hold the
previous clip as `<Video 1>` once poses exist. `chain` is only the pixel-exact last-frame
handoff.

`chains()`, `uses_asset()`, `uses_refs()` in `board.py` are the predicates; use them rather
than comparing strings. `uses_asset()` answers "does the still go into a KEYFRAME slot" and is
therefore false for `reference` — the question "must a still exist on disk" is
`Board.needs_still(beat)`, which is beat-level because a reference beat carrying motion answers
it differently. Same split as `chains()` vs `Board.follows_upstream()`.

**`reference` is the default cut, not an uploads-only special case.** It is a different
checkpoint (`config.UNET_REF`) taking up to `config.MAX_REF_IMAGES` (9) pictures referred to in
the prompt as `<Picture 1>`…`<Picture 9>` **1-based in connection order** while graph sockets are
0-based. `Board.pictures_for(n)` is the single place that order is decided:

1. the beat's own generated still (or its stop-motion sequence) — `config.REF_ROLE_OPENING`,
   and `config.OPEN_REFERENCE_STILL` tells the model to begin the clip on it;
2. the reel's locked cast reference (`Board.reference_for`, so `None` on the beat that *is* the
   reference) — `config.REF_ROLE_CAST` — **only when this beat binds no character or prop
   sheet**. A turnaround is the puppet; the composed wide is a camera;
3. bound staging sheets, characters and props before sets;
4. then the director's uploads, `beat<n>_ref1.png` upward — which are now *drawn or* uploaded;
   `pictures.py` puts a Gemini render into the same file, and nothing downstream can tell.

An **asset** cut that binds identity sheets uses the same list (still as Picture 1, then the
sheets) on ref2va: fl2va has no socket for a turnaround. Chain and bridge stay keyframe.

Everything a beat stores per picture is a list the same length as `ref_paths`, read and written
through one pair of methods (`Board.REF_SLOT_KEYS`, `_ref_slots`, `_store_ref_slots`) rather than
a trio each: `ref_prompts` (what it is FOR — reaches both prompts), `ref_draws` (the Gemini prompt
— reaches neither), `ref_chats` (its conversation), `ref_ids` (a stable handle). `remove_ref`
deletes index `i-1` from every one of them, renumbers the files, and rewrites the mentions — and
it reads all four lists **before** unlinking, because `_ref_slots` sizes itself off `ref_paths`
and a read taken afterwards is already a picture short.

`ref_ids` is the answer to the fact that a position is not an address here. Delete picture 2 of
four and `beat3_ref3.png` becomes `beat3_ref2.png`: an id is what lets a mention, a selection in
the modal and a queued draw job all still mean the picture they meant.

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
`OPEN_REFERENCE`/`CARRY_VIDEO`, plus `ARRIVE_ON_LAST` for a bridge. On ref2va those clauses
sit inside MiniMax's six-part reference format (`subject_definitions` / `summary` /
`retention_analysis` / `detailed_description` / `overall_soundscape` / `non_diegetic_music`)
rather than a concatenation. Getting the wording wrong does not fail; it produces a visible
restart jolt at the seam, or a character sheet as the opening frame. `config.py` documents
each clause. Panels still never go to H3. The sheet region map is scaffold, not `STAGE_ROLE`.

### Staleness cascades

`Board.states()` runs one downstream pass, not per-beat checks, because a *pending* upstream
edit does not change a downstream beat's own fingerprint. `STALE` means "you edited this";
`INVALIDATED` means "something it follows changed". `cascade()` expands a manual render
selection downstream; a cut (or a reference beat not carrying motion) breaks the run.

### Jobs and events

`jobs.Runner` is a single daemon worker thread with one queue — serial on purpose: one GPU
container, ComfyUI runs one graph at a time, chaining is serial anyway. `api.py` registers
handlers (`plan`, `chat`, `asset`, `still_chat`, `revise`, `direct`, `ref_draw`, `ref_chat`, `stage_draw`,
`stage_chat`, `caption`, `render`) and never blocks the request. `ref_draw` and `ref_chat` are two
kinds rather than one for the same reason `asset` and `still_chat` are: ✦ must not spend a model
turn. The `stage_*` pair are their own kinds for the reason those are: they address a reel-level
design rather than a picture on one beat, and the canvas has to say which sheet is busy.

`DELETE /beats/{n}/refs/{index}` refuses with 409 while a picture job for that beat is queued or
running. The job captured its index at submit and `remove_ref` renumbers, so it would otherwise
draw into whatever slid up into the slot.

`DELETE /staging/{id}` and the sheet upload refuse the same way, narrowed to that one design
(`api.stage_busy`). There is no renumbering to protect here — a design's id is minted once and
`stage_path` is keyed by it — so the guard is against deleting or overwriting the file a queued
draw is about to write into.

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
Modal. Don't move the `modal` shell-outs anywhere else, and don't give the browser the Google
API key either — the model is reached from the server, so one place decides what it is allowed
to spend. `config.GOOGLE_API_KEY` reads `X-GOOG-API-KEY` first (the spelling the image server
uses in the same `.env`), then `GEMINI_API_KEY`, then `GOOGLE_API_KEY`.

## Constraints that are not negotiable without new measurements

- **Beats are 5 s or 10 s, nothing else** (`config.BEAT_LENGTHS`). 5 s is the model's
  124-frame floor; 10 s is 243 frames, the longest render that has ever completed on this
  card. 15 s (362 frames) failed. Render time is superlinear in frame count.
- **Generation is 768×1344**, the closest multiple-of-32 vertical to ~1 MP; delivery scales to
  1080×1920 in `media.py`.
- **8 steps** is the measured sweet spot; 20 costs ~70% more.
- **B200 is required** — unpruned BF16 is ~115 GB resident and does not fit on 96 GB.
  The old "1.19× faster, 2.06× the rate" figure was the quantized job on this card;
  wall-clock on BF16/B200 is unmeasured. Escape hatch is B300 (288 GB), not 4×H200.
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

**The shell is three columns and no top bar.** `panels/Sidebar.tsx` (what exists, plus the two
ways to make a board), the canvas as a single white card, `panels/ChatPanel.tsx` (what you are
saying about it). The full-width bar that used to hold container state, the billing clock and the
render buttons is gone: the three readouts are `RailRow`s in the sidebar and the two controls that
spend money are `panels/CanvasToolbar.tsx`, floating over the board they would spend it on —
the price quoted is the price of the beats you can see. It confirms before spend when inspect
has not run or standing inspect failures remain; the render API itself never 409s over that.
`ChatPanel` still cannot render, and that
is load-bearing rather than an omission (see `StoryPanel`'s note, which moved with it).

**Colour is tokens, never hexes at the call site.** `index.css` `@theme` owns `ink` (the page and
a text field's ground), `panel` (every card), `edge` (the one border weight), `soft`/`softer`/
`hover`, `solid` (the black primary action) and the state family `warm`/`stale`/`live`/`danger`.
A literal `bg-[#…]` in a component is how the two grounds drifted apart file by file before, so
add a token instead. The primary button is black and the warm accent is reserved for *state* — a
cut, a missing still — because when both were amber "render", "generate" and "this needs a still"
read as one thing, which is the distinction the money bar exists to make. `.lift` / `.lift-lg` are
the only two elevations.

`BeatModal` is the expanded scene, opened by **⤢** on a node and rendered from `App` rather than
from the node — a `position: fixed` overlay inside a node is measured against React Flow's
transformed viewport and pans away with the canvas, so `studio.expanded` holds the beat number.
It is a second *view*, never a second copy: every control on it is the same endpoint the node
calls, and `ReferenceNote` is imported from `SequenceNode` rather than re-typed. The join and the
render checkbox are deliberately absent — both are decisions about the chain, which is the thing
the canvas exists to show.

The modal's thumbnail strip is where a picture is added, selected and removed; `PicturePanel` is
the right column for one. Four pieces exist so that neither view owns a copy of the other's:

- **`studio/src/beat.ts`** is the only place either picture numbering is computed. It mirrors
  `Board.pictures_for` (`videoPictures`, `<Picture N>`, empty on chain/bridge; asset cuts that
  bind character sheets are not empty) and
  `Board.still_pictures` (`stillPictures`, identity then panel then set/uploads, capped) line for
  line, and `board.py` is
  the authority it must not drift from. Before it, the arithmetic was hand-rolled in three
  components; `@` would have made it five.
- **`AddPicture`** owns the file input, the slot budget and the join warning, and is used by the
  node's button, the modal's `+` tile and the modal's drop target. Storing a picture moves the
  beat onto the reference join (`api.store_refs` does it before the first byte is written), so
  the warning has to exist wherever a picture can be added — as an inline confirm strip, since
  files must be *staged* to be cancellable: you cannot ask after the picker fires and before the
  upload unless you are holding them.
- **`AssetChat`** is the transcript and composer, with `StillChat` and `PictureChat` as thin
  wrappers. `PictureChat` passes `attach={null}` — in a still's conversation an attachment means
  "here is what I mean" and is stored because the still is drawn from the beat; in a picture's,
  the picture *is* the subject and a file would mint a tenth reference nobody asked for.
- **`Mentions.tsx`** is the first floating widget in this studio and is kept deliberately narrow:
  an absolutely positioned div in a wrapper the field owns — no portal, no fixed positioning, no
  generic `Popover` in `ui.tsx`, and anchored to the *field* rather than the caret. Three lines
  are non-obvious and commented: `onMouseDown` preventDefault (or the blur unmounts the menu
  before the click lands), `scrollIntoView` on open (the modal's right column is itself a
  scroller and clips it), and a `useLayoutEffect` restoring the caret (React does not preserve
  selection across a programmatic value change). Its `onKeyDown` returns a boolean, which is the
  whole Enter arbitration — and because it calls `stopPropagation`, the first Escape closes the
  menu and the second closes the modal, rather than one doing both.

**What a scene node no longer holds.** The still's upload/generate row, `StillChat`, the
references row with `AddPicture` and the numbered `ReferenceNote` list all moved to the Assets
stage — a 240 px card was never where a picture got judged, and what a still is *conditioned on*
cannot be read at that size at all. In their place is one row saying whether the still exists and
linking to the stage. `ScriptNode` went the same way: the style bible to Script, the cast
reference and `FillStills` to Assets, leaving a header card with `＋ add scene at end`, which is
structure. Every one of those controls still works and each is a second *view* of the same
endpoint, never a second copy — `ReferenceNote` is still exported from `SequenceNode` for
`BeatModal`, which is unchanged and remains the full-screen scene.

`agent.MEDIUM` is the one copy of the rules of the medium, shared by `SYSTEM` (the tool loop),
`REVISE_SYSTEM` (`agent.revise`, job kind `revise`, `POST /api/reels/{slug}/beats/{n}/text`,
behind **✎ revise** on the scene and action fields), and `DIRECT_SYSTEM` (`agent.direct`, job
kind `direct`, `POST /api/reels/{slug}/beats/{n}/direct`, behind **Direct this shot** on the
action). Same reason `planner.py` hands over the whole brief rather than a précis: two prompts
that write beats from two summaries of the same rules drift, and nothing fails when they do.
`revise` is a structured call rather than a tool loop because the beat and the field are in
the URL — what the chat agent spends a round working out. It is shown the beats either side,
because a continuation's action has to read as continuing and a shared scene line is what says
two beats are one shot; given the beat alone the model wrote both of those out. Its turn lands
in the board's own transcript, not a per-field one: it is a story edit, and the next
conversational turn reading a board that changed for no reason it can see is the drift
`transcript()` exists to prevent.

**Direct this shot** is the same structured call with no director note. `revise` does what
the director asked; this rewrites the action so MiniMax-H3 can shoot it — visible moves in
playback order, one gesture that fits the 5 s or 10 s, a named ending pose — without inventing
camera moves, dialogue, or the six-part wrapper `build_prompt` already assembles. Scene is
deliberately not in the rewrite: a shared scene line across a chain must not silently diverge.

`prompts/40s-stop-motion-script.md` is **the** specification of what a script for this pipeline
has to be, and all three ways into a board are written against it: a human pastes it into an
outside AI; `planner.brief()` hands the same file over with only its opening interview (section
0) spliced out, for the beat count and length the studio already asked for; and `develop.brief()`
hands it over with section 0 *intact*, because that section is the interview. `script.py`
normalises whatever comes back, from any path.

So: change the brief, not a copy of it. There is deliberately no summary of those rules inside
`planner.py` — there was one, and a summary that drifts from the document has the two paths
quietly writing to different specifications. Same for the review pass: it hands the whole brief
back and asks the model to run section 11's self-check, rather than restating the checks.

## Known gaps

Listed in [ARCHITECTURE.md](ARCHITECTURE.md) under "Known gaps" — the CLI can still request 15 s beats, cross-scene
consistency is unmeasured, the container may be over-provisioned at 8 cores / 128 GiB, and the
studio's per-step WebSocket progress has never been exercised against a live render.

**A still drawn from more than one picture is timed, not judged.** The cap, the notes clause and
the join guard are exercised on a real board, and reference counts affect Gemini request cost;
the exact latency depends on the selected model and output size.
`config.MAX_STILL_REFS` and `image/src/estimate.ts`. What has *not* been compared is whether more
pictures hold the cast better than one, or whether the *reference images show: …* clause helps
rather than giving Gemini one more thing to draw into the frame. Do not quote a quality claim;
`PAPERREEL_MAX_STILL_REFS` is how to explore it.

Unmeasured on Gemini: the still review's false-accept rate on *subtle* cast drift (it was
verified on the local model to reject an obvious mismatch and pass a good still, which is not
the same thing, and has not been re-run since the move), and how a board behaves when the API
refuses mid-turn — a 429 in the middle of a stills pass is the likely shape and nothing has
produced one yet. Planning measured 239 s end to end (draft plus self-check, thinking on for
both), so it is still a job you watch.

**Per-call latency on this machine is not the API's.** Every request measured during the move
came back in ~81 s regardless of payload — 1 KB prompt, 100 KB prompt and two 1.5 MB stills all
the same, while `curl` against the same endpoint answered in seconds. Something in the local
network path, not the model. Do not quote those numbers as Gemini's speed, and do not tune
timeouts against them.

**The expanded scene view is unexercised in a browser.** Its server side is not: a note with a
picture attached stores the picture, moves the join, redraws the still and writes both turns into
the transcript, and `revise` came back with the line rewritten and a sentence about it on both
fields. What nobody has clicked is the view — picking between assets, the modal following a beat
that changes underneath it, Esc during a running job. `revise` also has no self-check of any
kind: one call, no second pass, so a rewrite that drops half the movement is caught by the
director. Its `reply` field needed the prompt to name both JSON fields explicitly — given only
the schema the model filled `reply` with the beat's *other* line, reading the object as a form to
copy the board into. Do not drop that closing sentence.

The per-still conversation (`stills.converse`) has now run end to end against a live model and
Gemini — one turn with a picture attached rewrote the prompt and redrew the still — but that is one
turn. What is unverified is how often the model sets `regenerate` correctly (a question about the
picture that redraws it anyway costs a new Gemini request) and whether it really carries the
untouched half of a prompt through a rewrite rather than paraphrasing the style bible. Both show
up in the transcript on the node, which is where to look first.

**Moving the default cut to ref2va is reasoned, not measured.** No A/B render exists for how
much exactness the opening frame loses, how much the extra reference tokens cost per beat, or
whether the per-shot-boundary checkpoint swap is material. Do not quote a number for any of the
three. `asset` is the same cut on the keyframe path, so the comparison is one join click apart if
you are asked for it — and it is a paid render, so only on request.

**Drawn reference pictures have run end to end once, and the three ways they went wrong are the
comments above.** A first draw was 10.3 s at 1024×1024; a redraw of it — "make it much longer and
more battered" — took 19.5 s, kept the subject and applied the change. What is *not* verified:
whether `edit` mode really holds a picture across a bigger change than that, whether one turn of
`pictures.converse` sets `regenerate` correctly more often than not, and whether a picture drawn
from words alone reads as belonging to the same film as one drawn from the cast. That last one is
the live design question — the medium now travels as words (`REF_DRAW_STYLE_SUFFIX`) rather than
as a conditioning image, and it is free to try the other way by handing `pictures.conditioning`
the cast reference again. Expect the cast to come back with it.

**Staging has never reached a live Gemini request.** The server side is exercised end to end
against a real board — entries mint, bind, renumber the uploads below them, reach the clip as
`<Picture N>` and the still as prose, the budget shrinks, and a delete takes the sheet, the
bindings and rewrites every `@stage:` into what the design was for. The fingerprints are verified
byte-identical on a board that binds nothing, which is what keeps every existing reel out of
`stale`. What has NOT happened is one sheet being drawn: nobody has rendered a character sheet,
redrawn one from a note, or compared a scene conditioned on two design sheets against the same
scene on the cast reference alone — which is the entire claim. `SET_DRAW_STYLE_SUFFIX` and the
9:16 set shape are reasoned from `pictures.py`'s prop-sheet failure, not measured. The character
model sheet (`CHAR_SHEET_LAYOUT`, 16:9, four sections in one Gemini shot) is the same kind of
claim: one-shot Gemini may drop a section or smear labels; the review retry is the mitigation;
no quality claim until a live draw. `StagingPanel` and the bind toggles have not been clicked
in a browser either.

**Nothing arbitrates between a bound design and a beat's own picture of the same thing.** Both go
over, described twice. `ref_budget` notices the slot; nothing notices the duplication.

**No storyboard panel has been drawn against a live Gemini.** What is verified is the machinery
around it: the routes answer, the two job kinds run on the worker, `panel_path` renumbers with its
beat, the sheet builds from panels on disk, and both fingerprints are byte-identical before and
after a panel appears — which is what keeps every rendered board out of `stale`. What nobody has
seen is a sketch. So three claims are reasoned rather than measured: that `PANEL_STYLE_SUFFIX`
actually gets a grey pencil panel rather than the paper cutout it negates, that Lite at 1K is
legible enough to judge framing by, and that a panel is a useful read of a shot that will be made of
paper — the point where preview and product diverge most. One of the four is now measured, though:
`panels.write` on a five-beat board came back wide / medium-close-up / extreme close-up /
close-up / extreme wide, so the model does vary shot size across a reel rather than writing five
medium shots in a row.

**`panels._digest` names the bound designs, and that is the one prompt change in the stage
work.** Verified on a real board: `in shot: Vera, the clearing` appears on the beats that bind
something and nothing at all on the beats that do not. Names only — `SYSTEM` bans materials and
colour, and `role` carries both. It changes what a re-run produces and marks nothing stale
because a panel is in no fingerprint *at all*; the same edit in `config.build_prompt` or
`stills.py` would be a fingerprint change. **What is NOT measured is whether the names improve
the panels** — no A/B exists, and the panels above were written on a board whose two designs
were bound to two of five beats.

**`@`-mentions have never been typed in a browser.** The grammar, the two expansions, the
degrade-to-role path and the rewrite-on-delete are exercised against a real board; the menu, the
caret restore, the Escape arbitration and the legend are not. Nor is the thing they exist to
prevent: no model has yet been observed dropping a token, so `config.lost_mentions` has caught
nothing and is unproven in the only case that matters.

**The four stages exist; two of the four pages have never been seen in a browser.** What is
exercised end to end is the shell and the conversation. `make serve` answers every stage URL from
the built bundle (`/`, and all four `/reels/:slug/:stage`), a missing slug still 404s, and one
whole interview ran against a live model: the four section 0 questions came back on the first
turn, `defaults` produced the brief's own `2 × 10s + 4 × 5s` split, the self-check fixed seven
items, the mixed lengths survived the review, and the slug never moved. Every board's `states()`
is unchanged afterwards — the fully-rendered reel still reads `rendered` on all five beats, which
is the fingerprint check.

What has NOT happened: nobody has clicked the Storyboard grid's binding gesture, the contact
sheet, the Assets stage's conditioning strip, or the cast-first two-step. So four things are
reasoned rather than seen:

- that binding one design across seven shots by ticking panel cards is actually better than
  seven trips through `BeatModal` — the arithmetic is shared with `StagingBind` through
  `staging.ts`, but only one of the two has been driven;
- that the "drawn from" strip reads as an explanation rather than as more furniture. It is
  `beat.ts`'s `stillPictures` rendered directly, so it cannot disagree with what Gemini is
  handed — but "cannot be wrong" and "is useful" are different claims;
- that the two-step cast approval is worth the extra click. It is strictly cheaper than the
  batch it replaces (`stills.generate` already renders and reviews that beat alone), and it has
  not been run;
- that `verdict` pips are legible. Every existing board's review turns predate the key, so they
  all read "not reviewed" — which is true of them, and means the pips are only exercised by a
  new stills pass.

**The interview's per-turn latency is not the API's**, same caveat as everywhere else here: the
one measured run took minutes for the writing turn on this machine's network path. Do not tune a
timeout against it.

**The crew has reached a live Gemini once, and it was a 429.** 2026-08-17 `--stage storyboard`
on a copy of `evals/harness/golden-scripted`: all nine specialists hit `gemini-3.7-flash` and
failed with prepay credits depleted. No sheet, panel or roster was written. The skip-on-failure
path then stamped every storyboard phase `done`. That cannot recur: `crew.stage` reopens the
first failed phase, and `make harness` has a stub that asserts it. Claymation still has not
reached a live model. What is exercised around the crew, end to end and for nothing: all skills
load, render and build; the toolbox constructs; `next_stage` agrees with `resolveStage` on the
golden boards; every new route answers (`GET /api/agents`, `GET .../crew`, unknown agent 404,
empty message 422, bad stage 422, bad lens 404, bad medium 422); `import paperreel.api` still
works; **fingerprints stay byte-identical** when envelope / acts / notes are named then cleared;
and a clay board was driven through two full casts against a stub provider — the medium set, the
bible written, a design minted, a beat blocked, the blocking and the clay audio reaching the
video prompt, then three lenses filing `pass` / `fail` / `pass` into the beat's transcript with
a suggested fix.

So what is NOT known is everything about the *conversations and the pictures*. Specifically:

- **Nothing has been rendered in clay.** `CLAYMATION`'s fifteen fields are reasoned from
  `pictures.py`'s measured lessons and from what the two materials physically do, not from a
  render. The claim most likely to be wrong is that `judge` rejects the right things: a reviewer
  told to reject anything that is not "sculpted plasticine with visible thumbprints" may reject
  perfectly good clay for being too smooth.
- **`blocking` has never been in a paid render.** Whether a separate "In frame:" clause improves
  a shot or just gives the model one more thing to draw into the frame is unmeasured, and the
  slot's position (after staging, before scene) is reasoned from `SCENE_PREFIX`'s comment.
- **Whether a cast beats one agent is the whole claim and it is untested on a successful
  conversation.** Three specialists in sequence cost three turns where one agent costs one; the
  argument is that the one agent answers about whichever concern it noticed first, and nobody has
  watched either happen. The one live crew run died on a 429 before any specialist wrote.
- **The cross-check's false-fail rate is unknown.** Three reviewers each told "pass a still that
  is right" will still find something. If they fail everything, the verdicts become noise and the
  bound to report-only is what stops that being expensive.
- **`CREW_STILL_BUDGET` (72) is a guess and has never fired.** Counted in pose-frames, not beats: a reference cut draws up to nine stop-motion poses, so a per-beat cap of 24 ran out on the third scene.
- **The script-writer's system prompt is 35 KB**, nearly all of it the brief, carried through
  every round of history where `develop.turn` pays for it once. No mitigation short of prompt
  caching; `max_rounds: 8` is the lever.
- **The frontmatter reader is not YAML.** A user who writes valid YAML it rejects gets an error
  on a file that looks correct. Errors name the file, line and supported shapes; swapping in
  PyYAML later is one function.

`uv run crew.py --dry-run` prints every prompt of the next stage without sending one, which is
where to look before spending anything. `make harness` is the free regression. The cheapest
first real run is `--stage storyboard` on a board that already has a script, once Gemini credits
are not depleted.
