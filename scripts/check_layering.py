#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CutVoke 分层契约检查（纯标准库，零第三方依赖）。

分层定义（见 CONTRIBUTING.md「分层架构规则」）：

  - 接口层 interface : main, httpapi, mcp_server
      面向外部的入口（CLI / HTTP API / MCP server），可以依赖核心逻辑。
  - 核心逻辑层 logic : rational, protocol, model, service, store,
                       invariants, render, captions, keyframes
      引擎与数据模型，禁止反向依赖任何接口层。
  - 包入口           : __init__, __main__（中立，不参与禁止规则）

契约规则：
  1. 任何 logic 模块禁止 import 任何 interface 模块（核心逻辑不得反向依赖界面层）。
  2. 任何模块禁止 import `cutvoke.__main__`（避免循环/副作用）。

扫描范围：仓库根下的 src/cutvoke/ 全部 .py。

用法:
    python scripts/check_layering.py [SRC_DIR]
默认 SRC_DIR = 本文件上两级的 src 目录（即仓库根/src）。

退出码：0 通过，1 存在违规，2 用法/IO 错误。
"""
import ast
import os
import sys

INTERFACE_MODULES = {"main", "httpapi", "mcp_server"}

LOGIC_MODULES = {
    "rational", "protocol", "model", "service", "store",
    "invariants", "render", "captions", "keyframes",
}

VIOLATIONS = []


def module_path(src_root, filepath):
    """文件绝对路径 -> 模块点路径，如 cutvoke.core.service。"""
    rel = os.path.relpath(os.path.abspath(filepath), os.path.abspath(src_root))
    rel = rel.replace(os.sep, "/")
    if rel.endswith(".py"):
        rel = rel[:-3]
    parts = rel.split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def is_init_file(filepath):
    return os.path.basename(filepath) == "__init__.py"


def pkg_of(module, filepath):
    """模块的所属包（用于解析相对导入）。"""
    if is_init_file(filepath):
        return module
    return module.rsplit(".", 1)[0] if "." in module else ""


def resolve_import(node, pkg):
    """返回 import 语句涉及的全部「候选完全限定模块名」。

    对 `from X import a` 同时给出 X 与 X.a 两个候选：
      - X 为导入来源模块；
      - X.a 覆盖「a 本身是子模块」的情况。
    两者都参与接口层判定，足以捕获跨层导入（可能多报，但不会漏报）。
    """
    out = []
    if isinstance(node, ast.Import):
        for a in node.names:
            out.append(a.name)
        return out
    if isinstance(node, ast.ImportFrom):
        level = node.level
        mod = node.module or ""
        if level == 0:
            base = mod
        else:
            bits = pkg.split(".") if pkg else []
            ancestor_len = len(bits) - (level - 1)
            if ancestor_len <= 0:
                return []
            ancestor = ".".join(bits[:ancestor_len])
            base = (ancestor + "." + mod).strip(".") if mod else ancestor
        base = base.strip(".")
        for a in node.names:
            if base:
                out.append(base)
                out.append(base + "." + a.name)
            else:
                out.append(a.name)
        return out
    return out


def check_file(src_root, filepath):
    with open(filepath, encoding="utf-8") as f:
        try:
            tree = ast.parse(f.read(), filename=filepath)
        except SyntaxError as e:
            VIOLATIONS.append(f"{filepath}: 语法错误 {e}")
            return

    module = module_path(src_root, filepath)
    leaf = module.split(".")[-1]
    pkg = pkg_of(module, filepath)

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        for target in resolve_import(node, pkg):
            if target == "cutvoke.__main__":
                VIOLATIONS.append(
                    f"{filepath}: 禁止 import cutvoke.__main__（来自 {target}）"
                )
            if leaf in LOGIC_MODULES and target.split(".")[-1] in INTERFACE_MODULES:
                VIOLATIONS.append(
                    f"{filepath}: 核心逻辑模块 '{leaf}' 禁止依赖接口层模块 "
                    f"'{target.split('.')[-1]}'"
                )


def main(argv):
    src_root = argv[1] if len(argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
    )
    if not os.path.isdir(src_root):
        print(f"ERROR: src 目录不存在: {src_root}", file=sys.stderr)
        return 2

    py_files = []
    for dirpath, _dirnames, filenames in os.walk(src_root):
        for fn in filenames:
            if fn.endswith(".py"):
                py_files.append(os.path.join(dirpath, fn))
    py_files.sort()

    for fp in py_files:
        check_file(src_root, fp)

    print(f"[layering] 扫描模块数: {len(py_files)}")
    print(f"[layering] 接口层: {sorted(INTERFACE_MODULES)}")
    print(f"[layering] 核心逻辑层: {sorted(LOGIC_MODULES)}")
    if VIOLATIONS:
        print(f"\n[layering] 发现 {len(VIOLATIONS)} 处违规:")
        for v in VIOLATIONS:
            print(f"  - {v}")
        return 1
    print("[layering] PASS: 分层契约无违规")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
