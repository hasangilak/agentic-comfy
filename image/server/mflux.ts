import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'

export interface RenderRequest {
  prompt: string
  negativePrompt?: string
  outputPath: string
  width: number
  height: number
  steps: number
  seed: number
  /** When present, mflux-generate-flux2-edit is used with these as conditioning images. */
  referencePaths?: string[]
}

export interface RenderHandle {
  promise: Promise<void>
  cancel(): void
}

const MODEL = 'flux2-klein-4b'
const QUANTIZE = '8'

const TEXT_TO_IMAGE = 'mflux-generate-flux2'
const IMAGE_EDIT = 'mflux-generate-flux2-edit'

// tqdm renders "  50%|████      | 2/4 [00:03<00:03,  1.97s/it]" onto one line with \r.
const PERCENT_RE = /(\d{1,3})%\|/
const STEP_RE = /(\d+)\/(\d+)\s*\[/

function parseProgress(line: string): number | null {
  const percent = line.match(PERCENT_RE)
  if (percent) return Math.min(1, Number(percent[1]) / 100)
  const step = line.match(STEP_RE)
  if (step && Number(step[2]) > 0) return Math.min(1, Number(step[1]) / Number(step[2]))
  return null
}

export function render(
  req: RenderRequest,
  onProgress: (fraction: number) => void,
  onLog: (line: string) => void,
): RenderHandle {
  const isEdit = !!req.referencePaths?.length
  const bin = isEdit ? IMAGE_EDIT : TEXT_TO_IMAGE

  const args = ['--model', MODEL, '-q', QUANTIZE]
  if (isEdit) args.push('--image-paths', ...req.referencePaths!)
  args.push(
    '--prompt', req.prompt,
    '--width', String(req.width),
    '--height', String(req.height),
    '--steps', String(req.steps),
    '--seed', String(req.seed),
    '--output', req.outputPath,
  )
  if (req.negativePrompt?.trim()) args.push('--negative-prompt', req.negativePrompt.trim())

  let child: ChildProcessWithoutNullStreams | null = spawn(bin, args, {
    env: { ...process.env, HF_HUB_ENABLE_HF_TRANSFER: '1' },
  })

  let cancelled = false
  let tail = ''

  const consume = (chunk: Buffer) => {
    // tqdm overwrites its line with \r, so split on both to see every update.
    const text = chunk.toString()
    for (const raw of text.split(/[\r\n]/)) {
      const line = raw.trim()
      if (!line) continue
      const fraction = parseProgress(line)
      if (fraction !== null) onProgress(fraction)
      onLog(line)
      tail = line
    }
  }

  const promise = new Promise<void>((resolve, reject) => {
    child!.stdout.on('data', consume)
    child!.stderr.on('data', consume)
    child!.on('error', (err) => {
      reject(new Error(`could not start ${bin}: ${err.message}`))
    })
    child!.on('close', (code, signal) => {
      child = null
      if (cancelled) return reject(new Error('cancelled'))
      if (code === 0) return resolve()
      reject(new Error(`${bin} exited with code ${code ?? signal}: ${tail || 'no output'}`))
    })
  })

  return {
    promise,
    cancel() {
      cancelled = true
      child?.kill('SIGTERM')
    },
  }
}
