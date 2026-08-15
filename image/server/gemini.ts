import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

export interface RenderRequest {
  prompt: string
  negativePrompt?: string
  outputPath: string
  width: number
  height: number
  /** Kept in the scene contract for compatibility; Gemini controls its own inference steps. */
  steps: number
  /** Kept in the scene contract for compatibility; Gemini image models do not expose a seed. */
  seed: number
  /** Images are sent as inline base64 inputs to Gemini's image editing endpoint. */
  referencePaths?: string[]
  model?: string
  imageSize?: string
}

export interface RenderHandle {
  promise: Promise<void>
  cancel(): void
}

const INTERACTIONS_URL = 'https://generativelanguage.googleapis.com/v1beta/interactions'
const DEFAULT_MODEL = 'gemini-3-pro-image'
const DEFAULT_IMAGE_SIZE = '2K'
const DEFAULT_TIMEOUT_MS = 5 * 60 * 1000
const IMAGE_MODELS = new Set([
  'gemini-3-pro-image',
  'gemini-3.1-flash-lite-image',
  'gemini-3.1-flash-image',
])

type ImageSize = '1K' | '2K' | '4K'

interface GeminiImageOutput {
  data?: string
}

/**
 * An interaction answers in `steps`: one `thought` step, then a `model_output` step whose
 * `content` carries the image. `output_image` is read as well because an earlier shape of this
 * endpoint returned one — a build that still gets it should keep working rather than fail on a
 * response that plainly contains a picture.
 */
interface GeminiContent {
  type?: string
  mime_type?: string
  data?: string
  text?: string
}

interface GeminiStep {
  type?: string
  content?: GeminiContent[]
}

interface GeminiResponse {
  output_image?: GeminiImageOutput
  steps?: GeminiStep[]
  status?: string
  error?: { message?: string }
}

/** Every content part of every step, in order — the image may not be in the last one. */
function contents(body: GeminiResponse): GeminiContent[] {
  return (body.steps || []).flatMap((step) => step.content || [])
}

function imageData(body: GeminiResponse): string | undefined {
  const inline = contents(body).find((part) => part.type === 'image' && part.data)
  return inline?.data || body.output_image?.data
}

/**
 * What to say when a completed interaction carried no image.
 *
 * Almost always a refusal or a safety stop, and the model says why in a text part — so the text
 * is the error. "returned no output_image" named a field instead of the reason, which sent the
 * reader to the network tab for something the response had already spelled out.
 */
function noImageError(body: GeminiResponse, raw: string): Error {
  const said = contents(body)
    .map((part) => part.text?.trim())
    .filter((text): text is string => !!text)
    .join(' ')
  const detail = said || body.error?.message || raw.slice(0, 300) || 'no reason given'
  return new Error(
    `Gemini returned no image (status ${body.status || 'unknown'}): ${detail}`,
  )
}

function envFileValue(name: string): string | undefined {
  const here = path.dirname(fileURLToPath(import.meta.url))
  const candidates = [
    path.resolve(process.cwd(), '.env'),
    path.resolve(process.cwd(), '..', '.env'),
    path.resolve(here, '../../.env'),
  ]
  const key = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const linePattern = new RegExp(`^\\s*${key}\\s*=\\s*(.*?)\\s*$`)

  for (const file of [...new Set(candidates)]) {
    if (!fs.existsSync(file)) continue
    for (const line of fs.readFileSync(file, 'utf8').split(/\r?\n/)) {
      const match = line.match(linePattern)
      if (!match) continue
      const value = match[1]
      if ((value.startsWith('"') && value.endsWith('"')) ||
          (value.startsWith("'") && value.endsWith("'"))) {
        return value.slice(1, -1)
      }
      return value
    }
  }
  return undefined
}

function envValue(name: string): string | undefined {
  const value = process.env[name] ?? envFileValue(name)
  return value?.trim() || undefined
}

export function configured(): boolean {
  return !!envValue('X-GOOG-API-KEY') || !!envValue('GEMINI_API_KEY')
}

export function model(requested?: string): string {
  const selected = requested || envValue('GEMINI_IMAGE_MODEL') || DEFAULT_MODEL
  if (!IMAGE_MODELS.has(selected)) {
    throw new Error(
      `unsupported GEMINI_IMAGE_MODEL "${selected}"; use ${[...IMAGE_MODELS].join(', ')}`,
    )
  }
  return selected
}

function imageSize(selectedModel: string, requested?: string): ImageSize {
  // Lite currently supports 1K only. The other two models support the larger sizes.
  if (selectedModel === 'gemini-3.1-flash-lite-image') return '1K'
  const selected = requested || envValue('GEMINI_IMAGE_SIZE')
  return selected === '1K' || selected === '2K' || selected === '4K'
    ? selected
    : DEFAULT_IMAGE_SIZE
}

function aspectRatio(width: number, height: number): string {
  const ratio = width / height
  const supported = [
    ['1:1', 1],
    ['4:3', 4 / 3],
    ['3:4', 3 / 4],
    ['16:9', 16 / 9],
    ['9:16', 9 / 16],
    ['21:9', 21 / 9],
  ] as const
  return supported.reduce((best, candidate) =>
    Math.abs(candidate[1] - ratio) < Math.abs(best[1] - ratio) ? candidate : best,
  )[0]
}

function mimeType(file: string): string {
  switch (path.extname(file).toLowerCase()) {
    case '.jpg':
    case '.jpeg':
      return 'image/jpeg'
    case '.webp':
      return 'image/webp'
    case '.gif':
      return 'image/gif'
    default:
      return 'image/png'
  }
}

function imageInput(file: string) {
  return {
    type: 'image' as const,
    mime_type: mimeType(file),
    data: fs.readFileSync(file).toString('base64'),
  }
}

function promptWithNegative(req: RenderRequest): string {
  const negative = req.negativePrompt?.trim()
  // Scene `style` is the medium lock: paperreel sends bible + Medium.still (or .sheet /
  // .model / .set), and the standalone UI sends DEFAULT_STYLE. A second medium prepended
  // here is how a papercraft or clay still was drawn as paper-cutout anyway -- models
  // overweight the opener, so the cutout sentence won every time.
  const prompt = req.prompt.trim()
  return negative ? `${prompt} Avoid: ${negative}` : prompt
}

function responseError(body: GeminiResponse, status: number, raw: string): Error {
  const message = body.error?.message || raw.slice(0, 500) || 'unknown Gemini API error'
  return new Error(`Gemini image request failed (${status}): ${message}`)
}

export function render(
  req: RenderRequest,
  onProgress: (fraction: number) => void,
  onLog: (line: string) => void,
): RenderHandle {
  const controller = new AbortController()
  let cancelled = false
  let timedOut = false
  let requestTimeoutMs = DEFAULT_TIMEOUT_MS

  const promise = (async () => {
    const apiKey = envValue('X-GOOG-API-KEY') || envValue('GEMINI_API_KEY')
    if (!apiKey) {
      throw new Error('missing X-GOOG-API-KEY in the environment or .env')
    }

    const selectedModel = model(req.model)
    const references = (req.referencePaths || []).map(imageInput)
    const input = [
      { type: 'text' as const, text: promptWithNegative(req) },
      ...references,
    ]
    const body = {
      model: selectedModel,
      input,
      response_format: {
        type: 'image',
        // JPEG is the only value this endpoint accepts: `image/png` comes back as
        // "not supported for 'response_format.mime_type'". That is why frames are written as
        // .jpg, with `legacyFramePath` in store.ts still finding the .png ones rendered before.
        mime_type: 'image/jpeg',
        aspect_ratio: aspectRatio(req.width, req.height),
        image_size: imageSize(selectedModel, req.imageSize),
      },
    }

    onLog(`Gemini ${selectedModel} — ${body.response_format.aspect_ratio}, ${body.response_format.image_size}`)
    onProgress(0.05)

    const requestedTimeout = Number(envValue('GEMINI_IMAGE_TIMEOUT_MS') || DEFAULT_TIMEOUT_MS)
    requestTimeoutMs = Number.isFinite(requestedTimeout) && requestedTimeout > 0
      ? requestedTimeout
      : DEFAULT_TIMEOUT_MS
    const timeout = setTimeout(() => {
      timedOut = true
      controller.abort()
    }, requestTimeoutMs)
    let response: Response
    try {
      response = await fetch(INTERACTIONS_URL, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-goog-api-key': apiKey,
        },
        body: JSON.stringify(body),
        signal: controller.signal,
      })
    } finally {
      clearTimeout(timeout)
    }

    const raw = await response.text()
    let payload: GeminiResponse = {}
    try {
      payload = JSON.parse(raw) as GeminiResponse
    } catch {
      // The status and a bounded response body below are more useful than a JSON parse error.
    }
    if (!response.ok) throw responseError(payload, response.status, raw)

    const encoded = imageData(payload)
    if (!encoded) throw noImageError(payload, raw)

    fs.mkdirSync(path.dirname(req.outputPath), { recursive: true })
    fs.writeFileSync(req.outputPath, Buffer.from(encoded, 'base64'))
    onProgress(1)
    onLog(`Gemini image saved to ${path.basename(req.outputPath)}`)
  })().catch((error: unknown) => {
    if (cancelled) throw new Error('cancelled')
    if (timedOut) throw new Error(`Gemini image request timed out after ${Math.round(requestTimeoutMs / 1000)}s`)
    if (error instanceof Error && error.name === 'AbortError') throw new Error('Gemini image request aborted')
    throw error
  })

  return {
    promise,
    cancel() {
      cancelled = true
      controller.abort()
    },
  }
}
