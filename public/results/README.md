# Published benchmark results

The standalone website now consumes the actual `results/summary.json` at the repository
root. The optional build copies that file into `dist/results/summary.json`; do not place
new reports in this legacy public directory.

See [`web/README.md`](../../web/README.md#benchmark-contract) for the explicit version 1
schema. Rates are fractions between 0 and 1, with a split hash and task count. Missing or
invalid reports render a pending state. Never hand-author benchmark performance values.
