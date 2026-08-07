import { useEffect, useRef, useState } from 'react'

import type { Scene, SceneEvent } from '../shared/types.ts'

const MAX_LOG_LINES = 300

/** Subscribes to the scene's SSE stream and keeps a throttled log tail. */
export function useSceneStream(id: string | null) {
  const [scene, setScene] = useState<Scene | null>(null)
  const [logs, setLogs] = useState<string[]>([])
  const [connected, setConnected] = useState(false)
  const pending = useRef<string[]>([])

  useEffect(() => {
    if (!id) {
      setScene(null)
      setLogs([])
      setConnected(false)
      return
    }

    const source = new EventSource(`/api/scenes/${id}/events`)
    source.onopen = () => setConnected(true)
    source.onerror = () => setConnected(false)
    source.onmessage = (event) => {
      const payload = JSON.parse(event.data) as SceneEvent
      if (payload.type === 'scene' && payload.scene) setScene(payload.scene)
      if (payload.type === 'log' && payload.line) pending.current.push(payload.line)
    }

    // mflux emits a tqdm line per tick; flush on a timer so React is not re-rendered per line.
    const flush = setInterval(() => {
      if (!pending.current.length) return
      const batch = pending.current
      pending.current = []
      setLogs((prev) => [...prev, ...batch].slice(-MAX_LOG_LINES))
    }, 250)

    return () => {
      clearInterval(flush)
      source.close()
      pending.current = []
    }
  }, [id])

  return { scene, setScene, logs, clearLogs: () => setLogs([]), connected }
}

/** Persists a value in localStorage so a reload does not lose the selected scene. */
export function useStored<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key)
      return raw === null ? initial : (JSON.parse(raw) as T)
    } catch {
      return initial
    }
  })

  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(value))
    } catch {
      // Storage full or blocked; the app still works without persistence.
    }
  }, [key, value])

  return [value, setValue] as const
}
