# 开始使用

## 站点如何组织

每一份可执行部署方案都是一个 Recipe。它应记录：

1. 适用的模型、量化与硬件；
2. 固定的软件栈与源码 commit；
3. 部署前置条件；
4. 可复制的启动步骤；
5. 服务验证与性能基线；
6. 已知故障及排查入口。

## 新增 Recipe 的流程

1. 复制 [Recipe 模板](../maintainer/recipe-template.md)。
2. 将新页面放入 `deployment-guide/` 下合适的目录。
3. 在 `mkdocs.yml` 的 `nav:` 添加页面，令它出现在站点导航中。
4. 填写已核实的命令、版本和验证结果；不确定的内容保留 `TODO`。
5. 从仓库根目录运行：

    ```bash
    uv run mkdocs build --strict
    ```

## 不要手写重复版本

全局受控栈在 `mkdocs.yml` 的 `extra.stack` 中定义。页面通过宏引用，例如：

```markdown
`{{ "{{ stack.sglang_das.validated_commit }}" }}`
```

当前会渲染为：`{{ stack.sglang_das.validated_commit }}`。
