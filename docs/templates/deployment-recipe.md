# 部署 Recipe 模板

复制本模板创建一份经过验证的部署方案。未核实的信息保留 `TODO`，不要猜测参数、默认值或预期结果。

## 1. 概览

- **模型**：TODO
- **部署模式**：GPU-only / CPU offload / DFS / IFB / PD 分离
- **量化**：TODO
- **适用硬件与节点数**：TODO
- **验证状态**：草稿 / smoke tested / benchmark validated
- **最后验证日期**：TODO

## 2. 受控软件栈

- SGLang DAS 分支：`{{ stack.sglang_das.branch }}`
- SGLang DAS commit：`{{ stack.sglang_das.validated_commit }}`
- Mooncake commit：TODO
- wheel SHA256：TODO
- 容器镜像 digest：TODO
- 模型 revision：TODO

## 3. 前置条件

- 容器、模型路径、节点拓扑：TODO
- 网络和端口：TODO
- 缓存目录与清理规则：TODO
- 环境变量：TODO

## 4. 启动步骤

### 4.1 启动前检查

```bash
# TODO: 填入可执行预检命令。
```

### 4.2 服务启动

=== "单机或 GPU-only"

    ```bash
    # TODO
    ```

=== "CPU / DFS"

    ```bash
    # TODO
    ```

=== "PD 分离"

    ```bash
    # TODO: 按 Prefill、Decode、Router 顺序填写。
    ```

## 5. 服务验证

```bash
# TODO: 填入真实请求验证；仅 /v1/models 不应作为唯一就绪判断。
```

**预期结果**：TODO

## 6. 性能基线

| 场景 | 并发 | 输入/输出 | 指标 | 结果 | Run 证据 |
| --- | ---: | --- | --- | --- | --- |
| TODO | TODO | TODO | TTFT / 吞吐 / 命中率 | TODO | TODO |

## 7. 故障排查

| 现象 | 首先检查 | 根因 / 处理 |
| --- | --- | --- |
| TODO | TODO | TODO |
