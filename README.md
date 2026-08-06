# Paper-cutout Reels

Turn a one-line concept into a vertical 1080×1920 Instagram Reel in handcrafted
paper-cutout stop-motion, using MiniMax-H3 on a single GPU on Modal.

Scripts and still assets come from the Antigravity CLI (`agy`), which bills against
your Google plan quota rather than a metered API. Only rendering costs money, so the
stages are deliberately separable: iterate for free, pay once. A script you wrote
yourself is imported as it stands, with no planning turn at all.

```
concept ──agy──▶ ┐
                 ├──▶ storyboard.json ──agy──▶ opening asset
 your own script ┘                                  │
                                          compose 9:16 frame (local, free)
                                                    │
                          ONE warm Modal container: N chained beats  ◀── the only cost
                             beat N starts from beat N−1's last frame
                                                    │
                                        stitch ──▶ 1080×1920 H.264/AAC
```

## Setup

```bash
make login                                             # once — uvx modal setup
make models                                            # once, ~40 GiB into a Volume
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

A node canvas: talk to agy, get a script and a chain of shots, render when you're ready.

```bash
make install                                           # npm + the uv environment
make run                                               # :8787 API, :5173 UI — use the Vite URL
```

`make run` starts both servers and takes them down together, so Ctrl-C never leaves one
holding a port. `make serve` builds the frontend instead and serves everything from
:8787; `make backend` and `make frontend` run one at a time; `make stop` kills either.
`make help` lists the rest. Nothing in the Makefile can start a paid render.

By hand, if you prefer:

```bash
cd studio && npm install && npm run build && cd ..
uv run studio.py                                       # http://127.0.0.1:8787
```

### Bring your own script

`+ new` offers two ways in. **agy writes it** turns a one-line concept into a shot list, which
is the fastest way to have something on the canvas. **paste a script** adopts one that already
exists — hand-written, or written with an AI somewhere else — verbatim: beat order, per-beat
lengths and which beats are cuts all arrive as written, and no agy turn happens. Talking agy
into a script you have already finished is slower and loses detail on the way.

`prompts/40s-paper-cutout-script.md` is the prompt that gets an AI to write one. It interviews
you about beat structure and cast first, then returns the JSON the studio takes. Anything with
`title` and a `beats` array of `action` lines will import; everything else has a default.

The import is checked but not fussy. Beats are renumbered from their array order, beat 1 is
forced to open on its own still, lengths snap to 5 s or 10 s, and render settings (steps, seed)
stay the board's rather than the script's. What is *thin* — no style bible, a cut with no
`asset_prompt` — is reported as a note and imported anyway, since all of it is free to fix on
the canvas. An import never overwrites an existing reel; a second copy lands as `-2`.

### Your own stills, and switching generation off

An imported script has already decided its shots, so what is left is one image per cut — and
those often exist already, made somewhere else. The script node's **opening stills** block
fills them in one selection: pick or drop several images and they land on the beats that need
one, in filename order. Name a file `beat3.png` and it goes to that beat exactly, which is also
how you replace a still or put one on a beat that currently continues from the one before.
Uploading spends no quota, and each node's own ⤒ upload still works for fixing one.

The **my own** switch beside it turns image generation off for the reel: every ✦ generate
control disappears and the server refuses an asset job with a 409, so no stale tab or stray
request can spend the ~5-images-per-5-hours quota on a board whose stills you are supplying
yourself. `I'll supply the stills` on the import panel starts a reel that way, since otherwise
the first thing an imported script offers is a button that generates the stills it just
described. Both are reversible: switch back to **generated** and the controls return.

The board is a fixed chain — a script node, a row of sequence nodes, a reel node — so
there is no way to wire it wrong. Scenes can be inserted before or after any existing scene
and removed in place; the immediate neighbors reconnect automatically. Manual wiring stays
disabled, so a scene cannot branch, connect twice, point backward, or form a loop. The
**wire between two beats is the frame handoff**:

- **solid green** — this beat continues from the previous clip's last frame. Not a new shot
  at all: the same take carrying on, same set, same camera. Costs no image quota.
- **dashed amber** — this beat opens on its own still. A clean cut to somewhere else, one
  image from the quota.

Each join is told to the model as what it is, which is most of what continuity comes down
to. A cut is instructed to open on its still and hold that framing; a continuation is
instructed that its first frame is a freeze from the middle of a take already in motion, to
be carried on from that exact pose without re-posing, re-centring or re-establishing
anything. Given the cut wording, a continuation reads a mid-stride pose as a starting pose,
settles the puppet back to rest and begins again — the jolt you see at a seam.

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
and the script node fills a whole reel's stills from one multi-file selection. Uploading costs
no quota, which matters because image generation is capped at roughly five per five hours —
and generation can be switched off entirely, per reel. Supplying or generating a still makes
that beat a new shot, so its wire switches to a cut; anything far from 9:16 gets a crop
warning before you pay to discover it.

A beat is either **5 s or 10 s** — two buttons, no stepper (see the measured numbers for
why). Editing a beat marks it `edited` and everything chained below it `follows a change`,
and the render button re-prices itself. It renders **only what's dirty**, so fixing one beat in
a four-beat reel costs $0.28 rather than $1.13. Clips attach to their nodes as each beat
finishes, so beat 1 is watchable while beat 4 is still sampling.

Agy can rewrite, re-time, reorder, add and remove beats, and write the caption — all free.
It cannot render. Spending money stays a human action.

The server runs locally because it shells out to `agy` and `modal`; the browser never talks
to Modal, so the proxy tokens stay on your machine.

For frontend development, run `npm run dev` in `studio/` alongside `uv run studio.py` and
use the Vite URL — it proxies the API through.

## Make a reel from the CLI

```bash
# 1. plan — free
uv run storyboard.py --concept "a paper pig finds a hidden pond" --beats 4 --seconds 10
uv run storyboard.py --script story.json        # or adopt your own, no planner turn

# 2. opening asset — free, but image quota is the scarce resource
uv run storyboard.py --name <slug> --assets

# 3. render — the only paid stage; deploys, renders, stitches, stops
uv run storyboard.py --name <slug> --render
```

Everything lands in `reels/<slug>/`. `storyboard.json` is editable between stages —
rewrite a beat's action or drop in your own `beat1_asset.png` and re-run; completed
work is skipped.

Add `--draft` for a cheap 5 s-per-beat approval pass before committing.

`--assets` on a board that names a source per beat — an imported script, or one built in the
studio — generates exactly the stills its cuts need. `--render` here does not: it applies one
`--seconds` and one `--chain`/`--scenes` to the whole reel, so a script mixing 5 s and 10 s
beats, or cuts with continuations, renders as written only in the studio. The CLI says so when
it adopts one.

For a single clip, `reel.py` composes a frame from a separate background and character:

```bash
uv run reel.py --preview                       # compose only, no GPU
uv run reel.py --prompt "the pig walks right" --seconds 10
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

### Quota, not credits

`agy` authenticates as your Google account (`oauth-personal`); there's no API key and no
GCP billing project, so no per-token charge can land on a bill. Limits are enforced as
**quota that refreshes on a ~5 hour window**, with purchasable AI credits as an *opt-in*
overage (governed by an "AI Credit Overages" setting; leave it on "Never").

**Image generation has its own, much tighter pool — roughly five images per window.**
That is the real constraint on iteration speed, and the reason chaining matters: a
chained reel needs exactly **one** image regardless of beat count.

## Layout

```
paperreel/config.py     geometry, rates, prompt scaffold, measured constants
paperreel/media.py      chroma-key cutout, compositing, stitching  (local, free)
paperreel/comfy.py      ComfyUI client + the 15-node H3 graph
paperreel/planner.py    agy: storyboard and asset generation
paperreel/script.py     adopting a script written outside the studio
paperreel/pipeline.py   app lifecycle + chained batch rendering
paperreel/board.py      the board document; state derived from disk
paperreel/agent.py      agy conversation -> board operations
paperreel/jobs.py       one worker, one container, one event stream
paperreel/render.py     rendering with per-beat telemetry
paperreel/api.py        HTTP + SSE for the studio
comfyui_minimax_h3.py   the Modal GPU app
studio.py               the studio server
studio/                 React + TypeScript + React Flow canvas
prompts/                the authoring prompt for writing a script elsewhere
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
  reference image, and the bible is now in the video prompt too — but how far that holds
  over a long reel of hard cuts has not been quantified on real renders.
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
