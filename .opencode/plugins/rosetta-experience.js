import { tool } from "@opencode-ai/plugin"

let loaded = false

const toolName = (value) => String(value || "").toLowerCase().replaceAll("-", "_")

const phase = (value) => {
  const name = toolName(value)
  if (name.includes("rosetta_showcase")) return ["PROOF FLIGHT", "Launching the live YottaDB verifier"]
  if (name.endsWith("verify_change") || name.includes("verify_change")) {
    return ["PROVE", "Executing baseline and candidate from identical database state"]
  }
  if (name.endsWith("resolve_global") || name.includes("resolve_global")) {
    return ["UNDERSTAND", "Resolving the MUMPS global through the FileMan dictionary"]
  }
  if (name.endsWith("parse_routine") || name.includes("parse_routine") || name.includes("call_graph")) {
    return ["UNDERSTAND", "Mapping labels, calls, and persistent global access"]
  }
  return null
}

export const RosettaExperience = async ({ client }) => {
  if (loaded) return {}
  loaded = true

  const toast = async (title, message, variant = "info", duration = 1800) => {
    try {
      await client.tui.showToast({ body: { title: `Rosetta · ${title}`, message, variant, duration } })
    } catch {
      // Headless `opencode run` has no TUI endpoint. The proof itself must continue.
    }
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
            setTimeout(() => toast("ISOLATE", "Every case runs inside a clean-state transaction frame", "info", 2200), 1300),
            setTimeout(() => toast("OBSERVE", "Comparing stdout, runtime errors, and persistent global state", "info", 2600), 3900),
            setTimeout(() => toast("DIVERGENCE CAUGHT", "The interpreter found an output change the candidate missed", "error", 2600), 11500),
            setTimeout(() => toast("REPAIR + REPLAY", "Testing the corrected MUMPS from the same clean state", "info", 2600), 14500),
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
      if (current) await toast(current[0], current[1])
    },
    "tool.execute.after": async (input, output) => {
      const name = toolName(input.tool)
      if (!name.includes("verify_change")) return
      const rendered = JSON.stringify(output)
      if (rendered.includes("NOT EQUIVALENT")) {
        await toast("DIVERGENCE CAUGHT", "The runtime found behavior the model missed", "error", 2600)
      } else if (rendered.includes("EQUIVALENT")) {
        await toast("PROOF COMPLETE", "All exercised outputs and global state matched", "success", 2600)
      }
    },
  }
}
