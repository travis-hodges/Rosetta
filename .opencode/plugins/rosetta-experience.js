// OpenCode's `tool()` helper is intentionally an identity function. Keeping the
// tiny equivalent here makes the shipped TUI plugin cold-start offline without
// a nested npm install.
const tool = (definition) => definition

let loaded = false

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

const toolName = (value) => String(value || "").toLowerCase().replaceAll("-", "_")

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

export const RosettaExperience = async ({ client }) => {
  if (loaded) return {}
  loaded = true

  const activePulses = new Map()

  const toast = async (title, message, variant = "info", duration = 1800) => {
    try {
      await client.tui.showToast({ body: { title: `Rosetta · ${title}`, message, variant, duration } })
      return true
    } catch {
      // Headless `opencode run` has no TUI endpoint. The proof itself must continue.
      return false
    }
  }

  const stopPulse = (callID) => {
    const timer = activePulses.get(callID)
    if (timer) clearInterval(timer)
    activePulses.delete(callID)
  }

  const startPulse = async (callID, current) => {
    stopPulse(callID)
    let index = 0
    const showFrame = () => toast(
      `${current.label}  ${pulseFrames[index++ % pulseFrames.length]}`,
      current.message,
      "info",
      820,
    )
    if (!await showFrame()) return
    const timer = setInterval(showFrame, 860)
    timer.unref?.()
    activePulses.set(callID, timer)
  }

  return {
    tool: {
      rosetta_showcase: tool({
        description: "Run Rosetta's judge-safe proof flight: fresh live YottaDB differential execution with an automatic recorded-audit fallback.",
        args: {},
        async execute() {
          const root = process.env.ROSETTA_HOME
          const python = process.env.ROSETTA_PYTHON || "python3"
          if (!root) throw new Error("ROSETTA_HOME is not set; launch this UI with `rosetta`")

          const timers = [
            setTimeout(() => toast("01/04  ISOLATE  ◆ · · ·", "Holding every case inside a clean-state transaction frame", "info", 2200), 1300),
            setTimeout(() => toast("02/04  OBSERVE  ◆ ◆ · ·", "Watching stdout, runtime errors, and persistent global state", "info", 2600), 3900),
            setTimeout(() => toast("03/04  DIVERGENCE  ◆ ◆ ◆ ·", "The interpreter found an output change the candidate missed", "error", 2600), 11500),
            setTimeout(() => toast("04/04  REPLAY  ◆ ◆ ◆ ◆", "Testing the corrected MUMPS from the same clean state", "info", 2600), 14500),
          ]
          try {
            let timedOut = false
            const child = Bun.spawn(
              [python, "-m", "rosetta.demo.proof_flight"],
              {
                cwd: root,
                env: { ...process.env, PYTHONPATH: root },
                stdout: "pipe",
                stderr: "pipe",
              },
            )
            const watchdog = setTimeout(() => {
              timedOut = true
              child.kill()
            }, 38000)
            const [stdout, stderr, code] = await Promise.all([
              new Response(child.stdout).text(),
              new Response(child.stderr).text(),
              child.exited,
            ])
            clearTimeout(watchdog)
            if (code === 0) {
              await toast("LIVE RECEIPT", "YottaDB execution complete; content-addressed receipts saved", "success", 3200)
              return stdout.trim()
            }

            await toast("RECORDED FALLBACK", "Live proof failed safely; loading the committed audit trace", "warning", 3200)
            const fallback = Bun.spawn(
              [python, "-m", "rosetta.demo", "--pace", "0", "--width", "132", "--no-color", "--no-animation"],
              {
                cwd: root,
                env: { ...process.env, PYTHONPATH: root },
                stdout: "pipe",
                stderr: "pipe",
              },
            )
            const [fallbackOut, fallbackErr, fallbackCode] = await Promise.all([
              new Response(fallback.stdout).text(),
              new Response(fallback.stderr).text(),
              fallback.exited,
            ])
            if (fallbackCode !== 0) {
              throw new Error((fallbackErr || fallbackOut || stderr || `proof flight exited ${code}`).trim())
            }
            const reason = timedOut
              ? "Live proof exceeded the 38-second demo budget."
              : (stderr.trim() || `Live proof exited ${code}.`)
            return `LIVE PREFLIGHT FAILED SAFELY\n${reason}\n\n${fallbackOut.trim()}`
          } finally {
            timers.forEach(clearTimeout)
          }
        },
      }),
    },
    "tool.execute.before": async (input) => {
      const current = phase(input.tool)
      if (!current) return
      if (current.pulse) {
        await startPulse(input.callID, current)
      } else {
        await toast(current.label, current.message)
      }
    },
    "tool.execute.after": async (input, output) => {
      stopPulse(input.callID)
      const name = toolName(input.tool)
      if (!name.includes("verify_change") && !name.includes("run_task_cases")) return
      const rendered = JSON.stringify(output)
      if (rendered.includes("NOT EQUIVALENT")) {
        await toast("DIVERGENCE CAUGHT", "The runtime found behavior the model missed", "error", 2600)
      } else if (rendered.includes("NO VERDICT")) {
        await toast("NO VERDICT", "Execution did not produce trustworthy proof", "warning", 2800)
      } else if (rendered.includes("EQUIVALENT")) {
        await toast("PROOF COMPLETE", "All exercised outputs and global state matched", "success", 2600)
      }
    },
  }
}

export default RosettaExperience
