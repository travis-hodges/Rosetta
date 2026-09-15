/** @jsxImportSource @opentui/solid */
import { createMemo, createSignal, For, Show } from "solid-js"
import type { TuiPlugin, TuiPluginApi, TuiPluginModule } from "@opencode-ai/plugin/tui"
import path from "node:path"

import {
  STATUS,
  aggregateStatus,
  defaultExpandedDirectories,
  normalizeEvent,
  projectDirectoryTree,
  projectExplorerTree,
  scanSystem,
} from "../lib/rosetta-system-model.js"

const project = path.resolve(process.env.ROSETTA_PROJECT_DIR || process.cwd())

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
    selected: theme.backgroundElement,
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
const countLabel = (count: number, singular: string, plural = `${singular}s`) => `${count} ${count === 1 ? singular : plural}`

function Row(props: { api: TuiPluginApi; status?: string; label: string; note?: string; glyph?: string }) {
  const skin = () => colors(props.api)
  const tone = () => props.status
    ? (skin()[props.status as "idle" | "changed" | "verified"] || skin().muted)
    : skin().muted
  return (
    <box flexDirection="row" justifyContent="space-between">
      <box flexGrow={1} flexShrink={1} minWidth={0}>
        <text fg={tone()} wrapMode="none">  {props.glyph || (props.status ? statusGlyph(props.status) : "·")} {props.label}</text>
      </box>
      <Show when={props.note}>
        <text fg={tone()} wrapMode="none" flexShrink={0}> {props.note}</text>
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
      <box flexGrow={1} flexShrink={1} minWidth={0}>
        <text fg={skin().muted} wrapMode="none">▼ {props.label}</text>
      </box>
      <text fg={tone()} wrapMode="none" flexShrink={0}> {props.count}</text>
    </box>
  )
}

const fileNote = (item: any) => {
  if (item.additions || item.deletions) return `+${item.additions || 0} -${item.deletions || 0}`
  return item.change || ""
}

function ProjectDirectory(props: { api: TuiPluginApi; snapshot: any }) {
  const skin = () => colors(props.api)
  const [expanded, setExpanded] = createSignal(
    new Set<string>(defaultExpandedDirectories(props.snapshot.project.files)),
  )
  const tree = createMemo(() => projectExplorerTree(props.snapshot.project.files))
  const toggle = (key: string, next?: boolean) => {
    setExpanded((current) => {
      const value = new Set(current)
      const shouldExpand = next ?? !value.has(key)
      if (shouldExpand) value.add(key)
      else value.delete(key)
      return value
    })
  }
  return (
    <box flexDirection="column" flexShrink={0}>
      <box flexDirection="row" justifyContent="space-between">
        <text fg={skin().text}><b>PROJECT</b></text>
        <text fg={props.snapshot.project.changed ? skin().changed : skin().muted}>
          {countLabel(props.snapshot.project.files.length, "file")}
        </text>
      </box>
      <For each={tree()}>
        {(node: any) => (
          <ProjectTreeNode api={props.api} node={node} depth={0} expanded={expanded} toggle={toggle} />
        )}
      </For>
      <Show when={props.snapshot.project.files.length === 0}>
        <text fg={skin().muted}>  No project files</text>
      </Show>
    </box>
  )
}

function ProjectTreeNode(props: {
  api: TuiPluginApi
  node: any
  depth: number
  expanded: () => Set<string>
  toggle: (key: string, next?: boolean) => void
}) {
  const skin = () => colors(props.api)
  const [focused, setFocused] = createSignal(false)
  const open = () => props.node.type === "directory" && props.expanded().has(props.node.key)
  const tone = () => props.node.status === STATUS.CHANGED ? skin().changed : skin().muted
  const activate = () => props.toggle(props.node.key)
  const onKeyDown = (event: any) => {
    if (event.name === "return" || event.name === "space") activate()
    else if (event.name === "right" && !open()) props.toggle(props.node.key, true)
    else if (event.name === "left" && open()) props.toggle(props.node.key, false)
    else return
    event.preventDefault?.()
    event.stopPropagation?.()
  }

  return (
    <Show
      when={props.node.type === "directory"}
      fallback={
        <box flexDirection="row" justifyContent="space-between" paddingLeft={Math.min(props.depth * 2, 14)}>
          <text fg={tone()} wrapMode="none" truncate>
            {props.node.status === STATUS.CHANGED ? "●" : "·"} {props.node.name}
          </text>
          <Show when={fileNote(props.node)}>
            <text fg={tone()} wrapMode="none" flexShrink={0}> {fileNote(props.node)}</text>
          </Show>
        </box>
      }
    >
      <box flexDirection="column" flexShrink={0}>
        <box
          flexDirection="row"
          justifyContent="space-between"
          paddingLeft={Math.min(props.depth * 2, 12)}
          focusable
          backgroundColor={focused() ? skin().selected : undefined}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          onMouseDown={(event: any) => {
            if (event.button !== 0) return
            event.preventDefault?.()
            event.stopPropagation?.()
            activate()
          }}
          onKeyDown={onKeyDown}
        >
          <box flexGrow={1} flexShrink={1} minWidth={0}>
            <text fg={tone()} wrapMode="none" truncate>{open() ? "▼" : "▶"} {props.node.label}</text>
          </box>
          <text fg={tone()} wrapMode="none" flexShrink={0}> {props.node.count}</text>
        </box>
        <Show when={open()}>
          <For each={props.node.children}>
            {(child: any) => (
              <ProjectTreeNode
                api={props.api}
                node={child}
                depth={props.depth + 1}
                expanded={props.expanded}
                toggle={props.toggle}
              />
            )}
          </For>
        </Show>
      </box>
    </Show>
  )
}

function References(props: { api: TuiPluginApi; snapshot: any }) {
  const skin = () => colors(props.api)
  const references = () => props.snapshot.references
  return (
    <Show when={references().config || references().error || references().pending.length}>
      <box flexDirection="column" flexShrink={0}>
        <box flexDirection="row" justifyContent="space-between">
          <text fg={skin().text}><b>REFERENCES</b></text>
          <text fg={references().error || references().pending.length ? skin().changed : skin().muted}>
            {references().pending.length ? `${references().pending.length} NEEDED` : references().sources.length}
          </text>
        </box>
        <Show when={references().error}>
          <Row api={props.api} status={STATUS.CHANGED} label="invalid reference configuration" glyph="!" />
        </Show>
        <For each={references().pending}>
          {(request: any) => (
            <Row api={props.api} status={STATUS.CHANGED} glyph="!" label={request.language}
                 note={request.reason || "source needed"} />
          )}
        </For>
        <For each={references().sources.slice(0, 5)}>
          {(source: any) => (
            <Row api={props.api} status={source.available ? STATUS.IDLE : STATUS.CHANGED}
                 glyph={source.available ? "·" : "!"} label={source.title}
                 note={source.available ? source.language : "missing"} />
          )}
        </For>
        <Show when={references().sources.length > 5}>
          <text fg={skin().muted}>    {references().sources.length - 5} more sources</text>
        </Show>
        <For each={references().commands.slice(0, 3)}>
          {(command: any) => <Row api={props.api} label={`${command.name}: ${command.command}`} glyph="$" />}
        </For>
      </box>
    </Show>
  )
}

function Verification(props: { api: TuiPluginApi; snapshot: any }) {
  const skin = () => colors(props.api)
  const visible = () => props.snapshot.changes.length > 0 || props.snapshot.proofs.length > 0
  return (
    <Show when={visible()}>
      <box flexDirection="column" flexShrink={0}>
        <text fg={skin().text}><b>VERIFICATION</b></text>
        <Show when={props.snapshot.changes.length > 0}>
          <Section api={props.api} label="database changes" count={props.snapshot.changes.length}
                   status={props.snapshot.status.globals} />
          <For each={props.snapshot.changes.slice(-4).reverse()}>
            {(item: any) => (
              <Row api={props.api} status={item.status} label={item._name} note={statusLabel(item.status)} />
            )}
          </For>
        </Show>
        <Show when={props.snapshot.proofs.length > 0}>
          <Section api={props.api} label="routine proofs" count={props.snapshot.proofs.length}
                   status={props.snapshot.status.proofs} />
          <For each={props.snapshot.recentProofs}>
            {(item: any) => (
              <Row api={props.api} status={item.equivalent === true ? STATUS.VERIFIED : STATUS.CHANGED}
                   label={item.routine} note={item.equivalent === true ? "MATCH" : "DIVERGED"} />
            )}
          </For>
        </Show>
      </box>
    </Show>
  )
}

function Directory(props: { api: TuiPluginApi; snapshot: any }) {
  return (
    <box flexDirection="column" flexShrink={0}>
      <ProjectDirectory api={props.api} snapshot={props.snapshot} />
      <References api={props.api} snapshot={props.snapshot} />
      <Verification api={props.api} snapshot={props.snapshot} />
    </box>
  )
}

function SessionTitle(props: { api: TuiPluginApi; title: string }) {
  const skin = () => colors(props.api)
  return (
    <box flexDirection="column" paddingBottom={1}>
      <text fg={skin().muted} wrapMode="none">ROSETTA SESSION:</text>
      <text fg={skin().text} wrapMode="none" truncate><b>{props.title || "Untitled session"}</b></text>
    </box>
  )
}

function RosettaFooter(props: { api: TuiPluginApi }) {
  const skin = () => colors(props.api)
  const version = process.env.ROSETTA_VERSION || "development"
  return (
    <box flexDirection="row">
      <text fg={skin().accent}>● </text>
      <text fg={skin().text}><b>Rosetta</b></text>
      <text fg={skin().muted}> v{version}</text>
    </box>
  )
}

function SystemPanel(props: { api: TuiPluginApi; session_id: string; revision: () => number; snapshot: () => any }) {
  const skin = () => colors(props.api)
  const current = createMemo(() => {
    props.revision()
    const diff = props.api.state.session.diff(props.session_id)
    const changed = new Set(diff.flatMap((item) => [item.file, path.resolve(project, item.file)]))
    const diffByFile = new Map(diff.flatMap((item) => [
      [item.file, item],
      [path.resolve(project, item.file), item],
    ]))
    const base = props.snapshot()
    // Fold the session diff into the project directory so a file
    // edited in this session shows as CHANGED even before the watcher fires.
    const projectFiles = base.project.files.map((item: any) => {
      const relative = path.relative(project, item.file)
      const sessionChange = diffByFile.get(item.file) || diffByFile.get(relative)
      return changed.has(item.file) || changed.has(relative)
        ? { ...item, status: STATUS.CHANGED, change: "edited", additions: sessionChange?.additions, deletions: sessionChange?.deletions }
        : item
    })
    const projectSummary = projectDirectoryTree(projectFiles)
    return {
      ...base,
      project: {
        ...base.project,
        files: projectFiles,
        ...projectSummary,
        changed: projectFiles.filter((item: any) => item.status === STATUS.CHANGED).length,
        status: aggregateStatus(projectFiles.map((item: any) => item.status)),
      },
      status: {
        ...base.status,
        system: aggregateStatus([base.status.globals, base.status.proofs,
          aggregateStatus(projectFiles.map((item: any) => item.status))]),
      },
    }
  })
  const branch = () => props.api.state.vcs?.branch
  const changedCount = () => current().project.changed

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
          <b>{current().projectName.toUpperCase()}</b>
        </text>
        <text fg={changedCount() ? skin().changed : skin().info}>{changedCount() ? `${changedCount()} EDITED` : "CLEAN"}</text>
      </box>
      <text fg={skin().muted} wrapMode="none">
        {[branch(), countLabel(current().project.files.length, "file"),
          current().references.sources.length ? countLabel(current().references.sources.length, "source") : ""]
          .filter(Boolean).join(" · ")}
      </text>
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
  const summary = () => [
    countLabel(current().project.files.length, "file"),
    current().references.sources.length ? countLabel(current().references.sources.length, "reference source") : "",
    current().references.pending.length
      ? countLabel(current().references.pending.length, "reference needed", "references needed")
      : "",
  ].filter(Boolean).join(" · ")
  return (
    <box
      border
      borderColor={skin().border}
      paddingLeft={2}
      paddingRight={2}
      flexDirection="column"
    >
      <text fg={skin().accent} wrapMode="none" truncate>
        <b>{current().projectName.toUpperCase()}</b>
        <span style={{ fg: skin().muted }}> · {summary()}</span>
      </text>
    </box>
  )
}

const tui: TuiPlugin = async (api) => {
  const [revision, setRevision] = createSignal(0)
  let snapshot = scanSystem({
    project,
    explicitName: process.env.ROSETTA_SYSTEM_NAME || "",
  })

  let refreshTimer: ReturnType<typeof setTimeout> | undefined
  const refresh = (_file?: string) => {
    if (refreshTimer) clearTimeout(refreshTimer)
    refreshTimer = setTimeout(() => {
      snapshot = scanSystem({
        project,
        explicitName: process.env.ROSETTA_SYSTEM_NAME || "",
      })
      setRevision((value) => value + 1)
    }, 80)
  }

  // The terminal engine already owns project watching. Its events are enough to keep the
  // panel current; a second recursive watcher and a polling loop only rescan.
  api.event.on("file.edited", (event) => refresh(normalizeEvent(event).file))
  api.event.on("file.watcher.updated", (event) => refresh(normalizeEvent(event).file))

  api.lifecycle.onDispose(() => {
    if (refreshTimer) clearTimeout(refreshTimer)
  })

  api.slots.register({
    order: 10,
    slots: {
      sidebar_title(_context, props) {
        return <SessionTitle api={api} title={props.title} />
      },
      home_bottom() {
        return <HomeIdentity api={api} snapshot={() => snapshot} revision={revision} />
      },
      sidebar_content(_context, props) {
        return <SystemPanel api={api} session_id={props.session_id} snapshot={() => snapshot} revision={revision} />
      },
      sidebar_footer() {
        return <RosettaFooter api={api} />
      },
    },
  })
}

const plugin: TuiPluginModule & { id: string } = {
  id: "rosetta.system-map",
  tui,
}

export default plugin
