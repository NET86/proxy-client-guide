# 维护说明

这个项目的目标是长期、低人工维护代理客户端目录。设计原则是：**日常变化自动处理，高风险身份变化才人工介入**，不为极少出现的边界继续叠加额外系统。

## 目录分层

- `data/clients.json`：静态目录与可信身份。GitHub repository ID、App Store ID/seller、平台、内核等需要人工审核后通过 PR 修改；GitHub owner/name 属于自动观察状态。
- `data/observations.json`：自动核验结果。由 Refresh workflow 生成，不手工填写 `ok`、时间戳或 scope。
- `README.md`：由脚本根据上述两份数据生成，不直接手改。

## 自动更新闭环

`.github/workflows/refresh-directory.yml` 每 6 小时运行一次，也可手动触发。

流程只有四步：

1. 从当前 `main` 核验官方来源并生成 README/observations。
2. 对明确的身份冲突隐藏入口；普通网络/区域核验失败保留已配置入口并标记待确认。
3. 如生成文件发生变化，用当前 Refresh job 的短期 `GITHUB_TOKEN` 普通 push 到 `main`。
4. 执行 health gate；异常会让 workflow 失败并保留可见状态。

这个顺序是有意的：先发布按现有安全规则生成的异常状态，再让 health gate 失败，避免用户长期看到旧的正常状态。除非确认生成结果本身可能错误授权入口或破坏静态目录，否则不要改成 health-before-push。

Refresh 只写 `README.md` 和 `data/observations.json`。如果 push 恰好与新的 `main` 冲突，本次任务直接失败，不在同一个 run 中重放代码或复杂合并；下一次定时运行会从新的 `main` 重新核验并自动恢复。

## 哪些情况不需要人工

以下变化由 Refresh 自动处理：

- 官方仓库或 App Store 正常发布新版本。
- GitHub 同一 repository ID 的 owner/name 改名或迁移。
- GitHub 官方仓库归档后自动转入历史分组；取消归档时自动恢复原分类。
- 项目近期活动时间变化、短时网络失败后恢复。
- 版本状态、README 展示和更新时间的普通变化。

## 哪些情况需要人工

只有静态可信事实发生变化时才人工处理，例如：

- GitHub repository ID 或 App Store app ID/seller 与已确认身份明确冲突。
- 无结构化证据可可靠判断的合并、停更或继任关系。
- 新增/删除客户端，或修改平台、内核、官方下载入口。
- App Store 发布者发生合法迁移，需要更新固定 seller。

处理方式是更新 `data/clients.json` 并走正常 PR；不要直接修改 observations 伪造恢复。

## 安全边界

- GitHub 来源只固定 repository ID；owner/name 自动跟随同一仓库的官方迁移。App Store 固定 app ID 与 sellerName。
- 身份核验只证明来源连续，不代表所有者可信或二进制安全。旧路径被不同 repo ID 占用时阻断，不自动寻找替代仓库。
- 404、latest release 缺失或短期访问失败只证明当前目标不可用，不证明身份已改变；scope 未变且无身份冲突时保留已配置入口并标记待确认。
- 已确认的身份冲突不能被后续 404、缺包或时间回退解除；只有同一 scope 下的正向核验，或经人工确认的新身份配置，才能恢复。
- 静态 `legacy` 不因取消归档自动升级；动态归档可逆，不另存生命周期复核锁。归档只免除普通 release/core 新鲜度要求，已确认的下载身份冲突仍影响 health 和展示。
- Release scope 保留 `download_url`，并绑定实际展示的 `download_page_url`（如有）；同仓库 canonical 改名只重写请求地址，不改静态 evidence scope。仅在相同字段代表的证据语义不再兼容时才 bump scope version，不为普通代码改动 bump。
- 不自动把失效项目替换为同名 fork、第三方镜像或继任项目。
- 第三方历史资料不进入主下载列。
- 主分支只保留一个保护 ruleset：linear history + 禁止删除/强推。人工目录/代码变更仍按 PR 流程维护；不强制 PR/required check，以避免阻断 Refresh 使用短期 `GITHUB_TOKEN` 自动写回生成文件。
- Refresh 只为自身 job 申请短期 `contents: write`；不保存长期写密钥或仓库 secret。
- CI 只允许预期的 NET86 / GitHub / github-actions 提交身份。

## 本地验证

项目运行与测试只依赖 Python 标准库。

```bash
python scripts/build_readme.py --audit
python scripts/build_readme.py --health-check
python -m unittest discover -s tests -v
python -m py_compile scripts/build_readme.py scripts/check_commit_identity.py
python scripts/build_readme.py --check
git diff --check
```

## 故障恢复

- 普通上游网络故障：不人工改数据，等待下一次 Refresh。
- Refresh push 冲突：不强推、不 rebase 自动生成提交，等待下一次定时任务。
- Refresh 权限或保护规则配置损坏：修复 GitHub workflow/ruleset 后手动 dispatch 一次 Refresh。
- 身份异常：repo/app 身份明确冲突时保持入口隐藏并人工处理；同一 GitHub repo ID 的 owner/name 迁移无需人工。
- workflow 自身修改：合并后手动 dispatch 一次当前 `main` 的 Refresh 做真实验收。

如果未来确实出现长期无法靠这套简单闭环解决的问题，再针对真实故障增加机制；不提前为假设场景增加常驻复杂度。
