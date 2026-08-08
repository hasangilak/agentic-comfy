export type FrameStatus = 'pending' | 'running' | 'done' | 'error' | 'cancelled'

export type SceneStatus = 'idle' | 'running' | 'done' | 'error' | 'cancelled'

/** How each frame stays visually consistent with the rest of the scene. */
export type ConsistencyMode =
  /** Every frame is conditioned on the frame before it. Best motion continuity, slowest. */
  | 'chain'
  /** Every frame is conditioned on one fixed anchor image. Style holds, poses stay independent. */
  | 'anchor'
  /**
   * Conditioned on the references and told to change nothing the beat does not ask for.
   *
   * The same conditioning as `anchor`; the difference is the continuity clause, which every
   * other conditioned mode prepends and this one omits. That clause ends "but move the subject
   * into a clearly different pose and position as described" -- correct when the reference is
   * the previous frame of a moving sequence, and the exact opposite of what is wanted when the
   * reference IS the picture being edited and the instruction is "make the club longer".
   *
   * So: `anchor` re-poses the subject against a fixed look, `edit` holds the picture and
   * changes only what the beat names.
   */
  | 'edit'
  /** Pure text-to-image. Fastest, loosest continuity. */
  | 'none'

/**
 * Every mode this build understands, for `/api/health`.
 *
 * Declared here rather than in the server so the list cannot fall behind the union it is
 * advertising -- the annotation makes adding a mode without listing it a type error.
 */
export const CONSISTENCY_MODES: ConsistencyMode[] = ['chain', 'anchor', 'edit', 'none']

export interface AspectPreset {
  id: string
  label: string
  width: number
  height: number
}

export const ASPECT_PRESETS: AspectPreset[] = [
  { id: '16:9', label: '16:9 landscape', width: 1152, height: 640 },
  { id: '4:3', label: '4:3 landscape', width: 1152, height: 864 },
  { id: '1:1', label: '1:1 square', width: 1024, height: 1024 },
  { id: '9:16', label: '9:16 portrait', width: 640, height: 1152 },
  // The MiniMax-H3 generation grid used by the reel pipeline next door. H3's quality
  // profile is ~1 megapixel with both dimensions a multiple of 32, so a still handed to it
  // at any other size is cover-cropped and rescaled on the way in. Rendering here instead
  // means the frame reaches the video model exactly as it left this machine.
  //
  // It is ~1.8x the pixels of the 9:16 portrait preset above, so expect roughly that much
  // more time per frame. Use the portrait preset to judge staging, this one to hand over.
  { id: '9:16-reel', label: '9:16 reel · H3 grid', width: 768, height: 1344 },
]

export const MIN_FRAMES = 2
export const MAX_FRAMES = 9

/**
 * How many conditioning images one frame may be rendered from.
 *
 * Gemini image models accept multiple inline reference images. Four stays conservative across
 * the supported Nano Banana models and leaves room for the prompt without creating oversized
 * API payloads. Raise it only after comparing consistency and request latency.
 */
export const MAX_REFERENCES = 4

export const GEMINI_IMAGE_MODELS = [
  { id: 'gemini-3-pro-image', label: 'Nano Banana Pro', blurb: 'Highest quality and creative control.' },
  { id: 'gemini-3.1-flash-image', label: 'Nano Banana 2', blurb: 'Best balance of quality, speed, and references.' },
  { id: 'gemini-3.1-flash-lite-image', label: 'Nano Banana 2 Lite', blurb: 'Fastest and cheapest; 1K output only.' },
] as const

export type GeminiImageModel = typeof GEMINI_IMAGE_MODELS[number]['id']
export const GEMINI_IMAGE_SIZES = ['1K', '2K', '4K'] as const
export type GeminiImageSize = typeof GEMINI_IMAGE_SIZES[number]
export const DEFAULT_GEMINI_IMAGE_MODEL: GeminiImageModel = 'gemini-3-pro-image'
export const DEFAULT_GEMINI_IMAGE_SIZE: GeminiImageSize = '2K'

export interface Frame {
  index: number
  /** Seconds into the scene that this frame lands on. */
  timestamp: number
  /** How long this frame is held on screen, in seconds. */
  hold: number
  /** The beat the user wants to see — scene-level style is appended by the server. */
  beat: string
  status: FrameStatus
  seed: number
  /** URL under /files once rendered. */
  url?: string
  error?: string
  /** Wall-clock seconds the render took. */
  elapsed?: number
  /** 0..1 within this frame's own render. */
  progress: number
}

export interface SceneSettings {
  title: string
  description: string
  duration: number
  frameCount: number
  style: string
  negativePrompt: string
  aspectId: string
  steps: number
  seed: number
  /** Give every frame its own seed instead of reusing the scene seed. */
  varySeeds: boolean
  consistency: ConsistencyMode
  /** Gemini image model for this scene; omitted by older/API-created scenes. */
  geminiModel?: GeminiImageModel
  /** Gemini output size for this scene; Lite is forced to 1K by the server. */
  geminiImageSize?: GeminiImageSize
  /** Server-side path of an uploaded reference image, if any. */
  referencePath?: string
  referenceUrl?: string
  /**
   * More conditioning images, when one is not enough: a character sheet next to a prop next
   * to the palette. Used in place of `referencePath` when present, capped at
   * `MAX_REFERENCES`, and every entry must be a path this server can read.
   *
   * Separate from `referencePath` rather than replacing it because the local UI uploads one
   * image and has one slot for it, and because a caller written against the older shape must
   * keep conditioning on the image it sent. Programmatic callers — the reel pipeline next
   * door — send both: this list, and its first entry as `referencePath`.
   */
  referencePaths?: string[]
}

export interface Scene extends SceneSettings {
  id: string
  createdAt: number
  status: SceneStatus
  frames: Frame[]
  width: number
  height: number
}

export interface SceneEvent {
  type: 'scene' | 'log'
  scene?: Scene
  line?: string
}

export const DEFAULT_STYLE =
  'layered construction paper collage, visible cut paper edges, paper fiber texture, ' +
  'soft drop shadows between the layers, handcrafted papercraft diorama, flat lay, soft even studio light'

export const DEFAULT_NEGATIVE = ''

/**
 * Phase description injected into each frame so the scene reads as motion, not stills.
 * The staging is deliberately concrete — vague hints like "mid-action" leave the model
 * free to redraw the same pose, which kills the whole point of a frame sequence.
 */
export function beatHint(index: number, total: number): string {
  if (total <= 1) return 'single hero pose, centered composition'
  const p = index / (total - 1)
  if (p === 0) return 'opening pose: subject at the far left of frame, weight settled, limbs at rest'
  if (p < 0.35)
    return 'subject has moved a step to the right, weight shifting forward, near limb lifted and swinging'
  if (p < 0.65)
    return 'subject at the centre of frame, peak of the movement, limbs at their widest, strongest silhouette'
  if (p < 1)
    return 'subject past centre toward the right, weight landing, follow-through in the trailing limbs'
  return 'final pose: subject at the far right of frame, movement complete, weight settled again'
}

export function defaultBeat(description: string, index: number, total: number): string {
  const subject = description.trim() || 'the scene'
  return `${subject}. ${beatHint(index, total)}`
}

export function frameTimings(duration: number, frameCount: number) {
  const hold = duration / frameCount
  return Array.from({ length: frameCount }, (_, i) => ({
    timestamp: Number((i * hold).toFixed(2)),
    hold: Number(hold.toFixed(2)),
  }))
}
