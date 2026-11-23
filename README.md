# ShortTrip Assistant

一个面向课程项目的短途旅行助手：从 ChinaTravel 复现出发，研究需求理解、行程体验评价、个性化和局部修复。

当前 8795 统一工作台已接入个性化规划、分项比较、地图与行程编辑。查看 [工作台源码与启动说明](ShortTrip-Workbench-8795/README.md)，或从 [GitHub Releases](https://github.com/ccc-cccst/travel-assistant/releases/tag/workbench-8795-20261123) 下载包含固定旅行快照的运行包。

下方的安装步骤用于复现原始 ChinaTravel 基线。运行最新工作台请使用上方的工作台说明。

## 第一次使用

需要 Git、Windows PowerShell 和 Python 3.12。先接受仓库协作者邀请，再克隆：

```powershell
git clone https://github.com/ccc-cccst/travel-assistant.git
cd travel-assistant
.\Setup-Project.ps1
```

安装脚本会创建独立虚拟环境、安装固定依赖、下载固定版本的数据、准备离线查询并检查环境。它不会调用付费模型。网络或下载出错时保留错误信息，修复后可以重新执行。

数据、虚拟环境和个人密钥不在 GitHub 中，每位成员在本机准备自己的环境。

```powershell
# 已有完整数据时，只准备或更新 Python 环境
.\Setup-Project.ps1 -SkipData
# 配置检查，不调用模型
.\reproduction\Start-DeepSeekFlash.ps1 -DryRun
# 单条试跑；运行时在本机隐藏输入自己的 API 密钥
.\reproduction\Start-DeepSeekFlash.ps1 -Split smoke_local
# 不调用模型的上游单元测试
.\ChinaTravel\.venv\Scripts\python.exe .\reproduction\run_unit_tests.py
```

Flash 启动器保存了 2026-10-17 实验使用的模型 ID 和版本声明。远程模型服务可能变化，正式对比前要重新核实并记录版本。

## 目录

| 位置 | 用途 |
| --- | --- |
| `ChinaTravel/` | 官方源码快照，作为普通文件纳入本仓库，可在自己的功能分支上修改 |
| `reproduction/` | 本项目的运行、评测、环境检查和历史分析工具 |
| `reproduction/splits/` | 小规模实验固定样本的 ID，可重新生成查询文件 |
| `scripts/` | 发布前检查等协作辅助工具 |
| `docs/` | 项目范围、研究方向和实验记录说明 |
| `UPSTREAM.json` | 上游源码及数据集的固定版本 |
| `download_manifest.json` | 官方数据文件校验信息 |
| `CONTRIBUTING.md` | 新手协作流程、邀请朋友和提交修改的方法 |

`datasets/`、模型输出和运行日志只保存在本机。历史报告依赖的结果文件没有一起上传；历史分析脚本必须在具备相应运行记录时使用。

## 团队协作

每项任务建一个分支，通过 Pull Request（合并申请）让另一位成员看过再合入 `main`。具体操作见 [协作指南](CONTRIBUTING.md)。

首次发布由仓库所有者在本机登录 GitHub 后执行：

```powershell
gh auth login --hostname github.com --git-protocol https --web
.\Publish-GitHub.ps1 -RepositoryName travel-assistant -Visibility public
```

当前团队仓库为公开仓库。上述命令可用于连接或发布到这个仓库，上传前检查已提交文件；发布脚本不会自动邀请任何人。

## 来源与许可

ChinaTravel 来自 [LAMDA-NeSy/ChinaTravel](https://github.com/LAMDA-NeSy/ChinaTravel)，固定提交为 `f445e0011f42594fcc29b8c752ece06ded9a8218`。保留了原始 MIT 许可和作者信息，详见 [第三方说明](THIRD_PARTY_NOTICES.md)。团队原创部分的公开许可尚未另行指定。项目仍处于复现与开发阶段，未宣称已复现论文全量成绩。
