# SGLang DAS 部署最佳实践站

这是一个独立的 [MkDocs Material](https://squidfunk.github.io/mkdocs-material/) 文档站，只收录已脱敏的 SGLang DAS 部署 Recipe、受控软件栈和验证记录。

## 安全边界

- 不包含 SGLang DAS 源码、上游仓库完整 Git 历史或原始启动脚本归档。
- 节点、分布式初始化地址、DFS 根目录和内部工作目录必须以[部署占位符约定](docs/reference/deployment-placeholders.md)中的标记表示。
- 模型路径 `/ai_data/models/...` 和已验证的 `/guofy/packages/...` 路径按原样保留。
- 向该公开仓库提交前，必须执行严格构建和敏感信息扫描。

## 本地预览

在此目录运行：

```bash
uv run mkdocs serve --strict
```

浏览器打开 <http://127.0.0.1:8000/>。修改 `docs/` 下的 Markdown 或 `mkdocs.yml` 后会自动刷新。

## 严格构建

```bash
uv run mkdocs build --strict
```

输出写入 `site/`。该目录是构建产物，已忽略，不提交到 Git。

## 容器内临时预览

待仓库在目标容器中可用后，先在仓库根目录运行：

```bash
uv run mkdocs build --strict
python -m http.server 8000 --directory site
```

如果需要从容器外访问，必须先确认该容器的端口映射和内网安全要求。正式发布应改用 Nginx 或其他静态文件服务；不要将 `mkdocs serve` 作为长期线上服务。
