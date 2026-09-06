/** @jsxImportSource @opentui/solid */
import { createMemo, createSignal, For, Show } from "solid-js"
import type { TuiPlugin, TuiPluginApi, TuiPluginModule } from "@opencode-ai/plugin/tui"
import { watch } from "node:fs"
import path from "node:path"

import {
  STATUS,
  aggregateStatus,
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

function StatusText(props: { api: TuiPluginApi; status: string; children: any }) {
  const skin = () => colors(props.api)
  return (
    <text fg={skin()[props.status as "idle" | "changed" | "verified"] || skin().muted}>
      {statusGlyph(props.status)} {props.children}
    </text>
  )
}

function Node(props: { api: TuiPluginApi; label: string; status: string; width?: number }) {
  const skin = () => colors(props.api)
  const tone = () => skin()[props.status as "idle" | "changed" | "verified"] || skin().muted
  return (
    <box border borderColor={tone()} width={props.width || 15} alignItems="center" paddingLeft={1} paddingRight={1}>
      <text fg={tone()} wrapMode="none"><b>{props.label}</b></text>
    </box>
  )
}

function Topology(props: { api: TuiPluginApi; snapshot: any }) {
  const skin = () => colors(props.api)
  return (
    <box flexDirection="column" alignItems="center">
      <Node api={props.api} label={String(props.snapshot.identity).toUpperCase()} status={props.snapshot.status.system} width={17} />
      <text fg={skin().border}>│</text>
      <box flexDirection="row">
        <Node api={props.api} label="ROUTINES" status={props.snapshot.status.routines} width={17} />
        <text fg={skin().border}>─</text>
        <Node api={props.api} label="GLOBALS" status={props.snapshot.status.globals} width={17} />
      </box>
    </box>
  )
}

function Directory(props: { api: TuiPluginApi; snapshot: any }) {
  const skin = () => colors(props.api)
  const relativeRoot = () => path.relative(project, props.snapshot.routineRoot) || "."
  return (
    <box flexDirection="column">
      <text fg={skin().text}><b>SYSTEM DIRECTORY</b></text>
      <text fg={skin().muted} wrapMode="none">▼ {relativeRoot()}/  {props.snapshot.routines.length}</text>
      <For each={props.snapshot.routines}>
        {(item: any) => (
          <box flexDirection="row" justifyContent="space-between">
            <StatusText api={props.api} status={item.status}>
              {item.relative}
            </StatusText>
            <Show when={item.status !== STATUS.IDLE}>
              <text fg={skin()[item.status as "changed" | "verified"]}>{item.status === STATUS.CHANGED ? "Δ" : "✓"}</text>
            </Show>
          </box>
        )}
      </For>

      <text fg={skin().muted}>▼ globals/  {props.snapshot.changes.length}</text>
      <Show when={props.snapshot.changes.length === 0}>
        <text fg={skin().muted}>  ○ no persistent changes</text>
      </Show>
      <For each={props.snapshot.changes}>
        {(item: any) => (
          <StatusText api={props.api} status={item.status}>
            {item._name} · {statusLabel(item.status)}
          </StatusText>
        )}
      </For>

      <text fg={skin().muted}>▼ snapshots/  {props.snapshot.snapshots.length}</text>
      <For each={props.snapshot.snapshots}>
        {(item: any) => (
          <StatusText api={props.api} status={STATUS.VERIFIED}>
            {String(item.snapshot_id).split(/[\\/]/).at(-1)}
          </StatusText>
        )}
      </For>

      <text fg={skin().muted}>▼ proofs/  {props.snapshot.proofs.length}</text>
      <For each={props.snapshot.proofs.toReversed()}>
        {(item: any) => (
          <StatusText api={props.api} status={item.equivalent === true ? STATUS.VERIFIED : STATUS.CHANGED}>
            {item.routine} · {item.equivalent === true ? "CLEAN" : "DIVERGED"}
          </StatusText>
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
    return {
      ...base,
      routines,
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
      paddingTop={1}
      paddingBottom={1}
      paddingLeft={1}
      paddingRight={1}
      flexDirection="column"
      gap={1}
    >
      <box flexDirection="row" justifyContent="space-between">
        <text fg={skin().accent}><b>{current().identity.toUpperCase()} SYSTEM</b></text>
        <text fg={skin().info}>LIVE</text>
      </box>
      <text fg={skin().muted}>{language} · {runtime} · {container}/{instance}</text>
      <Topology api={props.api} snapshot={current()} />
      <box flexDirection="row" gap={2}>
        <text fg={skin().error}>● edited</text>
        <text fg={skin().success}>◆ verified</text>
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
