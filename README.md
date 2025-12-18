# Dify Docs PDF Exporter

将 [langgenius/dify-docs](https://github.com/langgenius/dify-docs/tree/main) 提供的 Mintlify 文档渲染为浏览器级别的 PDF。相较于直接抓取 HTML，本工具会在每个页面内复刻正文、还原排版、并以 Playwright 的 `page.pdf()` 输出，最终把所有章节合并成单一 PDF，顺序和左侧导航栏保持一致。

> ⚠️ **依赖的文档仓库**  
> 本工具不会自行下载 Dify 文档。你需要克隆官方仓库并在本地运行 `mintlify dev`。Mintlify CLI 可通过 `npm install -g mintlify` 安装，随后在 dify-docs 仓库内执行 `mintlify dev`（默认监听 `http://localhost:3000`）。

## 快速开始

1. **准备 Dify Docs**
   ```bash
   git clone https://github.com/langgenius/dify-docs.git
   cd dify-docs
   npm install -g mintlify       # 只需一次
   npm install                   # dify-docs 自身依赖
   mintlify dev                  # 启动本地文档站
   ```
2. **安装本工具依赖**
   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   playwright install chromium   # 只需一次
   ```
3. **导出 PDF**
   ```bash
   python dify_doc_crawler.py \
     --url http://localhost:3000/zh/use-dify/getting-started/introduction \
     --output dify_docs.pdf \
     --sidebar-manifest sidebar.json
   ```

## 主要特性

- **左侧栏顺序**：用 Playwright 展开 Mintlify 导航树，逐项抓取链接，顺序与站点完全一致。
- **DOM 重构**：每个页面都会复制正文、自动添加标题/URL 信息，并移除导航、按钮、反馈组件等噪声。
- **阅读友好排版**：自定义 CSS 控制内容宽度、行高、代码块背景（暗色底 + 浅色字）、表格/列表等，减少分页断裂。
- **浏览器级渲染**：依赖 Chromium 完整执行前端脚本、拉取样式，再调用 `page.pdf()`，输出质量高于简单 HTML→PDF 工具。
- **可调参数**：通过 `--content-width`、`--content-font-size`、`--content-line-height` 等参数微调，可使用 `--skip-content-rebuild` 回退到原页面。

## 常用参数

| 参数 | 描述 |
| ---- | ---- |
| `--url` | 起始页面（默认为 “入门 / 介绍”），用于推断侧边栏与 allowed-prefix。 |
| `--allowed-prefix` | 手动限制可抓取链接的前缀；若省略则自动根据 `--url` 前两级路径推断。 |
| `--output` | 最终合并的 PDF 文件名。 |
| `--sidebar-manifest` | 可将解析出的侧边栏顺序写入 JSON，便于排查缺漏。 |
| `--content-width` / `--content-padding` | 控制正文最大宽度与左右留白，单位像素。 |
| `--content-font-size` / `--content-line-height` | 控制正文字号与行高。 |
| `--skip-content-rebuild` | 若想直接打印原页面（不复制正文、不注入标题），可开启此开关。 |

更多参数（导航展开次数、等待时间、颜色主题、PDF 纸张大小等）请见 `python dify_doc_crawler.py --help`。

## 项目结构

```
.
├── dify_doc_crawler.py   # 主脚本：抓取、渲染、导出、合并
├── requirements.txt      # Python 依赖（Playwright、PyPDF2、tqdm）
├── README.md
├── sidebar.json          # 可选：记录一次抓取的侧边栏顺序
└── .gitignore
```

## 常见问题

- **代码块变成白底白字？**  
  现已在打印样式中强制暗色背景与浅色字体。如果仍有问题，检查是否传入了 `--skip-content-rebuild` 或额外的 CSS。

- **只导出 1 页？**  
  请确认 `--url` 与本地服务域名一致，或显式设置 `--allowed-prefix`，以免链接被过滤。

- **Playwright 报错 / Chromium 未安装？**  
  运行 `playwright install chromium` 并重试；如需代理或自定义浏览器，请参考 Playwright 官方文档。

## License

本仓库仅包含导出工具，与 Dify 官方文档同源内容无直接绑定。请遵守上游仓库授权条款。***
