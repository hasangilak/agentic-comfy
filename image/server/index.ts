import { execFile } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import cors from 'cors'
import express from 'express'
import multer from 'multer'

import {
  ASPECT_PRESETS,
  CONSISTENCY_MODES,
  DEFAULT_NEGATIVE,
  DEFAULT_STYLE,
  MAX_FRAMES,
  MAX_REFERENCES,
  MIN_FRAMES,
  type SceneSettings,
} from '../shared/types.ts'
import {
  OUT_ROOT,
  UPLOADS_ROOT,
  bus,
  cancelScene,
  createScene,
  deleteScene,
  getScene,
  listScenes,
  loadFromDisk,
  resetBeats,
  startScene,
  updateScene,
} from './store.ts'

const PORT = Number(process.env.PORT ?? 8791)

loadFromDisk()

const app = express()
app.use(cors())
app.use(express.json({ limit: '2mb' }))
app.use('/files', express.static(OUT_ROOT))

const upload = multer({
  storage: multer.diskStorage({
    destination: UPLOADS_ROOT,
    filename: (_req, file, cb) => {
      const safe = file.originalname.replace(/[^a-zA-Z0-9._-]/g, '_')
      cb(null, `${Date.now()}-${safe}`)
    },
  }),
  limits: { fileSize: 25 * 1024 * 1024 },
})

function fail(res: express.Response, err: unknown, status = 400) {
  res.status(status).json({ error: err instanceof Error ? err.message : String(err) })
}

// `service` and `limits` exist for programmatic callers, not for the web client, which
// already imports the same constants from shared/types.ts. The reel pipeline next door
// probes this endpoint to decide whether stills can be rendered here at all, and reads the
// frame cap from it rather than hardcoding a number that would drift the day it changes.
app.get('/api/health', (_req, res) => {
  res.json({
    ok: true,
    service: 'papercut-studio',
    aspects: ASPECT_PRESETS,
    // Advertised so a caller can tell a build that knows `edit` from one that does not. An
    // older server handed `consistency: "edit"` stores the string and then matches none of the
    // arms in `referenceFor`, which falls through to chain's backward walk -- conditioning on
    // a frame that does not exist, and quietly rendering from the text alone. Sending `anchor`
    // instead loses the "change nothing else" instruction but keeps the picture, which is the
    // better half to keep.
    modes: CONSISTENCY_MODES,
    // `maxReferences` is here for the same reason `maxFrames` is: the caller next door batches
    // its stills against these numbers, and one hardcoded on that side drifts the day this one
    // moves.
    limits: { minFrames: MIN_FRAMES, maxFrames: MAX_FRAMES, maxReferences: MAX_REFERENCES },
    defaults: { style: DEFAULT_STYLE, negativePrompt: DEFAULT_NEGATIVE },
  })
})

app.get('/api/scenes', (_req, res) => {
  res.json(listScenes())
})

app.post('/api/scenes', (req, res) => {
  try {
    const { beats, ...settings } = req.body as SceneSettings & { beats?: string[] }
    res.json(createScene(settings, beats))
  } catch (err) {
    fail(res, err)
  }
})

app.get('/api/scenes/:id', (req, res) => {
  const scene = getScene(req.params.id)
  if (!scene) return fail(res, new Error('scene not found'), 404)
  res.json(scene)
})

app.patch('/api/scenes/:id', (req, res) => {
  try {
    res.json(updateScene(req.params.id, req.body))
  } catch (err) {
    fail(res, err)
  }
})

app.post('/api/scenes/:id/beats/reset', (req, res) => {
  try {
    res.json(resetBeats(req.params.id))
  } catch (err) {
    fail(res, err)
  }
})

app.post('/api/scenes/:id/render', (req, res) => {
  try {
    const frames = Array.isArray(req.body?.frames) ? (req.body.frames as number[]) : undefined
    res.json(startScene(req.params.id, frames))
  } catch (err) {
    fail(res, err)
  }
})

app.post('/api/scenes/:id/cancel', (req, res) => {
  cancelScene(req.params.id)
  res.json(getScene(req.params.id) ?? null)
})

app.delete('/api/scenes/:id', (req, res) => {
  deleteScene(req.params.id)
  res.json({ ok: true })
})

app.post('/api/upload', upload.single('image'), (req, res) => {
  if (!req.file) return fail(res, new Error('no file uploaded'))
  res.json({
    path: req.file.path,
    url: `/files/uploads/${path.basename(req.file.path)}`,
  })
})

app.get('/api/scenes/:id/download', (req, res) => {
  const scene = getScene(req.params.id)
  if (!scene) return fail(res, new Error('scene not found'), 404)

  const dir = path.join(OUT_ROOT, 'scenes', scene.id)
  const files = scene.frames
    .filter((f) => f.status === 'done')
    .map((f) => `frame-${String(f.index + 1).padStart(2, '0')}.png`)
    .filter((name) => fs.existsSync(path.join(dir, name)))

  if (!files.length) return fail(res, new Error('no rendered frames yet'))

  const slug = (scene.title || 'scene').replace(/[^a-zA-Z0-9._-]/g, '_')
  const zipPath = path.join(os.tmpdir(), `${slug}-${scene.id}.zip`)
  fs.rmSync(zipPath, { force: true })

  execFile('zip', ['-j', '-q', zipPath, ...files], { cwd: dir }, (err) => {
    if (err) return fail(res, err, 500)
    res.download(zipPath, `${slug}-frames.zip`, () => fs.rmSync(zipPath, { force: true }))
  })
})

app.get('/api/scenes/:id/events', (req, res) => {
  const scene = getScene(req.params.id)
  if (!scene) return fail(res, new Error('scene not found'), 404)

  res.set({
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache, no-transform',
    Connection: 'keep-alive',
    'X-Accel-Buffering': 'no',
  })
  res.flushHeaders()

  const send = (payload: unknown) => res.write(`data: ${JSON.stringify(payload)}\n\n`)
  send({ type: 'scene', scene })

  const onScene = (next: unknown) => send({ type: 'scene', scene: next })
  const onLog = (line: string) => send({ type: 'log', line })
  bus.on(`scene:${scene.id}`, onScene)
  bus.on(`log:${scene.id}`, onLog)

  const keepAlive = setInterval(() => res.write(': ping\n\n'), 15_000)

  req.on('close', () => {
    clearInterval(keepAlive)
    bus.off(`scene:${scene.id}`, onScene)
    bus.off(`log:${scene.id}`, onLog)
  })
})

app.listen(PORT, '127.0.0.1', () => {
  console.log(`papercut-studio server on http://127.0.0.1:${PORT}`)
})
