# 软件栈

此页是部署文档引用的当前受控栈。发布一轮经验证的新栈时，更新 `mkdocs.yml` 的 `extra.stack`，并在 Git 历史中记录变更原因和验证证据。

| 项目 | 值 |
| --- | --- |
| SGLang DAS 仓库 | [{{ stack.sglang_das.repository }}]({{ stack.sglang_das.repository }}) |
| 分支 | `{{ stack.sglang_das.branch }}` |
| 已验证 commit | `{{ stack.sglang_das.validated_commit }}` |
| Mooncake commit | `{{ stack.mooncake.commit }}` |
| wheel SHA256 | `{{ stack.wheel.sha256 }}` |
| 容器镜像 digest | `{{ stack.container_image.digest }}` |
| 模型 revision | `{{ stack.model.revision }}` |

!!! note "当前本地 checkout 与受控 commit"

    本站固化的是用于验证的 commit，不自动假定本地 checkout 与它一致。
    在部署或压测前应显式检查 `git rev-parse HEAD`，并记录实际 wheel、镜像和 Mooncake 指纹。
