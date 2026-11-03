import { pinyin } from 'pinyin-pro'

function rememberRelativePath(file, relativePath) {
  try {
    Object.defineProperty(file, 'designRagRelativePath', {
      value: relativePath,
      configurable: true,
    })
  } catch {
    // Some browser File implementations are not extensible; the file name remains usable.
  }
  return file
}

function readFileEntry(entry, parentPath) {
  return new Promise((resolve, reject) => {
    entry.file(
      (file) => resolve([rememberRelativePath(file, `${parentPath}${file.name}`)]),
      reject,
    )
  })
}

function readDirectoryBatch(reader) {
  return new Promise((resolve, reject) => reader.readEntries(resolve, reject))
}

async function readDirectoryEntry(entry, parentPath) {
  const directoryPath = `${parentPath}${entry.name}/`
  const reader = entry.createReader()
  const entries = []
  while (true) {
    const batch = await readDirectoryBatch(reader)
    if (!batch.length) break
    entries.push(...batch)
  }
  const nested = await Promise.all(entries.map((child) => readEntry(child, directoryPath)))
  return nested.flat()
}

async function readEntry(entry, parentPath = '') {
  if (entry.isFile) return readFileEntry(entry, parentPath)
  if (entry.isDirectory) return readDirectoryEntry(entry, parentPath)
  return []
}

export async function collectDroppedFiles(dataTransfer) {
  const items = [...(dataTransfer?.items || [])].filter((item) => item.kind === 'file')
  const entries = items.map((item) => item.webkitGetAsEntry?.()).filter(Boolean)
  if (!entries.length) return [...(dataTransfer?.files || [])]
  const nested = await Promise.all(entries.map((entry) => readEntry(entry)))
  return nested.flat()
}

export function fileDisplayPath(file) {
  return file.designRagRelativePath || file.webkitRelativePath || file.name
}
function asciiKebab(value) {
  return String(value || '')
    .normalize('NFKC')
    .toLowerCase()
    .replace(/ü/g, 'v')
    .normalize('NFKD')
    .replace(/\p{M}/gu, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
}

/** Convert a display name into a stable ASCII identifier without changing the display name. */
export function toProjectExternalKey(projectName) {
  const tokens = String(projectName || '').normalize('NFKC').match(/[\p{Script=Han}]+|[\p{L}\p{N}]+/gu) || []
  return tokens
    .flatMap((token) => {
      if (/^[\p{Script=Han}]+$/u.test(token)) {
        return pinyin(token, { toneType: 'none', type: 'array' }).map(asciiKebab).filter(Boolean)
      }
      const ascii = asciiKebab(token)
      return ascii ? [ascii] : []
    })
    .join('-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
}

export function inferProjectDefaults(files) {
  const rootFolders = new Set()
  for (const file of files || []) {
    const segments = fileDisplayPath(file).replace(/\\/g, '/').split('/').filter(Boolean)
    if (segments.length < 2) return null
    rootFolders.add(segments[0])
  }
  if (rootFolders.size !== 1) return null
  const projectName = [...rootFolders][0]
  const projectExternalKey = toProjectExternalKey(projectName)
  return projectExternalKey ? { projectName, projectExternalKey } : null
}