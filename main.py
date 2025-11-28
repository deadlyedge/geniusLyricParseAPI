import re
import os
import requests
import time
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException, Query, Depends, Request
from fastapi.security import OAuth2PasswordBearer
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from bs4 import BeautifulSoup, Tag
from typing import Annotated, Callable
from dotenv import load_dotenv

load_dotenv(verbose=True)
SECRET = os.getenv("SECRET")
if not SECRET:
    raise ValueError("SECRET is not set in the environment variables.")
REQUEST_TIMES_PER_MINTUE = int(os.getenv("REQUEST_TIMES_PER_MINTUE", 60))
GENIUS_URL = "https://genius.com"

# Rate limiting configuration
rate_limit_dict = {}


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        # Get client IP
        client_ip = request.client.host if request.client else "unknown"
        current_time = time.time()

        # Clean up old entries
        rate_limit_dict.clear()

        # Check rate limit
        if client_ip in rate_limit_dict:
            requests = rate_limit_dict[client_ip]
            if len(requests) >= REQUEST_TIMES_PER_MINTUE:
                oldest_request = requests[0]
                if current_time - oldest_request < 60:  # Within 1 minute
                    return JSONResponse(
                        status_code=429, content={"error": "Too many requests"}
                    )
                requests.pop(0)
        else:
            rate_limit_dict[client_ip] = []

        rate_limit_dict[client_ip].append(current_time)
        return await call_next(request)


# 应用信息
app = FastAPI(title="Genius Lyrics Scraper", version="0.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


async def check_auth(token: Annotated[str, Depends(oauth2_scheme)]):
    if token != SECRET:
        raise HTTPException(status_code=401, detail="Invalid token")


# -----------------------------
# 公共工具
# -----------------------------
def is_genius_path(path: str) -> bool:
    """
    简单校验 URL 是否属于 genius.com。
    /Guns-n-roses-november-rain-lyrics
    """
    try:
        p = urlparse(path)
        # 检查是否以 / 开头，并以 -lyrics 结尾。
        # 对首字母执行如下规则：如果首字符是数字则跳过大小写校验；否则要求首字符为字母且为大写。
        path_part = p.path[1:]
        if not path_part:
            return False
        first_char = path_part[0]
        if first_char.isdigit():
            return p.path.startswith("/") and p.path.endswith("-lyrics")
        return (
            p.path.startswith("/")
            and first_char.isalpha()
            and first_char.isupper()
            and p.path.endswith("-lyrics")
        )

    except Exception:
        return False


# -----------------------------
# 抓取模块（Fetching）
# -----------------------------
def fetch_page(url: str, timeout: int = 10) -> str:
    """
    负责向目标 URL 发起请求并返回页面 HTML。
    将网络/请求相关错误转换为 HTTPException(502) 由 FastAPI 返回给客户端。
    """
    headers = {"User-Agent": "GeniusLyricsScraper/1.0 (+https://example.com)"}
    # add retry logic
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            # add interval
            if attempt < 2:
                import time

                time.sleep(1)
                continue
            if attempt == 2:
                # 将请求失败统一为 502 Bad Gateway
                raise HTTPException(status_code=502, detail=f"Error fetching page: {e}")

    # 未知错误
    raise HTTPException(status_code=502, detail="Unknown error occurred")


# -----------------------------
# 整理模块（Parsing / Cleaning）
# -----------------------------
def parse_lyrics(html: str) -> str:
    """
    仅使用主策略解析歌词：查找所有 data-lyrics-container="true" 的容器，
    依次提取并清理文本，最后以规范化文本返回。

    严格去掉所有“回退策略”。如果找不到符合的容器或容器内容为空，
    抛出 ValueError 由上层转换为 404。
    """

    def clean_and_extract_from_container(container: Tag) -> str:
        # 将 <br> 替换为换行符，确保 get_text 时保留换行
        for br in container.find_all("br"):
            br.replace_with("\n")

        # 删除不可见或辅助性元素（常见的样式）
        invisible_style_re = re.compile(
            r"position\s*:\s*absolute|opacity\s*:\s*0", re.I
        )
        for bad in container.find_all(style=invisible_style_re):
            bad.decompose()

        # 删除 data-exclude-from-selection 的元素
        for bad in container.find_all(attrs={"data-exclude-from-selection": True}):
            bad.decompose()

        # 优先按 <p> 段落组织歌词内容
        ps = container.find_all("p")
        if not ps:
            return container.get_text(separator="\n").strip()

        paragraphs = []
        for p in ps:
            text = p.get_text(separator="\n").strip()
            if text:
                paragraphs.append(text)
        return "\n\n".join(paragraphs)

    def clean_text(text: str) -> str:
        # 统一换行符
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        # 折叠多余空白（连续换行压缩为单个换行）
        text = re.sub(r"\n+", "\n", text)

        # 将成对括号内的多行与过多空白压缩为单行，例如:
        # "[\nVerse\n]" -> "[Verse]"
        def _collapse_bracket(m):
            open_c = m.group(1)
            inner = m.group(2)
            close_c = m.group(3)
            pairs = {"[": "]", "(": ")", "{": "}"}
            if pairs.get(open_c) != close_c:
                return m.group(0)
            inner_collapsed = re.sub(r"\s+", " ", inner).strip()
            return f"{open_c}{inner_collapsed}{close_c}"

        text = re.sub(r"([\[\(\{])([\s\S]*?)([\]\)\}])", _collapse_bracket, text)
        return text.strip()

    soup = BeautifulSoup(html, "html.parser")

    # 仅保留主策略：查找所有 data-lyrics-container="true"
    containers = soup.find_all(attrs={"data-lyrics-container": "true"})
    if not containers:
        raise ValueError("Lyrics containers not found on the page.")

    parts = []
    for c in containers:
        txt = clean_and_extract_from_container(c)
        if txt:
            parts.append(txt)

    if not parts:
        raise ValueError("Found lyrics containers but they contained no text.")

    combined = "\n\n".join(parts)
    combined = re.sub(r"\n(\[)", r"\n\n\1", clean_text(combined))
    return combined


# -----------------------------
# 响应模块（API Endpoint）
# -----------------------------
@app.get("/lyrics")
def get_lyrics(
    path: str = Query(..., examples=["/Guns-n-roses-november-rain-lyrics"]),
    _=Depends(check_auth),
):
    """
    GET /lyrics?path=<genius-lyrics-path>

    流程：
    1. 验证 URL（is_genius_path）
    2. 抓取页面（fetch_page）
    3. 解析歌词（parse_lyrics）
    4. 返回结构化 JSON 或相应的 HTTP 错误
    """
    if not is_genius_path(path):
        raise HTTPException(
            status_code=400,
            detail="Invalid Genius PATH. Provide a PATH under genius.com",
        )

    # 构造 URL
    url = GENIUS_URL + path

    # 抓取 HTML（fetch）
    html = fetch_page(url)

    # 解析并整理歌词（parse / clean）
    try:
        lyrics = parse_lyrics(html)
    except ValueError as e:
        # 主策略找不到歌词 -> 认为资源不存在或格式不符合 -> 404
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        # 如果 parse 或 fetch 显式抛出 HTTPException，直接透传
        raise
    except Exception:
        # 其他未知错误 -> 500
        raise HTTPException(status_code=500, detail="Failed to parse lyrics")

    # 响应（response）
    return {"status": "ok", "url": url, "lyrics": lyrics}


# -----------------------------
# 本地运行入口（仅用于开发）
# -----------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
