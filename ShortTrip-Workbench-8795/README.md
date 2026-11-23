# ShortTrip 8795 工作台

ShortTrip 根据日期、预算、兴趣和预约生成行程，提供可比较的备选、逐日地图，以及核验后采用的手动和 AI 修改。

## 下载与启动

1. 从 [GitHub Releases](https://github.com/ccc-cccst/travel-assistant/releases/tag/workbench-8795-20261123) 下载 `ShortTrip-Workbench-8795-20261123.zip`，完整解压到可写目录。ZIP 包含源码、Windows 启动脚本、固定旅行快照和演示案例。
2. 安装 Windows 64 位 Python 3.12，双击 `Start.cmd`。第一次运行会联网安装 Python 依赖。
3. 打开 [新建规划](http://127.0.0.1:8795/?new=1) 或 [上海至杭州演示归档](http://127.0.0.1:8795/?trip=personalized&day=1)。也可以打开 [方案比较体验测试](http://127.0.0.1:8795/study.html)。

如果本机已经运行另一套 ShortTrip，可从 PowerShell 使用不同端口启动：

```powershell
.\Start.cmd --port 8895 --planner-port 8881
```

从表单填写城市、人数、出发日期和天数可使用程序规划。启用 DeepSeek 前，在启动进程的环境中设置自己的 `DEEPSEEK_API_KEY`。高德在线事实与路线查询可配置自己的 `AMAP_KEY`。地图底图和在线路线查询需要网络。使用本地 Qwen 时需要另行启动兼容模型服务。票房、库存及指定日期价格以实际渠道为准。

## 目录

| 目录 | 内容 |
|---|---|
| `shorttrip-webui/shorttrip-webui` | 8795 网页工作台、地图、在线事实和编辑 |
| `combined version_personalized` | 需求理解、构想、试排、核验、比较和实验工具 |
| `ShortTrip/chinatravel/planning` | 底层构造器与独立验证 |
| `ShortTrip/artifacts/data` | ZIP 内含固定快照、版本和数据收据 |
| `ShortTrip/finetuning` | 专项训练代码，训练样本与模型权重另行准备 |

[项目完整介绍](combined%20version_personalized/docs/2026-11-23-ShortTrip当前项目完整介绍.md)覆盖算法、数据、交互和历史实测。文中的历史实验引用指向原开发工作区，对完整历史记录的回放需要相应实验产物。ChinaTravel 和 Leaflet 组件的许可保留在交付目录。数据来源和固定版本见快照内的 `install.json` 与相关收据。

本地规划记录、编辑修订、反馈和缓存会在使用后写入解压目录。建议每位使用者在自己的目录运行。
