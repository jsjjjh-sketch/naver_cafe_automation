# crawler.py  (교체본)
import re
import time
import html
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
import requests
from bs4 import BeautifulSoup

SESSION = requests.Session()
SESSION.headers.update({
    # 모바일 UA가 가장 잘 열림
    "User-Agent": ("Mozilla/5.0 (Linux; Android 11; SM-G973N) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Mobile Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://m.blog.naver.com/",
    "Connection": "close",
})

NAVER_TIMEOUT = (10, 20)

def _clean_text(txt: str) -> str:
    txt = html.unescape(txt)
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip()

def _is_naver_blog(netloc: str) -> bool:
    return netloc.endswith("blog.naver.com") or netloc.endswith("m.blog.naver.com")

def _normalize_naver_url(url: str) -> str:
    """
    입력 가능한 모든 네이버 블로그 링크를 표준 PostView URL로 정규화.
    """
    if not re.match(r"^https?://", url, flags=re.I):
        url = "https://" + url  # 스킴 누락 보정

    u = urlparse(url)

    if not _is_naver_blog(u.netloc):
        return url  # 비네이버는 그대로

    # 1) 이미 PostView.naver 형태면 쿼리 보정만
    if u.path.lower().endswith("postview.naver"):
        qs = parse_qs(u.query)
        blogId = qs.get("blogId", [""])[0]
        logNo = qs.get("logNo", [""])[0]
        if blogId and logNo:
            base = ("https", "blog.naver.com", "/PostView.naver",
                    "", urlencode({"blogId": blogId, "logNo": logNo}), "")
            return urlunparse(base)

    # 2) /{id}/{logNo} 또는 m.blog.naver.com/{id}/{logNo}
    m = re.match(r"^/(?:PostList.naver)?/?([A-Za-z0-9_.-]+)/(\d+)", u.path)
    if m:
        blogId, logNo = m.group(1), m.group(2)
        base = ("https", "blog.naver.com", "/PostView.naver",
                "", urlencode({"blogId": blogId, "logNo": logNo}), "")
        return urlunparse(base)

    # 3) /{id} 하나만 온 경우(모바일에서 내 블로그 홈)
    m2 = re.match(r"^/([A-Za-z0-9_.-]+)/?$", u.path)
    if m2:
        # 홈은 본문이 없으니 원본으로 둠(이 경우는 실패로 처리될 수 있음)
        return url

    return url

def _get(url: str, max_retry: int = 3) -> requests.Response:
    last_exc = None
    for i in range(max_retry):
        try:
            resp = SESSION.get(url, timeout=NAVER_TIMEOUT, allow_redirects=True)
            # 봇 차단 회피용 짧은 대기
            if resp.status_code in (429, 503):
                time.sleep(1.5)
                continue
            if resp.status_code == 403:
                # referer 강화 후 1회 재시도
                SESSION.headers["Referer"] = url
                time.sleep(0.8)
                resp = SESSION.get(url, timeout=NAVER_TIMEOUT, allow_redirects=True)
            resp.raise_for_status()
            return resp
        except Exception as e:
            last_exc = e
            time.sleep(0.8)
    raise last_exc

def _extract_naver_blog(html_text: str) -> str:
    """
    네이버 블로그는 실제 본문이 iframe 내부(/PostView.naver 내부 구조)일 때가 많다.
    모바일 뷰의 #postViewArea, .se-main-container, .se_component_wrap 등을 우선 수집.
    """
    soup = BeautifulSoup(html_text, "lxml")

    # 에디터 3.x
    candidates = []
    for sel in [
        "#postViewArea",
        ".se-main-container",
        ".se_component_wrap",
        "div#postViewArea div",
        "div#post-view",
    ]:
        for el in soup.select(sel):
            txt = _clean_text(el.get_text(" "))
            if len(txt) > 80:
                candidates.append(txt)

    if candidates:
        # 가장 긴 텍스트 선택
        return max(candidates, key=len)

    # 백업: article 태그
    article = soup.find("article")
    if article:
        t = _clean_text(article.get_text(" "))
        if len(t) > 80:
            return t

    # 최후: 페이지 전체에서 본문 후보 추출
    body = soup.find("body")
    if body:
        t = _clean_text(body.get_text(" "))
        return t[:8000]  # 과도한 길이 방지

    return ""

def fetch_and_clean(url: str) -> str:
    """
    1) URL 정규화(스킴·패턴 보정)
    2) GET + 리다이렉트 추적
    3) 네이버 블로그면 특화 파서, 아니면 일반 파서
    """
    norm = _normalize_naver_url(url)
    resp = _get(norm)
    text = resp.text

    u = urlparse(norm)
    if _is_naver_blog(u.netloc):
        content = _extract_naver_blog(text)
    else:
        # 일반 사이트 파싱
        soup = BeautifulSoup(text, "lxml")
        # 메타 description 우선
        desc = soup.find("meta", attrs={"name": "description"})
        if not desc:
            desc = soup.find("meta", attrs={"property": "og:description"})
        if desc and desc.get("content"):
            content = desc["content"].strip()
        else:
            # 본문 후보
            main = soup.find("main") or soup.find("article") or soup.body
            content = _clean_text(main.get_text(" ") if main else soup.get_text(" "))
    if not content:
        raise ValueError("본문 추출 실패")
    return content[:12000]  # 상한선
