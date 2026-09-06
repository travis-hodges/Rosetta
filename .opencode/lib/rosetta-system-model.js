import { createHash } from "node:crypto"
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs"
import path from "node:path"

export const STATUS = Object.freeze({
  IDLE: "idle",
  CHANGED: "changed",
  VERIFIED: "verified",
})

const IGNORED_DIRECTORIES = new Set([
  ".git",
  ".claude",
  "__pycache__",
  "node_modules",
  ".venv",
  "venv",
  "dist",
])

export function digestFile(file) {
  return createHash("sha256").update(readFileSync(file)).digest("hex")
}

export function newestBy(items, key) {
  const found = new Map()
  for (const item of items) {
    const name = key(item)
    if (!name) continue
    const prior = found.get(name)
    if (!prior || String(item.created_at || item.applied_at || item.rolled_back_at || "") >=
      String(prior.created_at || prior.applied_at || prior.rolled_back_at || "")) {
      found.set(name, item)
    }
  }
  return found
}

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

export function walkRoutines(directory) {
  if (!existsSync(directory)) return []
  const files = []
  const visit = (current) => {
    for (const item of readdirSync(current, { withFileTypes: true })) {
      if (item.isDirectory()) {
        if (!IGNORED_DIRECTORIES.has(item.name)) visit(path.join(current, item.name))
        continue
      }
      if (item.isFile() && item.name.toLowerCase().endsWith(".m")) {
        const file = path.join(current, item.name)
        files.push({
          file,
          relative: path.relative(directory, file) || item.name,
          routine: item.name.slice(0, -2).toUpperCase(),
          mtimeMs: statSync(file).mtimeMs,
        })
      }
    }
  }
  visit(directory)
  return files.sort((a, b) => a.relative.localeCompare(b.relative))
}

export function routineStatus(item, proofByRoutine, modified = false) {
  const proof = proofByRoutine.get(item.routine)
  if (proof?.equivalent === true && proof.candidate_sha256) {
    try {
      if (digestFile(item.file) === proof.candidate_sha256) return STATUS.VERIFIED
    } catch {
      return modified ? STATUS.CHANGED : STATUS.IDLE
    }
  }
  if (modified) return STATUS.CHANGED
  return STATUS.IDLE
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

export function resolveRoutineRoot(project, corpus) {
  const selected = path.resolve(corpus || project)
  const bundled = path.join(selected, "data", "routines")
  if (existsSync(bundled)) return bundled
  return selected
}

export function inferIdentity({ project, corpus, explicitName }) {
  if (explicitName?.trim()) return explicitName.trim()
  const routineRoot = resolveRoutineRoot(project, corpus)
  const normalized = routineRoot.split(path.sep).join("/").toLowerCase()
  if (normalized.endsWith("/data/routines") || normalized.includes("/vista")) return "VA VistA"
  return path.basename(path.resolve(corpus || project)) || "Legacy system"
}

export function scanSystem({ project, corpus, modifiedFiles = new Set(), explicitName = "" }) {
  const routineRoot = resolveRoutineRoot(project, corpus)
  const proofs = readJsonFiles(path.join(project, ".rosetta", "proofs"))
    .filter((item) => item.schema === "rosetta-proof/v1")
  const proofByRoutine = newestBy(proofs, (item) => String(item.routine || "").toUpperCase())
  const routines = walkRoutines(routineRoot).map((item) => {
    const projectRelative = path.relative(project, item.file)
    const modified = modifiedFiles.has(item.file) || modifiedFiles.has(projectRelative) || modifiedFiles.has(item.relative)
    return { ...item, status: routineStatus(item, proofByRoutine, modified) }
  })
  const rawChanges = readJsonFiles(path.join(project, ".rosetta", "changes"))
  const verifiedChanges = new Set(
    rawChanges.filter((item) => item.verified === true).map((item) => item.change_id).filter(Boolean),
  )
  const changes = rawChanges.map((item) => ({
    ...item,
    status: verifiedChanges.has(item.change_id) ? STATUS.VERIFIED : databaseArtifactStatus(item),
  }))
  const snapshots = changes.filter((item) => typeof item.snapshot_id === "string" && item.snapshot_id)
  const routineState = aggregateStatus(routines.map((item) => item.status))
  const globalState = aggregateStatus(changes.map((item) => item.status))
  const proofState = aggregateStatus(
    [...proofByRoutine.values()].map((item) => item.equivalent === true ? STATUS.VERIFIED : STATUS.CHANGED),
  )

  return {
    identity: inferIdentity({ project, corpus, explicitName }),
    routineRoot,
    routines,
    proofs,
    changes,
    snapshots,
    status: {
      routines: routineState,
      globals: globalState,
      proofs: proofState,
      system: aggregateStatus([routineState, globalState]),
    },
  }
}
