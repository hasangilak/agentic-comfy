import { EventEmitter } from 'node:events'
import fs from 'node:fs'
import path from 'node:path'
import { randomUUID } from 'node:crypto'

import {
  ASPECT_PRESETS,
  MAX_FRAMES,
  MIN_FRAMES,
  defaultBeat,
  frameTimings,
  type Frame,
  type Scene,
  type SceneSettings,
} from '../shared/types.ts'
import { render, type RenderHandle } from './mflux.ts'

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

const scenes = new Map<string, Scene>()
export const bus = new EventEmitter()
bus.setMaxListeners(0)

/** mflux holds ~18 GB of weights, so only one render may be in flight at a time. */
let renderLock: Promise<void> = Promise.resolve()
const active = new Map<string, RenderHandle>()

function sceneDir(id: string) {
  return path.join(SCENES_ROOT, id)
}

function framePath(id: string, index: number) {
  return path.join(sceneDir(id), `frame-${String(index + 1).padStart(2, '0')}.png`)
}

function frameUrl(id: string, index: number) {
  const name = `frame-${String(index + 1).padStart(2, '0')}.png`
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
 * Picks the conditioning image for a frame:
 * - chain  -> the previously rendered frame, falling back to the uploaded reference
 * - anchor -> the uploaded reference, falling back to frame 1
 * - none   -> nothing, pure text-to-image
 */
function referenceFor(scene: Scene, index: number): string[] | undefined {
  if (scene.consistency === 'none') return undefined

  if (scene.consistency === 'anchor') {
    if (scene.referencePath && fs.existsSync(scene.referencePath)) return [scene.referencePath]
    const first = framePath(scene.id, 0)
    if (index > 0 && fs.existsSync(first)) return [first]
    return undefined
  }

  for (let i = index - 1; i >= 0; i--) {
    const prev = framePath(scene.id, i)
    if (fs.existsSync(prev)) return [prev]
  }
  if (scene.referencePath && fs.existsSync(scene.referencePath)) return [scene.referencePath]
  return undefined
}

function composePrompt(scene: Scene, frame: Frame, hasReference: boolean): string {
  const parts: string[] = []
  if (hasReference) parts.push(CONTINUITY_CLAUSE)
  parts.push(frame.beat.trim())
  if (scene.style.trim()) parts.push(scene.style.trim())
  return parts.join(' ').replace(/\s+/g, ' ').trim()
}

async function renderFrame(scene: Scene, index: number) {
  const frame = scene.frames[index]
  const references = referenceFor(scene, index)
  const prompt = composePrompt(scene, frame, !!references)
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

/** Serialises every render through one global lock so MLX never holds two model copies. */
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
