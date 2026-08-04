# Paper-cutout Reels

Turn a one-line concept into a vertical 1080×1920 Instagram Reel in handcrafted
paper-cutout stop-motion, using MiniMax-H3 on a single GPU on Modal.

Scripts and still assets come from the Antigravity CLI (`agy`), which bills against
your Google plan quota rather than a metered API. Only rendering costs money, so the
stages are deliberately separable: iterate for free, pay once.

```
concept ──agy──▶ storyboard.json ──agy──▶ opening asset
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
uvx modal setup                                        # once
uvx modal run comfyui_minimax_h3.py::download_models   # once, ~40 GiB into a Volume
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

## Make a reel

```bash
# 1. plan — free
uv run storyboard.py --concept "a paper pig finds a hidden pond" --beats 4 --seconds 10

# 2. opening asset — free, but image quota is the scarce resource
uv run storyboard.py --name <slug> --assets

# 3. render — the only paid stage; deploys, renders, stitches, stops
uv run storyboard.py --name <slug> --render
```

Everything lands in `reels/<slug>/`. `storyboard.json` is editable between stages —
rewrite a beat's action or drop in your own `beat1_asset.png` and re-run; completed
work is skipped.

Add `--draft` for a cheap 5 s-per-beat approval pass before committing.

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
| 6 × 10.1 s (60 s) | ~1550 | ~$1.70 | 0.028 | extrapolated |
| 4 × 15.1 s | — | — | ~0.035 | **fails — 362 frames never completed** |

Render time grows **faster than linearly** with frame count (video attention is
quadratic in sequence length): 1.96× the frames cost 2.35× the time. Longer clips buy
fewer seams, not lower cost.

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
paperreel/pipeline.py   app lifecycle + chained batch rendering
comfyui_minimax_h3.py   the Modal GPU app
storyboard.py           CLI: full reel
reel.py                 CLI: single clip
minimax_h3.py           alternative: full-precision BF16 on 4×H200 via SGLang
```

`minimax_h3.py` is an unrelated, lossless path — faster per clip but roughly 8× the
hourly rate. Kept for reference; the pipeline above does not use it.

## Known gaps

- **15 s beats (362 frames) are unproven** and have failed once on this card. `config.PROVEN_MAX_FRAMES`
  is 243; exceeding it logs a warning.
- **Character consistency across independent scenes is untested.** `--scenes` needs one
  asset per beat and relies on a shared `style_bible` to keep the character stable;
  only chained mode has been validated.
- **No job API yet.** Rendering blocks for minutes, so a UI needs submit/poll rather
  than request/response.
- **Container may be over-provisioned** at 8 cores / 64 GiB — that's 23% of the bill
  and was never measured against actual usage.

## License

Review the MiniMax-H3 model card license before commercial use. Serving it on Modal
doesn't grant usage rights.
