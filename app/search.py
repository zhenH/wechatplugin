"""联网搜索：Bing HTML 为主，DuckDuckGo 兜底（均无需 API Key）。

解析失败/网络受限时返回失败提示字符串，不抛异常——铸造师会基于已有资料继续。
"""
import html as html_mod
import re
import urllib.parse
import urllib.request

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _fetch(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8", "ignore")


def _clean(s: str) -> str:
    return html_mod.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def _parse_bing(html_text: str, max_results: int) -> list[tuple[str, str, str]]:
    out = []
    pattern = re.compile(
        r'<li class="b_algo".*?<h2[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
        r'(?:<p[^>]*>(.*?)</p>)?',
        re.S,
    )
    for m in pattern.finditer(html_text):
        url, title, snippet = m.group(1), _clean(m.group(2)), _clean(m.group(3) or "")
        if url.startswith("http"):
            out.append((title, url, snippet))
        if len(out) >= max_results:
            break
    return out


def _parse_ddg(html_text: str, max_results: int) -> list[tuple[str, str, str]]:
    out = []
    pattern = re.compile(
        r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
        r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
        re.S,
    )
    for m in pattern.finditer(html_text):
        url, title, snippet = m.group(1), _clean(m.group(2)), _clean(m.group(3))
        if url.startswith("http"):
            out.append((title, url, snippet))
        if len(out) >= max_results:
            break
    return out


def _format(results: list[tuple[str, str, str]]) -> str:
    lines = []
    for i, (title, url, snippet) in enumerate(results, 1):
        lines.append(f"{i}. {title}\n   链接: {url}\n   摘要: {snippet}")
    return "\n\n".join(lines)


def web_search(query: str, max_results: int = 5) -> str:
    """搜索并返回格式化结果；任何失败都返回提示文本而非抛异常。"""
    try:
        html_text = _fetch(
            "https://www.bing.com/search?q="
            + urllib.parse.quote(query)
            + "&setlang=zh-hans&cc=cn"
        )
        results = _parse_bing(html_text, max_results)
        if results:
            return _format(results)
    except Exception:
        pass
    try:
        html_text = _fetch(
            "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
        )
        results = _parse_ddg(html_text, max_results)
        if results:
            return _format(results)
    except Exception:
        pass
    return "（联网搜索失败：网络受限或搜索引擎拒绝访问，请基于已有资料继续）"
