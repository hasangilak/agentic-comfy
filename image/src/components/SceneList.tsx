import type { Scene } from '../../shared/types.ts'
import { IconTrash } from './icons.tsx'

interface Props {
  scenes: Scene[]
  activeId: string | null
  loading: boolean
  onSelect: (id: string) => void
  onNew: () => void
  onDelete: (id: string) => void
}

export function SceneList({ scenes, activeId, loading, onSelect, onNew, onDelete }: Props) {
  return (
    <aside className="scene-list" aria-label="Scenes">
      <button className="primary" type="button" onClick={onNew}>
        New scene
      </button>

      {loading && <p className="hint">Loading scenes…</p>}
      {!loading && !scenes.length && (
        <p className="hint">No scenes yet. The first one you create lands here.</p>
      )}

      <ul>
        {scenes.map((scene) => {
          const done = scene.frames.filter((f) => f.status === 'done').length
          const active = scene.id === activeId
          return (
            <li key={scene.id} className={active ? 'active' : ''}>
              <button
                type="button"
                className="scene-pick"
                aria-current={active ? 'true' : undefined}
                onClick={() => onSelect(scene.id)}
              >
                <strong>{scene.title || 'Untitled scene'}</strong>
                <small>
                  {done}/{scene.frames.length} frames · {scene.duration}s · {scene.status}
                </small>
              </button>
              <button
                type="button"
                className="ghost tiny scene-delete"
                aria-label={`Delete ${scene.title || 'untitled scene'}`}
                onClick={() => onDelete(scene.id)}
              >
                <IconTrash />
              </button>
            </li>
          )
        })}
      </ul>
    </aside>
  )
}
