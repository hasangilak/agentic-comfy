import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

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
import {
  estimateFrameSeconds,
  formatDuration,
  observedFrameSeconds,
  referenceCount,
} from './estimate.ts'
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
    // Carried even though nothing here can add a second picture: a scene created over the API
    // (the reel pipeline next door sends several) must not lose them the first time it is edited
    // in this UI, and the time estimate is counted off them.
    referencePaths: scene.referencePaths,
  }
}

export default function App() {
  const [scenes, setScenes] = useState<Scene[]>([])
  const [activeId, setActiveId] = useStored<string | null>('papercut.activeScene', null)
  const [draft, setDraft] = useState<SceneSettings>(makeDefaults)
  const [beats, setBeats] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [playFrom, setPlayFrom] = useState<number | null>(null)
  const [showLog, setShowLog] = useState(false)
  const [listLoading, setListLoading] = useState(true)
  const logRef = useRef<HTMLPreElement>(null)

  const { scene, logs, connected } = useSceneStream(activeId)

  const refreshList = useCallback(async () => {
    try {
      setScenes(await api.listScenes())
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setListLoading(false)
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

  // A log that does not follow its own tail is a log you have to chase.
  useEffect(() => {
    const el = logRef.current
    if (showLog && el) el.scrollTop = el.scrollHeight
  }, [logs, showLog])

  const isNew = activeId === null
  const running = scene?.status === 'running'
  // The stream has not delivered the selected scene yet — not the same thing as "no scene".
  const loadingScene = !isNew && !scene

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
        references: referenceCount(scene),
      }))
    : 0
  const remaining = scene ? perFrame * (scene.frames.length - doneCount) : 0

  return (
    <div className="app">
      <header className="topbar">
        <h1>Papercut Studio</h1>
        <p className="topbar-sub">Stop-motion keyframes on flux2-klein-4b, local.</p>
        <p className="conn" role="status">
          <span className="dot" data-connected={connected} />
          {connected ? 'Render server connected' : 'Render server not connected'}
        </p>
      </header>

      <div className="layout">
        <SceneList
          scenes={scenes}
          activeId={activeId}
          loading={listLoading}
          onSelect={setActiveId}
          onNew={() => {
            setActiveId(null)
            setDraft(makeDefaults())
            setBeats([])
          }}
          onDelete={async (id) => {
            const target = scenes.find((s) => s.id === id)
            const rendered = target?.frames.filter((f) => f.status === 'done').length ?? 0
            const label = target?.title || 'this untitled scene'
            const ok = window.confirm(
              rendered > 0
                ? `Delete “${label}” and its ${rendered} rendered frame${rendered === 1 ? '' : 's'}? The files are removed from out/ and cannot be recovered.`
                : `Delete “${label}”? It has no rendered frames yet.`,
            )
            if (!ok) return
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
            {loadingScene && (
              <div className="empty" aria-busy="true">
                <h2>Loading scene…</h2>
                <p>Reading the frames off disk.</p>
              </div>
            )}

            {isNew && (
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
                    <small role="status" aria-live="polite">
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
                      disabled={running || busy}
                      onClick={async () => {
                        const next = await guard(() => api.resetBeats(scene.id))
                        if (next) setBeats(next.frames.map((f) => f.beat))
                      }}
                    >
                      Reset beats
                    </button>
                    <button
                      type="button"
                      disabled={doneCount < 2}
                      title={doneCount < 2 ? 'Playback needs at least two rendered frames' : undefined}
                      onClick={() => setPlayFrom(0)}
                    >
                      Play
                    </button>
                    {doneCount ? (
                      <a className="button" href={api.downloadUrl(scene.id)}>
                        Download zip
                      </a>
                    ) : (
                      <span className="button disabled" aria-disabled="true">
                        Download zip
                      </span>
                    )}
                    {running ? (
                      <button
                        type="button"
                        className="danger"
                        onClick={() => void guard(() => api.cancel(scene.id))}
                      >
                        Stop
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="primary"
                        disabled={busy}
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
                      onOpen={() => setPlayFrom(frame.index)}
                    />
                  ))}
                </div>

                <details
                  className="log"
                  open={showLog}
                  onToggle={(e) => setShowLog(e.currentTarget.open)}
                >
                  <summary>mflux output ({logs.length})</summary>
                  <pre ref={logRef} tabIndex={0}>
                    {logs.length ? logs.join('\n') : 'No output yet — nothing has rendered this session.'}
                  </pre>
                </details>
              </>
            )}
          </section>
        </main>
      </div>

      {error && (
        <div className="toast" role="alert">
          <p>{error}</p>
          <button type="button" className="ghost tiny" onClick={() => setError(null)}>
            Dismiss
          </button>
        </div>
      )}

      {playFrom !== null && scene && (
        <Player scene={scene} startFrame={playFrom} onClose={() => setPlayFrom(null)} />
      )}
    </div>
  )
}
