# 维护说明

这个项目用于长期、低人工维护代理客户端目录：**日常变化自动处理，高风险身份变化才人工介入**。

## 目录分层

- `data/clients.json`：静态目录与可信身份。GitHub repository ID、App Store ID/seller、平台、内核等经人工审核后通过 PR 修改；GitHub owner/name 自动观察。
- `data/observations.json`：Refresh 自动生成的核验状态，不手工修改 `ok`、时间戳或 scope。
- `README.md`：根据上述两份数据自动生成，不直接手改。

## 自动更新闭环

`.github/workflows/refresh-directory.yml` 每 6 小时运行一次，也可手动触发。

流程只有四步：

1. 基于当前 `main` 核验官方来源并生成 observations/README。
2. 身份冲突时隐藏入口；普通网络或区域失败标记待确认，并保留已配置入口。
3. 生成文件有变化时，用 Refresh job 的短期 `GITHUB_TOKEN` 普通 push 到 `main`。
4. 最后执行 health gate；异常状态会先写回，再让 workflow 失败。

Refresh 只写 `README.md` 和 `data/observations.json`。push 冲突时本轮失败，下一轮从最新 `main` 重新核验。

## 时间字段

- `last_run_at`：核验任务最近运行时间；同一北京时间日期内不重复更新。
- `last_success_at`：组件最近成功核验时间；失败时保留旧值，并用于 7 天有效期判断。
- `observed_at`：当前观测状态的时间；正常且内容未变时不随每日成功心跳更新。

## 自动处理

Refresh 自动处理新版本、同一 GitHub repository ID 的改名或迁移、官方仓库归档/恢复、活动时间变化和短时失败恢复。

## 人工处理

以下情况修改 `data/clients.json` 并走正常 PR，不直接修改 observations：

- GitHub repository ID 或 App Store app ID/seller 与已确认身份冲突。
- 无结构化证据可判断的合并、停更或继任关系。
- 新增/删除客户端，或修改平台、内核、官方下载入口。
- App Store 发布者迁移，需要更新固定 seller。

## 安全边界

- GitHub 固定 repository ID；owner/name 返回 404 时再按固定 repository ID 查询后才视为缺失；App Store 固定 app ID 与 sellerName。同一 GitHub 仓库改名或迁移自动跟随。
- 身份冲突会隐藏下载入口；404、Latest 缺失或短期失败只标记待确认。已确认的身份冲突只能由同一 scope 的正向核验或人工更新配置恢复。
- `scope` 绑定核验所依据的身份和下载目标；相关配置变化后不沿用旧核验结果。
- GitHub 官方仓库归档后转入历史；动态归档可恢复，静态 `legacy` 不自动恢复。第三方资料只用于历史项目。
- 不自动替换为同名 fork、第三方镜像或继任项目。
- 主分支保持 linear history，禁止删除和强推。Refresh 只使用短期 `contents: write`，不保存长期写密钥。
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

- 上游网络故障：等待下一次 Refresh，不手工改 observations。
- push 冲突：不强推、不自动 rebase，等待下一次 Refresh。
- workflow 或保护规则异常：修复后手动运行一次 Refresh。
- 身份冲突：保持入口隐藏，人工核对并更新静态配置。
- workflow 代码变更：合并后手动运行一次 Refresh 验收。
