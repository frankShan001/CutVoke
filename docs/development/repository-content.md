# Public repository contents / 公开仓库收录范围

The repository serves users installing CutVoke and developers maintaining it.
Version files needed to use, build, test, modify or legally redistribute the product.

| Keep in Git | Keep outside Git |
| --- | --- |
| Application/engine source, configuration and dependency locks | Dependency folders, caches, binaries and local databases |
| Repeatable tests and tools with documented inputs | Scripts tied to a personal project or one past verification run |
| Current user guides, architecture and contribution instructions | Development journals, task transcripts, milestone reports and draft notes |
| Published documentation images | Temporary screenshots, contact sheets and comparison renders |
| Active artwork sources, extraction coordinates, licenses and provenance | Replaced backups, unused artwork experiments and private media |
| Runtime resource integrity/qualification metadata and release download checksums | Personal review sessions and build/test logs |

`tests/` contains automated product checks. `tools/` contains commands used to
build, maintain and verify the repository. Keep them separate so contributors
can find the test suite without browsing maintenance scripts.

Write reproducible temporary outputs under ignored `.artifacts/`. Keep personal
archives outside the checkout. Local history directories are also rejected by
the repository check if accidentally force-added. Do not add old process files
to source distributions. `CHANGELOG.md` records changes relevant to users.

Resource source/qualification records are product inputs: preserve the original
bytes and licensing information needed to reproduce or validate shipped assets.
`resources/source/provenance.json` resolves original asset paths still embedded
in published metadata; it does not require private development archives.

Run `python tools/verify/check_repository_layout.py` before submitting changes.
PR validation should identify the command, relevant environment and result;
attach only the minimal synthetic or explicitly shareable evidence needed for review.

## 中文

以使用者和贡献者为读者：保留运行、构建、测试、修改及合法分发产品所需的文件。
源码、依赖锁、自动化测试、通用工具、现行文档、正式配图和当前素材来源纳入 Git。
个人开发日志、对话记录、阶段报告、一次性验收脚本及临时截图保存在仓库外；
可重复生成的输出写入 `.artifacts/`。用户可见的版本变化写入 `CHANGELOG.md`。

内置资源的来源、许可证和运行时校验/资格元数据属于产品输入，需要保留原始字节。
公开下载包的校验和用于识别用户实际下载的文件，也应保留。
测试继续位于 `tests/`，开发维护命令位于 `tools/`。
提交前运行目录检查；PR 只附与改动有关、可公开且足够复核的最少验证材料。
