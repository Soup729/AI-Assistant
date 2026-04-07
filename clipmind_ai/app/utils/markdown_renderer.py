"""
Markdown 渲染器：基于 mistune 3.x + Pygments 实现代码高亮。
生成的 HTML 专用于 QTextBrowser（Qt HTML 子集），使用内联 CSS。
"""

import html
import re
from typing import Optional

import mistune
from mistune.renderers.html import HTMLRenderer
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name, guess_lexer, TextLexer
from pygments.util import ClassNotFound


# ── 等宽字体 ────────────────────────────────────────────────────────────────

_MONO_FONT = "'JetBrains Mono','Cascadia Code','Fira Code',Consolas,monospace"

_CODE_PRE_STYLE = (
    "background-color:#1e1e1e;color:#d4d4d4;"
    "font-family:" + _MONO_FONT + ";font-size:13px;"
    "padding:12px 16px;border-radius:6px;overflow-x:auto;line-height:1;"
    "display:block;white-space:pre;"
)


# ── 自定义 Renderer ─────────────────────────────────────────────────────────

class _QTextBrowserRenderer(HTMLRenderer):
    def __init__(self):
        super().__init__()
        self._formatter = HtmlFormatter(cssclasses=False, nowrap=False)

    def _highlight_block(self, code: str, info: str = "") -> str:
        code = code.rstrip("\n")
        if not code:
            return ""

        lexer: "Lexer" = TextLexer()
        if info:
            lang = info.strip().lower().replace("lang:", "").replace("language:", "")
            try:
                lexer = get_lexer_by_name(lang)
            except ClassNotFound:
                lexer = TextLexer()
        else:
            try:
                lexer = guess_lexer(code, timeout=1.0)
            except Exception:
                lexer = TextLexer()

        try:
            highlighted = highlight(code, lexer, self._formatter)
        except Exception:
            highlighted = html.escape(code)

        # 剥除 Pygments 外层 <div class="highlight">，注入内联 style 到 <pre>
        highlighted = re.sub(r"^<div[^>]*>", "", highlighted)
        highlighted = re.sub(r"</div>\s*$", "", highlighted)
        highlighted = re.sub(
            r"<pre([^>]*)>",
            "<pre\\1 style=\"" + _CODE_PRE_STYLE + "\">",
            highlighted,
            count=1,
        )
        return highlighted

    def block_code(self, code: str, info: Optional[str] = None) -> str:
        return self._highlight_block(code, info or "") + "\n"

    def codespan(self, text: str) -> str:
        escaped = html.escape(text)
        style = (
            "background-color:#2d2d2d;color:#ce9178;"
            "font-family:" + _MONO_FONT + ";font-size:12px;padding:1px 5px;border-radius:3px;"
        )
        return '<span style="' + style + '">' + escaped + "</span>"

    def heading(self, text: str, level: int, **kwargs) -> str:
        size_map = {1: "16px", 2: "15px", 3: "14px", 4: "13px"}
        color_map = {1: "#e0e0e0", 2: "#cccccc", 3: "#b0b0b0"}
        margin_map = {1: "12px 0 6px", 2: "10px 0 4px", 3: "8px 0 3px"}
        size = size_map.get(level, "13px")
        color = color_map.get(level, "#a0a0b0")
        margin = margin_map.get(level, "6px 0 2px")
        weight = "700" if level == 1 else "600"
        return (
            "<h" + str(level) + ' style="color:' + color + ";font-size:" + size +
            ";font-weight:" + weight + ";margin:" + margin + ';padding:0;">' +
            text + "</h" + str(level) + ">\n"
        )

    def paragraph(self, text: str) -> str:
        return '<p style="margin:6px 0;color:#d0d0d0;line-height:1;">' + text + "</p>\n"

    def block_quote(self, text: str) -> str:
        return (
            '<blockquote style="border-left:3px solid #4a9eff;margin:8px 0;'
            'padding:4px 12px;color:#999;background-color:rgba(74,158,255,0.06);">'
            + text + "</blockquote>\n"
        )

    def list(self, text: str, ordered: bool, **kwargs) -> str:
        tag = "ol" if ordered else "ul"
        return "<" + tag + ' style="margin:6px 0;padding-left:24px;color:#d0d0d0;">' + text + "</" + tag + ">\n"

    def list_item(self, text: str) -> str:
        return '<li style="color:#d0d0d0;margin:3px 0;line-height:1.6;">' + text + "</li>\n"

    def emphasis(self, text: str) -> str:
        return '<em style="color:#b0b0b0;font-style:italic;">' + text + "</em>"

    def strong(self, text: str) -> str:
        return '<strong style="color:#e0e0e0;font-weight:700;">' + text + "</strong>"

    def link(self, text: str, url: str, title: Optional[str] = None) -> str:
        url = html.escape(url or "#")
        title_attr = ' title="' + html.escape(title) + '"' if title else ""
        return (
            '<a href="' + url + '"' + title_attr + ' style="color:#4a9eff;text-decoration:none;">'
            + text + "</a><span style=\"color:#555;font-size:0.85em;\"> </span>"
        )

    def thematic_break(self) -> str:
        return '<hr style="border:none;border-top:1px solid #333;margin:12px 0;">\n'

    def text(self, text: str) -> str:
        return html.escape(text).replace("\n", "<br>\n")

    def linebreak(self) -> str:
        return "<br>\n"

    def softbreak(self) -> str:
        return "<br>\n"


# ── 无代码高亮的 Renderer ────────────────────────────────────────────────────

class _QTextBrowserRendererNoHighlight(HTMLRenderer):
    """不含代码高亮的 Renderer，代码块仅做转义和样式包装。"""

    def block_code(self, code: str, info: Optional[str] = None) -> str:
        escaped = html.escape(code.rstrip("\n"))
        return (
            f'<pre style="{_CODE_PRE_STYLE}">{escaped}</pre>\n'
        )

    def codespan(self, text: str) -> str:
        escaped = html.escape(text)
        style = (
            "background-color:#2d2d2d;color:#ce9178;"
            "font-family:" + _MONO_FONT + ";font-size:12px;padding:1px 5px;border-radius:3px;"
        )
        return '<span style="' + style + '">' + escaped + "</span>"


# ── 公开 API ────────────────────────────────────────────────────────────────

_markdown = mistune.create_markdown(
    renderer=_QTextBrowserRenderer(),
    plugins=["strikethrough", "table", "url", "task_lists"],
)

_markdown_no_highlight = mistune.create_markdown(
    renderer=_QTextBrowserRendererNoHighlight(),
    plugins=["strikethrough", "table", "url", "task_lists"],
)


def render_markdown(text: str, enable_code_highlight: bool = True) -> str:
    """
    将 Markdown 文本渲染为 QTextBrowser 兼容的 HTML。
    - 代码块使用 JetBrains Mono + Pygments 高亮（可选）
    - 非代码内容自动 HTML 转义

    Args:
        text: Markdown 文本
        enable_code_highlight: 是否启用代码语法高亮
    """
    if not text:
        return ""
    md = _markdown if enable_code_highlight else _markdown_no_highlight
    return md(text)
