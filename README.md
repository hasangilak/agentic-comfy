# Paper-cutout Reels

Turn a one-line concept into a vertical 1080×1920 Instagram Reel in handcrafted
paper-cutout stop-motion, using MiniMax-H3 on a single GPU on Modal.

Everything except the video is local and unmetered. Scripts come from **qwen3.6 on Ollama**
on this machine — the same model then carries out board edits, writes the caption, and *looks
at* every still with its vision head to check it belongs in the reel. Opening stills come from
**Papercut Studio** in `image/`, `flux2-klein-4b` through mflux, also local. Only rendering
costs money, so the stages are deliberately separable: iterate for free, pay once. A script you
wrote yourself is imported as it stands, with no planning turn at all.

```
concept ──qwen──▶ ┐
                  ├──▶ storyboard.json ──mflux──▶ opening still  (image/, local, free)
  your own script ┘                                  │
                                       qwen looks at it: same cast? ──reject──▶ rewrite, again
                                                    │
                                          compose 9:16 frame (local, free)
                                                    │
                          ONE warm Modal container: N chained beats  ◀── the only cost
                             beat N starts from beat N−1's last frame
                                                    │
                                        stitch ──▶ 1080×1920 H.264/AAC
```

## Setup

```bash
make qwen                                              # once, ~23 GiB into Ollama
make login                                             # once — uvx modal setup
make models                                            # once, ~59 GiB into a Volume
```

The download is separate on purpose: it lands in a persistent Volume, so cold starts
never re-pay for it. **Never needs re-running** — that's why cold start is ~10–30 s
rather than the minutes a HuggingFace pull would take.

### Endpoint authentication

The deployment is **authenticated by default**. The URL serves the full ComfyUI API,
the model weights, and every render in the output Volume, so a public URL is a real
exposure.

Mint a proxy-auth token at <https://modal.com/settings/proxy-auth-tokens> — these are
`wk-`/`ws-` credentials, a *different* type from the `ak-` CLI token in `~/.modal.toml`,
which will earn a 401 — then:

```bash
export MODAL_PROXY_TOKEN_ID=wk-...
export MODAL_PROXY_TOKEN_SECRET=ws-...
```

To poke at ComfyUI's browser UI, deploy a throwaway public instance instead (a browser
cannot attach auth headers to the UI's WebSocket requests), and re-deploy without the
flag afterwards:

```bash
PAPERREEL_PUBLIC=1 uvx modal deploy comfyui_minimax_h3.py
```

## The studio

A node canvas: talk to the local model, get a script and a chain of shots, render when you're
ready.

```bash
make install                                           # npm + the uv environment
make run                                               # :8791 stills, :8787 API, :5173 UI
```

Use the Vite URL — it proxies the API through. `make run` starts all three servers and
takes them down together, so Ctrl-C never leaves one holding a port (`make studio` is an
alias for it). On a session that is only editing a script, `make backend` and `make frontend`
are the same thing without the image server.
`make serve` builds the frontend instead and serves everything from :8787; `make backend`,
`make frontend` and `make images` run one at a time; `make stop` kills every server either
project has running, whichever target started it (an in-flight mflux render survives it —
`make stop-mflux` ends that one, losing the frame).
`make help` lists the rest. Nothing in the Makefile can start a paid render.

### Where stills come from, and how they are checked

**Papercut Studio** (`image/`, mflux `flux2-klein-4b`) is the only generator: ~11 s a still,
~19 s anchored, no quota, and it renders straight onto H3's 768×1344 generation grid so the
frame reaches the video model exactly as it was approved rather than centre-cropped on the way
in. With that server not listening there is nothing to fall back to — a beat's still is an
upload, which is what the **my own** switch below is for. That is also the case on a machine
without Apple Silicon, where mflux cannot run at all.

Papercut is shown the reel's cast reference in **anchor** mode: every still is conditioned on
one image, so a cut changes the setting rather than the characters. A board with no still at
all renders its first beat alone, unconditioned — that image becomes the reference the rest are
anchored to.

Then the model looks at what came back. A style bible is words, and the same paragraph that
produced a round-eared pig in beat 1 produces a sharper-eared one in beat 4 — neither prompt
wrong, and until now nothing ever checked. Each finished still goes to qwen's vision head
alongside the cast reference, judged on the cast, the medium, the palette and the frame, and
explicitly *not* on the setting or the framing, which are supposed to differ. A still that
misses gets its `asset_prompt` rewritten against the specific mismatch and is rendered again:

```
[stills] beat 4: done in 20.3s
[stills] beat 4: the characters (foxes) do not match the recurring character in the style bible
[stills] beat 4: the palette (greens, oranges) violates the strict 6-colour limit
[stills] beat 4: prompt rewritten -> Vertical 9:16 portrait composition of a handcrafted …
[stills] beats [4]: rendering again from the rewritten prompts
```

One retry, not five (`PAPERREEL_STILL_ATTEMPTS`): mflux is free but not instant, and a still
rejected twice is usually telling you the style bible is the problem. The rewritten prompt is
saved to the board, so the node shows what changed and the next render starts from the
corrected wording. `PAPERREEL_STILL_REVIEW=0` turns the whole pass off.

The reference-defining beat is rendered and reviewed entirely on its own, before anything else
starts. Its still *is* the reference the others are matched against, so reviewing it at the end
of a batch would be too late to matter.

By hand, if you prefer:

```bash
cd studio && npm install && npm run build && cd ..
uv run studio.py                                       # http://127.0.0.1:8787
```

### Bring your own script

`+ new` offers two ways in. **write it for me** turns a one-line concept into a shot list,
which is the fastest way to have something on the canvas. **paste a script** adopts one that
already exists — hand-written, or written with an AI somewhere else — verbatim: beat order,
per-beat lengths and which beats are cuts all arrive as written, and no model turn happens.
Talking a model into a script you have already finished is slower and loses detail on the way.

Both paths are written against the *same* brief. `prompts/40s-paper-cutout-script.md` is the
prompt that gets an AI to write a script — and it is also, verbatim, what the local model is
handed by **write it for me**, with only its opening interview replaced by the beat count and
length the studio already asked you for. There is deliberately no second, shorter copy of those
rules inside `planner.py`: a summary that drifts from the document would have the two ways into
a board quietly writing to different specifications.

Then the model marks its own work. The brief ends in a 22-point self-check, so a second pass
hands the whole brief back with the draft and asks it to run that list — which catches the
faults that are invisible on the page and unmissable in the render: a chained beat whose action
does not pick up where the last one ended, a character description that has drifted from the
style bible, a `[Character Description]` placeholder left in an `asset_prompt`, no genuine
close-up anywhere in the film. Every correction is named in the job log. Plan plus review is
about five minutes and costs nothing; `PAPERREEL_PLAN_REVIEW=0` skips it.

The joins are the model's to choose, which they were not before: it decides where the cuts go
and where a take carries on unbroken, inside the rules of section 2. That used to be overwritten
with "beat 1 is a cut, everything after it chains", because a cut cost one image from a
five-per-five-hours quota. Stills are local and free now, so the shape of the film can be
decided by the shape of the story.

Anything with `title` and a `beats` array of `action` lines will import; everything else has a
default.

The import is checked but not fussy. Beats are renumbered from their array order, beat 1 is
forced onto a join that stands on its own — the default cut, unless the script asked for the
exact-keyframe one — lengths snap to 5 s or 10 s, and render settings (steps, seed) stay the
board's rather than the script's. What is *thin* — no style bible, a cut with no
`asset_prompt` — is reported as a note and imported anyway, since all of it is free to fix on
the canvas. An import never overwrites an existing reel; a second copy lands as `-2`.

### Your own stills, and switching generation off

An imported script has already decided its shots, so what is left is one image per cut — and
those often exist already, made somewhere else. The script node's **opening stills** block
fills them in one selection: pick or drop several images and they land on the beats that need
one, in filename order. Name a file `beat3.png` and it goes to that beat exactly, which is also
how you replace a still or put one on a beat that currently continues from the one before.
Uploading costs nothing, and each node's own ⤒ upload still works for fixing one.

The **my own** switch beside it turns image generation off for the reel: every ✦ generate
control disappears and the server refuses an asset job with a 409, so no stale tab or stray
request can overwrite a still you supplied yourself — and neither can the model, which is
refused by the same check from the same place. `I'll supply the stills` on the import panel
starts a reel that way, since otherwise
the first thing an imported script offers is a button that generates the stills it just
described. Both are reversible: switch back to **generated** and the controls return.

The board is a fixed chain — a script node, a row of sequence nodes, a reel node — so
there is no way to wire it wrong. Scenes can be inserted before or after any existing scene
and removed in place; the immediate neighbors reconnect automatically. Manual wiring stays
disabled, so a scene cannot branch, connect twice, point backward, or form a loop. The
**wire between two beats is the frame handoff**:

- **solid green** — this beat continues from the previous clip's last frame. Not a new shot
  at all: the same take carrying on, same set, same camera. Needs no still.
- **dashed amber, labelled `◈`** — a **reference** cut, and the default one: this beat opens
  on its own still, handed to `ref2va` as `<Picture 1>`, with the reel's cast reference
  alongside it as `<Picture 2>` for the whole clip. A clean cut to somewhere else, one local
  render of ~11–19 s. Room for seven more pictures of the cast, the set or a prop, and it can
  instead carry the previous clip in as a reference *video*.
- **solid green, labelled `→ still`** — a **bridge**: it continues from the previous clip
  *and* is given its own still as the frame it has to arrive at. One unbroken take that ends
  on a composition you chose. Needs a still, same as a cut.
- **dashed amber, labelled `✂`** — an **asset** cut: the same cut, except the still goes to
  `fl2va` as an exact keyframe and nothing else goes with it. The opening frame lands pixel
  for pixel; the cast is not re-asserted after it. The join for a beat whose first frame has
  to be exact, and the one every board written before the default moved still uses.

The bridge exists because H3 takes two keyframes, a first and a last, and the wire only ever
used the first. Click a beat's join line to walk the four. Use it when a continuous shot has
to reach a specific state — the lamp lit, the character back on its mark — or when a long run
of continuations has drifted and needs pinning back to a still: the model has to land on that
frame, so the drift is corrected inside the beat instead of accumulating past it. Its
**closing still** slot is the same upload and generate you already have, and its
`asset_prompt` describes the ending rather than the opening. Everything downstream still
follows it: a bridge takes its first frame from the clip before, so re-rendering upstream
invalidates it exactly like a plain continuation.

Each join is told to the model as what it is, which is most of what continuity comes down
to. A cut is instructed to open on its still and hold that framing; a continuation is
instructed that its first frame is a freeze from the middle of a take already in motion, to
be carried on from that exact pose without re-posing, re-centring or re-establishing
anything. Given the cut wording, a continuation reads a mid-stride pose as a starting pose,
settles the puppet back to rest and begins again — the jolt you see at a seam. A bridge gets
the continuation wording plus an instruction that the second still is where this same move
ends and must be reached only on the last frame — without that, the model treats it as another
shot to cut to, arrives early and then sits there.

### The reference join — nine pictures instead of a keyframe, and the default cut

A keyframe is one image. This join is a different checkpoint rather than a different wiring:
`ref2va` takes **up to 9 reference images** (plus 3 videos and 3 audio clips, which this
pipeline does not wire) and **no first or last frame at all**. The prompt tells the model about
them as `<Picture 1>`…`<Picture 9>` in exactly that order, so position is meaning.

**Two of those nine fill themselves,** which is what makes this the default cut rather than an
uploads-only special case. `<Picture 1>` is the beat's own still — generated from its
`asset_prompt` exactly as a keyframe cut's is, and named to the model as the composition this
shot opens on. `<Picture 2>` is the reel's locked cast reference. Consistency is the whole
argument: a keyframe fixes frame zero exactly and says nothing about the nine seconds after it,
where several references keep pulling the puppets back towards their design through every
sampling step. What it gives up is exactness — the opening is close rather than identical — and
render time, since reference tokens ride the whole sampling run where a keyframe is one VAE
encode. `asset` is still there for the beat that needs the frame itself.

Drop more pictures on a scene's reference tray and they upload in order, starting at
`<Picture 3>`; the node shows the numbers the prompt uses. Remove one and the rest renumber,
because the numbers are what the prompt refers to.

Each picture gets its own one-line **prompt** on the node, numbered to match: what the model
should take *from* that picture. It matters more than it sounds. Shown a picture with no
explanation, ref2va assumes the picture is the scene — hand it a still of the cast standing in
the finished set and it will reproduce that *and* act the beat out with a second copy of the
same puppet, which is two moths on one lamp post. Told `<Picture 1> is the same single Moth
that performs the action, not an extra one` and `<Picture 2> is the set only, no puppet`, it
collapses back to one. The notes go into the render prompt, so editing one marks the beat
stale, exactly like editing the action. `reel.py --ref-note` is the same thing from the CLI,
paired to `--ref` by position.

**Carrying the previous clip.** A reference beat has no keyframe slot, so continuity cannot be
a frame handoff — but the node takes reference *video* (3 clips, 2–15 s each), and the tick box
on the node puts the **last 3 seconds of the previous clip** in as `<Video 1>`. The prompt then
swaps "compose the opening frame yourself" for "open on the moment `<Video 1>` ends and carry
its movement onward, same set, same camera, no restart". Only the tail is sent: reference
tokens ride through every sampling step, so a whole 10 s clip would cost ~9× this for motion
that stopped mattering seconds ago. Turning it on makes the scene depend on the one before it
again — re-rendering upstream marks it `follows a change`, and it re-enters the render
cascade. `config.REF_VIDEO_SECONDS` is the tail length; `config.REF_VIDEO_WITH_AUDIO` (off)
also pairs the clip's soundtrack into the matching audio slot.

What it costs is the handoff. A reference beat cannot land on a still, and it cannot take a
frame-exact continuation — its version of one is "here is where the take had got to". Render
time grows with the number of pictures, too, since reference tokens ride through every sampling
step rather than being encoded once like a keyframe, so the render-time predictions in
`config.py` — fitted on the keyframe path — read slightly low for a cut on this join.

The one shape that still cannot be generated into is a beat **carrying** the previous clip: it
already has an opening, and the prompt may only ever give the model one answer to where the shot
begins, so ✦ generate is gone from the node and the server refuses an asset job for it with a
409. Every other reference beat generates its still like any other cut.

Both checkpoints live on the Volume (19.5 GiB each), and ComfyUI loads whichever the queued beat
names, so a reel that mixes cuts with continuations now pays one model swap per shot boundary —
rendering in beat order, which the batch already does for chaining's sake, keeps it to that.
`reel.py --ref cast.png --ref set.png` is the same checkpoint from the CLI, with no still wired:
there, every `--ref` is a design reference and the model composes the opening itself.

The other half is that both kinds hold the same cast. The **cast reference** on the script
node is one image that every generated still is conditioned on, so a cut changes the setting
rather than the characters; the style bible goes into the video prompt as well, so a beat
that drifts mid-clip drifts toward that description rather than toward the model's own idea
of a paper fox. Beat 1's still is the reference by default — pin your own to override it.

A beat's video prompt is **the style bible, then its scene line, then its action**: what the
production looks like, where this shot is, and what moves. The scene line is an input to the
render, not a note to yourself — editing it on the node marks the beat stale, exactly like
editing the action. Without it the model has only one still to infer the setting from, which
is how a background quietly becomes a different place halfway through a clip.

Every beat has its own persistent **upload** and **generate** controls, so all scene assets
can be prepared before any video rendering starts. Dragging an image onto a frame works too,
and the script node fills a whole reel's stills from one multi-file selection. Uploading needs
no image server, which matters on a machine where mflux cannot run — and generation can be
switched off entirely, per reel. Supplying or generating a still makes
that beat a new shot, so its wire switches to a cut — unless it is already a bridge, where the
still is the frame it lands on and the continuation is left alone. Anything far from 9:16 gets
a crop warning before you pay to discover it.

A take you don't like is discardable: **× clip** on the rendered-output header, twice (it
disarms after four seconds). The beat drops back to `ready`, everything chained below it reads
as following a change, and the render button re-prices itself to include them. The file is
moved to the reel's `.discarded/` rather than deleted — renders are the only thing here that
costs money, so a mis-click should not be final. The beat's own still and its reference
pictures are left alone.

A beat is either **5 s or 10 s** — two buttons, no stepper (see the measured numbers for
why). Editing a beat marks it `edited` and everything chained below it `follows a change`,
and the render button re-prices itself. It renders **only what's dirty**, so fixing one beat in
a four-beat reel costs $0.28 rather than $1.13. Clips attach to their nodes as each beat
finishes, so beat 1 is watchable while beat 4 is still sampling.

The model can rewrite, re-time, reorder, add and remove beats, ask the image server for the
stills a board is missing, and write the caption — all free. A turn is a tool loop, so it can
edit a beat, read the board back to see what that did, and then ask for the stills that change
created, all inside one turn, with each step in the job log. It cannot render. Spending money
stays a human action.

The server runs locally because it drives Ollama, the image server and the `modal` CLI, all on
this machine; the browser never talks to Modal, so the proxy tokens stay on yours.

For frontend development, run `npm run dev` in `studio/` alongside `uv run studio.py` and
use the Vite URL — it proxies the API through.

## Make a reel from the CLI

```bash
# 1. plan — free
uv run storyboard.py --concept "a paper pig finds a hidden pond" --beats 4 --seconds 10
uv run storyboard.py --script story.json        # or adopt your own, no planner turn

# 2. opening stills — free; needs `make images` running, and reviews what it renders
uv run storyboard.py --name <slug> --assets

# 3. render — the only paid stage; deploys, renders, stitches, stops
uv run storyboard.py --name <slug> --render
```

Everything lands in `reels/<slug>/`. `storyboard.json` is editable between stages —
rewrite a beat's action or drop in your own `beat1_asset.png` and re-run; completed
work is skipped.

Add `--draft` for a cheap 5 s-per-beat approval pass before committing.

`--assets` on a board that names a source per beat — an imported script, or one built in the
studio — generates exactly the stills it needs, which is every beat but a plain continuation.
`--render` honours those joins too, but it applies one `--seconds` to the whole reel, so a script
mixing 5 s and 10 s beats renders as written only in the studio. The CLI says so when it adopts
one. `--scenes` is the deliberate override: every beat opens on its own still, whatever the board
says — as the default cut, unless the board asked for the exact-keyframe one.

For a single clip, `reel.py` composes a frame from a separate background and character:

```bash
uv run reel.py --preview                       # compose only, no GPU
uv run reel.py --prompt "the pig walks right" --seconds 10
uv run reel.py --ref cast.png --ref set.png    # reference mode, up to 9, no keyframe
```

## Measured numbers

All on RTX PRO 6000, 768×1344, 8 steps, chained beats.

| Beats × length | Container-sec | Cost | $/s of video | Status |
| --- | --- | --- | --- | --- |
| 4 × 5.2 s | 382 | $0.42 | 0.024 | proven |
| 4 × 10.1 s | 1036 | $1.13 | 0.028 | proven |
| 6 × 10.1 s (60 s) | ~1550 | ~$1.74 | 0.028 | extrapolated |
| 4 × 15.1 s | — | — | ~0.035 | failed — 362 frames never completed |

Render time grows **faster than linearly** with frame count (video attention is
quadratic in sequence length): 1.96× the frames cost 2.35× the time. Longer clips buy
fewer seams, not lower cost.

That last row is why **a beat is either 5 s or 10 s and nothing else**. 5 s is the model's
124-frame floor; 10 s snaps to 243 frames, which is exactly the longest render that has ever
completed on this card. The failing length is unreachable by construction rather than by
warning, and there is no third option to reason about.

Other measurements worth keeping:

- **Steps:** 8 vs 20 saves ~41% (115 s vs 196 s at 124 frames). 8 was judged good on
  flat paper art.
- **Model load:** ~22 s, paid **once per container**. Rendering N beats in one warm
  container instead of N separate runs is the single biggest structural saving.
- **B200 is not worth it:** 1.19× faster in steady state but 2.06× the rate — the same
  batch cost $0.80 against $0.42 on the RTX PRO 6000. The quantization is tuned for
  `sm_120` and single-stream diffusion never touches B200's extra bandwidth. Only
  revisit it for clip lengths that need >96 GB.
- **Parallelism does not save money.** Billing is per container-second, so 4 containers
  for T costs the same as 1 for 4T — and *more*, since each container repays the model
  load. Chaining is serial by construction anyway. Parallelism is a latency tool, not a
  cost tool.

### No quota, no key, no per-token charge

Nothing outside the GPU render is metered. The language model is `qwen3.6` under Ollama on
this machine — 36B MoE, 23 GiB of weights, with `vision`, `tools` and `thinking` all reported
by `ollama show`, which is what lets one model write the script, drive the board through tool
calls and look at the stills. The stills come from mflux, also local. No API key exists to
leak and no request leaves the laptop.

This replaced the Antigravity CLI, and one number is why it mattered: `agy`'s image tool
allowed roughly **five generations per five-hour window**, and its agent turns came out of the
same plan quota. That single limit shaped most of the original design — it is why chaining is
the default and why a reel was built to need one image rather than one per beat. Both are still
good filmmaking; neither is forced any more. Chaining is now an editorial choice about seams.

What the absence of a meter bought, concretely, is two passes that were never worth a quota
slot: the script marks its own work against the brief's 22-point self-check, and every still is
looked at next to the cast reference before the board accepts it.

Knobs, all with `PAPERREEL_` prefixes: `QWEN_MODEL`, `OLLAMA_URL`, `QWEN_NUM_CTX` (32768 — the
review pass holds the whole brief plus a draft plus its correction), `PLAN_THINK`,
`PLAN_REVIEW`, `STILL_REVIEW`, `STILL_ATTEMPTS`, `QWEN_TIMEOUT`, `QWEN_KEEP_ALIVE`.

Reasoning is off everywhere except the planning pair, and that is measured rather than assumed:
an unambiguous board edit took **0.9 s** with thinking off and **14 s** with it on, for the same
single tool call. Writing a script is the one place the wall clock is worth it.

## Layout

```
paperreel/config.py     geometry, rates, prompt scaffold, measured constants
paperreel/media.py      chroma-key cutout, compositing, stitching  (local, free)
paperreel/comfy.py      ComfyUI client + the 15-node H3 graph
paperreel/qwen.py       Ollama: structured output, tool calls, vision
paperreel/papercut.py   stills from image/, over HTTP on this machine
paperreel/stills.py     rendering stills, then looking at them
paperreel/planner.py    the authoring brief -> a script, then its own self-check
paperreel/script.py     adopting a script written outside the studio
paperreel/pipeline.py   app lifecycle + chained batch rendering
paperreel/board.py      the board document; state derived from disk
paperreel/agent.py      the tool loop -> board operations
paperreel/jobs.py       one worker, one container, one event stream
paperreel/render.py     rendering with per-beat telemetry
paperreel/api.py        HTTP + SSE for the studio
comfyui_minimax_h3.py   the Modal GPU app
studio.py               the studio server
studio/                 React + TypeScript + React Flow canvas
prompts/                the authoring prompt for writing a script elsewhere
image/                  Papercut Studio: the local mflux stills renderer (own README)
Makefile                install, run, and the one-time Modal steps
storyboard.py           CLI: full reel
reel.py                 CLI: single clip
minimax_h3.py           alternative: full-precision BF16 on 4×H200 via SGLang
```

`reels/<slug>/storyboard.json` is the only database, and beat state is *derived* from what
is on disk rather than stored. Hand-edit the JSON, drop in your own PNG, or run
`storyboard.py`, and the canvas reflects it — the CLI and the studio cannot drift apart.

`minimax_h3.py` is an unrelated, lossless path — faster per clip but roughly 8× the
hourly rate. Kept for reference; the pipeline above does not use it.

## Known gaps

- **The CLI can still ask for 15 s beats**, which have failed once on this card. The studio
  cannot — `config.BEAT_LENGTHS` caps it at 243 frames — but `storyboard.py --seconds 15`
  bypasses that and only logs a warning.
- **Cross-scene consistency is improved but not measured.** Independent scenes no longer
  rely on the `style_bible` text alone — every still is generated conditioned on the cast
  reference image, the bible is in the video prompt too, and the model now looks at each
  finished still and rejects one whose cast has drifted. That last check is verified to fire
  correctly on an obvious mismatch and to pass a good still, but its rate of false accepts on
  *subtle* drift over a long reel of hard cuts has not been quantified.
- **The default cut moved to `ref2va` and nothing has been measured on it.** The argument is
  sound — a keyframe fixes frame zero and says nothing about the nine seconds after it, where
  the cast reference riding through every sampling step keeps asserting the design — but no
  A/B render exists. Three specific unknowns: how much *less* exact the opening frame is when
  the still is conditioning rather than a keyframe latent; how much slower a two-picture cut
  is, since `SECONDS_PER_FRAME` is fitted on the keyframe path and will read low; and whether
  the checkpoint swap at every shot boundary costs enough container time to matter. `asset` is
  the same cut on the old path, so an A/B is one join click and two renders.
- **The review passes are not deterministic.** The script self-check and the still review are
  the same model reading its own output, so two runs of the same board can disagree about
  whether something needs fixing. The still review is pinned to temperature 0.1 to keep that
  narrow, and both are capped — one still retry, one script pass — so a swing costs wall clock
  and never loops. Turn either off with `PAPERREEL_STILL_REVIEW=0` / `PAPERREEL_PLAN_REVIEW=0`.
- **Planning is slow.** Draft plus self-check is about five minutes on a 36B local model,
  against a few seconds for a hosted one. It costs nothing and it is a job the UI shows
  progress for, but it is not interactive.
- **Container may be over-provisioned** at 8 cores / 64 GiB — that's 23% of the bill
  and was never measured against actual usage.
- **The studio's per-step progress is unverified against a live render.** ComfyUI's `/ws`
  through Modal's auth proxy has not been exercised yet; if it fails, the phase strip and
  per-beat timing still work from `/history` polling, only the `step 5/8` detail is lost.
- **Cost readouts are estimates**, derived from wall clock × $0.001089/s rather than
  Modal's billing API, and they exclude the scale-down tail. Expect them to read slightly low.

## License

Review the MiniMax-H3 model card license before commercial use. Serving it on Modal
doesn't grant usage rights.
