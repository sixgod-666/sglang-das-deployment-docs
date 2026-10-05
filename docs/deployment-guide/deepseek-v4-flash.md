# DeepSeek-V4-Flash

!!! info "页面状态"

    这是站点骨架的示例页面，用于确认 MkDocs Material 的 Markdown、标签页、代码复制和版本宏均可工作。
    启动命令和参数由后续实际验证后填入；本页不声明任何未经核实的部署配置。

## 受控软件栈

| 组件 | 当前记录 |
| --- | --- |
| SGLang DAS 分支 | `{{ stack.sglang_das.branch }}` |
| SGLang DAS 已验证 commit | `{{ stack.sglang_das.validated_commit }}` |
| Mooncake commit | `{{ stack.mooncake.commit }}` |
| wheel SHA256 | `{{ stack.wheel.sha256 }}` |
| 镜像 digest | `{{ stack.container_image.digest }}` |
| 模型 revision | `{{ stack.model.revision }}` |

## 部署模式

=== "GPU-only"

    ```bash
    # TODO: 填入已验证的 GPU-only 启动脚本或命令。
    ```

=== "CPU offload"

    ```bash
    # TODO: 填入已验证的 CPU offload 启动脚本或命令。
    ```

=== "DFS"

    ```bash
    # TODO: 填入已验证的 DFS 启动脚本或命令。
    ```

=== "PD 分离"

    ```bash
    # TODO: 分别填入 Prefill、Decode、Router 的已验证启动步骤。
    ```

## 验证

```bash
# TODO: 填入服务就绪与真实请求验证命令。
```

## 关联模板

完整填写结构见 [Recipe 模板](../maintainer/recipe-template.md)。
