import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import urljoin

def fetch_and_clean(url):
    """
    네이버 블로그 전용 본문 크롤러 (가장 안정적으로 동작하던 버전)
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        )
    }

    # iframe 내부 URL 추출
    res = requests.get(url, headers=headers, timeout=10)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")

    iframe = soup.find("iframe")
    if iframe and iframe.get("src"):
        inner_url = urljoin("https://blog.naver.com", iframe["src"])
        inner_html = requests.get(inner_url, headers=headers, timeout=10).text
        soup = BeautifulSoup(inner_html, "html.parser")

    # 본문 추출
    texts = []
    for tag in ["div", "p", "span"]:
        for node in soup.find_all(tag):
            text = node.get_text(" ", strip=True)
            if len(text) > 100:
                texts.append(text)

    main_text = max(texts, key=len, default="")
    clean_text = re.sub(r"#\S+", "", main_text)
    clean_text = re.sub(r"광고|협찬", "", clean_text)
    clean_text = re.sub(r"\s+", " ", clean_text).strip()
    clean_text = clean_text[:8000]

    if not clean_text:
        raise ValueError("본문 추출 실패")

    return clean_text
