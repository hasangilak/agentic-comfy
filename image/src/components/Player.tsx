import { useEffect, useRef, useState } from 'react'

import type { Scene } from '../../shared/types.ts'

const SPEEDS = [0.25, 0.5, 1, 2]

interface Props {
  scene: Scene
  onClose: () => void
}

/** Plays the rendered frames back at their real hold durations so timing can be judged. */
export function Player({ scene, onClose }: Props) {
  const rendered = scene.frames.filter((f) => f.url)
  const [index, setIndex] = useState(0)
  const [playing, setPlaying] = useState(true)
  const [speed, setSpeed] = useState(1)
  const timer = useRef<number | null>(null)

  useEffect(() => {
    if (!playing || rendered.length < 2) return
    const current = rendered[Math.min(index, rendered.length - 1)]
    const delay = Math.max(40, (current.hold * 1000) / speed)
    timer.current = window.setTimeout(() => setIndex((i) => (i + 1) % rendered.length), delay)
    return () => {
      if (timer.current) window.clearTimeout(timer.current)
    }
  }, [index, playing, speed, rendered])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
      if (event.key === ' ') {
        event.preventDefault()
        setPlaying((p) => !p)
      }
      if (event.key === 'ArrowRight') {
        setPlaying(false)
        setIndex((i) => (i + 1) % rendered.length)
      }
      if (event.key === 'ArrowLeft') {
        setPlaying(false)
        setIndex((i) => (i - 1 + rendered.length) % rendered.length)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, rendered.length])

  if (!rendered.length) return null
  const current = rendered[Math.min(index, rendered.length - 1)]

  return (
    <div className="player-backdrop" onClick={onClose}>
      <div className="player" onClick={(e) => e.stopPropagation()}>
        <img src={current.url} alt={`frame ${current.index + 1}`} />

        <div className="player-bar">
          <button type="button" onClick={() => setPlaying((p) => !p)}>
            {playing ? 'Pause' : 'Play'}
          </button>

          <div className="player-dots">
            {rendered.map((frame, i) => (
              <button
                key={frame.index}
                type="button"
                className={i === index ? 'active' : ''}
                title={`${frame.timestamp.toFixed(2)}s`}
                onClick={() => {
                  setPlaying(false)
                  setIndex(i)
                }}
              />
            ))}
          </div>

          <div className="segmented">
            {SPEEDS.map((s) => (
              <button key={s} type="button" className={s === speed ? 'active' : ''} onClick={() => setSpeed(s)}>
                {s}×
              </button>
            ))}
          </div>

          <span className="player-meta">
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
