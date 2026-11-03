import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { build } from 'vite'

const scriptDirectory = dirname(fileURLToPath(import.meta.url))
const adminRoot = resolve(scriptDirectory, '..')
const writer = resolve(scriptDirectory, 'write-vite-output.py')
const python = process.env.DESIGN_RAG_PYTHON
  || process.env.PYTHON
  || resolve(adminRoot, '..', '..', '.venv', 'Scripts', 'python.exe')

function writeOutput(records) {
  return new Promise((resolveWrite, rejectWrite) => {
    const child = spawn(python, [writer, resolve(adminRoot, 'dist')], {
      cwd: adminRoot,
      stdio: ['pipe', 'inherit', 'inherit'],
      windowsHide: true,
    })
    child.once('error', (error) => rejectWrite(new Error(`Could not start Python build writer (${python}): ${error.message}`)))
    child.once('exit', (code) => {
      if (code === 0) resolveWrite()
      else rejectWrite(new Error(`Python build writer exited with code ${code ?? 'unknown'}`))
    })
    for (const record of records) child.stdin.write(`${JSON.stringify(record)}\n`)
    child.stdin.end()
  })
}

const buildOutput = await build({
  configFile: resolve(adminRoot, 'vite.config.js'),
  build: { write: false },
})
const records = []
for (const output of Array.isArray(buildOutput) ? buildOutput : [buildOutput]) {
  for (const entry of output.output) {
    const content = entry.type === 'asset' ? entry.source : entry.code
    records.push({ fileName: entry.fileName, data: Buffer.from(content).toString('base64') })
  }
}
await writeOutput(records)
