import type { Frame, Scene } from '../../shared/types.ts'
import { IconExpand } from './icons.tsx'

interface Props {
  frame: Frame
  scene: Scene
  onBeatChange: (beat: string) => void
  onRender: () => void
  onOpen: () => void
  disabled: boolean
}

export function FrameCard({ frame, scene, onBeatChange, onRender, onOpen, disabled }: Props) {
  const ratio = scene.width / scene.height
  const number = String(frame.index + 1).padStart(2, '0')
  const percent = Math.round(frame.progress * 100)

  return (
    <article className={`frame-card status-${frame.status}`}>
      <header>
        <span className="frame-index">{number}</span>
        <span className="frame-time">
          {frame.timestamp.toFixed(2)}s → {(frame.timestamp + frame.hold).toFixed(2)}s
        </span>
        <span className={`badge ${frame.status}`}>{frame.status}</span>
      </header>

      <div className="frame-thumb" style={{ aspectRatio: String(ratio) }}>
        {frame.url ? (
          <button
            type="button"
            className="frame-open"
            onClick={onOpen}
            aria-label={`Open frame ${frame.index + 1} in the player`}
          >
            <img src={frame.url} alt={frame.beat || `Frame ${frame.index + 1}`} />
            <span className="frame-open-badge">
              <IconExpand />
            </span>
          </button>
        ) : (
          <div className="frame-placeholder">
            {frame.status === 'running' ? `${percent}%` : '—'}
          </div>
        )}
        {frame.status === 'running' && (
          <div
            className="frame-progress"
            role="progressbar"
            aria-label={`Frame ${frame.index + 1} render progress`}
            aria-valuenow={percent}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <div style={{ transform: `scaleX(${Math.min(1, Math.max(0, frame.progress))})` }} />
          </div>
        )}
      </div>

      <label className="frame-beat">
        <span className="visually-hidden">Beat for frame {frame.index + 1}</span>
        <textarea
          rows={4}
          value={frame.beat}
          onChange={(e) => onBeatChange(e.target.value)}
          disabled={disabled}
          placeholder="What this frame shows"
        />
      </label>

      {frame.error && <p className="error frame-error">{frame.error}</p>}

      <footer>
        <small>
          frame {number}
          {frame.elapsed !== undefined && ` · ${frame.elapsed}s`}
        </small>
        <div className="frame-actions">
          {frame.url && (
            <a className="button ghost" href={frame.url} download={`frame-${number}.png`}>
              Save
            </a>
          )}
          <button type="button" onClick={onRender} disabled={disabled}>
            {frame.url ? 'Redo' : 'Render'}
          </button>
        </div>
      </footer>
    </article>
  )
}
