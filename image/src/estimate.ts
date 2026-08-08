import type { Scene, SceneSettings } from '../shared/types.ts'
import { ASPECT_PRESETS } from '../shared/types.ts'

/** A deliberately broad first estimate for a remote Gemini image request. */
const BASE_RENDER_SECONDS = 25
const REFERENCE_SECONDS = 7
const BASE_PIXELS = 1024 * 1024

export function estimateFrameSeconds(opts: {
  width: number
  height: number
  steps: number
  /** How many conditioning images the frame is rendered from. 0 is text-to-image. */
  references: number
}) {
  const scale = (opts.width * opts.height) / BASE_PIXELS
  return (BASE_RENDER_SECONDS + REFERENCE_SECONDS * opts.references) * scale
}

/** How many pictures a frame of this scene conditions on, as `referenceFor` will resolve it. */
export function referenceCount(settings: SceneSettings): number {
  if (settings.consistency === 'none') return 0
  // Chain conditions on the previous frame alone, whatever was uploaded.
  if (settings.consistency === 'chain') return 1
  const uploaded = settings.referencePaths?.length ?? (settings.referencePath ? 1 : 0)
  // Edit has no fallback: with nothing uploaded it renders from the text, so the floor of 1
  // below -- which is right for anchor, where frame 1 stands in -- would overcharge it by a
  // whole edit pass.
  if (settings.consistency === 'edit') return uploaded
  return Math.max(1, uploaded)
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
