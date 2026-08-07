import type { Scene, SceneSettings } from '../shared/types.ts'
import { ASPECT_PRESETS } from '../shared/types.ts'

/**
 * Measured on an Apple Silicon Mac with flux2-klein-4b at 8-bit:
 * 1.97 s/step for text-to-image and ~3.2 s/step when a reference image is
 * attached, both at 1024x1024, plus process start and weight load per frame.
 */
const SECONDS_PER_STEP_TXT = 1.97
const SECONDS_PER_STEP_EDIT = 3.2
const FRAME_OVERHEAD = 3
const BASE_PIXELS = 1024 * 1024

export function estimateFrameSeconds(opts: {
  width: number
  height: number
  steps: number
  usesReference: boolean
}) {
  const scale = (opts.width * opts.height) / BASE_PIXELS
  const perStep = opts.usesReference ? SECONDS_PER_STEP_EDIT : SECONDS_PER_STEP_TXT
  return perStep * scale * opts.steps + FRAME_OVERHEAD
}

export function estimateSceneSeconds(settings: SceneSettings) {
  const aspect = ASPECT_PRESETS.find((a) => a.id === settings.aspectId) ?? ASPECT_PRESETS[0]
  const perFrame = estimateFrameSeconds({
    width: aspect.width,
    height: aspect.height,
    steps: settings.steps,
    usesReference: settings.consistency !== 'none',
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
