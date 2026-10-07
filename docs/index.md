# SGLang DAS 部署最佳实践

这是独立维护的公开部署站点，用于沉淀经审查、已脱敏的启动方案、运行前置条件、软件栈版本和验证方法。

!!! warning "安全与内容边界"

    本站不替代 SGLang 官方文档，也不包含 SGLang DAS 源码、内部网络拓扑、存储根目录或原始脚本归档。
    GPU-only、CPU offload、DFS、W4A8、W8A8、IFB 和 PD 分离等部署实践中的环境特定值统一使用占位符。

## 当前受控栈

- 仓库：[{{ stack.sglang_das.repository }}]({{ stack.sglang_das.repository }})
- 分支：`{{ stack.sglang_das.branch }}`
- 已验证 commit：`{{ stack.sglang_das.validated_commit }}`

## 快速入口

- [开始使用](getting-started/index.md)
- [部署方案总览](deployment-guide/index.md)
- [Benchmark Tools](benchmark-tools/index.md)
- [软件栈](reference/software-stack.md)
- [Recipe 模板](maintainer/recipe-template.md)
