import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  DEFAULT_NEGATIVE,
  DEFAULT_STYLE,
  defaultBeat,
  type Scene,
  type SceneSettings,
} from '../shared/types.ts'
import { api } from './api.ts'
import { FrameCard } from './components/FrameCard.tsx'
import { Player } from './components/Player.tsx'
import { SceneForm } from './components/SceneForm.tsx'
import { SceneList } from './components/SceneList.tsx'
import { estimateFrameSeconds, formatDuration, observedFrameSeconds } from './estimate.ts'
import { useSceneStream, useStored } from './hooks.ts'

function makeDefaults(): SceneSettings {
  return {
    title: '',
    description: '',
    duration: 10,
    frameCount: 5,
    style: DEFAULT_STYLE,
    negativePrompt: DEFAULT_NEGATIVE,
    aspectId: '16:9',
    steps: 4,
    seed: Math.floor(Math.random() * 1_000_000),
    varySeeds: false,
    consistency: 'chain',
  }
}

function settingsOf(scene: Scene): SceneSettings {
  return {
    title: scene.title,
    description: scene.description,
    duration: scene.duration,
    frameCount: scene.frameCount,
    style: scene.style,
    negativePrompt: scene.negativePrompt,
    aspectId: scene.aspectId,
    steps: scene.steps,
    seed: scene.seed,
    varySeeds: scene.varySeeds,
    consistency: scene.consistency,
    referencePath: scene.referencePath,
    referenceUrl: scene.referenceUrl,
  }
}

export default function App() {
  const [scenes, setScenes] = useState<Scene[]>([])
  const [activeId, setActiveId] = useStored<string | null>('papercut.activeScene', null)
  const [draft, setDraft] = useState<SceneSettings>(makeDefaults)
  const [beats, setBeats] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [playing, setPlaying] = useState(false)
  const [showLog, setShowLog] = useState(false)

  const { scene, logs, connected } = useSceneStream(activeId)

  const refreshList = useCallback(async () => {
    try {
      setScenes(await api.listScenes())
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [])

  useEffect(() => {
    void refreshList()
  }, [refreshList])

  // Keep the sidebar counters in step with the live scene without refetching everything.
  useEffect(() => {
    if (!scene) return
    setScenes((prev) => {
      const found = prev.some((s) => s.id === scene.id)
      return found ? prev.map((s) => (s.id === scene.id ? scene : s)) : [scene, ...prev]
    })
  }, [scene])

  // Load the selected scene's settings and beats into the editors.
  useEffect(() => {
    if (!scene) return
    setDraft(settingsOf(scene))
    setBeats(scene.frames.map((f) => f.beat))
  }, [scene?.id, scene?.frames.length]) // eslint-disable-line react-hooks/exhaustive-deps

  const isNew = activeId === null
  const running = scene?.status === 'running'

  const dirty = useMemo(() => {
    if (isNew || !scene) return true
    return JSON.stringify(draft) !== JSON.stringify(settingsOf(scene))
  }, [draft, scene, isNew])

  const beatsDirty = useMemo(() => {
    if (!scene) return false
    return JSON.stringify(beats) !== JSON.stringify(scene.frames.map((f) => f.beat))
  }, [beats, scene])

  async function guard<T>(fn: () => Promise<T>) {
    setBusy(true)
    setError(null)
    try {
      return await fn()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      return null
    } finally {
      setBusy(false)
    }
  }

  async function handleSubmit() {
    if (isNew) {
      const created = await guard(() =>
        api.createScene(
          draft,
          Array.from({ length: draft.frameCount }, (_, i) =>
            defaultBeat(draft.description, i, draft.frameCount),
          ),
        ),
      )
      if (created) {
        setActiveId(created.id)
        void refreshList()
      }
      return
    }
    await guard(() => api.updateScene(scene!.id, { ...draft, beats }))
  }

  async function saveBeats() {
    if (!scene || !beatsDirty || running) return
    await guard(() => api.updateScene(scene.id, { beats }))
  }

  const doneCount = scene?.frames.filter((f) => f.status === 'done').length ?? 0
  const perFrame = scene
    ? (observedFrameSeconds(scene) ??
      estimateFrameSeconds({
        width: scene.width,
        height: scene.height,
        steps: scene.steps,
        usesReference: scene.consistency !== 'none',
      }))
    : 0
  const remaining = scene ? perFrame * (scene.frames.length - doneCount) : 0

  return (
    <div className="app">
      <header className="topbar">
        <h1>
          Papercut Studio<span className="dot" data-connected={connected} />
        </h1>
        <p>Stop-motion keyframes on flux2-klein-4b, local.</p>
      </header>

      <div className="layout">
        <SceneList
          scenes={scenes}
          activeId={activeId}
          onSelect={setActiveId}
          onNew={() => {
            setActiveId(null)
            setDraft(makeDefaults())
            setBeats([])
          }}
          onDelete={async (id) => {
            await guard(() => api.remove(id))
            if (id === activeId) setActiveId(null)
            void refreshList()
          }}
        />

        <main className="workspace">
          <SceneForm
            value={draft}
            onChange={setDraft}
            onSubmit={handleSubmit}
            submitLabel={isNew ? 'Create scene' : dirty ? 'Save changes' : 'Saved'}
            busy={busy || running}
            dirty={isNew ? true : dirty}
          />

          <section className="frames-panel">
            {!scene && (
              <div className="empty">
                <h2>Describe a scene, pick 2–9 frames, hit create.</h2>
                <p>
                  A 10-second beat split into 5 frames gives you 2s per hold — enough to read as
                  motion when you shoot it back as stop motion.
                </p>
              </div>
            )}

            {scene && (
              <>
                <div className="toolbar">
                  <div className="toolbar-info">
                    <strong>{scene.title || 'Untitled scene'}</strong>
                    <small>
                      {doneCount}/{scene.frames.length} rendered · {scene.width}×{scene.height} ·{' '}
                      {scene.consistency} · {scene.status}
                      {running && remaining > 0 && ` · ~${formatDuration(remaining)} left`}
                    </small>
                  </div>

                  <div className="toolbar-actions">
                    {beatsDirty && !running && (
                      <button type="button" onClick={() => void saveBeats()}>
                        Save beats
                      </button>
                    )}
                    <button
                      type="button"
                      className="ghost"
                      disabled={running}
                      onClick={async () => {
                        const next = await guard(() => api.resetBeats(scene.id))
                        if (next) setBeats(next.frames.map((f) => f.beat))
                      }}
                    >
                      Reset beats
                    </button>
                    <button type="button" disabled={doneCount < 2} onClick={() => setPlaying(true)}>
                      Play
                    </button>
                    <a
                      className={`button ${doneCount ? '' : 'disabled'}`}
                      href={api.downloadUrl(scene.id)}
                    >
                      Download zip
                    </a>
                    {running ? (
                      <button type="button" className="danger" onClick={() => void guard(() => api.cancel(scene.id))}>
                        Stop
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="primary"
                        onClick={async () => {
                          if (beatsDirty) await saveBeats()
                          await guard(() => api.render(scene.id))
                        }}
                      >
                        Render all {scene.frames.length}
                      </button>
                    )}
                  </div>
                </div>

                {scene.consistency === 'chain' && (
                  <p className="hint chain-note">
                    Chain mode renders in order — each frame conditions on the previous one, so
                    re-rendering frame {Math.min(2, scene.frames.length)} will not match until the
                    frames after it are redone too.
                  </p>
                )}

                <div className="frames-grid">
                  {scene.frames.map((frame, i) => (
                    <FrameCard
                      key={frame.index}
                      frame={{ ...frame, beat: beats[i] ?? frame.beat }}
                      scene={scene}
                      disabled={running || busy}
                      onBeatChange={(next) =>
                        setBeats((prev) => {
                          const copy = [...prev]
                          copy[i] = next
                          return copy
                        })
                      }
                      onRender={async () => {
                        if (beatsDirty) await saveBeats()
                        await guard(() => api.render(scene.id, [i]))
                      }}
                      onOpen={() => setPlaying(true)}
                    />
                  ))}
                </div>

                <details className="log" open={showLog} onToggle={(e) => setShowLog(e.currentTarget.open)}>
                  <summary>mflux output ({logs.length})</summary>
                  <pre>{logs.slice(-80).join('\n')}</pre>
                </details>
              </>
            )}
          </section>
        </main>
      </div>

      {error && (
        <div className="toast error" onClick={() => setError(null)}>
          {error}
        </div>
      )}

      {playing && scene && <Player scene={scene} onClose={() => setPlaying(false)} />}
    </div>
  )
}
