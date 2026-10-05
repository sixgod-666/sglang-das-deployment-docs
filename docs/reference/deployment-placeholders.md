# 部署占位符约定

为避免部署 Recipe 暴露内部网络拓扑、存储目录和工作空间，文档中的下列标记必须在实际部署前按目标环境替换。占位符只用于文档示例，不是可直接运行的值。

| 占位符 | 替换为 |
| --- | --- |
| `<PREFILL_NODE_IP>` | Prefill SGLang 服务节点的 IP 地址或可解析 hostname。 |
| `<DECODE_NODE_IP>` | Decode SGLang 服务节点的 IP 地址或可解析 hostname。 |
| `<PREFILL_DIST_INIT_ADDR>` | Prefill 的 RDMA / 分布式初始化地址，必须包含端口，例如 `hostname:port`。 |
| `<DECODE_DIST_INIT_ADDR>` | Decode 的 RDMA / 分布式初始化地址，必须包含端口，例如 `hostname:port`。 |
| `<DFS_ROOT>` | Mooncake DFS 与 Offload 使用的存储根目录。目录权限、容量和挂载方式必须先验证。 |
| `<WORKSPACE_ROOT>` | 内部自动化配置、压测 Run 或工作空间的根目录。 |
| `<LOG_FILE>` | 服务进程重定向日志的目标文件；建议使用当前 Run 独立的路径。 |

## 保留的路径

以下路径记录的是模型或已验证的软件包布局，不属于内部拓扑信息，Recipe 中保留其原始形式：

- 模型绝对路径：`/ai_data/models/...`
- 已验证的配置与依赖路径：`/guofy/packages/...`

## 使用要求

1. 先替换节点、分布式初始化地址和存储目录，再启动服务。
2. 确认 `<PREFILL_DIST_INIT_ADDR>`、`<DECODE_DIST_INIT_ADDR>` 对应的 NIC、端口和路由在目标节点可达。
3. 通过环境搭建章节补齐镜像、wheel 和运行前检查命令；未填充的 `TODO` 不应当视为已验证部署步骤。
4. 向外部共享 Recipe 前，继续使用这些占位符，不要恢复内部 IP、存储根目录或工作空间路径。
