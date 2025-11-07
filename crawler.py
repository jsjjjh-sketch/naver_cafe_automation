import requests
from bs4 import BeautifulSoup
import re
import os
from openai import OpenAI

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))


def fetch_and_clean(url):
    """
    입력된 URL의 본문 또는 음성을 텍스트로 변환해 반환.
    지원: 네이버 블로그 / 뉴스 / 카페 / 인스타그램 / 일반 웹 / YouTube (자막 + 음성)
    """

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        )
    }

    # --- 1️⃣ YouTube 처리 (자막 + 음성) ---
    if "youtube.com" in url or "youtu.be" in url:
        try:
            from youtube_transcript_api import YouTubeTranscriptApi

            video_id = (
                url.split("v=")[-1].split("&")[0]
                if "v=" in url
                else url.split("/")[-1]
            )
            transcript = YouTubeTranscriptApi.get_transcript(
                video_id, languages=["ko", "en"]
            )
            captions = " ".join([x["text"] for x in transcript])
            if len(captions) > 50:
                return re.sub(r"\s+", " ", captions.strip())[:8000]
        except Exception:
            pass  # 자막 없으면 음성 다운로드로 이동

        # --- 1-2️⃣ 자막 없을 때: 오디오 추출 후 Whisper 변환 ---
        try:
            from pytube import YouTube
            import tempfile

            yt = YouTube(url)
            audio_stream = yt.streams.filter(only_audio=True).first()

            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as temp_audio:
                audio_path = temp_audio.name
                audio_stream.download(filename=audio_path)

            with open(audio_path, "rb") as audio_file:
                transcript = client.audio.transcriptions.create(
                    model="gpt-4o-mini-transcribe",
                    file=audio_file
                )

            os.remove(audio_path)
            return transcript.text[:8000]
        except Exception as e:
            return f"유튜브 음성 인식 실패: {e}"

    # --- 2️⃣ 일반 HTML 요청 ---
    res = requests.get(url, headers=headers, timeout=10)
    res.raise_for_status()
    html = res.text
    soup = BeautifulSoup(html, "html.parser")

    # --- 3️⃣ 네이버 블로그 ---
    if "blog.naver.com" in url:
        iframe = soup.find("iframe")
        if iframe and iframe.get("src"):
            inner_url = iframe["src"]
            inner_html = requests.get(inner_url, headers=headers).text
            soup = BeautifulSoup(inner_html, "html.parser")

    # --- 4️⃣ 본문 후보 추출 ---
    candidates = []
    for tag in ["article", "div", "p"]:
        for node in soup.find_all(tag):
            text = node.get_text(" ", strip=True)
            if len(text) > 100:
                candidates.append(text)
    main_text = max(candidates, key=len, default="")

    # --- 5️⃣ 인스타그램 ---
    if "instagram.com" in url:
        meta = soup.find("meta", {"property": "og:description"})
        if meta and meta.get("content"):
            main_text = meta["content"]

    # --- 6️⃣ 네이버 카페 ---
    if "cafe.naver.com" in url:
        article = soup.find("div", {"class": re.compile("article|content")})
        if article:
            main_text = article.get_text(" ", strip=True)

    # --- 7️⃣ 정제 ---
    clean_text = re.sub(r"#\S+", "", main_text)
    clean_text = re.sub(r"광고|협찬", "", clean_text)
    clean_text = re.sub(r"\s+", " ", clean_text).strip()
    clean_text = clean_text[:8000]

    return clean_text or "본문을 불러오지 못했습니다."
