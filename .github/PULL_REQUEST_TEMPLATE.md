English / 简体中文均可。贡献指南：[English](https://github.com/frankShan001/CutVoke/blob/main/CONTRIBUTING.md) · [简体中文](https://github.com/frankShan001/CutVoke/blob/main/CONTRIBUTING.zh-CN.md)。安全问题先读 [SECURITY.md](https://github.com/frankShan001/CutVoke/blob/main/SECURITY.md)。

## What changed / 改动内容

Describe the problem and the change in a few sentences.

## Verification / 验证

List the commands you ran and any manual checks you performed.

```text
command
result
```

## Compatibility / 兼容性

- Does this change the project format, command schema, HTTP API, MCP tools, or exported media?
- Does it add a third-party asset, font, filter, or binary? If so, include its source and license.

## Checklist / 检查项

- [ ] The change is limited to the problem described above.
- [ ] Backend changes pass `python -m compileall -q src` and `python tools/verify/check_layering.py`.
- [ ] Frontend changes pass `npm run typecheck` and `npm run build` in `apps/editor`.
- [ ] Rendering changes were checked with real media.
- [ ] User-facing behavior is reflected in the documentation or command schema.
