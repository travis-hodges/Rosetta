# Widget SDK v1

## Safe replacement with revision tokens

The internal Widget SDK updates a widget with `replace(id, body, revision)`.
A caller must pass the last observed revision token. A stale token returns
`REVISION_CONFLICT`; read the current widget before retrying. Omitting the
revision token is an error, never permission to overwrite concurrent changes.

```javascript
const widget = await sdk.read('sample');
await sdk.replace(widget.id, {label: 'Revised label'}, widget.revision);
```

This small fictional SDK manual is a generalization fixture, not a deployed SDK.
