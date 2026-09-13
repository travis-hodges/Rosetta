// Native tool events are the evidence. Toasts describe only the current call.
export const RosettaExperience = async ({ client }) => {
  const toast = async (title, message) => {
    try {
      await client.tui.showToast({ body: { title, message, variant: "info", duration: 1800 } })
    } catch {
      // Headless runs have no TUI endpoint; native tool events still render.
    }
  }
  return {
    "tool.execute.before": async (input, output) => {
      const name = input.tool.replace(/^.*reference_/, "reference_")
      const args = output?.args || {}
      if (name === "reference_search" || name === "reference_examples") {
        await toast("Searching references", args.query || "Repository technical sources")
      } else if (name === "reference_read") {
        await toast("Reading reference", args.document || "Selected technical passage")
      } else if (name === "reference_sources") {
        await toast("Available references", "Repository-provided technical sources")
      }
    },
  }
}
export default RosettaExperience
