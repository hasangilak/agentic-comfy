# Papercut Studio

A local UI for generating paper-cutout stop-motion keyframes. You describe a scene and its
length, choose how many frames to cut it into (2–9), and it renders each frame with
`flux2-klein-4b` through [mflux](https://github.com/filipstrand/mflux) on the local GPU.

Nothing leaves the machine.

## Requirements

- Apple Silicon Mac with mflux installed and `flux2-klein-4b` weights cached
- Node 20+

```sh
npm install
npm run dev
```

The web app runs on <http://localhost:5173> and the render server on `127.0.0.1:8791`.
Override the server port with `PORT=… npm run dev:server`.

## How a scene works

A scene is a duration plus a frame count. Ten seconds split into five frames gives each
frame a 2.0 s hold — the app shows the resulting hold and fps as you drag the sliders.

Every frame carries its own **beat**: a one-line description of what that moment shows.
Beats are seeded from the scene description with concrete staging (subject enters left,
peaks at centre, exits right) because vague hints like "mid-action" let the model redraw
the same pose. Edit any beat and re-render just that frame.

The scene's **style suffix** is appended to every beat, so the papercraft look stays
identical across the sequence without retyping it.

## Consistency modes

| Mode | Conditioning | Use it for |
| --- | --- | --- |
| **Chain** | each frame conditions on the previous frame | walk cycles and continuous motion — best flow, renders strictly in order |
| **Anchor** | every frame conditions on one uploaded reference | a fixed character or set across independent poses |
| **Free** | no image conditioning | fastest, loosest — style suffix alone holds the look |

Chain and Anchor use `mflux-generate-flux2-edit`, which costs roughly 3.2 s/step against
1.97 s/step for plain text-to-image, so they render slower.

Upload a reference image to lock a character's design. In Chain mode the reference seeds
frame 1 and each frame takes over from there; in Anchor mode every frame sees it.

## Timing

Measured on this machine at 1152×640, 4 steps:

- text-to-image frame — about 7 s
- chained edit frame — about 13 s

So a 5-frame scene in Chain mode lands around a minute. The header shows a live estimate
that switches to your machine's real measured average once frames start landing.

Renders are serialised through one global lock — mflux holds roughly 18 GB of weights and
two concurrent renders would thrash.

## Output

Frames are written to `out/scenes/<id>/frame-NN.png` alongside a `scene.json` that survives
restarts. **Play** runs them back at their real hold durations so you can judge the timing
before committing; **Download zip** packages the rendered frames.

## Known behaviour

- Chain mode drifts. Around frame 3+ the model may add or drop background elements. Re-roll
  that frame, or switch to Anchor if the drift matters more than the flow.
- Re-rendering a middle frame in Chain mode does not update the frames after it — they were
  conditioned on the old version. Re-render the tail too.
- Hands are `flux2-klein-4b`'s consistent weak point. Keep them out of frame or expect fixes.
