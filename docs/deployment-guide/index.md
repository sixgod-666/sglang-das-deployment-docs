# 部署方案总览

此处按**具体模型部署形态**收录经过验证的 Recipe。每份页面对应一套可单独启动、验证和回溯的软件栈与配置。

!!! note "命名约定"

    页面标题暂按当前使用的模型/量化/并行策略名称展示。
    在确认模型官方全称或权重目录名后，更新标题和文件名；不要为方便而在多个页面使用同一个模糊简称。

| 部署方案 | 状态 | 量化 / 并行标识 | 内容入口 |
| --- | --- | --- | --- |
| 1. DeepSeek-V4-Flash-0731-W4A8-INT4-Channel-Attn-W8A8-INT8-Channel-CP8EP8 | 待整理 | W4A8 / CP8EP8 | [打开 Recipe](DeepSeek-V4-Flash-0731-W4A8-INT4-Channel-Attn-W8A8-INT8-Channel(cp8ep8).md) |
| 2. DeepSeek-V4-Flash-0731-W4A8-INT4-Channel-Attn-W8A8-INT8-Channel-DP8EP8 | 待整理 | W4A8 / DP8EP8 | [打开 Recipe](DeepSeek-V4-Flash-0731-W4A8-INT4-Channel-Attn-W8A8-INT8-Channel(dp8ep8).md) |

每份 Recipe 后续独立补齐：受控软件栈、GPU-only / CPU / DFS / PD 等适用形态、启动步骤、真实服务验证、性能基线和故障排查。
