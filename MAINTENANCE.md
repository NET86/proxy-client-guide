# 维护说明

这个项目的目标是长期、低人工维护代理客户端目录。设计原则是：**日常变化自动处理，高风险身份变化才人工介入**，不为极少出现的边界继续叠加额外系统。

## 目录分层

- `data/clients.json`：静态目录与可信身份。项目地址、GitHub repository/owner ID、App Store ID/seller、平台、内核等需要人工审核后通过 PR 修改。
- `data/observations.json`：自动核验结果。由 Refresh workflow 生成，不手工填写 `ok`、时间戳或 scope。
- `README.md`：由脚本根据上述两份数据生成，不直接手改。

## 自动更新闭环

`.github/workflows/refresh-directory.yml` 每 6 小时运行一次，也可手动触发。

流程只有四步：

1. 从当前 `main` 核验官方来源并生成 README/observations。
2. 对确定的身份异常、来源失效等情况先隐藏不可信下载入口。
3. 如生成文件发生变化，用 `refresh-production` Environment 中的仓库专用 deploy key 普通 push 到 `main`。
4. 执行 health gate；异常会让 workflow 失败并保留可见状态。

Refresh 只写 `README.md` 和 `data/observations.json`。如果 push 恰好与新的 `main` 冲突，本次任务直接失败，不在同一个 run 中重放代码或复杂合并；下一次定时运行会从新的 `main` 重新核验并自动恢复。

## 哪些情况不需要人工

以下变化由 Refresh 自动处理：

- 官方仓库或 App Store 正常发布新版本。
- 项目近期活动时间变化。
- 短时网络失败后恢复。
- 版本状态、README 展示和更新时间的普通变化。

## 哪些情况需要人工

只有静态可信事实发生变化时才人工处理，例如：

- GitHub 仓库、owner 或 App Store seller 与已确认身份不一致。
- 项目迁移、合并、停更或继任关系需要判断。
- 新增/删除客户端，或修改平台、内核、官方下载入口。
- 已确认官方项目发生合法身份迁移，需要更新固定 ID/seller。

处理方式是更新 `data/clients.json` 并走正常 PR；不要直接修改 observations 伪造恢复。

## 安全边界

- GitHub 来源固定 repository ID 与 owner ID；App Store 固定 app ID 与 sellerName。
- 身份核验只证明来源连续，不代表二进制安全。
- 不自动把失效项目替换为同名 fork、第三方镜像或继任项目。
- 第三方历史资料不进入主下载列。
- 主分支只保留一个保护 ruleset：PR + required `validate` + linear history + 禁止删除/强推；Refresh deploy key 作为唯一自动写回例外。
- `GITHUB_TOKEN` 在 Refresh 中保持只读，生成文件写入使用独立 deploy key。
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
- deploy key / Environment 配置损坏：修复 GitHub 设置后手动 dispatch 一次 Refresh。
- 身份异常：保持下载入口隐藏，人工确认官方迁移后通过 PR 更新静态 pin。
- workflow 自身修改：合并后手动 dispatch 一次当前 `main` 的 Refresh 做真实验收。

如果未来确实出现长期无法靠这套简单闭环解决的问题，再针对真实故障增加机制；不提前为假设场景增加常驻复杂度。
