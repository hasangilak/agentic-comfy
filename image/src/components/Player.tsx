import { useEffect, useMemo, useRef, useState } from 'react'

import type { Scene } from '../../shared/types.ts'

const SPEEDS = [0.25, 0.5, 1, 2]

interface Props {
  scene: Scene
  /** Scene frame index to open on. Falls back to the first rendered frame. */
  startFrame?: number
  onClose: () => void
}

/** Plays the rendered frames back at their real hold durations so timing can be judged. */
export function Player({ scene, startFrame = 0, onClose }: Props) {
  // Memoised so the advance timer below is not torn down by every unrelated scene update.
  const rendered = useMemo(() => scene.frames.filter((f) => f.url), [scene.frames])
  const [index, setIndex] = useState(() => {
    const at = rendered.findIndex((f) => f.index === startFrame)
    return at === -1 ? 0 : at
  })
  const [playing, setPlaying] = useState(true)
  const [speed, setSpeed] = useState(1)
  const surface = useRef<HTMLDivElement>(null)

  const count = rendered.length
  const safeIndex = Math.min(index, Math.max(0, count - 1))

  useEffect(() => {
    if (!playing || count < 2) return
    const delay = Math.max(40, (rendered[safeIndex].hold * 1000) / speed)
    const timer = window.setTimeout(() => setIndex((i) => (i + 1) % count), delay)
    return () => window.clearTimeout(timer)
  }, [safeIndex, playing, speed, count, rendered])

  // Modal focus: take it on open, hand it back on close.
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    surface.current?.focus()
    return () => previous?.focus?.()
  }, [])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') return onClose()
      // Space belongs to whatever control has focus; only the surface itself toggles playback.
      const onControl = (event.target as HTMLElement | null)?.closest('button, a, input, textarea')
      if (event.key === ' ' && !onControl) {
        event.preventDefault()
        setPlaying((p) => !p)
      }
      if (event.key === 'ArrowRight') {
        setPlaying(false)
        setIndex((i) => (i + 1) % count)
      }
      if (event.key === 'ArrowLeft') {
        setPlaying(false)
        setIndex((i) => (i - 1 + count) % count)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, count])

  if (!count) return null
  const current = rendered[safeIndex]

  return (
    <div className="player-backdrop" onClick={onClose}>
      <div
        className="player"
        ref={surface}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={`Playback — ${scene.title || 'untitled scene'}`}
        onClick={(e) => e.stopPropagation()}
      >
        <img
          src={current.url}
          alt={`Frame ${current.index + 1} of ${scene.frames.length}: ${current.beat}`}
        />

        <div className="player-bar">
          <button type="button" onClick={() => setPlaying((p) => !p)}>
            {playing ? 'Pause' : 'Play'}
          </button>

          <div className="player-dots" role="group" aria-label="Jump to frame">
            {rendered.map((frame, i) => (
              <button
                key={frame.index}
                type="button"
                className={i === safeIndex ? 'active' : ''}
                aria-label={`Frame ${frame.index + 1}, ${frame.timestamp.toFixed(2)} seconds`}
                aria-current={i === safeIndex}
                onClick={() => {
                  setPlaying(false)
                  setIndex(i)
                }}
              >
                <span />
              </button>
            ))}
          </div>

          <div className="segmented" role="group" aria-label="Playback speed">
            {SPEEDS.map((s) => (
              <button
                key={s}
                type="button"
                className={s === speed ? 'active' : ''}
                aria-pressed={s === speed}
                onClick={() => setSpeed(s)}
              >
                {s}×
              </button>
            ))}
          </div>

          <span className="player-meta" role="status" aria-live="polite">
            {current.timestamp.toFixed(2)}s · frame {current.index + 1}/{scene.frames.length}
          </span>

          <button type="button" className="ghost" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  )
}
