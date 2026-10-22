# 团队协作指南

先阅读 [团队协作规则](docs/TEAM_RULES.md)。本页保留具体操作步骤；AI 编程助手同时读取 [AGENTS.md](AGENTS.md)。

## 先理解四个词

- 仓库 Repository：GitHub 上保存代码和修改历史的项目。
- 分支 Branch：为一项任务单独工作的版本，例如 `feat/experience-metrics`。
- 提交 Commit：给本地修改保存一个带说明的历史记录。
- Pull Request（PR）：请求把分支里的修改合并到团队主分支。

推荐流程：同步主分支 → 新建任务分支 → 修改并验证 → 提交 → 上传分支 → 提 PR → 同伴检查 → 合并。

## 仓库所有者邀请朋友

1. 打开 GitHub 项目页面，进入 **Settings → Collaborators**（界面也可能显示 Collaborators and teams）。
2. 点击 **Add people**，输入朋友的 GitHub 用户名，发送邀请。
3. 当前仓库公开，任何人都能查看和克隆；朋友接受协作者邀请后，才获得向本仓库上传分支的权限。

朋友使用自己的账号，不需要共享账号密码或 API 密钥。发送邀请前核对用户名。

官方说明：https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/repository-access-and-collaboration/inviting-collaborators-to-a-personal-repository

## 不熟悉命令时使用 GitHub Desktop

从 https://desktop.github.com/ 下载并登录自己的账号。

1. **File → Clone repository**，选择团队仓库，设置本机路径。
2. 在编辑器中打开克隆后的文件夹，按 README 准备环境。
3. 开始新任务前切回 `main`，点击 **Fetch origin**；出现 **Pull origin** 时拉取更新。
4. **Current branch → New branch**，给本次任务起名。
5. 改完后查看 Changes，只选择本次任务文件；填写说明，点击 Commit。
6. 点击 **Publish branch / Push origin** 上传分支，再点击 **Create Pull Request**。
7. 在 PR 里写明解决什么问题、怎么验证，请一位同伴检查后合并。

如果出现合并冲突，先保留双方修改并一起处理。合并后再从最新 `main` 开始下一项任务。

## 命令行示例

工作区没有未保存修改时，开始新任务：

```powershell
git switch main
git pull --ff-only
git switch -c feat/experience-metrics
```

完成修改后，先确认需要提交哪些文件。下面以修改 README 为例：

```powershell
git status
git diff
git add README.md
python .\scripts\check_publish.py
git commit -m "docs: clarify project setup"
git push -u origin HEAD
gh pr create
```

如果没有安装 gh，也可以在 GitHub 网页上点击 **Compare & pull request**。

## 每个 PR 写什么

- 解决的问题或对应实验案例。
- 修改了什么，以及预期行为。
- 验证方式：命令、样例、对照结果。
- 暂时还存在的限制。

## 文件和实验约定

- 源码、固定配置、样本 ID 和小型人工整理结果可以提交。
- 密钥只在本机隐藏输入或使用进程环境变量；不要写进代码、聊天截图、日志或 PR。
- 虚拟环境、下载数据、原始模型响应、临时文件和完整日志由 `.gitignore` 排除。
- 不同方法的模型版本、样本、数据版本与预算要能对应；调试结果和正式对照分开记录。
- `ChinaTravel/` 已作为普通源码文件保存，不需要执行 `git submodule`，也不要在里面再次 `git init`。

## 分工建议

- 数据与工具：地点、路线、天气、字段来源和缓存。
- 规划与验证：候选、硬约束检查、体验指标和局部修复。
- 交互与实验：标签、行程展示、修改差异、对照实验和记录。

同一时间尽量避免多人修改同一函数。每次 PR 聚焦一项任务，便于检查与合并。
