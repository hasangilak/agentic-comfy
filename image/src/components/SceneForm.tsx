import { useRef, useState } from 'react'

import {
  ASPECT_PRESETS,
  MAX_FRAMES,
  MIN_FRAMES,
  type ConsistencyMode,
  type SceneSettings,
} from '../../shared/types.ts'
import { api } from '../api.ts'
import { estimateSceneSeconds, formatDuration } from '../estimate.ts'

const CONSISTENCY_OPTIONS: { id: ConsistencyMode; label: string; blurb: string }[] = [
  { id: 'chain', label: 'Chain', blurb: 'Each frame builds on the one before. Best motion flow.' },
  { id: 'anchor', label: 'Anchor', blurb: 'Every frame follows one reference. Style locked, poses free.' },
  { id: 'none', label: 'Free', blurb: 'Text only. Fastest, loosest continuity.' },
]

interface Props {
  value: SceneSettings
  onChange: (next: SceneSettings) => void
  onSubmit: () => void
  submitLabel: string
  busy: boolean
  dirty?: boolean
}

export function SceneForm({ value, onChange, onSubmit, submitLabel, busy, dirty }: Props) {
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)

  const set = <K extends keyof SceneSettings>(key: K, next: SceneSettings[K]) =>
    onChange({ ...value, [key]: next })

  const fps = value.frameCount / value.duration
  const hold = value.duration / value.frameCount
  const estimate = estimateSceneSeconds(value)

  async function handleFile(file: File | undefined) {
    if (!file) return
    setUploading(true)
    setUploadError(null)
    try {
      const result = await api.upload(file)
      onChange({ ...value, referencePath: result.path, referenceUrl: result.url })
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : String(err))
    } finally {
      setUploading(false)
    }
  }

  return (
    <form
      className="panel scene-form"
      onSubmit={(event) => {
        event.preventDefault()
        onSubmit()
      }}
    >
      <label className="field">
        <span>Scene title</span>
        <input
          value={value.title}
          onChange={(e) => set('title', e.target.value)}
          placeholder="Pig crosses the meadow"
        />
      </label>

      <label className="field">
        <span>What happens in the scene</span>
        <textarea
          rows={4}
          value={value.description}
          onChange={(e) => set('description', e.target.value)}
          placeholder="A paper pig walks left to right across a lush meadow, ears flapping, as a paper butterfly loops overhead"
        />
      </label>

      <label className="field">
        <span>
          Scene length <b>{value.duration}s</b>
        </span>
        <input
          type="range"
          min={1}
          max={30}
          step={1}
          value={value.duration}
          onChange={(e) => set('duration', Number(e.target.value))}
        />
      </label>

      <div className="field">
        <span>
          Frames <b>{value.frameCount}</b>
        </span>
        <div className="segmented">
          {Array.from({ length: MAX_FRAMES - MIN_FRAMES + 1 }, (_, i) => i + MIN_FRAMES).map((n) => (
            <button
              key={n}
              type="button"
              className={n === value.frameCount ? 'active' : ''}
              onClick={() => set('frameCount', n)}
            >
              {n}
            </button>
          ))}
        </div>
        <small className="hint">
          {hold.toFixed(2)}s per frame · {fps.toFixed(1)} fps · est. {formatDuration(estimate)} to render
        </small>
      </div>

      <div className="field">
        <span>Consistency</span>
        <div className="segmented wide">
          {CONSISTENCY_OPTIONS.map((option) => (
            <button
              key={option.id}
              type="button"
              className={option.id === value.consistency ? 'active' : ''}
              onClick={() => set('consistency', option.id)}
            >
              {option.label}
            </button>
          ))}
        </div>
        <small className="hint">
          {CONSISTENCY_OPTIONS.find((o) => o.id === value.consistency)?.blurb}
        </small>
      </div>

      <div className="field">
        <span>Reference image {value.consistency === 'none' && <em>(ignored in Free mode)</em>}</span>
        <div className="reference">
          {value.referenceUrl ? (
            <img src={value.referenceUrl} alt="Uploaded reference image" />
          ) : (
            <div className="reference-empty">None</div>
          )}
          <div className="reference-actions">
            <input
              ref={fileInput}
              type="file"
              accept="image/*"
              hidden
              onChange={(e) => void handleFile(e.target.files?.[0])}
            />
            <button type="button" onClick={() => fileInput.current?.click()} disabled={uploading}>
              {uploading ? 'Uploading…' : value.referenceUrl ? 'Replace' : 'Upload'}
            </button>
            {value.referenceUrl && (
              <button
                type="button"
                className="ghost"
                onClick={() => onChange({ ...value, referencePath: undefined, referenceUrl: undefined })}
              >
                Clear
              </button>
            )}
          </div>
        </div>
        {uploadError && (
          <small className="error" role="alert">
            {uploadError}
          </small>
        )}
      </div>

      <details className="advanced">
        <summary>Look and quality</summary>

        <label className="field">
          <span>Style suffix — appended to every frame</span>
          <textarea rows={3} value={value.style} onChange={(e) => set('style', e.target.value)} />
        </label>

        <label className="field">
          <span>Negative prompt</span>
          <input
            value={value.negativePrompt}
            onChange={(e) => set('negativePrompt', e.target.value)}
            placeholder="leave empty — it doubles render time"
          />
        </label>

        <div className="field-row">
          <label className="field">
            <span>Aspect</span>
            <select value={value.aspectId} onChange={(e) => set('aspectId', e.target.value)}>
              {ASPECT_PRESETS.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.label} · {a.width}×{a.height}
                </option>
              ))}
            </select>
          </label>

          <label className="field">
            <span>
              Steps <b>{value.steps}</b>
            </span>
            <input
              type="range"
              min={2}
              max={12}
              step={1}
              value={value.steps}
              onChange={(e) => set('steps', Number(e.target.value))}
            />
          </label>
        </div>

        <div className="field-row">
          <label className="field">
            <span>Seed</span>
            <input
              type="number"
              value={value.seed}
              onChange={(e) => set('seed', Number(e.target.value))}
              min={0}
            />
          </label>
          <label className="field checkbox">
            <input
              type="checkbox"
              checked={value.varySeeds}
              onChange={(e) => set('varySeeds', e.target.checked)}
            />
            <span>Vary seed per frame</span>
          </label>
        </div>
        <small className="hint">
          A shared seed keeps frames closer together; varying it gives each frame more independent motion.
        </small>
      </details>

      <button
        className={dirty === false ? 'ghost' : 'primary'}
        type="submit"
        disabled={busy || dirty === false}
      >
        {submitLabel}
      </button>
    </form>
  )
}
