# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

One user: the author, working alone on their own paper-cutout stop-motion films. They describe a shot, and want to see whether the sequence reads as motion before spending real time on it. There is no second audience — no team, no clients, no handoff. The interface never has to explain itself to a stranger, but it does have to stay legible to someone returning to a half-finished scene days later.

## Product Purpose

Papercut Studio turns a written scene description into a sequence of rendered keyframes that can be played back at their real hold durations. The user sets a duration and a frame count; the app derives each frame's timestamp and hold, seeds a per-frame beat with concrete staging, appends a shared style suffix, and renders each frame through Gemini.

Success is judging a shot's timing and staging from the rendered playback — deciding "that works" or "that doesn't" — without leaving the machine and without hand-writing a prompt per frame.

## Positioning

The papercut look is the product, not a preset. This is a tool for making layered construction-paper cutout animation specifically, and every product decision may assume that subject matter. The style field exists to tune the papercraft world, not to leave it for another aesthetic.

The mechanism a neighboring tool could not truthfully copy: a scene is a *duration split into frames*, so every frame arrives with a derived timestamp, hold, and staged beat already attached, and the sequence plays back at real timing. Frame-to-frame consistency is an explicit scene-level choice (chain / anchor / free), not a hope.

## Operating Context

- The UI and render server run locally, while prompts and reference images are sent to Google's Gemini API for image generation.
- Rendering is slow and visible, and latency varies by Gemini model, output size, and reference count. Waiting is a normal part of the usage scene, not an edge case.
- Renders are serialized through one global lock so scene ordering and cancellation remain predictable.
- Work is iterative and partial: re-roll a single frame, edit one beat, re-render the tail. Scenes are returned to across sessions.
- Output lives on disk at `out/scenes/<id>/frame-NN.png` with a `scene.json` beside it, so scenes survive restarts.

## Capabilities and Constraints

Confirmed capabilities:

- Scene = title, description, duration, frame count (2–9), style suffix, negative prompt, aspect preset, steps, seed, and vary-seeds toggle.
- Per-frame editable beat, seeded from the description with concrete staging (enters left, peaks at centre, exits right) rather than vague hints.
- Three consistency modes: **chain** (each frame conditions on the previous), **anchor** (every frame conditions on one uploaded reference), **free** (pure text-to-image).
- Reference image upload to lock a character or set.
- Live per-frame progress and a running time estimate that switches from a static prediction to the machine's own measured average once frames land.
- Playback at real hold durations; zip download of rendered frames.
- Aspect presets: 16:9 (1152×640), 4:3 (1152×864), 1:1 (1024×1024), 9:16 (640×1152).

Technical constraints (current implementation facts, not user-declared promises — a future decision could change these):

- Backend is Gemini Nano Banana, defaulting to `gemini-3-pro-image`; chain and anchor send inline reference images.
- One render at a time, globally serialized.
- The filesystem is the store — no database.

Known model behavior the product must stay honest about:

- Chain mode drifts around frame 3+; the model may add or drop background elements.
- Re-rendering a middle frame in chain mode leaves later frames conditioned on the old version.
- Gemini-generated images carry Google's SynthID watermark metadata.

## Brand Commitments

Name: **Papercut Studio**. No logo, no established voice beyond the README's plain, measured, non-promotional tone — it states timings and failure modes without selling.

## Evidence on Hand

- Real rendered output in `out/` (`klein4b_*.png`, `qwen_*.png`, `hercules_paper.png`) and completed scenes under `out/scenes/`, with metadata JSON alongside several stills.
- Real measured timings on this machine, recorded in the README.

No testimonials, users, benchmarks against other tools, pricing, or licensing exist. Future work must not fabricate them.

## Product Principles

1. **The papercut world is the subject.** Assume cutout paper, layered construction paper, visible cut edges. Do not generalize the product into a style-agnostic image tool.
2. **Timing is the unit of judgment.** Duration, hold, and fps are the numbers the user decides on. Anything that obscures the timeline works against the product.
3. **The local UI stays in control.** Generation is explicit, and the app keeps scene files locally while clearly routing image prompts and references through Gemini.
4. **Waiting is designed, not hidden.** Renders take tens of seconds and serialize. Progress, queue position, and honest estimates are core surface, not decoration.
5. **Partial work is the normal state.** Re-rolling one frame, editing one beat, returning to a half-rendered scene — these are the main path, not recovery cases.

## Accessibility & Inclusion

No product-specific requirement established beyond ordinary standards.
