#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Dify 文档抓取与 PDF 导出（Playwright 侧边栏顺序版）"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from PyPDF2 import PdfReader, PdfWriter
from tqdm import tqdm

try:
    from playwright.sync_api import (
        TimeoutError as PlaywrightTimeoutError,
        sync_playwright,
    )
except ImportError:  # pragma: no cover
    sync_playwright = None  # type: ignore
    PlaywrightTimeoutError = None  # type: ignore


DEFAULT_START_URL = "https://docs.dify.ai/zh/use-dify/getting-started/introduction"
DEFAULT_ALLOWED_PREFIX = None
DEFAULT_OUTPUT_FILE = "dify_docs.pdf"
DEFAULT_TEMP_DIR = "temp_pdfs"
DEFAULT_HIDE_SELECTORS = (
    ".feedback-toolbar",
    "#pagination",
    ".chat-assistant-floating-input",
    "footer",
    "[role='contentinfo']",
    "#footer",
    "#sidebar",
    "#header",
    "[role='banner']",
)
DEFAULT_NAV_TOGGLE_SELECTORS = (
    "button[aria-label*='Navigation']",
    "button[aria-label*='导航']",
    "button:has-text('Navigation')",
    "button:has-text('导航')",
)


@dataclass
class DocPage:
    """代表侧边栏中的一个页面"""

    url: str
    label: Optional[str] = None


@dataclass
class PageArtifact:
    """记录渲染结果"""

    url: str
    html_path: Optional[str]
    pdf_path: Optional[str]
    title: Optional[str] = None
    label: Optional[str] = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "从 Dify 中文文档左侧边栏读取页面顺序，使用 Playwright 调用浏览器"\
            "自带的 print to PDF 导出每个页面，并最终合并为单个 PDF"
        )
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_START_URL,
        help=f"入口页面（默认: {DEFAULT_START_URL}）",
    )
    parser.add_argument(
        "--allowed-prefix",
        default=DEFAULT_ALLOWED_PREFIX,
        help="只导出该前缀下的链接（默认: 根据 --url 自动推断）",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT_FILE,
        help=f"导出的合并 PDF 文件名（默认: {DEFAULT_OUTPUT_FILE}）",
    )
    parser.add_argument(
        "--temp-dir",
        default=DEFAULT_TEMP_DIR,
        help=f"单页 PDF/HTML 的临时目录（默认: {DEFAULT_TEMP_DIR}）",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="保留已有临时文件，默认运行前会清空目录",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="最多导出多少个侧边栏链接（默认: 不限制）",
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=0.4,
        help="页面之间的最小间隔，秒（默认 0.4）",
    )
    parser.add_argument(
        "--nav-timeout",
        type=int,
        default=40,
        help="页面跳转超时（秒，默认 40）",
    )
    parser.add_argument(
        "--render-timeout",
        type=int,
        default=20,
        help="等待核心内容选择器出现的超时（秒，默认 20）",
    )
    parser.add_argument(
        "--wait-selector",
        default="#content-area",
        help="确认页面加载完成所等待的 CSS 选择器（默认 #content-area）",
    )
    parser.add_argument(
        "--extra-wait",
        type=float,
        default=0.5,
        help="选择器出现后额外等待的秒数（默认 0.5）",
    )
    parser.add_argument(
        "--page-format",
        default="A4",
        help="PDF 纸张尺寸（默认 A4）",
    )
    parser.add_argument(
        "--margin",
        type=int,
        default=12,
        help="PDF 页边距，单位 mm（默认 12）",
    )
    parser.add_argument(
        "--device-scale",
        type=float,
        default=1.25,
        help="Chromium 渲染 deviceScaleFactor（默认 1.25）",
    )
    parser.add_argument(
        "--viewport-width",
        type=int,
        default=1200,
        help="Chromium viewport 宽度（默认 1200）",
    )
    parser.add_argument(
        "--viewport-height",
        type=int,
        default=1800,
        help="Chromium viewport 高度（默认 1800）",
    )
    parser.add_argument(
        "--color-scheme",
        choices=("light", "dark", "no-preference"),
        default="light",
        help="强制的 prefers-color-scheme（默认 light）",
    )
    parser.add_argument(
        "--locale",
        default="zh-CN",
        help="Chromium locale（默认 zh-CN）",
    )
    parser.add_argument(
        "--sidebar-selector",
        default="#sidebar-group",
        help="包含文档导航的容器选择器（默认 #sidebar-group）",
    )
    parser.add_argument(
        "--sidebar-scroll-container",
        default="#sidebar-content",
        help="需要滚动以加载全部链接的容器（默认 #sidebar-content）",
    )
    parser.add_argument(
        "--sidebar-link-selector",
        default="#sidebar-group a[href]",
        help="用于读取链接的选择器（默认 #sidebar-group a[href]）",
    )
    parser.add_argument(
        "--sidebar-toggle-selector",
        default="#sidebar-group button[aria-expanded]",
        help="展开目录树的按钮选择器（默认 #sidebar-group button[aria-expanded]）",
    )
    parser.add_argument(
        "--sidebar-expand-attempts",
        type=int,
        default=20,
        help="展开多级目录时的最大循环次数（默认 20）",
    )
    parser.add_argument(
        "--sidebar-scroll-attempts",
        type=int,
        default=15,
        help="滚动加载链接的尝试次数（默认 15）",
    )
    parser.add_argument(
        "--sidebar-scroll-wait",
        type=float,
        default=0.25,
        help="滚动之间等待的秒数（默认 0.25）",
    )
    parser.add_argument(
        "--sidebar-manifest",
        default=None,
        help="可选择将解析到的侧边栏顺序写入 JSON 文件",
    )
    parser.add_argument(
        "--hide-selector",
        action="append",
        dest="hide_selectors",
        default=None,
        help="打印前额外隐藏的 CSS 选择器，可重复传入",
    )
    parser.add_argument(
        "--no-default-hides",
        action="store_true",
        help="不自动隐藏默认的反馈/下一页/助手等元素",
    )
    parser.add_argument(
        "--nav-toggle-selector",
        action="append",
        dest="nav_toggle_selectors",
        default=None,
        help="当页面需要点击按钮才能显示侧边栏时使用的选择器（可重复）",
    )
    parser.add_argument(
        "--content-width",
        type=int,
        default=920,
        help="导出内容区域的最大宽度（像素，默认 920）",
    )
    parser.add_argument(
        "--content-padding",
        type=int,
        default=24,
        help="内容左右保留的内边距（像素，默认 24）",
    )
    parser.add_argument(
        "--content-font-size",
        type=float,
        default=15.0,
        help="正文基准字号（像素，默认 15）",
    )
    parser.add_argument(
        "--content-line-height",
        type=float,
        default=1.6,
        help="正文行高（默认 1.6）",
    )
    parser.add_argument(
        "--skip-content-rebuild",
        action="store_true",
        help="不重构页面 DOM，直接在原页面上打印（默认: 自动提取正文）",
    )

    return parser.parse_args()


def normalize_url(url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    fragmentless = parsed._replace(fragment="")
    normalized = fragmentless.geturl()
    if normalized.endswith("/") and fragmentless.path not in ("", "/"):
        normalized = normalized.rstrip("/")
    return normalized


def derive_allowed_prefix_from_url(url: str) -> str:
    from urllib.parse import urlparse

    normalized = normalize_url(url)
    parsed = urlparse(normalized)
    segments = [seg for seg in parsed.path.split("/") if seg]
    if len(segments) >= 2:
        base_segments = segments[:2]
    else:
        base_segments = segments
    if base_segments:
        base_path = "/" + "/".join(base_segments)
    else:
        base_path = ""
    candidate = parsed._replace(path=base_path, params="", query="", fragment="")
    return normalize_url(candidate.geturl())


def sanitize_filename(url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    path = parsed.path.strip("/")
    if not path:
        path = "index"
    if parsed.query:
        path = f"{path}_{parsed.query}"
    raw = f"{parsed.netloc}_{path}"
    raw = re.sub(r"[^0-9A-Za-z._-]+", "_", raw)
    return raw[:150]


def ensure_temp_dir(path: str, keep_existing: bool) -> None:
    if os.path.exists(path) and not keep_existing:
        shutil.rmtree(path)
    os.makedirs(path, exist_ok=True)


def get_hide_selectors(args: argparse.Namespace) -> List[str]:
    selectors = [] if args.no_default_hides else list(DEFAULT_HIDE_SELECTORS)
    if args.hide_selectors:
        selectors.extend(args.hide_selectors)
    cleaned = []
    for selector in selectors:
        if not selector:
            continue
        selector = selector.strip()
        if selector:
            cleaned.append(selector)
    return cleaned


def get_nav_toggle_selectors(args: argparse.Namespace) -> List[str]:
    selectors = list(DEFAULT_NAV_TOGGLE_SELECTORS)
    if args.nav_toggle_selectors:
        selectors.extend(args.nav_toggle_selectors)
    cleaned = []
    for selector in selectors:
        if not selector:
            continue
        selector = selector.strip()
        if selector and selector not in cleaned:
            cleaned.append(selector)
    return cleaned


def inject_css(page, css: str, source: str) -> None:
    if not css.strip():
        return
    try:
        page.add_style_tag(content=css)
    except Exception:
        try:
            page.evaluate(
                """({css, source}) => {
                    const style = document.createElement('style');
                    style.setAttribute('data-source', source || 'difydocstool');
                    style.textContent = css;
                    (document.head || document.documentElement).appendChild(style);
                }""",
                {"css": css, "source": source},
            )
        except Exception:
            print(f"[WARN] 无法注入样式: {source}")


def apply_hide_styles(page, selectors: Sequence[str]) -> None:
    if not selectors:
        return
    css_rules = "\n".join(f"{selector} {{ display: none !important; }}" for selector in selectors)
    inject_css(page, css_rules, "difydocstool-hide")


def build_layout_override_css(
    args: argparse.Namespace,
) -> Tuple[str, int, int, float, float]:
    width = max(600, int(args.content_width or 0))
    padding = max(0, int(args.content_padding or 0))
    font_size = max(11.0, float(args.content_font_size or 0))
    line_height = max(1.2, float(args.content_line_height or 0))
    code_font = max(font_size - 1.0, 10.0)

    css = f"""
:root {{
  color-scheme: light !important;
}}
html, body {{
  background: #fff !important;
  color: #111 !important;
  font-size: {font_size}px !important;
  line-height: {line_height} !important;
  font-family: "Inter", "SF Pro Display", "PingFang SC", "Microsoft YaHei", sans-serif !important;
  margin: 0 !important;
  padding: 0 !important;
  -webkit-print-color-adjust: exact !important;
  print-color-adjust: exact !important;
}}
#difydocstool-root {{
  max-width: {width}px !important;
  padding: {padding + 12}px {padding}px {padding + 24}px !important;
  margin: 0 auto !important;
  box-sizing: border-box !important;
}}
#difydocstool-root h1 {{
  font-size: {font_size * 1.5:.1f}px !important;
  line-height: {line_height * 1.1:.2f} !important;
  margin: 0 0 0.4em !important;
  color: #0f172a !important;
}}
#difydocstool-root h2 {{
  font-size: {font_size * 1.25:.1f}px !important;
  margin-top: 1.4em !important;
  margin-bottom: 0.4em !important;
  color: #111827 !important;
}}
#difydocstool-root h3 {{
  font-size: {font_size * 1.1:.1f}px !important;
  margin-top: 1.2em !important;
  margin-bottom: 0.3em !important;
  color: #111827 !important;
}}
#difydocstool-root p {{
  margin: 0 0 0.85em !important;
}}
#difydocstool-root ul,
#difydocstool-root ol {{
  padding-left: 1.4em !important;
  margin-bottom: 0.9em !important;
}}
#difydocstool-root .difydocstool-source-url {{
  font-size: {font_size * 0.85:.1f}px !important;
  color: #6b7280 !important;
  margin: 0 0 1.2em !important;
  word-break: break-all !important;
}}
#difydocstool-root table {{
  width: 100% !important;
  border-collapse: collapse !important;
  margin: 1em 0 !important;
  font-size: {font_size * 0.95:.1f}px !important;
}}
#difydocstool-root table th,
#difydocstool-root table td {{
  border: 1px solid #e5e7eb !important;
  padding: 8px 10px !important;
  vertical-align: top !important;
}}
#difydocstool-root blockquote {{
  border-left: 4px solid #d1d5db !important;
  margin: 1.2em 0 !important;
  padding: 0.4em 1em !important;
  color: #4b5563 !important;
  background: #f9fafb !important;
}}
#difydocstool-root pre {{
  white-space: pre-wrap !important;
  word-break: break-word !important;
  font-size: {code_font:.1f}px !important;
  background: #0f172a !important;
  color: #f8fafc !important;
  border: 1px solid #0b1220 !important;
  padding: 14px 16px !important;
  border-radius: 10px !important;
  margin: 1.2em 0 !important;
}}
#difydocstool-root pre code {{
  color: #e2e8f0 !important;
  background: transparent !important;
}}
#difydocstool-root code {{
  font-size: {code_font:.1f}px !important;
  background: #f1f5f9 !important;
  padding: 0.1em 0.4em !important;
  border-radius: 6px !important;
  color: #0f172a !important;
}}
#difydocstool-root img,
#difydocstool-root video,
#difydocstool-root svg,
#difydocstool-root canvas {{
  max-width: 100% !important;
  height: auto !important;
  border-radius: 10px !important;
  margin: 1em auto !important;
  display: block !important;
}}
#difydocstool-root :where(h1, h2, h3, h4) {{
  break-after: avoid-page !important;
  page-break-after: avoid !important;
}}
#difydocstool-root :where(pre, table, blockquote, figure, img) {{
  break-inside: avoid !important;
  page-break-inside: avoid !important;
}}
"""
    return css.strip(), width, padding, font_size, line_height


def inject_layout_css(page, css: str) -> None:
    inject_css(page, css, "difydocstool-layout")


def rebuild_print_view(page, args) -> None:
    if args.skip_content_rebuild:
        return

    content_selectors = [
        "#content",
        "#content-area",
        "main article",
        "main",
        "[data-markdown]",
    ]
    heading_selectors = [
        "header h1#page-title",
        "#content h1",
        "#content-area h1",
        "main h1",
    ]
    removal_selectors = [
        ".feedback-toolbar",
        "#pagination",
        ".chat-assistant-floating-input",
        "[role='contentinfo']",
        "footer",
        "nav",
        "header",
        ".left-0.right-0.sticky",
        "form",
        ".hidden-print",
    ]

    script = """
    ({ contentSelectors, headingSelectors, removalSelectors, pageUrl }) => {
        const findNode = (selectors) => {
            for (const selector of selectors) {
                if (!selector) continue;
                const node = document.querySelector(selector);
                if (node) return node;
            }
            return null;
        };
        const content = findNode(contentSelectors);
        if (!content) {
            return { success: false, reason: 'content_not_found', selectors: contentSelectors };
        }
        const clone = content.cloneNode(true);
        removalSelectors.forEach((selector) => {
            clone.querySelectorAll(selector).forEach((node) => node.remove());
        });
        clone.querySelectorAll('button').forEach((btn) => {
            const label = (btn.getAttribute('aria-label') || '').toLowerCase();
            if (label.includes('复制') || label.includes('copy') || !btn.textContent?.trim()) {
                btn.remove();
            }
        });
        clone.querySelectorAll('input, textarea, select').forEach((node) => node.remove());
        const headingNode = findNode(headingSelectors) || content.querySelector('h1');
        const headingText = headingNode ? headingNode.textContent.trim() : (document.title || '').trim();

        const root = document.createElement('article');
        root.id = 'difydocstool-root';
        root.setAttribute('data-source-url', pageUrl);

        if (headingText) {
            const titleBlock = document.createElement('div');
            titleBlock.className = 'difydocstool-title-block';
            const h1 = document.createElement('h1');
            h1.textContent = headingText;
            titleBlock.appendChild(h1);
            if (pageUrl) {
                const urlRow = document.createElement('p');
                urlRow.className = 'difydocstool-source-url';
                urlRow.textContent = pageUrl;
                titleBlock.appendChild(urlRow);
            }
            root.appendChild(titleBlock);
        }

        root.appendChild(clone);
        document.body.innerHTML = '';
        document.body.appendChild(root);
        document.documentElement.style.background = '#fff';
        document.body.style.background = '#fff';
        document.documentElement.scrollTop = 0;
        document.body.scrollTop = 0;
        return { success: true, heading: headingText };
    }
    """

    result = page.evaluate(
        script,
        {
            "contentSelectors": content_selectors,
            "headingSelectors": heading_selectors,
            "removalSelectors": removal_selectors,
            "pageUrl": page.url,
        },
    )
    if not isinstance(result, dict):
        print("[WARN] 重构页面失败（未知返回值）")
    elif not result.get("success"):
        print(f"[WARN] 重构页面失败: {result}")
def try_open_sidebar(page, args) -> bool:
    selectors = get_nav_toggle_selectors(args)
    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = locator.count()
        except Exception:
            continue
        if count == 0:
            continue
        try:
            locator.first.scroll_into_view_if_needed()
        except Exception:
            pass
        try:
            locator.first.click()
        except Exception:
            try:
                locator.first.evaluate("el => el.click()")
            except Exception:
                continue
        page.wait_for_timeout(250)
        try:
            if page.locator(args.sidebar_selector).is_visible():
                return True
        except Exception:
            continue
    return False


def ensure_sidebar_visible(page, args) -> None:
    timeout_ms = max(args.render_timeout, 5) * 1000
    print("[INFO] 等待文档侧边栏加载...")
    page.wait_for_selector(args.sidebar_selector, timeout=timeout_ms)

    sidebar = page.locator(args.sidebar_selector)
    try:
        if sidebar.is_visible():
            return
    except Exception:
        pass

    print("[INFO] 侧边栏未直接可见，尝试点击导航按钮展开菜单...")
    opened = try_open_sidebar(page, args)
    if opened:
        try:
            sidebar.wait_for(state="visible", timeout=timeout_ms // 2 or 1000)
            print("[INFO] 导航面板已展开")
            return
        except PlaywrightTimeoutError:
            print("[WARN] 导航按钮已点击，但仍无法确认侧边栏可见，继续尝试解析 DOM")
            return

    print("[WARN] 无法自动展开侧边栏，将尝试直接解析 DOM（可能缺少某些页面）")


def expand_sidebar_sections(page, toggle_selector: str, max_cycles: int) -> None:
    base_selector = (toggle_selector or "").strip()
    if not base_selector:
        return

    selector = f"{base_selector}[aria-expanded='false']"
    max_cycles = max(1, max_cycles)

    for _ in range(max_cycles):
        try:
            toggles = page.locator(selector)
            count = toggles.count()
        except Exception:
            return
        if count == 0:
            return
        for idx in range(count):
            try:
                button = toggles.nth(idx)
            except Exception:
                continue
            try:
                button.scroll_into_view_if_needed()
            except Exception:
                pass
            try:
                button.click()
            except Exception:
                try:
                    button.evaluate("el => el.click()")
                except Exception:
                    continue
            page.wait_for_timeout(150)

    try:
        pending = page.locator(selector).count()
    except Exception:
        pending = 0
    if pending:
        print(
            f"[WARN] 仍有 {pending} 个侧边栏节点无法展开，请考虑增大 --sidebar-expand-attempts 的数值"
        )


def read_sidebar_entries(page, link_selector: str) -> List[dict]:
    try:
        entries = page.eval_on_selector_all(
            link_selector,
            "els => els.map(el => ({ href: el.href, text: el.textContent || '' }))",
        )
    except Exception:
        entries = []
    return entries


def collect_sidebar_docs(page, args, start_url: str) -> List[DocPage]:
    prefix = normalize_url(args.allowed_prefix)
    page.goto(start_url, wait_until="domcontentloaded")
    ensure_sidebar_visible(page, args)

    expand_sidebar_sections(page, args.sidebar_toggle_selector, args.sidebar_expand_attempts)

    target_selector = args.sidebar_scroll_container or args.sidebar_selector
    last_count = -1
    for _ in range(max(args.sidebar_scroll_attempts, 1)):
        entries = read_sidebar_entries(page, args.sidebar_link_selector)
        if len(entries) <= last_count:
            break
        last_count = len(entries)
        try:
            page.eval_on_selector(target_selector, "el => { el.scrollTop = el.scrollHeight; }")
        except Exception:
            page.mouse.wheel(0, 800)
        page.wait_for_timeout(max(args.sidebar_scroll_wait, 0) * 1000)

    entries = read_sidebar_entries(page, args.sidebar_link_selector)

    docs: List[DocPage] = []
    seen = set()
    for entry in entries:
        href = entry.get("href") if isinstance(entry, dict) else None
        if not href:
            continue
        normalized = normalize_url(href)
        if not normalized.startswith(prefix):
            continue
        if normalized in seen:
            continue
        label = None
        if isinstance(entry, dict):
            text = (entry.get("text") or "").strip()
            label = text or None
        docs.append(DocPage(url=normalized, label=label))
        seen.add(normalized)
        if args.max_pages and len(docs) >= args.max_pages:
            break

    if start_url not in seen:
        docs.insert(0, DocPage(url=start_url, label="入口页面"))

    if args.sidebar_manifest and docs:
        try:
            manifest = [
                {"index": idx + 1, "url": doc.url, "label": doc.label}
                for idx, doc in enumerate(docs)
            ]
            with open(args.sidebar_manifest, "w", encoding="utf-8") as file:
                json.dump(manifest, file, ensure_ascii=False, indent=2)
            print(f"[INFO] 已保存侧边栏清单: {args.sidebar_manifest} ({len(manifest)} 条)")
        except OSError as exc:
            print(f"[WARN] 写入侧边栏清单失败: {exc}")

    print(f"[INFO] 侧边栏解析完成，共 {len(docs)} 个链接")
    return docs


def render_pages(
    page,
    pages: Sequence[DocPage],
    temp_dir: str,
    args,
    hide_selectors: Sequence[str],
    layout_css: str,
) -> List[PageArtifact]:
    if not pages:
        print("[WARN] 没有可渲染的页面")
        return []

    if sync_playwright is None or PlaywrightTimeoutError is None:
        raise SystemExit(
            "Playwright 未安装。请先执行 `pip install playwright` 与 `playwright install chromium`."
        )

    artifacts: List[PageArtifact] = []
    margins = {
        "top": f"{args.margin}mm",
        "right": f"{args.margin}mm",
        "bottom": f"{args.margin}mm",
        "left": f"{args.margin}mm",
    }

    print(f"[INFO] 开始渲染 {len(pages)} 个页面 ...")

    for index, doc in enumerate(pages, 1):
        filename_base = f"{index:03d}_{sanitize_filename(doc.url)}"
        html_path = os.path.join(temp_dir, f"{filename_base}.html")
        pdf_path = os.path.join(temp_dir, f"{filename_base}.pdf")
        label_info = f" ({doc.label})" if doc.label else ""
        print(f"[INFO] ({index}/{len(pages)}) {doc.url}{label_info}")
        try:
            page.goto(doc.url, wait_until="domcontentloaded")
            if args.wait_selector:
                page.wait_for_selector(
                    args.wait_selector,
                    timeout=args.render_timeout * 1000,
                    state="visible",
                )
            if args.extra_wait > 0:
                page.wait_for_timeout(args.extra_wait * 1000)

            rebuild_print_view(page, args)
            if hide_selectors:
                apply_hide_styles(page, hide_selectors)
            if layout_css:
                inject_layout_css(page, layout_css)

            title = page.title()
            html_content = page.content()
            with open(html_path, "w", encoding="utf-8") as file:
                file.write(html_content)

            page.emulate_media(media="screen")
            page.pdf(
                path=pdf_path,
                format=args.page_format,
                print_background=True,
                display_header_footer=False,
                margin=margins,
                prefer_css_page_size=False,
            )

            artifacts.append(
                PageArtifact(
                    url=doc.url,
                    html_path=html_path,
                    pdf_path=pdf_path,
                    title=title,
                    label=doc.label,
                )
            )
        except PlaywrightTimeoutError:
            print(f"[WARN] 渲染超时，跳过: {doc.url}")
        except Exception as exc:
            print(f"[WARN] 渲染失败 ({exc}): {doc.url}")

        if args.request_delay > 0:
            time.sleep(args.request_delay)

    return artifacts


def merge_pdfs(pdf_files: List[str], output_path: str) -> bool:
    if not pdf_files:
        print("[WARN] 没有可合并的 PDF 文件")
        return False

    writer = PdfWriter()
    for pdf in tqdm(pdf_files, desc="合并 PDF"):
        try:
            reader = PdfReader(pdf)
            for page in reader.pages:
                writer.add_page(page)
        except Exception as exc:
            print(f"[WARN] 读取 PDF 失败 ({exc}): {pdf}")

    if not writer.pages:
        print("[WARN] 无法生成合并文件，所有 PDF 都失败了吗？")
        return False

    with open(output_path, "wb") as merged_file:
        writer.write(merged_file)

    return True


def print_summary(artifacts: Sequence[PageArtifact], elapsed: float) -> None:
    success_count = sum(1 for artifact in artifacts if artifact.pdf_path)
    print("\n" + "-" * 80)
    print(f"[INFO] 渲染完成，总页面数: {len(artifacts)}, 成功 PDF: {success_count}")
    print(f"[INFO] 耗时: {elapsed:.2f} 秒")


def main() -> None:
    args = parse_args()
    ensure_temp_dir(args.temp_dir, args.keep_temp)

    if sync_playwright is None:
        raise SystemExit("请先安装 Playwright，并执行 `playwright install chromium`")

    start_url = normalize_url(args.url)
    if args.allowed_prefix:
        allowed_prefix = normalize_url(args.allowed_prefix)
    else:
        allowed_prefix = derive_allowed_prefix_from_url(start_url)
        print(f"[INFO] 未指定 allowed-prefix，自动推断为: {allowed_prefix}")
    args.allowed_prefix = allowed_prefix

    print(f"[INFO] 入口 URL: {start_url}")
    print(f"[INFO] 限制前缀: {allowed_prefix}")
    print(f"[INFO] 输出文件: {args.output}")
    print(f"[INFO] 临时目录: {args.temp_dir}\n")

    hide_selectors = get_hide_selectors(args)
    if hide_selectors:
        print(f"[INFO] 打印前将隐藏以下选择器: {', '.join(hide_selectors)}")
    layout_css, layout_width, layout_padding, layout_font, layout_line = build_layout_override_css(args)
    print(
        "[INFO] 排版参数:"
        f" 内容最大宽度 {layout_width}px,"
        f" 左右内边距 {layout_padding}px,"
        f" 基准字号 {layout_font:.1f}px,"
        f" 行高 {layout_line:.2f}"
    )

    start_time = time.time()
    artifacts: List[PageArtifact] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": args.viewport_width, "height": args.viewport_height},
            device_scale_factor=args.device_scale,
            locale=args.locale,
            color_scheme=None if args.color_scheme == "no-preference" else args.color_scheme,
        )
        context.set_default_navigation_timeout(args.nav_timeout * 1000)
        page = context.new_page()

        docs = collect_sidebar_docs(page, args, start_url)
        if not docs:
            print("[ERROR] 没有解析到任何有效链接，程序终止")
            context.close()
            browser.close()
            return

        artifacts = render_pages(page, docs, args.temp_dir, args, hide_selectors, layout_css)

        context.close()
        browser.close()

    elapsed = time.time() - start_time
    print_summary(artifacts, elapsed)

    pdf_files = [artifact.pdf_path for artifact in artifacts if artifact.pdf_path]
    if merge_pdfs(pdf_files, args.output):
        print(f"[INFO] 合并 PDF 已生成: {args.output}")
    else:
        print("[WARN] 未生成最终 PDF，请检查日志")


if __name__ == "__main__":  # pragma: no cover
    try:
        main()
    except KeyboardInterrupt:
        print("\n[WARN] 用户中断")
    except Exception as exc:
        print(f"[ERROR] 未处理异常: {exc}")
        sys.exit(1)
