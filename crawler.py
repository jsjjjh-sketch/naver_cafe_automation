import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from pytube import YouTube
from youtube_transcript_api import YouTubeTranscriptApi

# ------------------------------
# URL 자동 정리 및 텍스트 추출
# ------------------------------
def fetch_and_clean(url: str) -> str:
    """자동으로 매체를 판별하고, 텍스트를 크롤링 후 정제."""
    if not url.startswith("http"):
        # /PostView.naver?... 같은 상대경로 보정
        url = urljoin("https://blog.naver.com", url)

    # --- 네이버 블로그 ---
    if "blog.naver.com" in url:
        return crawl_naver_blog(url)

    # --- 네이버 카페 ---
    elif "cafe.naver.com" in url:
        return crawl_naver_cafe(url)

    # --- 뉴스 ---
    elif any(x in url for x in ["news.naver.com", "n.news.naver.com"]):
        return crawl_naver_news(url)

    # --- 인스타그램 ---
    elif "instagram.com" in url:
        return crawl_instagram(url)

    # --- 유튜브 ---
    elif "youtube.com" in url or "youtu.be" in url:
        return crawl_youtube(url)

    else:
        # 일반 웹페이지
        return crawl_generic(url)


# ------------------------------
# 네이버 블로그 크롤링
# ------------------------------
def crawl_naver_blog(url):
    res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")

    # iframe 내부 실제 본문 URL 추출
    iframe = soup.find("iframe", id="mainFrame")
    if iframe and iframe.get("src"):
        inner_url = urljoin("https://blog.naver.com", iframe["src"])
        res = requests.get(inner_url, headers={"User-Agent": "Mozilla/5.0"})
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")

    texts = [t.get_text(strip=True) for t in soup.find_all(["p", "span", "div"])]
    clean_text = " ".join(texts)
    return clean_text[:8000]


# ------------------------------
# 네이버 카페 크롤링
# ------------------------------
def crawl_naver_cafe(url):
    res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")

    texts = [t.get_text(strip=True) for t in soup.find_all(["p", "span", "div"])]
    return " ".join(texts)[:8000]


# ------------------------------
# 네이버 뉴스 크롤링
# ------------------------------
def crawl_naver_news(url):
    res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")

    body = soup.find("article") or soup.find("div", {"id": "dic_area"})
    if not body:
        body = soup
    return body.get_text(" ", strip=True)[:8000]


# ------------------------------
# 인스타그램 포스트 텍스트 추출
# ------------------------------
def crawl_instagram(url):
    try:
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(res.text, "html.parser")
        desc = soup.find("meta", attrs={"property": "og:description"})
        if desc:
            return desc["content"]
        return "인스타그램 포스트의 내용을 가져올 수 없습니다."
    except Exception as e:
        return f"[instagram error] {e}"


# ------------------------------
# 유튜브: 제목 + 자막 텍스트
# ------------------------------
def crawl_youtube(url):
    try:
        yt = YouTube(url)
        transcript = YouTubeTranscriptApi.get_transcript(yt.video_id, languages=["ko", "en"])
        text = " ".join([x["text"] for x in transcript])
        return f"[제목] {yt.title}\n{text[:8000]}"
    except Exception as e:
        return f"[youtube error] {e}"


# ------------------------------
# 일반 웹페이지 (fallback)
# ------------------------------
def crawl_generic(url):
    res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")
    text = " ".join([t.get_text(strip=True) for t in soup.find_all(["p", "span", "div"])])
    return text[:8000]
