import type { Scene, SceneSettings } from '../shared/types.ts'
import { ASPECT_PRESETS } from '../shared/types.ts'

/**
 * Measured on an Apple Silicon Mac with flux2-klein-4b at 8-bit:
 * 1.97 s/step for text-to-image and ~3.2 s/step when a reference image is
 * attached, both at 1024x1024, plus process start and weight load per frame.
 *
 * The edit cost is per reference, not per render: the same prompt, seed and steps at 768x1344
 * took 18.6 s with one reference and 31.4 s with two, so the second picture cost 12.8 s — about
 * 3.2 s/step again once the pixel scale is divided out. Multiplying is therefore the right
 * shape, and it keeps the same ~15% underread the single-reference model already had.
 */
const SECONDS_PER_STEP_TXT = 1.97
const SECONDS_PER_STEP_EDIT = 3.2
const FRAME_OVERHEAD = 3
const BASE_PIXELS = 1024 * 1024

export function estimateFrameSeconds(opts: {
  width: number
  height: number
  steps: number
  /** How many conditioning images the frame is rendered from. 0 is text-to-image. */
  references: number
}) {
  const scale = (opts.width * opts.height) / BASE_PIXELS
  const perStep = opts.references
    ? SECONDS_PER_STEP_EDIT * opts.references
    : SECONDS_PER_STEP_TXT
  return perStep * scale * opts.steps + FRAME_OVERHEAD
}

/** How many pictures a frame of this scene conditions on, as `referenceFor` will resolve it. */
export function referenceCount(settings: SceneSettings): number {
  if (settings.consistency === 'none') return 0
  // Chain conditions on the previous frame alone, whatever was uploaded.
  if (settings.consistency === 'chain') return 1
  return Math.max(1, settings.referencePaths?.length ?? (settings.referencePath ? 1 : 0))
}

export function estimateSceneSeconds(settings: SceneSettings) {
  const aspect = ASPECT_PRESETS.find((a) => a.id === settings.aspectId) ?? ASPECT_PRESETS[0]
  const perFrame = estimateFrameSeconds({
    width: aspect.width,
    height: aspect.height,
    steps: settings.steps,
    references: referenceCount(settings),
  })
  return perFrame * settings.frameCount
}

/** Once frames have actually rendered, trust the measurement over the model. */
export function observedFrameSeconds(scene: Scene): number | null {
  const times = scene.frames.map((f) => f.elapsed).filter((v): v is number => typeof v === 'number')
  if (!times.length) return null
  return times.reduce((a, b) => a + b, 0) / times.length
}

export function formatDuration(seconds: number) {
  if (seconds < 60) return `${Math.round(seconds)}s`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  return `${minutes}m ${String(rest).padStart(2, '0')}s`
}
