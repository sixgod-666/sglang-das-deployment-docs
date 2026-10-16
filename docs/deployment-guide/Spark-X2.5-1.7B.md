# Spark-X2.5-1.7B

本页合并 `02-cpu-13-yb.conf` 与 `04-dfs-13-yb.conf` 两份脱敏配置快照，结构参照 DeepSeek CP8/EP8 部署页面。两种方案均为单节点 Prefill / Decode（PD）分离，区别在于 Mooncake 缓存层：CPU 方案使用 CPU segment，DFS 方案额外启用 DFS 与 SSD Offload。

!!! warning "使用边界"

    这是历史配置的公开脱敏快照，不是可直接执行的启动脚本。部署前必须按目标环境替换节点、模型、软件包、缓存根目录和日志路径，并重新核对 wheel、镜像、NIC 拓扑、目录权限与容量。

## 1. 配置总览

| 项目 | CPU（L1 + L3） | DFS（L1 + L3 + L4） |
| --- | --- | --- |
| 原始配置 | `02-cpu-13-yb.conf` | `04-dfs-13-yb.conf` |
| 模型 | Spark-X2.5-1.7B | Spark-X2.5-1.7B |
| Prefill | 两个 TP2 实例 | 两个 TP2 实例 |
| Decode | TP2 | TP2 |
| Mooncake client segment | 300GB | 150GB |
| local buffer | 未配置 | 4GB |
| DFS / SSD Offload | 未启用 | bucket DFS + SSD Offload |
| Router | PD disaggregation + mini-lb | PD disaggregation + mini-lb |
| RDMA 设备 | `shca_0,shca_1,shca_2,shca_3` | `shca_0,shca_1,shca_2,shca_3` |

## 2. 脱敏约定

| 原配置类别 | 公开文档记号 |
| --- | --- |
| 单节点 P/D/Mooncake/Router 地址 | `<NODE_IP>` |
| 模型目录 | `<MODEL_ROOT>/Spark-X2.5-1.7B` |
| DFS / Offload 根目录 | `<DFS_ROOT>` |
| 拓扑、chat template、环境准备脚本 | `<PACKAGE_ROOT>/Spark_config/...` |
| Python wrapper 根目录 | `<WRAPPER_ROOT>` |
| 运行日志 | `<LOG_FILE>` |

## 3. CPU（L1 + L3）

=== "自动化配置"

    ```ini
    [common]
    model_path = <MODEL_ROOT>/Spark-X2.5-1.7B
    bootstrap_port = 31820
    p_node_ip = <NODE_IP>
    d_node_ip = <NODE_IP>
    mc_node_ip = <NODE_IP>
    ib_devices = shca_0,shca_1,shca_2,shca_3
    topo_config = <PACKAGE_ROOT>/Spark_config/deep_topo.config

    [pd_disagg]
    prefill_nodes = ${common:p_node_ip}
    decode_nodes = ${common:d_node_ip}
    mooncake_clients = ${common:mc_node_ip}
    router_ip = ${common:p_node_ip}

    [mooncake_master]
    rpc_port = 50156
    rpc_address = ${common:p_node_ip}
    enable_http_metadata_server = true
    http_metadata_server_host = ${common:p_node_ip}
    http_metadata_server_port = 8095
    rpc_enable_tcp_no_delay = true
    enable_metric_reporting = true
    metrics_port = 9014
    default_kv_lease_ttl = 30000
    allocation_strategy = free_ratio_first
    eviction_ratio = 0.1
    eviction_high_watermark_ratio = 0.9

    [mooncake_master_global]
    NODE_IP = ${common:p_node_ip}
    MASTER_BIND_HOST = ${common:p_node_ip}
    METADATA_BIND_HOST = ${common:p_node_ip}

    [mooncake_client]
    master_server_address = ${common:p_node_ip}:50156
    metadata_server = http://${common:p_node_ip}:8095/metadata
    host = ${common:mc_node_ip}
    port = 50052
    enable_http_server = true
    http_port = 9300
    protocol = rdma
    device_names = shca_0
    global_segment_size = 300GB

    [mooncake_client_global]
    MOONCAKE_LOCAL_HOSTNAME = ${common:mc_node_ip}
    MOONCAKE_TE_META_DATA_SERVER = http://${common:p_node_ip}:8080/metadata
    MASTER_HOST = ${common:p_node_ip}
    STORE_BIND_HOST = ${common:p_node_ip}
    MC_ENABLE_DEST_DEVICE_AFFINITY = 1
    MC_IB_GID_INDEX = 0
    MC_IB_PCI_RELAXED_ORDERING = 0

    [router]
    pd-disaggregation = true
    prefill_endpoints = http://${common:p_node_ip}:20001 31821,http://${common:p_node_ip}:30000 31820
    decode_port = 30001
    mini-lb = true
    request-timeout-secs = 21600
    host = ${common:p_node_ip}
    port = 30320
    log-level = info
    ```

=== "Prefill / Decode 参数"

    ```ini
    [sglang_prefill]
    tp = 2
    model-path = ${common:model_path}
    mem-fraction-static = 0.85
    served-model-name = spark-x2.5-1.7b
    max-running-requests = 12
    tool-call-parser = spark
    reasoning-parser = qwen3
    allow-auto-truncate = true
    enable-cache-report = true
    chat-template = <PACKAGE_ROOT>/Spark_config/chat_template.jinja
    attention-backend = triton
    prefill-attention-backend = triton
    decode-attention-backend = triton
    disaggregation-mode = prefill
    disaggregation-transfer-backend = mooncake
    disaggregation-bootstrap-port = 31820
    disaggregation-ib-device = ${common:ib_devices}
    chunked-prefill-size = 16384
    page-size = 64
    enable-unified-cache-external-linker = true
    tokenizer-backend = fastokens

    [sglang_prefill_2]
    # 与 sglang_prefill 相同；bootstrap port=31821，port=20001。

    [sglang_decode]
    tp = 2
    model-path = ${common:model_path}
    mem-fraction-static = 0.85
    context-length = 1048576
    served-model-name = spark-x2.5-1.7b
    max-running-requests = 12
    tool-call-parser = spark
    reasoning-parser = qwen3
    allow-auto-truncate = true
    enable-cache-report = true
    disaggregation-mode = decode
    disaggregation-transfer-backend = mooncake
    disaggregation-ib-device = ${common:ib_devices}
    disaggregation-decode-retraction-backup = host_pool
    hicache-ratio = 0.4
    triton-attention-num-kv-splits = 128
    port = 30001
    tokenizer-backend = fastokens
    ```

## 4. DFS（L1 + L3 + L4）

=== "自动化配置"

    ```ini
    [common]
    node_ip = <NODE_IP>
    dfs_root = <DFS_ROOT>
    ib_devices = shca_0,shca_1,shca_2,shca_3
    model_path = <MODEL_ROOT>/Spark-X2.5-1.7B
    chat_template = <PACKAGE_ROOT>/Spark_config/chat_template.jinja
    dcu_prepare = <PACKAGE_ROOT>/Spark_config/dcu_host_prepare.sh

    [pd_disagg]
    prefill_nodes = ${common:node_ip}
    decode_nodes = ${common:node_ip}
    mooncake_clients = ${common:node_ip}
    router_ip = ${common:node_ip}

    [mooncake_master_global]
    NODE_IP = ${common:node_ip}
    MASTER_BIND_HOST = ${common:node_ip}
    METADATA_BIND_HOST = ${common:node_ip}
    MOONCAKE_ENABLE_DFS = 1
    MOONCAKE_DFS_FS_ADAPTER = posix
    MOONCAKE_DFS_ROOT_DIR = ${common:dfs_root}
    MOONCAKE_DFS_ALLOCATOR_TYPE = bucket
    MOONCAKE_DFS_BUCKET_CAPACITY = 4294967296
    MOONCAKE_DFS_MAX_BUCKET_COUNT = 5120
    MOONCAKE_DFS_ALIGNMENT = 4096
    MOONCAKE_DFS_SINGLE_TENANT = True
    MOONCAKE_DFS_BUCKET_META_LOG_THRESHOLD = 4194304
    MOONCAKE_DFS_EVICTION_ENABLED = True
    MOONCAKE_DFS_EVICTION_HIGH_WATERMARK = 0.80
    MOONCAKE_DFS_EVICTION_LOW_WATERMARK = 0.60
    MOONCAKE_DFS_EVICTION_CHECK_INTERVAL = 2
    MOONCAKE_DFS_DEFERRED_FREE_SECONDS = 10

    [mooncake_master]
    enable_offload = true
    rpc_port = 50156
    rpc_address = ${common:node_ip}
    enable_http_metadata_server = true
    http_metadata_server_host = ${common:node_ip}
    http_metadata_server_port = 8095
    enable_metric_reporting = true
    metrics_port = 9014
    allocation_strategy = free_ratio_first
    eviction_ratio = 0.1
    eviction_high_watermark_ratio = 0.9

    [mooncake_client_global]
    MOONCAKE_LOCAL_HOSTNAME = ${common:node_ip}
    MOONCAKE_TE_META_DATA_SERVER = http://${common:node_ip}:8080/metadata
    MASTER_HOST = ${common:node_ip}
    STORE_BIND_HOST = ${common:node_ip}
    MOONCAKE_OFFLOAD_FILE_STORAGE_PATH = ${common:dfs_root}
    MC_STORE_CLIENT_METRIC = 1
    MOONCAKE_ENABLE_DFS = 1
    MOONCAKE_DFS_FS_ADAPTER = posix
    MOONCAKE_DFS_ROOT_DIR = ${common:dfs_root}
    MOONCAKE_DFS_ALLOCATOR_TYPE = bucket
    MOONCAKE_DFS_BUCKET_CAPACITY = 4294967296
    MOONCAKE_DFS_MAX_BUCKET_COUNT = 5120
    MOONCAKE_OFFLOAD_ENABLED = True
    MOONCAKE_OFFLOAD_STORAGE_BACKEND_DESCRIPTOR = distributed_storage_backend

    [mooncake_client]
    enable_offload = true
    local_buffer_size = 4GB
    master_server_address = ${common:node_ip}:50156
    metadata_server = http://${common:node_ip}:8095/metadata
    host = ${common:node_ip}
    port = 50052
    enable_http_server = true
    http_port = 9300
    protocol = rdma
    device_names = shca_0
    global_segment_size = 150GB
    ```

=== "Prefill / Decode / Router 参数"

    ```ini
    [sglang_prefill]
    command = env -u MC_FORCE_TCP -u LD_PRELOAD python -m sglang.launch_server
    log_file = <LOG_FILE>
    tp = 2
    model-path = ${common:model_path}
    mem-fraction-static = 0.85
    served-model-name = spark-x2.5-1.7b
    max-running-requests = 12
    tool-call-parser = spark
    reasoning-parser = qwen3
    allow-auto-truncate = true
    enable-cache-report = true
    chat-template = ${common:chat_template}
    attention-backend = triton
    disaggregation-mode = prefill
    disaggregation-transfer-backend = mooncake
    disaggregation-bootstrap-port = 31820
    disaggregation-ib-device = ${common:ib_devices}
    chunked-prefill-size = 16384
    page-size = 64
    enable-unified-cache-external-linker = true
    unified-cache-external-linker-backend = mooncake
    mooncake-enable-page-wise-load = true
    mooncake-enable-waiting-queue-dfs-prefetch = true
    mooncake-enable-waiting-queue-dfs-prefetch-workers = 1
    mooncake-enable-waiting-queue-dfs-prefetch-max-requests = 12

    [sglang_prefill_2]
    # 与 sglang_prefill 相同；bootstrap port=31821，port=20001。

    [global_decode]
    ROCSHMEM_MAX_NUM_CONTEXTS = 60
    MOONCAKE_MASTER = localhost:50051
    HIP_VISIBLE_DEVICES = 2,3

    [sglang_decode]
    command = sglang serve
    log_file = <LOG_FILE>
    tp = 2
    model-path = ${common:model_path}
    mem-fraction-static = 0.85
    context-length = 1048576
    max-running-requests = 12
    tool-call-parser = spark
    reasoning-parser = qwen3
    enable-cache-report = true
    disaggregation-mode = decode
    disaggregation-transfer-backend = mooncake
    disaggregation-ib-device = ${common:ib_devices}
    hicache-ratio = 0.4
    triton-attention-num-kv-splits = 128
    port = 30001

    [router]
    command = python -m sglang_router.launch_router
    log_file = <LOG_FILE>
    prefill_endpoints = http://${common:node_ip}:20001 31821,http://${common:node_ip}:30000 31820
    decode_port = 30001
    pd-disaggregation = true
    mini-lb = true
    request-timeout-secs = 21600
    host = ${common:node_ip}
    port = 30320
    log-level = info
    ```

## 5. 两种方案的关键差异

- CPU 方案只使用 Mooncake CPU segment，client segment 为 **300GB**；不设置 DFS bucket、SSD offload 或 DFS prefetch。
- DFS 方案 client segment 为 **150GB**，另有 **4GB local buffer**，启用 DFS bucket、SSD offload、page-wise load 和等待队列 prefetch。
- DFS bucket 为 4GiB，最大 5120 个 bucket；master 全局驱逐水位为 0.80 / 0.60，检查间隔 2 秒，延迟释放 10 秒。
- 两种方案均使用 TP2、Spark/Qwen3 parser、Triton attention、`enable-cache-report=true` 与 `spark-x2.5-1.7b` 服务名。
