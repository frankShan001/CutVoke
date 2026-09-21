## What changed

Describe the problem and the change in a few sentences.

## Verification

List the commands you ran and any manual checks you performed.

```text
command
result
```

## Compatibility

- Does this change the project format, command schema, HTTP API, MCP tools, or exported media?
- Does it add a third-party asset, font, filter, or binary? If so, include its source and license.

## Checklist

- [ ] The change is limited to the problem described above.
- [ ] Backend changes pass `python -m compileall -q src` and `python scripts/check_layering.py`.
- [ ] Frontend changes pass `npm run typecheck` and `npm run build` in `web/app`.
- [ ] Rendering changes were checked with real media.
- [ ] User-facing behavior is reflected in the documentation or command schema.
