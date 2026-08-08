import { EventEmitter } from 'node:events'
import fs from 'node:fs'
import path from 'node:path'
import { randomUUID } from 'node:crypto'

import {
  ASPECT_PRESETS,
  MAX_FRAMES,
  MAX_REFERENCES,
  MIN_FRAMES,
  defaultBeat,
  frameTimings,
  type Frame,
  type Scene,
  type SceneSettings,
} from '../shared/types.ts'
import { render, type RenderHandle } from './gemini.ts'

export const OUT_ROOT = path.resolve(process.cwd(), 'out')
export const SCENES_ROOT = path.join(OUT_ROOT, 'scenes')
export const UPLOADS_ROOT = path.join(OUT_ROOT, 'uploads')

fs.mkdirSync(SCENES_ROOT, { recursive: true })
fs.mkdirSync(UPLOADS_ROOT, { recursive: true })

// Locks the look to the reference while explicitly licensing the pose to change —
// without that second half, chain mode reproduces the previous frame almost exactly.
const CONTINUITY_CLAUSE =
  'Keep the exact same paper cutout collage style, the same characters and costumes, ' +
  'the same background, lighting and camera angle as the reference image, ' +
  'but move the subject into a clearly different pose and position as described. Now:'

// The same sentence for a frame conditioned on several images. Kept as its own string rather
// than pluralised at runtime so a single-reference render composes the byte-identical prompt it
// always did — the clause is part of what a scene looked like, and rewording it silently would
// change every re-roll of every existing scene.
const CONTINUITY_CLAUSE_MULTI =
  'Keep the exact same paper cutout collage style, the same characters and costumes, ' +
  'the same background, lighting and camera angle as the reference images, ' +
  'but move the subject into a clearly different pose and position as described. Now:'

const scenes = new Map<string, Scene>()
export const bus = new EventEmitter()
bus.setMaxListeners(0)

/** Keep one remote image request in flight at a time so the existing scene ordering is stable. */
let renderLock: Promise<void> = Promise.resolve()
const active = new Map<string, RenderHandle>()

function sceneDir(id: string) {
  return path.join(SCENES_ROOT, id)
}

function framePath(id: string, index: number) {
  return path.join(sceneDir(id), `frame-${String(index + 1).padStart(2, '0')}.jpg`)
}

function legacyFramePath(id: string, index: number) {
  return path.join(sceneDir(id), `frame-${String(index + 1).padStart(2, '0')}.png`)
}

function existingFramePath(id: string, index: number) {
  const current = framePath(id, index)
  return fs.existsSync(current) ? current : legacyFramePath(id, index)
}

function frameUrl(id: string, index: number) {
  const name = `frame-${String(index + 1).padStart(2, '0')}.jpg`
  return `/files/scenes/${id}/${name}`
}

function persist(scene: Scene) {
  fs.mkdirSync(sceneDir(scene.id), { recursive: true })
  fs.writeFileSync(path.join(sceneDir(scene.id), 'scene.json'), JSON.stringify(scene, null, 2))
}

function emit(scene: Scene) {
  bus.emit('scene', scene)
  bus.emit(`scene:${scene.id}`, scene)
}

function log(sceneId: string, line: string) {
  bus.emit(`log:${sceneId}`, line)
}

function update(scene: Scene, persistToDisk = true) {
  scenes.set(scene.id, scene)
  if (persistToDisk) persist(scene)
  emit(scene)
}

export function clampFrameCount(n: number) {
  return Math.max(MIN_FRAMES, Math.min(MAX_FRAMES, Math.round(n)))
}

function resolveAspect(aspectId: string) {
  return ASPECT_PRESETS.find((a) => a.id === aspectId) ?? ASPECT_PRESETS[0]
}

function randomSeed() {
  return Math.floor(Math.random() * 1_000_000)
}

export function listScenes(): Scene[] {
  return [...scenes.values()].sort((a, b) => b.createdAt - a.createdAt)
}

export function getScene(id: string) {
  return scenes.get(id)
}

export function createScene(settings: SceneSettings, beats?: string[]): Scene {
  const frameCount = clampFrameCount(settings.frameCount)
  const aspect = resolveAspect(settings.aspectId)
  const timings = frameTimings(settings.duration, frameCount)
  const seed = settings.seed || randomSeed()

  const id = randomUUID().slice(0, 8)
  const frames: Frame[] = timings.map((t, i) => ({
    index: i,
    timestamp: t.timestamp,
    hold: t.hold,
    beat: beats?.[i]?.trim() || defaultBeat(settings.description, i, frameCount),
    status: 'pending',
    seed: settings.varySeeds ? seed + i : seed,
    progress: 0,
  }))

  const scene: Scene = {
    ...settings,
    id,
    frameCount,
    seed,
    createdAt: Date.now(),
    status: 'idle',
    width: aspect.width,
    height: aspect.height,
    frames,
  }
  update(scene)
  return scene
}

export function deleteScene(id: string) {
  cancelScene(id)
  scenes.delete(id)
  fs.rmSync(sceneDir(id), { recursive: true, force: true })
  bus.emit('list')
}

/**
 * Every conditioning image a scene was given, in the order the caller sent them.
 *
 * `referencePaths` wins over `referencePath` when both are present, and the single field is
 * read as a one-image list otherwise — which is what keeps a scene created by the local UI,
 * or by an older caller, conditioned on exactly the image it uploaded.
 *
 * Missing files are dropped rather than raised on: a reel next door can delete the still a
 * scene pointed at between two renders, and losing one picture of four is a better render
 * than none.
 */
function uploadedReferences(scene: Scene): string[] {
  const listed = scene.referencePaths?.length ? scene.referencePaths : [scene.referencePath]
  return listed
    .filter((p): p is string => !!p && fs.existsSync(p))
    .slice(0, MAX_REFERENCES)
}

/**
 * Picks the conditioning images for a frame:
 * - chain  -> the previously rendered frame, falling back to the uploaded references
 * - anchor -> the uploaded references, falling back to frame 1
 * - edit   -> the uploaded references, and nothing else
 * - none   -> nothing, pure text-to-image
 */
function referenceFor(scene: Scene, index: number): string[] | undefined {
  if (scene.consistency === 'none') return undefined
  const uploaded = uploadedReferences(scene)

  // Edit conditions on what it was handed and never falls back to frame 1. The mode means
  // "hold THIS picture and change only what the beat says", so silently editing a different
  // image would be worse than rendering from the text alone -- and a caller that meant to send
  // a reference and did not has a bug worth seeing.
  if (scene.consistency === 'edit') return uploaded.length ? uploaded : undefined

  if (scene.consistency === 'anchor') {
    if (uploaded.length) return uploaded
    const first = existingFramePath(scene.id, 0)
    if (index > 0 && fs.existsSync(first)) return [first]
    return undefined
  }

  // Chain conditions on the newest rendered frame ALONE, uploads included or not: the point of
  // the mode is that each frame continues the one before it, and a second picture beside it
  // pulls the look back towards something that is not where the motion had got to.
  for (let i = index - 1; i >= 0; i--) {
    const prev = existingFramePath(scene.id, i)
    if (fs.existsSync(prev)) return [prev]
  }
  return uploaded.length ? uploaded : undefined
}

function composePrompt(scene: Scene, frame: Frame, references: number): string {
  const parts: string[] = []
  // Edit is the one conditioned mode that omits the clause -- see ConsistencyMode. Checked
  // before the reference count rather than folded into it, because the mode is the reason and
  // "it happened to have references" is not: a future mode that also holds its reference should
  // land here too, and a reader should not have to work out which arm of the count it fell down.
  if (scene.consistency === 'edit') {
    // nothing prepended
  } else if (references === 1) parts.push(CONTINUITY_CLAUSE)
  else if (references > 1) parts.push(CONTINUITY_CLAUSE_MULTI)
  parts.push(frame.beat.trim())
  if (scene.style.trim()) parts.push(scene.style.trim())
  return parts.join(' ').replace(/\s+/g, ' ').trim()
}

async function renderFrame(scene: Scene, index: number) {
  const frame = scene.frames[index]
  const references = referenceFor(scene, index)
  const prompt = composePrompt(scene, frame, references?.length ?? 0)
  const outputPath = framePath(scene.id, index)

  frame.status = 'running'
  frame.progress = 0
  frame.error = undefined
  update(scene, false)

  const startedAt = Date.now()
  log(scene.id, `frame ${index + 1}/${scene.frames.length} — ${prompt}`)

  const handle = render(
    {
      prompt,
      negativePrompt: scene.negativePrompt,
      outputPath,
      width: scene.width,
      height: scene.height,
      steps: scene.steps,
      seed: frame.seed,
      model: scene.geminiModel,
      imageSize: scene.geminiImageSize,
      referencePaths: references,
    },
    (fraction) => {
      frame.progress = fraction
      update(scene, false)
    },
    (line) => log(scene.id, line),
  )
  active.set(`${scene.id}:${index}`, handle)

  try {
    await handle.promise
    frame.status = 'done'
    frame.progress = 1
    frame.elapsed = Number(((Date.now() - startedAt) / 1000).toFixed(1))
    frame.url = `${frameUrl(scene.id, index)}?v=${Date.now()}`
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    frame.status = message === 'cancelled' ? 'cancelled' : 'error'
    frame.error = message === 'cancelled' ? undefined : message
    throw err
  } finally {
    active.delete(`${scene.id}:${index}`)
    update(scene)
  }
}

/** Serialises every render through one global lock so cloud requests stay ordered and cancellable. */
function queue<T>(job: () => Promise<T>): Promise<T> {
  const result = renderLock.then(job, job)
  renderLock = result.then(
    () => undefined,
    () => undefined,
  )
  return result
}

export function startScene(id: string, onlyIndices?: number[]) {
  const scene = scenes.get(id)
  if (!scene) throw new Error('scene not found')
  if (scene.status === 'running') return scene

  const targets = onlyIndices?.length
    ? onlyIndices.filter((i) => i >= 0 && i < scene.frames.length)
    : scene.frames.map((f) => f.index)

  scene.status = 'running'
  for (const i of targets) {
    scene.frames[i].status = 'pending'
    scene.frames[i].progress = 0
    scene.frames[i].error = undefined
  }
  update(scene)

  void queue(async () => {
    try {
      for (const i of targets) {
        if (scene.status !== 'running') break
        await renderFrame(scene, i)
      }
      if (scene.status === 'running') {
        const failed = scene.frames.some((f) => f.status === 'error')
        scene.status = failed ? 'error' : 'done'
      }
    } catch (err) {
      if (scene.status === 'running') {
        scene.status = err instanceof Error && err.message === 'cancelled' ? 'cancelled' : 'error'
      }
    } finally {
      for (const f of scene.frames) {
        if (f.status === 'pending' || f.status === 'running') f.status = 'cancelled'
      }
      update(scene)
    }
  })

  return scene
}

export function cancelScene(id: string) {
  const scene = scenes.get(id)
  if (!scene) return
  scene.status = 'cancelled'
  for (const [key, handle] of active) {
    if (key.startsWith(`${id}:`)) handle.cancel()
  }
  update(scene)
}

export function updateScene(id: string, patch: Partial<SceneSettings> & { beats?: string[] }): Scene {
  const scene = scenes.get(id)
  if (!scene) throw new Error('scene not found')
  if (scene.status === 'running') throw new Error('cannot edit a scene while it is rendering')

  Object.assign(scene, patch)
  delete (scene as unknown as Record<string, unknown>).beats

  if (patch.aspectId) {
    const aspect = resolveAspect(patch.aspectId)
    scene.width = aspect.width
    scene.height = aspect.height
  }

  if (patch.frameCount !== undefined) {
    scene.frameCount = clampFrameCount(patch.frameCount)
    const next: Frame[] = []
    for (let i = 0; i < scene.frameCount; i++) {
      next.push(
        scene.frames[i] ?? {
          index: i,
          timestamp: 0,
          hold: 0,
          beat: defaultBeat(scene.description, i, scene.frameCount),
          status: 'pending',
          seed: scene.varySeeds ? scene.seed + i : scene.seed,
          progress: 0,
        },
      )
    }
    scene.frames = next
  }

  const timings = frameTimings(scene.duration, scene.frameCount)
  scene.frames.forEach((frame, i) => {
    frame.index = i
    frame.timestamp = timings[i].timestamp
    frame.hold = timings[i].hold
    frame.seed = scene.varySeeds ? scene.seed + i : scene.seed
    if (patch.beats?.[i] !== undefined) frame.beat = patch.beats[i]
  })

  update(scene)
  return scene
}

/** Rebuilds every beat from the current description, discarding manual edits. */
export function resetBeats(id: string): Scene {
  const scene = scenes.get(id)
  if (!scene) throw new Error('scene not found')
  scene.frames.forEach((frame, i) => {
    frame.beat = defaultBeat(scene.description, i, scene.frameCount)
  })
  update(scene)
  return scene
}

export function loadFromDisk() {
  if (!fs.existsSync(SCENES_ROOT)) return
  for (const entry of fs.readdirSync(SCENES_ROOT)) {
    const file = path.join(SCENES_ROOT, entry, 'scene.json')
    if (!fs.existsSync(file)) continue
    try {
      const scene = JSON.parse(fs.readFileSync(file, 'utf8')) as Scene
      // Nothing survives a restart mid-render.
      if (scene.status === 'running') scene.status = 'cancelled'
      for (const frame of scene.frames) {
        if (frame.status === 'running' || frame.status === 'pending') frame.status = 'cancelled'
      }
      scenes.set(scene.id, scene)
    } catch {
      // Skip unreadable scenes rather than refusing to boot.
    }
  }
}
