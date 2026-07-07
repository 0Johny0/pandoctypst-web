# Pandoc Web

基于 pandoc/typst 的文档转换与编辑平台。

## 功能

- **转换器**：上传文件转换为 PDF / HTML / DOCX / EPUB / Markdown / LaTeX
- **编辑器**：在线创建和编辑 `.typ` / `.md` 文件，实时预览，编译下载 PDF / EPUB

## 快速开始

```bash
docker compose up --build
```

访问 `http://localhost:5000`。

## 字体

需要在宿主机安装中文字体。Linux 通常已有 `font-noto-cjk`，macOS 需挂载系统字体目录。

## 部署

镜像发布在 GitHub Container Registry：

```bash
docker pull ghcr.io/<your-username>/pandoc-web:main
docker run -p 5000:5000 -v /usr/share/fonts:/usr/share/fonts:ro ghcr.io/<your-username>/pandoc-web:main
```
