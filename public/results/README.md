# Published benchmark results

The landing page fetches `/results/summary.json` at runtime and renders the benchmark
table from it. If the file is absent the page shows an explicit "no run published" state.

**Nothing on the landing page is hand-typed.** Copy a real run's `results/summary.json`
here before building; never author this file by hand.

Expected shape:

```json
{
  "generated_at": "2026-09-06T04:12:00Z",
  "conditions": [
    {
      "name": "Baseline (no tools)",
      "pass_at_1": 0.0,
      "pass_at_3": 0.0,
      "false_confidence_rate": 0.0
    }
  ]
}
```

Rates may be given as fractions (`0.31`) or percentages (`31`); the page normalises both.
