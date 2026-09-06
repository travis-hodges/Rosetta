/** @jsxImportSource @opentui/solid */
import { createMemo, createSignal, For, Show } from "solid-js"
import type { TuiPlugin, TuiPluginApi, TuiPluginModule } from "@opencode-ai/plugin/tui"
import { watch } from "node:fs"
import path from "node:path"

import {
  STATUS,
  aggregateStatus,
  directoryTree,
  normalizeEvent,
  scanSystem,
} from "../lib/rosetta-system-model.js"

const project = path.resolve(process.env.ROSETTA_PROJECT_DIR || process.cwd())
const corpus = path.resolve(process.env.ROSETTA_CORPUS_DIR || project)
const runtime = process.env.ROSETTA_RUNTIME || "YottaDB"
const container = process.env.ROSETTA_CONTAINER || "rosetta-verify"
const instance = process.env.ROSETTA_INSTANCE || "vehu"
const language = process.env.ROSETTA_SYSTEM_LANGUAGE || "MUMPS"

const colors = (api: TuiPluginApi) => {
  const theme = api.theme.current
  return {
    idle: theme.textMuted,
    changed: theme.error,
    verified: theme.success,
    text: theme.text,
    muted: theme.textMuted,
    border: theme.border,
    panel: theme.backgroundPanel,
    accent: theme.primary,
    info: theme.info,
  }
}

const statusLabel = (status: string) => {
  if (status === STATUS.CHANGED) return "EDITED"
  if (status === STATUS.VERIFIED) return "VERIFIED"
  return "CLEAN"
}

const statusGlyph = (status: string) => status === STATUS.CHANGED ? "●" : status === STATUS.VERIFIED ? "◆" : "○"

function Node(props: { api: TuiPluginApi; label: string; status: string }) {
  const skin = () => colors(props.api)
  const tone = () => skin()[props.status as "idle" | "changed" | "verified"] || skin().muted
  return (
    <box border borderColor={tone()} flexGrow={1} flexShrink={1} minWidth={0} alignItems="center">
      <text fg={tone()} wrapMode="none"><b>{props.label}</b></text>
    </box>
  )
}

// Two 17-column boxes plus a joiner do not fit a ~34-column sidebar, and the
// overflow tore the panel's own right border, so flex shares the row instead of
// asserting a width. The identity box that used to sit above these two is gone:
// it repeated the panel header for four of the pane's two dozen rows, and those
// rows are the directory listing.
function Topology(props: { api: TuiPluginApi; snapshot: any }) {
  return (
    <box flexDirection="row" flexShrink={0}>
      <Node api={props.api} label="ROUTINES" status={props.snapshot.status.routines} />
      <Node api={props.api} label="GLOBALS" status={props.snapshot.status.globals} />
    </box>
  )
}

// The layout, not a file list. `directoryTree` has already collapsed the
// untouched bulk into a count per directory, so every section below -- the
// routines, the persistent changes, the snapshots and the proofs -- stays on
// screen at the same time. A section with nothing in it still shows its row,
// because "no persistent changes yet" is information about the system.
function Row(props: { api: TuiPluginApi; status: string; label: string; note?: string }) {
  const skin = () => colors(props.api)
  const tone = () => skin()[props.status as "idle" | "changed" | "verified"] || skin().muted
  return (
    <box flexDirection="row" justifyContent="space-between">
      <text fg={tone()} wrapMode="none">  {statusGlyph(props.status)} {props.label}</text>
      <Show when={props.note}>
        <text fg={tone()} wrapMode="none">{props.note}</text>
      </Show>
    </box>
  )
}

function Section(props: { api: TuiPluginApi; label: string; count: number; status?: string }) {
  const skin = () => colors(props.api)
  const tone = () => props.status
    ? (colors(props.api)[props.status as "idle" | "changed" | "verified"] || skin().muted)
    : skin().muted
  return (
    <box flexDirection="row" justifyContent="space-between">
      <text fg={skin().muted} wrapMode="none">▼ {props.label}</text>
      <text fg={tone()} wrapMode="none">{props.count}</text>
    </box>
  )
}

function Directory(props: { api: TuiPluginApi; snapshot: any }) {
  const skin = () => colors(props.api)
  return (
    <box flexDirection="column" flexGrow={1} flexShrink={1}>
      <text fg={skin().text} flexShrink={0}><b>SYSTEM DIRECTORY</b></text>

      <box flexDirection="column" flexShrink={1} overflow="hidden">
      <For each={props.snapshot.tree}>
        {(dir: any) => (
          <box flexDirection="column" flexShrink={0}>
            <Section api={props.api} label={dir.label} count={dir.count} status={dir.status} />
            <For each={dir.children}>
              {(item: any) => (
                <Row
                  api={props.api}
                  status={item.status}
                  label={item.name}
                  note={item.status === STATUS.CHANGED ? "Δ" : "✓"}
                />
              )}
            </For>
            <Show when={dir.hidden > 0}>
              <text fg={skin().muted} wrapMode="none">  ○ {dir.hidden} unchanged</text>
            </Show>
          </box>
        )}
      </For>
      </box>

      <Section api={props.api} label="globals/" count={props.snapshot.changes.length}
               status={props.snapshot.status.globals} />
      <Show when={props.snapshot.changes.length === 0}>
        <text fg={skin().muted} wrapMode="none">  ○ no persistent changes</text>
      </Show>
      <For each={props.snapshot.changes}>
        {(item: any) => (
          <Row api={props.api} status={item.status} label={item._name} note={statusLabel(item.status)} />
        )}
      </For>

      <Section api={props.api} label="snapshots/" count={props.snapshot.snapshots.length} />
      <For each={props.snapshot.snapshots}>
        {(item: any) => (
          <Row api={props.api} status={STATUS.VERIFIED}
               label={String(item.snapshot_id).split(/[\\/]/).at(-1)} />
        )}
      </For>

      <Section api={props.api} label="proofs/" count={props.snapshot.proofs.length}
               status={props.snapshot.status.proofs} />
      <For each={props.snapshot.recentProofs}>
        {(item: any) => (
          <Row api={props.api} status={item.equivalent === true ? STATUS.VERIFIED : STATUS.CHANGED}
               label={item.routine} note={item.equivalent === true ? "CLEAN" : "DIVERGED"} />
        )}
      </For>
    </box>
  )
}

function SystemPanel(props: { api: TuiPluginApi; session_id: string; revision: () => number; snapshot: () => any }) {
  const skin = () => colors(props.api)
  const current = createMemo(() => {
    props.revision()
    const diff = props.api.state.session.diff(props.session_id)
    const changed = new Set(diff.flatMap((item) => [item.file, path.resolve(project, item.file)]))
    const base = props.snapshot()
    const routines = base.routines.map((item: any) => {
      if (item.status === STATUS.VERIFIED) return item
      const relative = path.relative(project, item.file)
      return changed.has(item.file) || changed.has(relative) ? { ...item, status: STATUS.CHANGED } : item
    })
    const routineState = aggregateStatus(routines.map((item: any) => item.status))
    // The tree is what the pane draws, so it has to be rebuilt from the
    // session-adjusted statuses. Reusing `base.tree` here showed the last
    // filesystem scan and no live edits at all.
    const root = path.relative(project, base.routineRoot) || base.routineRoot
    return {
      ...base,
      routines,
      tree: directoryTree(routines, { root }),
      status: {
        ...base.status,
        routines: routineState,
        system: aggregateStatus([routineState, base.status.globals]),
      },
    }
  })

  return (
    <box
      border
      borderColor={skin().border}
      backgroundColor={skin().panel}
      paddingLeft={1}
      paddingRight={1}
      flexDirection="column"
    >
      <box flexDirection="row" justifyContent="space-between" flexShrink={0}>
        <text fg={skin()[current().status.system as "idle" | "changed" | "verified"] || skin().accent}
              wrapMode="none">
          <b>{statusGlyph(current().status.system)} {current().identity.toUpperCase()} SYSTEM</b>
        </text>
        <text fg={skin().info}>LIVE</text>
      </box>
      <text fg={skin().muted} wrapMode="none">{language} · {runtime} · {instance}</text>
      <Topology api={props.api} snapshot={current()} />
      <box flexDirection="row" gap={2} flexShrink={0}>
        <text fg={skin().changed}>● edited</text>
        <text fg={skin().verified}>◆ verified</text>
        <text fg={skin().muted}>○ clean</text>
      </box>
      <Directory api={props.api} snapshot={current()} />
    </box>
  )
}

function HomeIdentity(props: { api: TuiPluginApi; snapshot: () => any; revision: () => number }) {
  const skin = () => colors(props.api)
  const current = createMemo(() => {
    props.revision()
    return props.snapshot()
  })
  return (
    <box
      border
      borderColor={skin().border}
      paddingLeft={2}
      paddingRight={2}
      paddingTop={1}
      paddingBottom={1}
      flexDirection="column"
    >
      <text fg={skin().accent}><b>{current().identity.toUpperCase()} SYSTEM</b></text>
      <text fg={skin().muted}>{language} · {runtime} · {current().routines.length} routines · {container}/{instance}</text>
    </box>
  )
}

const tui: TuiPlugin = async (api) => {
  const touched = new Set<string>()
  const [revision, setRevision] = createSignal(0)
  let snapshot = scanSystem({
    project,
    corpus,
    explicitName: process.env.ROSETTA_SYSTEM_NAME || "",
    modifiedFiles: touched,
  })

  let refreshTimer: ReturnType<typeof setTimeout> | undefined
  const refresh = (file?: string) => {
    if (file) {
      touched.add(file)
      touched.add(path.resolve(project, file))
    }
    if (refreshTimer) clearTimeout(refreshTimer)
    refreshTimer = setTimeout(() => {
      snapshot = scanSystem({
        project,
        corpus,
        explicitName: process.env.ROSETTA_SYSTEM_NAME || "",
        modifiedFiles: touched,
      })
      setRevision((value) => value + 1)
    }, 80)
  }

  api.event.on("file.edited", (event) => refresh(normalizeEvent(event).file))
  api.event.on("file.watcher.updated", (event) => refresh(normalizeEvent(event).file))

  const watchers: ReturnType<typeof watch>[] = []
  for (const root of new Set([snapshot.routineRoot, path.join(project, ".rosetta")])) {
    try {
      watchers.push(watch(root, { recursive: true }, (_event, file) => refresh(file ? path.join(root, String(file)) : root)))
    } catch {
      // Missing artifact directories are picked up by the bounded refresh below.
    }
  }
  const poll = setInterval(() => refresh(), 1200)
  poll.unref?.()
  api.lifecycle.onDispose(() => {
    if (refreshTimer) clearTimeout(refreshTimer)
    clearInterval(poll)
    watchers.forEach((item) => item.close())
  })

  api.slots.register({
    order: 10,
    slots: {
      home_bottom() {
        return <HomeIdentity api={api} snapshot={() => snapshot} revision={revision} />
      },
      sidebar_content(_context, props) {
        return <SystemPanel api={api} session_id={props.session_id} snapshot={() => snapshot} revision={revision} />
      },
    },
  })
}

const plugin: TuiPluginModule & { id: string } = {
  id: "rosetta.system-map",
  tui,
}

export default plugin
