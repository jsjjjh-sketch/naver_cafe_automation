import re, html, time, random, requests
from urllib.parse import urlparse, parse_qs, urlunparse, urljoin
from bs4 import BeautifulSoup

USER_AGENTS = [
    "Mozilla/5.0 (Linux; Android 11; SM-G973N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
]

session = requests.Session()

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
            session.headers["User-Agent"] = random.choice(USER_AGENTS)
            res = session.get(url, timeout=(8, 15))
            if res.status_code == 200:
                time.sleep(random.uniform(0.8, 2.0))
                return res.text
            time.sleep(random.uniform(1.0, 2.0))
        except Exception:
            time.sleep(random.uniform(1.0, 2.0))
    raise ValueError("요청 실패")

def _extract_blog(html_text):
    soup = BeautifulSoup(html_text, "lxml")
    selectors = [
        ".se-main-container", "#postViewArea", ".se_textView",
        ".se_component_wrap", "div#viewTypeSelector", "div#contentArea"
    ]
    texts = []
    for sel in selectors:
        for el in soup.select(sel):
            t = _clean(el.get_text(" "))
            if len(t) > 100:
                texts.append(t)
    if not texts:
        iframe = soup.find("iframe", id="mainFrame")
        if iframe and iframe.get("src"):
            inner_url = urljoin("https://blog.naver.com", iframe["src"])
            return _extract_blog(_get_html(inner_url))
    if texts:
        return max(texts, key=len)
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
