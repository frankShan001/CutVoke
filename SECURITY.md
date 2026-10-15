# Reporting security issues / 安全问题报告

CutVoke is an early-stage local application. Security fixes are made on current `main`; there is no separate supported stable release branch or guaranteed response time.

Please do not disclose working exploits, credentials, personal paths or private media in a public Issue or PR. In the repository's **Security** tab, use **Report a vulnerability** if GitHub private vulnerability reporting is available. This repository does not advertise a dedicated security email or an external reporting service.

If that private reporting option is unavailable, open a minimal public Issue asking the maintainer to enable a private reporting route. Include only the affected component and a request for private contact; wait for a verified route before sending reproduction details. Do not send secrets to an unverified address or account.

For a private report, include the affected commit, OS, interface (HTTP/MCP/desktop), the smallest safe reproduction, impact and any proposed fix. Use synthetic files, and remove tokens and personal data. Ordinary crashes or rendering bugs without sensitive details can use the normal bug-report template.

## 简体中文

安全修复以当前 `main` 为准，目前没有单独维护的稳定版分支，也不承诺固定响应时限。请勿在公开 Issue/PR 中贴可利用的漏洞细节、凭证或个人媒体。

若仓库 **Security → Report a vulnerability** 可用，请使用 GitHub 的私密漏洞报告。若不可用，只开一条不含漏洞利用细节的公开 Issue，请维护者启用私密报告渠道；确认真实渠道后再提交复现材料。本仓库未公布专用安全邮箱或外部安全服务。
