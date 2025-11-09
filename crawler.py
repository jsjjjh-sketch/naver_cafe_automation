# crawler.py (본문 추출 강화판)
import re, html, time, requests
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse, urljoin
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Linux; Android 11; SM-G973N) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0.0.0 Mobile Safari/537.36")

session = requests.Session()
session.headers.update({
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
    "Connection": "close",
})

def _clean(txt):
    txt = html.unescape(txt)
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip()

def _normalize(url):
    if not url.startswith("http"):
        url = "https://" + url
    u = urlparse(url)
    if "m.blog.naver.com" in u.netloc:
        return url
    if "blog.naver.com" in u.netloc:
        qs = parse_qs(u.query)
        if "blogId" in qs and "logNo" in qs:
            new = ("https", "m.blog.naver.com", f"/{qs['blogId'][0]}/{qs['logNo'][0]}", "", "", "")
            return urlunparse(new)
        m = re.match(r"^/([^/]+)/(\d+)", u.path)
        if m:
            return f"https://m.blog.naver.com/{m.group(1)}/{m.group(2)}"
    return url

def _get_html(url):
    for _ in range(3):
        try:
            res = session.get(url, timeout=(8, 15))
            if res.status_code == 200:
                return res.text
            time.sleep(1)
        except Exception:
            time.sleep(1)
    raise ValueError("요청 실패")

def _extract_blog(html_text):
    soup = BeautifulSoup(html_text, "lxml")

    # 1️⃣ 최신 에디터(4.x, 3.x)
    sel_list = [
        ".se-main-container", ".se_component_wrap", "div#postViewArea",
        ".se_textView", "div#viewTypeSelector", "div#contentArea"
    ]
    texts = []
    for sel in sel_list:
        for el in soup.select(sel):
            t = _clean(el.get_text(" "))
            if len(t) > 100:
                texts.append(t)

    # 2️⃣ iframe 내부 추적
    if not texts:
        iframe = soup.find("iframe", id="mainFrame")
        if iframe and iframe.get("src"):
            inner_url = urljoin("https://blog.naver.com", iframe["src"])
            inner_html = _get_html(inner_url)
            return _extract_blog(inner_html)

    if texts:
        return max(texts, key=len)

    # 3️⃣ 백업: article 태그나 body
    article = soup.find("article")
    if article:
        return _clean(article.get_text(" "))
    body = soup.find("body")
    return _clean(body.get_text(" ")) if body else ""

def fetch_and_clean(url):
    norm = _normalize(url)
    html_text = _get_html(norm)
    text = _extract_blog(html_text)
    if not text or len(text) < 50:
        raise ValueError("본문 추출 실패")
    return text[:12000]
