import re, json, time, random
from urllib.parse import urlparse, parse_qs
import requests
from bs4 import BeautifulSoup, NavigableString, Tag
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Linux; Android 13; SM-G996N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Mobile Safari/537.36",
]

AD_PATTERNS = [
    r"\b협찬\b", r"\b광고\b", r"\b체험단\b", r"\b원고료\b",
    r"제품을 무상 제공", r"소정의\s*고료", r"제공받아\s*작성",
]
AD_REGEX = re.compile("|".join(AD_PATTERNS))
HASHTAG_REGEX = re.compile(r"(?:^|\s)#[^\s#]{1,40}")
MULTI_WS = re.compile(r"[ \t\u3000]{2,}")
MULTI_NL = re.compile(r"\n{3,}")

class FetchError(Exception):
    pass

def _session():
    s = requests.Session()
    s.headers["User-Agent"] = random.choice(UA_POOL)
    s.headers["Accept-Language"] = "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"
    return s

def normalize_blog_url(url: str):
    """
    다양한 네이버 블로그 URL을 blogId, logNo로 정규화.
    반환: (blogId, logNo)
    """
    u = urlparse(url)
    host = u.netloc.lower()
    path = u.path.strip("/")
    qs = parse_qs(u.query)

    # 1) PC형: https://blog.naver.com/PostView.nhn?blogId=xxx&logNo=yyy
    if "blog.naver.com" in host and ("PostView" in path or "PostView.naver" in path):
        return qs.get("blogId", [None])[0], qs.get("logNo", [None])[0]

    # 2) 블로그 홈형: https://blog.naver.com/xxx/yyy
    if "blog.naver.com" in host and len(path.split("/")) >= 2:
        a, b = path.split("/", 1)
        if b.isdigit():
            return a, b

    # 3) 모바일형: https://m.blog.naver.com/xxx/yyy
    if "m.blog.naver.com" in host and len(path.split("/")) >= 2:
        a, b = path.split("/", 1)
        if b.isdigit():
            return a, b

    # 4) 기타: 최후수단으로 PC 페이지에서 mainFrame 따라가기
    return None, None

@retry(
    retry=retry_if_exception_type(FetchError),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    stop=stop_after_attempt(3)
)
def get(url, session: requests.Session, mobile=False):
    resp = session.get(url, timeout=15)
    if resp.status_code in (429, 503):
        raise FetchError(f"rate limited {resp.status_code}")
    if resp.status_code != 200:
        raise FetchError(f"bad status {resp.status_code}")
    # 간헐적 차단 우회: 모바일 페이지는 종종 200이지만 빈 본문일 수 있음
    if mobile and len(resp.text) < 2000:
        raise FetchError("mobile thin response")
    return resp.text

def extract_from_mobile(blogId, logNo, session):
    """
    m.blog.naver.com/{blogId}/{logNo}에서 본문 HTML 추출
    """
    murl = f"https://m.blog.naver.com/{blogId}/{logNo}"
    html = get(murl, session, mobile=True)
    soup = BeautifulSoup(html, "lxml")

    # 1) 직관적 본문 컨테이너
    body = soup.select_one("div.se-main-container") or soup.select_one("#post-view")
    if body:
        return str(body)

    # 2) 스토어드 상태 JSON에서 contentHtml 찾기
    #   window.__INITIAL_STATE__ = {...}
    for sc in soup.find_all("script"):
        if sc.string and "INITIAL_STATE" in sc.string:
            txt = sc.string
            # 대략적 파서
            try:
                jtxt = txt.split("INITIAL_STATE__=")[1]
                jtxt = jtxt.split(";</script>")[0] if ";</script>" in jtxt else jtxt
                jtxt = jtxt.strip().rstrip(";")
                state = json.loads(jtxt)
                # 구조는 변경될 수 있으므로 방어적으로 탐색
                content_html = None
                # 흔한 경로 후보
                candidates = [
                    ["post", "content", "renderedContent"],
                    ["post", "contentHtml"],
                    ["postView", "post", "content", "content"],
                ]
                def deep_get(d, path):
                    cur = d
                    for k in path:
                        if isinstance(cur, dict) and k in cur:
                            cur = cur[k]
                        else:
                            return None
                    return cur
                for p in candidates:
                    content_html = deep_get(state, p)
                    if content_html:
                        break
                if content_html:
                    return content_html
            except Exception:
                pass

    # 3) 실패
    return None

def extract_from_pc(url, session):
    """
    PC 페이지에서 mainFrame src 추출 후 본문 파싱
    """
    html = get(url, session)
    soup = BeautifulSoup(html, "lxml")
    iframe = soup.select_one("iframe#mainFrame")
    if not iframe or not iframe.get("src"):
        return None

    frame_url = iframe["src"]
    if frame_url.startswith("/"):
        frame_url = f"https://blog.naver.com{frame_url}"

    fhtml = get(frame_url, session)
    fsoup = BeautifulSoup(fhtml, "lxml")
    # 과거 에디터
    area = fsoup.select_one("#postViewArea") or fsoup.select_one(".se-main-container")
    return str(area) if area else None

def clean_html_to_text(html_fragment: str) -> str:
    soup = BeautifulSoup(html_fragment, "lxml")

    # 제거 대상: script/style/noscript
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    # 광고·배너로 흔한 클래스 제거(보수적)
    for tag in soup.find_all(attrs={"class": re.compile(r"(ad|banner|promotion)", re.I)}):
        tag.decompose()

    # 그림 캡션 중 과도한 출처/상호 링크 제거
    for tag in soup.find_all(["a", "span"], string=re.compile(r"(구매|상세|예약|문의|링크)", re.I)):
        if tag.parent and tag.parent.name in ("figcaption", "p"):
            tag.decompose()

    # 줄바꿈 보전
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for p in soup.find_all("p"):
        if p.text.strip():
            p.insert_after(NavigableString("\n"))

    text = soup.get_text("\n")

    # 해시태그 제거
    text = HASHTAG_REGEX.sub(" ", text)

    # 광고문구 줄 단위 필터(선택)
    lines = []
    for line in text.splitlines():
        L = line.strip()
        if not L:
            continue
        if AD_REGEX.search(L):
            continue
        lines.append(L)

    text = "\n".join(lines)

    # 공백 정리
    text = MULTI_WS.sub(" ", text)
    text = MULTI_NL.sub("\n\n", text).strip()
    return text

def fetch_and_clean(url: str):
    """
    최종 단일 진입 함수: URL -> 정제 텍스트
    """
    sess = _session()
    blogId, logNo = normalize_blog_url(url)

    # 1) 모바일 우선
    if blogId and logNo:
        html_frag = extract_from_mobile(blogId, logNo, sess)
        if not html_frag:
            # PC 폴백
            html_frag = extract_from_pc(f"https://blog.naver.com/{blogId}/{logNo}", sess)
    else:
        # 알 수 없는 형태 → PC에서 폴백
        html_frag = extract_from_pc(url, sess)

    if not html_frag:
        raise FetchError("본문 추출 실패")

    cleaned = clean_html_to_text(html_frag)
    # 크롤링 매너
    time.sleep(random.uniform(0.8, 1.5))
    return cleaned

# 사용 예시
if __name__ == "__main__":
    test = "https://blog.naver.com/PostView.naver?blogId=xxxx&logNo=123456789012"
    try:
        print(fetch_and_clean(test)[:1000])
    except Exception as e:
        print("error:", e)
