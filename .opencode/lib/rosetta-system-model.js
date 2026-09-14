import { execFileSync } from "node:child_process"
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs"
import path from "node:path"

export const STATUS = Object.freeze({
  IDLE: "idle",
  CHANGED: "changed",
  VERIFIED: "verified",
})

// How many of the newest proofs the sidebar lists under `proofs/`.
export const RECENT_PROOFS = 5

export function readJsonFiles(directory) {
  if (!existsSync(directory)) return []
  const rows = []
  for (const name of readdirSync(directory).sort()) {
    if (!name.endsWith(".json")) continue
    const file = path.join(directory, name)
    try {
      const value = JSON.parse(readFileSync(file, "utf8"))
      if (value && typeof value === "object" && !Array.isArray(value)) {
        rows.push({ ...value, _file: file, _name: name })
      }
    } catch {
      // A writer may still be completing the file. The next refresh retries it.
    }
  }
  return rows
}

export function databaseArtifactStatus(item) {
  if (item?.verified === true) return STATUS.VERIFIED
  return STATUS.CHANGED
}

export function aggregateStatus(statuses) {
  if (statuses.includes(STATUS.CHANGED)) return STATUS.CHANGED
  if (statuses.includes(STATUS.VERIFIED)) return STATUS.VERIFIED
  return STATUS.IDLE
}

export function normalizeEvent(event) {
  const data = event?.properties || event?.data || {}
  return {
    type: String(event?.type || ""),
    file: typeof data.file === "string" ? data.file : "",
    action: typeof data.event === "string" ? data.event : "change",
  }
}

export function inferIdentity({ project, explicitName }) {
  if (explicitName?.trim()) return explicitName.trim()
  return path.basename(path.resolve(project)) || "Project"
}

// Fallback exclusions for projects that are not Git repositories. Git-backed
// projects use `git ls-files`, which already applies the repository's own ignore
// rules and therefore does not need Rosetta to guess which product directories
// matter.
const PROJECT_IGNORED = new Set([
  ".git",
  ".next",
  ".rosetta",
  ".vercel",
  "node_modules",
  "dist",
  "build",
  "coverage",
  "target",
  "__pycache__",
  ".venv",
  "venv",
])

export const PROJECT_DIRECTORY_BUDGET = 7
export const PROJECT_FILE_BUDGET = 10
const PROJECT_SCAN_LIMIT = 5000

const SOURCE_EXTENSIONS = new Set([
  ".c", ".cc", ".cpp", ".cs", ".css", ".go", ".h", ".hpp", ".html", ".java",
  ".js", ".json", ".jsx", ".kt", ".m", ".md", ".php", ".py", ".rb", ".rs",
  ".sh", ".sql", ".swift", ".toml", ".ts", ".tsx", ".vue", ".yaml", ".yml", ".zig",
])

const KEY_FILES = /^(agents\.md|readme(?:\.[^.]+)?|rosetta\.json|package\.json|pyproject\.toml|cargo\.toml|go\.mod|makefile|dockerfile)$/i
const ENTRY_FILES = /^(?:app|cli|index|main|server|test|verify)(?:\.[^.]+)$/i
const LOW_VALUE_FILES = /^(?:license|copying|notice|gnu-fdl)(?:[._-]|$)|(?:^|[._-])lock(?:[._-]|$)/i

function nul(command, args, cwd, maxBuffer = 8_000_000) {
  return execFileSync(command, args, { cwd, encoding: "buffer", maxBuffer, stdio: ["ignore", "pipe", "ignore"] })
    .toString("utf8")
    .split("\0")
    .filter(Boolean)
}

function gitProjectFiles(directory) {
  try {
    return nul("git", ["ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", "."], directory)
      .slice(0, PROJECT_SCAN_LIMIT)
  } catch {
    return null
  }
}

function gitProjectChanges(directory) {
  const changed = new Map()
  try {
    const records = nul("git", ["status", "--porcelain=v1", "-z", "--untracked-files=all", "--", "."], directory)
    for (let index = 0; index < records.length; index += 1) {
      const record = records[index]
      if (record.length < 4) continue
      const code = record.slice(0, 2)
      const relative = record.slice(3)
      const change = code.includes("?") ? "new"
        : code.includes("D") ? "deleted"
          : code.includes("R") ? "renamed"
            : "edited"
      changed.set(relative, change)
      // In -z mode Git follows a rename/copy record with the original path.
      if (code.includes("R") || code.includes("C")) index += 1
    }
  } catch {
    // A non-Git project still gets a filesystem map and live session changes.
  }
  return changed
}

export function walkProject(directory) {
  if (!existsSync(directory)) return []
  const files = []
  const visit = (current) => {
    if (files.length >= PROJECT_SCAN_LIMIT) return
    let entries
    try {
      entries = readdirSync(current, { withFileTypes: true })
    } catch {
      return
    }
    for (const item of entries) {
      if (item.isDirectory()) {
        if (PROJECT_IGNORED.has(item.name)) continue
        visit(path.join(current, item.name))
        continue
      }
      if (!item.isFile()) continue
      const file = path.join(current, item.name)
      files.push({
        file,
        relative: path.relative(directory, file) || item.name,
        name: item.name,
        mtimeMs: statSync(file).mtimeMs,
      })
    }
  }
  visit(directory)
  return files.sort((a, b) => a.relative.localeCompare(b.relative))
}

export function projectFileStatus(_item, modified = false) {
  return modified ? STATUS.CHANGED : STATUS.IDLE
}

function filePriority(item) {
  if (item.status === STATUS.CHANGED) return 100
  if (KEY_FILES.test(item.name)) return 50
  if (ENTRY_FILES.test(item.name)) return 30
  if (LOW_VALUE_FILES.test(item.name)) return -10
  if (SOURCE_EXTENSIONS.has(path.extname(item.name).toLowerCase())) return 20
  return 0
}

export function projectDirectoryTree(files, {
  directoryBudget = PROJECT_DIRECTORY_BUDGET,
  fileBudget = PROJECT_FILE_BUDGET,
  perDirectory = 4,
} = {}) {
  const groups = new Map()
  for (const item of files) {
    const parent = path.dirname(item.relative)
    const key = parent === "." ? "" : parent
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key).push(item)
  }
  const directories = [...groups.entries()].map(([key, items]) => ({
    key,
    items,
    changed: items.filter((item) => item.status === STATUS.CHANGED).length,
    priority: Math.max(0, ...items.map(filePriority)),
  })).sort((a, b) =>
    b.changed - a.changed ||
    (a.key === "" ? -1 : b.key === "" ? 1 : 0) ||
    b.priority - a.priority ||
    a.key.split(path.sep).length - b.key.split(path.sep).length ||
    a.key.localeCompare(b.key)
  )

  let remaining = fileBudget
  const tree = directories.slice(0, directoryBudget).map(({ key, items }) => {
    const ranked = [...items].sort((a, b) =>
      filePriority(b) - filePriority(a) || a.name.localeCompare(b.name)
    )
    const useful = ranked.filter((item) => filePriority(item) > 0)
    const count = Math.min(perDirectory, remaining, useful.length)
    const children = useful.slice(0, count).map((item) => ({ ...item, name: path.basename(item.relative) }))
    remaining -= children.length
    return {
      key,
      label: key ? `${key}/` : "./",
      count: items.length,
      changed: items.filter((item) => item.status === STATUS.CHANGED).length,
      status: aggregateStatus(items.map((item) => item.status)),
      children,
      hidden: items.length - children.length,
    }
  })
  return {
    tree,
    hiddenDirectories: Math.max(0, directories.length - tree.length),
    totalDirectories: directories.length,
  }
}

// The compact directory summary above is useful for status rollups, but it is
// not an explorer: omitted directories cannot be opened.  Keep a second,
// lossless representation for the interactive sidebar.  Every directory node
// contains its real children and rolls up the status of every descendant file.
export function projectExplorerTree(files) {
  const root = { key: "", name: "", directories: new Map(), files: [] }

  for (const item of files) {
    const parts = String(item.relative || item.name || "")
      .split(/[\\/]+/)
      .filter(Boolean)
    if (!parts.length) continue

    let current = root
    for (const part of parts.slice(0, -1)) {
      const key = current.key ? `${current.key}/${part}` : part
      if (!current.directories.has(part)) {
        current.directories.set(part, { key, name: part, directories: new Map(), files: [] })
      }
      current = current.directories.get(part)
    }
    current.files.push({
      ...item,
      type: "file",
      key: item.relative,
      name: parts.at(-1),
    })
  }

  const build = (directory) => {
    const directories = [...directory.directories.values()].map((child) => {
      const children = build(child)
      const descendantFiles = children.flatMap((node) =>
        node.type === "file" ? [node] : node.descendantFiles
      )
      return {
        type: "directory",
        key: child.key,
        name: child.name,
        label: `${child.name}/`,
        children,
        descendantFiles,
        count: descendantFiles.length,
        changed: descendantFiles.filter((item) => item.status === STATUS.CHANGED).length,
        status: aggregateStatus(descendantFiles.map((item) => item.status)),
        priority: Math.max(0, ...descendantFiles.map(filePriority)),
      }
    })

    const nodes = [...directories, ...directory.files]
    return nodes.sort((a, b) =>
      Number(b.status === STATUS.CHANGED) - Number(a.status === STATUS.CHANGED) ||
      Number(b.type === "directory") - Number(a.type === "directory") ||
      (b.priority || filePriority(b)) - (a.priority || filePriority(a)) ||
      a.name.localeCompare(b.name)
    )
  }

  const stripInternalFiles = (nodes) => nodes.map((node) => {
    if (node.type === "file") return node
    const { descendantFiles: _descendantFiles, priority: _priority, ...visible } = node
    return { ...visible, children: stripInternalFiles(node.children) }
  })

  return stripInternalFiles(build(root))
}

export function defaultExpandedDirectories(files) {
  const expanded = new Set()
  for (const item of files) {
    if (item.status !== STATUS.CHANGED) continue
    const parts = String(item.relative || "").split(/[\\/]+/).filter(Boolean)
    for (let depth = 1; depth < parts.length; depth += 1) {
      expanded.add(parts.slice(0, depth).join("/"))
    }
  }
  return [...expanded]
}

function projectView(files) {
  const summary = projectDirectoryTree(files)
  return {
    files,
    ...summary,
    changed: files.filter((item) => item.status === STATUS.CHANGED).length,
    status: aggregateStatus(files.map((item) => item.status)),
  }
}

export function scanProjectDirectory({ project, modifiedFiles = new Set() }) {
  const listed = gitProjectFiles(project)
  const changes = gitProjectChanges(project)
  const discovered = listed === null ? walkProject(project) : listed.map((relative) => {
    const file = path.join(project, relative)
    let mtimeMs = 0
    try {
      mtimeMs = statSync(file).mtimeMs
    } catch {
      // Deleted tracked files remain useful worktree information.
    }
    return { file, relative, name: path.basename(relative), mtimeMs }
  })
  const files = discovered.map((item) => {
    const projectRelative = path.relative(project, item.file)
    const change = changes.get(item.relative)
    const modified = Boolean(change) || modifiedFiles.has(item.file) || modifiedFiles.has(projectRelative) || modifiedFiles.has(item.relative)
    return { ...item, change: change || (modified ? "edited" : ""), status: projectFileStatus(item, modified) }
  })
  return projectView(files)
}

export function referenceSummary(project) {
  let current = path.resolve(project)
  let configPath = ""
  for (;;) {
    const candidate = path.join(current, "rosetta.json")
    if (existsSync(candidate)) {
      configPath = candidate
      break
    }
    if (existsSync(path.join(current, ".git"))) break
    const parent = path.dirname(current)
    if (parent === current) break
    current = parent
  }
  const root = configPath ? path.dirname(configPath) : current
  let pending = []
  let requestError = ""
  const requestPath = path.join(root, ".rosetta", "reference-requests.json")
  if (existsSync(requestPath)) {
    try {
      const document = JSON.parse(readFileSync(requestPath, "utf8"))
      if (document.version !== 1 || !Array.isArray(document.requests)) throw new Error("invalid reference request state")
      pending = document.requests
        .filter((request) => request && typeof request.language === "string" && request.language.trim())
        .map((request) => ({ language: request.language.trim(), reason: String(request.reason || "") }))
    } catch (error) {
      requestError = error instanceof Error ? error.message : String(error)
    }
  }
  if (!configPath) return { config: "", sources: [], commands: [], pending, error: requestError }
  try {
    const config = JSON.parse(readFileSync(configPath, "utf8"))
    const sources = Array.isArray(config.sources) ? config.sources
      .filter((source) => source && typeof source === "object")
      .map((source) => {
        const target = typeof source.path === "string" ? path.resolve(root, source.path) : ""
        return {
          id: String(source.id || "reference"),
          title: String(source.title || source.id || "Reference"),
          kind: String(source.kind || "documentation"),
          language: String(source.language || ""),
          available: Boolean(target && existsSync(target)),
        }
      }) : []
    const commands = config.commands && typeof config.commands === "object" && !Array.isArray(config.commands)
      ? Object.entries(config.commands).filter(([, argv]) => Array.isArray(argv)).map(([name, argv]) => ({
        name,
        command: argv.map(String).join(" "),
      }))
      : []
    return { config: configPath, sources, commands, pending, error: requestError }
  } catch (error) {
    return { config: configPath, sources: [], commands: [], pending, error: error instanceof Error ? error.message : String(error) }
  }
}

export function scanSystem({ project, modifiedFiles = new Set(), explicitName = "" }) {
  // Inventory the project once without trying to infer a language or carve out
  // a special corpus. The agent's available tools handle unfamiliar source.
  const completeProject = scanProjectDirectory({ project, modifiedFiles })
  const proofs = readJsonFiles(path.join(project, ".rosetta", "proofs"))
    .filter((item) => item.schema === "rosetta-proof/v1")
  const rawChanges = readJsonFiles(path.join(project, ".rosetta", "changes"))
  const verifiedChanges = new Set(
    rawChanges.filter((item) => item.verified === true).map((item) => item.change_id).filter(Boolean),
  )
  const changes = rawChanges.map((item) => ({
    ...item,
    status: verifiedChanges.has(item.change_id) ? STATUS.VERIFIED : databaseArtifactStatus(item),
  }))
  const snapshots = changes.filter((item) => typeof item.snapshot_id === "string" && item.snapshot_id)
  const globalState = aggregateStatus(changes.map((item) => item.status))
  const proofState = aggregateStatus(
    proofs.map((item) => item.equivalent === true ? STATUS.VERIFIED : STATUS.CHANGED),
  )

  return {
    projectName: path.basename(path.resolve(project)) || "Project",
    identity: inferIdentity({ project, explicitName }),
    project: completeProject,
    references: referenceSummary(project),
    proofs,
    // Proofs accumulate forever -- one file per verifier run. The panel shows
    // the newest few; the section header still carries the true total.
    recentProofs: proofs.slice(-RECENT_PROOFS).reverse(),
    changes,
    snapshots,
    status: {
      globals: globalState,
      proofs: proofState,
      system: aggregateStatus([completeProject.status, globalState, proofState]),
    },
  }
}
