#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
提取 testData/creep.html 中歌词的示例脚本。
策略：
- 优先定位 data-lyrics-container="true"
- 删除 page 中用于布局的隐藏绝对定位 span（position:absolute / opacity:0）
- 提取容器内所有 <p> 的文本，使用换行和段落分隔
- 如果没有找到 data-lyrics-container，则回退到查找包含 "Lyrics" 的标题并取下一个 <p>
"""
import re
from bs4 import BeautifulSoup

HTML_PATH = "testData/creep.html"


def clean_and_extract_from_container(container):
    # 删除常见的“不可见/辅助”元素（position:absolute / opacity:0）
    invisible_style_re = re.compile(r"position\s*:\s*absolute|opacity\s*:\s*0", re.I)
    for bad in container.find_all(style=invisible_style_re):
        bad.decompose()

    # 有时 header 区域会带有 data-exclude-from-selection，若在 container 内也删除
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


def extract_lyrics(html_path=HTML_PATH):
    with open(html_path, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")

    # 优先定位 data-lyrics-container="true"
    container = soup.find(attrs={"data-lyrics-container": "true"})
    if container:
        return clean_and_extract_from_container(container)

    # 回退策略：查找标题包含 "Lyrics" 的元素并取它之后的第一个 <p>
    title = soup.find(lambda t: t.name in ("h1", "h2", "h3", "div", "span") and "Lyrics" in t.get_text())
    if title:
        p = title.find_next("p")
        if p:
            # 清理附近无用元素再取文本
            for bad in p.find_all(style=re.compile(r"position\s*:\s*absolute|opacity\s*:\s*0", re.I)):
                bad.decompose()
            return p.get_text(separator="\n").strip()

    # 最后尝试直接搜索页面中第一个包含 "[Verse" 的 <p> 或者长文本 <p>
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

    return ""


if __name__ == "__main__":
    lyrics = extract_lyrics()
    if lyrics:
        print(lyrics)
    else:
        print("未能找到歌词内容。")
