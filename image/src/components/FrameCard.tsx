import type { Frame, Scene } from '../../shared/types.ts'

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

  return (
    <article className={`frame-card status-${frame.status}`}>
      <header>
        <span className="frame-index">{String(frame.index + 1).padStart(2, '0')}</span>
        <span className="frame-time">
          {frame.timestamp.toFixed(2)}s → {(frame.timestamp + frame.hold).toFixed(2)}s
        </span>
        <span className={`badge ${frame.status}`}>{frame.status}</span>
      </header>

      <div className="frame-thumb" style={{ aspectRatio: String(ratio) }}>
        {frame.url ? (
          <img src={frame.url} alt={`frame ${frame.index + 1}`} onClick={onOpen} />
        ) : (
          <div className="frame-placeholder">
            {frame.status === 'running' ? `${Math.round(frame.progress * 100)}%` : '—'}
          </div>
        )}
        {frame.status === 'running' && (
          <div className="frame-progress">
            <div style={{ width: `${frame.progress * 100}%` }} />
          </div>
        )}
      </div>

      <textarea
        rows={3}
        value={frame.beat}
        onChange={(e) => onBeatChange(e.target.value)}
        disabled={scene.status === 'running'}
        placeholder="What this frame shows"
      />

      {frame.error && <p className="error frame-error">{frame.error}</p>}

      <footer>
        <small>
          seed {frame.seed}
          {frame.elapsed !== undefined && ` · ${frame.elapsed}s`}
        </small>
        <div className="frame-actions">
          {frame.url && (
            <a className="button ghost" href={frame.url} download={`frame-${frame.index + 1}.png`}>
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
