import type { Scene } from '../../shared/types.ts'

interface Props {
  scenes: Scene[]
  activeId: string | null
  onSelect: (id: string) => void
  onNew: () => void
  onDelete: (id: string) => void
}

export function SceneList({ scenes, activeId, onSelect, onNew, onDelete }: Props) {
  return (
    <aside className="scene-list">
      <button className="primary" type="button" onClick={onNew}>
        New scene
      </button>

      {!scenes.length && <p className="hint">No scenes yet.</p>}

      <ul>
        {scenes.map((scene) => {
          const done = scene.frames.filter((f) => f.status === 'done').length
          return (
            <li key={scene.id} className={scene.id === activeId ? 'active' : ''}>
              <button type="button" className="scene-pick" onClick={() => onSelect(scene.id)}>
                <strong>{scene.title || 'Untitled scene'}</strong>
                <small>
                  {done}/{scene.frames.length} frames · {scene.duration}s · {scene.status}
                </small>
              </button>
              <button
                type="button"
                className="ghost tiny"
                title="Delete scene"
                onClick={() => onDelete(scene.id)}
              >
                ✕
              </button>
            </li>
          )
        })}
      </ul>
    </aside>
  )
}
