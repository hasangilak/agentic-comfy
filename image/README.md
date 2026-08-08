# Papercut Studio

A UI for generating paper-cutout stop-motion keyframes with Google's Gemini Nano Banana image
models. Describe a scene and its length, choose how many frames to cut it into (2–9), and the
local render server sends each prompt to Gemini and saves the returned image in the existing
scene store.

## Requirements

- A Google AI API key in the repository `.env` as `X-GOOG-API-KEY`
- Node 20+

```sh
npm install
npm run dev
```

The web app runs on <http://localhost:5173> and the render server on `127.0.0.1:8791`.
Override the server port with `PORT=… npm run dev:server`.

The **Look and quality** section in the UI lets each scene choose the Gemini model and output
size. The default model is `gemini-3-pro-image`. For API-created scenes, or to change the
fallback defaults without using the UI:

```sh
GEMINI_IMAGE_MODEL=gemini-3.1-flash-image
GEMINI_IMAGE_SIZE=2K
```

Supported models are `gemini-3-pro-image`, `gemini-3.1-flash-image`, and
`gemini-3.1-flash-lite-image`. Lite automatically uses 1K output because that is its supported
resolution. The model and image size are read from `.env` or the server environment.

## How a scene works

A scene is a duration plus a frame count. Ten seconds split into five frames gives each frame a
2.0 s hold — the app shows the resulting hold and fps as you drag the sliders.

Every frame carries its own **beat**: a one-line description of what that moment shows. Beats are
seeded from the scene description with concrete staging (subject enters left, peaks at centre,
exits right) because vague hints let the model redraw the same pose. Edit any beat and re-render
just that frame.

The scene's **style suffix** is appended to every beat, and the Gemini renderer adds a stable
paper-cutout direction so the medium stays consistent across a sequence.

## Consistency modes

| Mode | Conditioning | Use it for |
| --- | --- | --- |
| **Chain** | each frame conditions on the previous frame | walk cycles and continuous motion |
| **Anchor** | every frame conditions on the uploaded reference(s) | a fixed character or set across poses |
| **Free** | no image conditioning | fastest, loosest — style suffix alone holds the look |

Chain and Anchor send reference images as inline inputs to Gemini. The API supports multiple
reference images, so the existing four-image cap remains useful for a character sheet, prop, or
palette while keeping requests bounded. Chain passes only the previous frame; Anchor passes every
uploaded reference.

## Timing

Gemini request latency varies by model, image size, and reference count. The header starts with a
broad estimate and switches to the measured average once frames start landing. Renders remain
serialised through one global lock so scene order and cancellation stay predictable.

## Output

Frames are written to `out/scenes/<id>/frame-NN.png` alongside a `scene.json` that survives
restarts. **Play** runs them back at their real hold durations so you can judge the timing before
committing; **Download zip** packages the rendered frames.

## Feeding the reel pipeline

The **9:16 reel** aspect preset renders at 768×1344, the generation grid MiniMax-H3 uses in the
paperreel project one directory up. A still made at another size is cover-cropped on the way into
the video model, so this preset is the one to pick when the frame becomes a shot.

paperreel drives this server directly — start it with `make images` from the parent directory (or
`make dev-server` here) and the reel studio's ✦ generate buttons render through Gemini. It falls
back on its own if this server is not listening.

## Known behaviour

- Chain mode can drift. If a later frame changes the set, re-roll it or switch to Anchor when the
  fixed reference matters more than the flow.
- Re-rendering a middle frame in Chain mode does not update frames after it; re-render the tail if
  the changed frame should influence them.
- Gemini image output includes Google's SynthID watermark metadata.
