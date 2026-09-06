// The pure half of the Rosetta experience plugin: which phase a tool is in,
// and the frames the running indicator cycles through.
//
// This lives in `lib/` rather than in the plugin module on purpose. The harness
// loads a plugin file by iterating *every* export and requiring each one to be
// a function (or an object with a `.server` function); a single non-function
// export makes the whole module fail with "Plugin export is not a function"
// and silently costs you every hook in it. So a plugin module exports plugins,
// and nothing else. Values and helpers go here, where tests can reach them.

export const pulseFrames = [
  "◆ · · · ·",
  "· ◆ · · ·",
  "· · ◆ · ·",
  "· · · ◆ ·",
  "· · · · ◆",
  "· · · ◆ ·",
  "· · ◆ · ·",
  "· ◆ · · ·",
]

export const toolName = (value) => String(value || "").toLowerCase().replaceAll("-", "_")

export const phase = (value) => {
  const name = toolName(value)
  if (name.includes("rosetta_showcase")) {
    return { label: "PROOF FLIGHT", message: "Launching the live YottaDB verifier", pulse: false }
  }
  if (name.endsWith("verify_change") || name.includes("verify_change")) {
    return {
      label: "PROVE",
      message: "Baseline + candidate · identical state · output + globals",
      pulse: true,
    }
  }
  if (name.endsWith("run_task_cases") || name.includes("run_task_cases")) {
    return {
      label: "PROVE",
      message: "Replaying the held cases through the executable verifier",
      pulse: true,
    }
  }
  if (name.endsWith("execute_routine") || name.includes("execute_routine")) {
    return {
      label: "EXECUTE",
      message: "Capturing stdout, runtime errors, and persistent global state",
      pulse: true,
    }
  }
  if (name.endsWith("resolve_global") || name.includes("resolve_global")) {
    return {
      label: "UNDERSTAND",
      message: "Resolving the MUMPS global through the FileMan dictionary",
      pulse: false,
    }
  }
  if (name.endsWith("parse_routine") || name.includes("parse_routine") || name.includes("call_graph")) {
    return {
      label: "UNDERSTAND",
      message: "Mapping labels, calls, and persistent global access",
      pulse: false,
    }
  }
  return null
}
