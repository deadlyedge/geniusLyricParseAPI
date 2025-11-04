# from typing import Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query
import requests
from bs4 import BeautifulSoup, Tag
import re

app = FastAPI(title="Genius Lyrics Scraper", version="0.1")


def is_genius_url(url: str) -> bool:
    try:
        p = urlparse(url)
        if not p.scheme or not p.netloc:
            return False
        host = p.netloc.lower()
        return host.endswith("genius.com")
    except Exception:
        return False


def extract_lyrics(html: str) -> str:
    """
    Parse Genius page HTML and extract lyrics text, then clean common site artifacts.

    Strategy:
    - Prefer elements with data-lyrics-container="true" (current Genius structure).
      There may be multiple such containers; extract and join them in order.
    - Fallback to older .lyrics div.
    - Fallback to elements whose class contains 'Lyrics__Root' (another common pattern).
    - Preserve line breaks by converting <br> to newline characters.
    - Remove invisible/helper elements (position:absolute / opacity:0) and
      elements annotated with data-exclude-from-selection.
    - Fallback strategies: heading containing "Lyrics" then next <p>; search for
      paragraphs containing "[Verse" / "[Chorus" or long <p>.
    """

    def clean_and_extract_from_container(container: Tag) -> str:
        # 将 <br> 统一替换为换行符，方便后续 get_text(separator="\n")
        for br in container.find_all("br"):
            br.replace_with("\n")

        # 删除常见的“不可见/辅助”元素（position:absolute / opacity:0）
        invisible_style_re = re.compile(
            r"position\s*:\s*absolute|opacity\s*:\s*0", re.I
        )
        for bad in container.find_all(style=invisible_style_re):
            bad.decompose()

        # 删除 data-exclude-from-selection 的元素（有时用于 header/分享按钮）
        for bad in container.find_all(attrs={"data-exclude-from-selection": True}):
            bad.decompose()

        ps = container.find_all("p")
        if not ps:
            # 如果没有 <p>，直接返回容器的纯文本
            return container.get_text(separator="\n").strip()

        paragraphs = []
        for p in ps:
            text = p.get_text(separator="\n").strip()
            if text:
                paragraphs.append(text)
        return "\n\n".join(paragraphs)

    def clean_text(text: str) -> str:
        """
        Clean text by
        1. 规范换行符为 '\n' 并将连续的空行合并为一个换行；
        2. 将三种成对括号 [] () {} 的标注与其中间的文本内容合并为一行（例如把"\n[\nVerse\n]\n"替换为"\n[Verse]\n"; 把"(\ntext\n)"整理为"\n(text)\n"）；
        """
        # 统一换行
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        # 折叠过多连续空行（将 3 个及以上换行压缩为 2 个，保留段间空行）
        text = re.sub(r"\n+", "\n", text)

        # 回调：把成对括号内的多行/冗余空白合并为单行（仅在匹配的成对括号中处理）
        def _collapse_bracket(m):
            open_c = m.group(1)
            inner = m.group(2)
            close_c = m.group(3)
            pairs = {"[": "]", "(": ")", "{": "}"}
            # 确保是配对的括号（例如不会把 '[' 配对到 ')'）
            if pairs.get(open_c) != close_c:
                return m.group(0)
            # 将内部所有空白（包括换行）压缩为单个空格，并去除首尾空白
            inner_collapsed = re.sub(r"\s+", " ", inner).strip()
            return f"{open_c}{inner_collapsed}{close_c}"

        # 非贪婪匹配任意成对括号并处理
        text = re.sub(r"([\[\(\{])([\s\S]*?)([\]\)\}])", _collapse_bracket, text)

        return text

    soup = BeautifulSoup(html, "html.parser")

    # 优先：查找所有 data-lyrics-container="true" 的 container（可能有多个）
    containers = soup.find_all(attrs={"data-lyrics-container": "true"})
    if containers:
        parts = []
        print(f"Found {len(containers)} lyrics containers.")
        for c in containers:
            txt = clean_and_extract_from_container(c)
            if txt:
                parts.append(txt)
        if parts:
            # only add \n\n if next line start with "[" or just add \n
            txt = re.sub(r"\n(\[)", r"\n\n\1", clean_text("\n\n".join(parts)))
            # txt = clean_text(txt)
            return txt

    # 回退：老的 .lyrics div 结构
    old_div = soup.find("div", class_="lyrics")
    if old_div:
        return clean_and_extract_from_container(old_div)

    # 回退：类名中包含 Lyrics__Root 的根节点（另一种常见结构）
    root = soup.find(class_=re.compile(r"Lyrics__Root"))
    if root:
        return clean_and_extract_from_container(root)

    # 回退策略：查找标题包含 "Lyrics" 的元素并取它之后的第一个 <p>
    title = soup.find(
        lambda t: t.name in ("h1", "h2", "h3", "div", "span")
        and "Lyrics" in t.get_text()
    )
    if title:
        p = title.find_next("p")
        if p:
            # 清理附近无用元素再取文本
            for bad in p.find_all(
                style=re.compile(r"position\s*:\s*absolute|opacity\s*:\s*0", re.I)
            ):
                bad.decompose()
            return p.get_text(separator="\n").strip()

    # 最后尝试直接搜索页面中第一个包含 "[Verse" 或 "[Chorus" 的 <p>，或长文本 <p>
    p_candidate = None
    for p in soup.find_all("p"):
        text = p.get_text()
        if "[Verse" in text or "[Chorus" in text:
            p_candidate = p
            break
        if not p_candidate and len(text.strip()) > 200:
            p_candidate = p
    if p_candidate:
        return p_candidate.get_text(separator="\n").strip()

    # 如果都没找到，则抛出异常由调用方返回 404
    raise ValueError("Lyrics not found on the provided Genius page.")


@app.get("/lyrics")
def get_lyrics(
    url: str = Query(..., example="https://genius.com/Michael-jackson-thriller-lyrics"),
):
    """
    GET /lyrics?url=<genius-lyrics-url>
    Returns:
    {
      "status": "ok",
      "url": "...",
      "lyrics": "full lyrics text"
    }
    """
    if not is_genius_url(url):
        raise HTTPException(
            status_code=400, detail="Invalid Genius URL. Provide a URL under genius.com"
        )

    try:
        headers = {"User-Agent": "GeniusLyricsScraper/1.0 (+https://example.com)"}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Error fetching page: {e}")

    try:
        lyrics = extract_lyrics(resp.text)
        print(f"{lyrics}")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to parse lyrics")

    return {"status": "ok", "url": url, "lyrics": lyrics}


if __name__ == "__main__":
    # Launch a development server when run directly:
    # python main.py
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
