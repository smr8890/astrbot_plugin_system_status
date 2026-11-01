# astrbot_plugin_system_status

移植自 [yenai-plugin](https://github.com/yeyang52/yenai-plugin) 的系统状态卡片，使用 AstrBot T2I 服务渲染。

## 功能

- `/状态`：系统状态卡片
  - Bot 信息区：多平台列表、头像、平台、运行时间、消息数、会话数、群聊/私聊数
  - 系统信息：系统版本、主机名、运行时间、插件数
  - 资源使用：CPU、RAM、SWAP、GPU 圆环
  - 磁盘使用：容量环、进度条、磁盘 IO
  - 网络状态：上传/下载速度、累计流量、网络测试、网络折线图
  - 进程负载：进程状态统计、Top 进程列表
  - 系统信息：优先使用 `fastfetch`，未安装时自动用 Python 生成
- `/状态监控`：CPU、RAM、Network、Disk IO 四张 SVG 折线图

## 安装

将插件目录放入 AstrBot 的 `data/plugins/`，重启 AstrBot 或重载插件。

依赖：

- AstrBot >= 4.x（使用 `Star.html_render` / T2I）
- `psutil`
- `aiohttp`
- `Pillow`

AstrBot 官方镜像已包含这些依赖；其他环境如缺失可自行安装。

## 配置

插件配置通过 AstrBot WebUI 的插件配置页面修改，主要配置项：

- `monitor.interval`：监控采样间隔，默认 60 秒
- `monitor.history_size`：每个指标保留的数据点，默认 60
- `systemResources`：显示 CPU、RAM、SWAP、GPU
- `processLoad`：进程区开关、Top 数量、排序方式
- `showFastFetch`：是否显示系统信息区
- `psTestSites`：网络测试站点、超时、并发
- `style.backdrop`：背景图 API；失败时使用内置背景
- `style.backdropDefault`：`random` 或指定内置背景文件名
- `style.startColumn`：是否启用多列布局；默认关闭，渲染为一张长图
- `embed_font`：是否内嵌 FZB / NZBZ 字体
- `render.viewport_width`：状态卡片视口宽度
- `render.monitor_viewport_width`：监控页视口宽度
- `bot.name` / `bot.avatar`：Bot 名称和头像覆盖
- `hostname.override`：Docker 容器主机名覆盖

## 数据说明

- 插件运行在 Docker 中时，资源数据是**容器内可见视角**；CPU/内存/磁盘可能与宿主机不同。
- 总消息数来自 AstrBot `platform_stats`，可能存在延迟。
- 会话数来自 `conversations` 表；群聊/私聊数根据会话 ID 中的 `GroupMessage` / `FriendMessage` 统计。
- 多平台列表会遍历当前运行中的平台适配器。平台消息数按适配器类型统计，因此同类型多实例会共享统计值。
- GPU 仅在有 `nvidia-smi` 时显示，否则自动隐藏。
- FastFetch 未安装时，会生成等价的 Python 系统信息，保持卡片结构完整。

## 许可与致谢

- 卡片视觉、CSS、图标、背景和字体（FZB、NZBZ、MapleMono 图标子集）来源：[yenai-plugin](https://github.com/yeyang52/yenai-plugin)，遵循 GPL-3.0。
- 本项目自身遵循仓库中的 AGPL-3.0 许可。
- 如果公开分发，请保留上述来源说明，并注意 FZB/NZBZ 字体及背景图片的授权。
