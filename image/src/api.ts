import type { Scene, SceneSettings } from '../shared/types.ts'

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({ error: res.statusText }))
    throw new Error(body.error ?? res.statusText)
  }
  return res.json() as Promise<T>
}

const headers = { 'Content-Type': 'application/json' }

export const api = {
  listScenes: () => fetch('/api/scenes').then(json<Scene[]>),

  getScene: (id: string) => fetch(`/api/scenes/${id}`).then(json<Scene>),

  createScene: (settings: SceneSettings, beats?: string[]) =>
    fetch('/api/scenes', { method: 'POST', headers, body: JSON.stringify({ ...settings, beats }) }).then(json<Scene>),

  updateScene: (id: string, patch: Partial<SceneSettings> & { beats?: string[] }) =>
    fetch(`/api/scenes/${id}`, { method: 'PATCH', headers, body: JSON.stringify(patch) }).then(json<Scene>),

  resetBeats: (id: string) => fetch(`/api/scenes/${id}/beats/reset`, { method: 'POST' }).then(json<Scene>),

  render: (id: string, frames?: number[]) =>
    fetch(`/api/scenes/${id}/render`, { method: 'POST', headers, body: JSON.stringify({ frames }) }).then(json<Scene>),

  cancel: (id: string) => fetch(`/api/scenes/${id}/cancel`, { method: 'POST' }).then(json<Scene | null>),

  remove: (id: string) => fetch(`/api/scenes/${id}`, { method: 'DELETE' }).then(json<{ ok: true }>),

  upload: async (file: File) => {
    const form = new FormData()
    form.append('image', file)
    return fetch('/api/upload', { method: 'POST', body: form }).then(json<{ path: string; url: string }>)
  },

  downloadUrl: (id: string) => `/api/scenes/${id}/download`,
}
