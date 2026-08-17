# Paper-cutout Reels

Turn a one-line concept into a vertical 1080×1920 Instagram Reel in handcrafted
paper-cutout stop-motion, using MiniMax-H3 on a single GPU on Modal.

Scripts, board edits, captions and still *review* go through **Gemini**. Opening stills come
from **Papercut Studio** (`image/`), on the same `X-GOOG-API-KEY`. Video is the only stage that
spends GPU money. A script you wrote yourself imports as it stands.

```
concept ─Gemini─▶ ┐
                  ├──▶ storyboard.json ──Gemini──▶ opening still  (image/, API)
  your own script ┘                                  │
                                     Gemini looks at it: same cast? ──reject──▶ rewrite, again
                                                    │
                                          compose 9:16 frame (local, free)
                                                    │
                          ONE warm Modal container: N chained beats  ◀── the only cost
                             beat N starts from beat N−1's last frame
                                                    │
                                        stitch ──▶ 1080×1920 H.264/AAC
```

Why the joins, fingerprints, crew, and costs are what they are:
[ARCHITECTURE.md](ARCHITECTURE.md).

## Setup

```bash
# X-GOOG-API-KEY=... in .env                          # the script, the stills, one key
make login                                             # once — uvx modal setup
make models                                            # once, ~177 GiB into a Volume
```

The download lands in a persistent Volume, so cold starts never re-pay for it. Never needs
re-running.

The GPU endpoint is **authenticated by default**. Mint a proxy-auth token at
<https://modal.com/settings/proxy-auth-tokens> — `wk-`/`ws-`, a *different* type from the
`ak-` CLI token in `~/.modal.toml`, which will earn a 401 — then:

```bash
export MODAL_PROXY_TOKEN_ID=wk-...
export MODAL_PROXY_TOKEN_SECRET=ws-...
```

A public ComfyUI UI needs a throwaway public deploy (browsers cannot attach auth headers to
the UI WebSocket). Re-deploy without the flag afterwards:

```bash
PAPERREEL_PUBLIC=1 uvx modal deploy comfyui_minimax_h3.py
```

## Run the studio

```bash
make install                                           # npm + the uv environment
make run                                               # :8791 stills, :8787 API, :5173 UI
```

Use the Vite URL — it proxies `/api` and `/media`. `make run` starts all three servers and
takes them down together (`make studio` is an alias). `make serve` builds the frontend and
serves everything from :8787. `make help` lists the rest.

**Nothing in the Makefile can start a paid render.** Rendering is a studio button or an
explicit CLI flag.

The four stages — script, storyboard, assets, studio — are pages, not gates. Only studio
spends GPU money. The price is on the button.

## Make a reel from the CLI

```bash
# 1. plan — free
uv run storyboard.py --concept "a paper pig finds a hidden pond" --beats 4 --seconds 10
uv run storyboard.py --script story.json        # or adopt your own, no planner turn

# 2. storyboard — free; a rough sketch per shot, plus a contact sheet
uv run storyboard.py --name <slug> --panels

# 3. opening stills — free; needs `make images` running, and reviews what it renders
uv run storyboard.py --name <slug> --assets

# 4. render — the only paid stage; deploys, renders, stitches, stops
uv run storyboard.py --name <slug> --render
```

Everything lands in `reels/<slug>/`. `storyboard.json` is editable between stages.
`--draft` is a cheap 5 s-per-beat approval pass. `--panels` is deliberately not part of
`--all`: nothing it makes is rendered from.

For a single clip:

```bash
uv run reel.py --preview                       # compose only, no GPU
uv run reel.py --prompt "the pig walks right" --seconds 10
uv run reel.py --ref cast.png --ref set.png    # reference mode, up to 9, no keyframe
```

Crew, without spending GPU:

```bash
make harness                              # golden boards; calls no model
uv run crew.py --name <slug> --where
uv run crew.py --name <slug> --dry-run    # next phase's prompts, unsent
```

## License

Review the MiniMax-H3 model card license before commercial use. Serving it on Modal
doesn't grant usage rights.
