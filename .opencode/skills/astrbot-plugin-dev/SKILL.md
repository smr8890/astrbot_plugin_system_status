---
name: astrbot-plugin-dev
description: 开发 astrbot_plugin_system_status 时使用，包含模块结构、开发流程、中文提交和版本确认习惯。
---

# AstrBot 系统状态插件开发规范

## 适用范围

用于开发本插件：

- 状态卡片 `/状态`
- 监控图表 `/状态监控`
- 平台适配器、配置项、渲染、缓存和版本发布

同时遵守插件根目录以及 AstrBot 的 `AGENTS.md`。若本 skill 与用户明确指令冲突，以用户当次指令为准。

## 模块结构

- `main.py`：插件注册、命令入口、初始化。
- `services/collector.py`：采集编排，保持 `SystemCollector.collect()` 和 `export_identity_cache()` 接口稳定。
- `services/platforms.py`：平台列表、昵称/头像、身份缓存。
- `services/system_info.py`：系统信息、硬件信息、fastfetch。
- `services/processes.py`：进程采集。
- `services/resources.py`：资源圆环、磁盘。
- `services/network.py`：网络与站点测试。
- `services/db_stats.py`：数据库消息/会话统计。
- `services/renderer.py`：HTML 渲染和头像解析。
- `services/sampler.py`：后台资源采样。
- `_conf_schema.json`：WebUI 配置项。
- `resources/state`：模板、CSS、JS、图标、背景。

优先修改职责对应的模块，不要重新把逻辑堆回 `collector.py`。

## 开发流程

1. 先读相关实现和用户当前配置，确认实际问题。
2. 只做最小必要修改，避免无关格式化和重构。
3. 修改后验证语法、配置 JSON 和关键输出。
4. 需要提交时，先给出拟定提交信息，等待用户确认后再执行。
5. 只在用户明确要求时升级版本号。

## 提交与版本习惯

- 使用中文沟通。
- 使用中文 conventional commit，例如：

```text
feat: 完善 Discord 与 QQ Webhook 适配
fix: 修正头像缓存未刷新的问题
refactor: 拆分系统状态采集模块
chore: 升级版本到 1.6.0
```

- 用户说“拟定提交”时，只展示提交信息和改动摘要，不执行 `git commit`。
- 用户明确说“提交”后再执行提交。
- 用户说“拟定版本更新”时，先给出版本号和变更方案，等待确认后再修改版本文件。
- 用户没有明确要求时，不升级版本号。

## 版本号位置

升级版本号时，以下位置必须同步修改：

- `metadata.yaml`：`version` 字段。
- `main.py`：`@register(...)` 的版本参数。
- `services/collector.py`：`_read_plugin_version()` 中读取 `metadata.yaml` 失败时的回退版本号。
- `CHANGELOG.md`：新增对应版本章节。
