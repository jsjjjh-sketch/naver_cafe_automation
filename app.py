import os
import math
import re
import openai 
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify
from urllib.parse import urlparse

app = Flask(__name__)
openai.api_key = os.getenv("OPENAI_API_KEY")

if not openai.api_key:
    raise RuntimeError("OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")


# ---------------------------------------------------------
# 1) URL 정규화
# ---------------------------------------------------------
def normalize_url(url: str) -> str:
    return url.replace("https://blog.naver.com", "https://m.blog.naver.com")


# ---------------------------------------------------------
# 2) 네이버 블로그 판별
# ---------------------------------------------------------
def is_naver_blog(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        return ("blog.naver.com" in host)
    except:
        return False


# ---------------------------------------------------------
# 3) 네이버 블로그 본문 전용 파서
# ---------------------------------------------------------
def extract_naver_blog_text(html: str) -> str:
    soup = BeautifulSoup(html, 'html.parser')

    # 스크립트 제거
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    container = None

    # 1) 최신/모바일 에디터
    if not container:
        container = soup.find("div", class_=re.compile(r"\bse-main-container\b"))

    # 2) 구형 에디터
    if not container:
        container = soup.find("div", id=re.compile(r"^(postViewArea|printPost1)$"))

    # 3) 보조 선택자
    if not container:
        container = soup.find("div", id="post-view") or soup.find(
            "div", class_=re.compile(r"\bse_component_wrap\b")
        )

    # 4) fallback
    if not container:
        container = soup.body or soup

    text = container.get_text(separator=" ", strip=True)

    # 네이버 블로그 공통 UI 텍스트 제거
    noise_patterns = [
        r"이웃추가",
        r"공감\s*\d*",
        r"댓글\s*\d*",
        r"공유하기",
        r"신고하기",
    ]
    for pat in noise_patterns:
        text = re.sub(pat, " ", text)

    text = re.sub(r'\s+', ' ', text).strip()
    return text


# ---------------------------------------------------------
# 4) 일반 HTML 텍스트 파서
# ---------------------------------------------------------
def extract_generic_text(html: str) -> str:
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator=" ").strip()
    text = re.sub(r'\s+', ' ', text)
    return text


# ---------------------------------------------------------
# 5) URL에서 본문 내용 추출 → 네이버 블로그면 전용 파서 적용
# ---------------------------------------------------------
def extract_text_from_url(url: str) -> str:
    try:
        res = requests.get(url, timeout=10, headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0 Safari/537.36"
            )
        })
        res.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"크롤링 실패: {e}")

    html = res.text

    # 네이버 블로그 전용 파서
    if is_naver_blog(url):
        text = extract_naver_blog_text(html)
    else:
        text = extract_generic_text(html)

    if not text:
        raise RuntimeError("본문 추출 실패: 내용이 비어 있음")

    return text


# ---------------------------------------------------------
# 6) 요약 모델 프롬프트 조립
# ---------------------------------------------------------
def build_prompt(text, length, keyword, count, version_count):
    try:
        with open("prompt_rules.txt", "r", encoding="utf-8") as f:
            rule_template = f.read()
        system_prompt = rule_template.format(
            length=length,
            keyword_text=f"- '{keyword}' 단어를 자연스럽게 {count}회 이상 포함" if keyword else "",
            extra_text=""
        )
    except Exception:
        system_prompt = f"""
        조건:
        - 자연스러운 구어체
        - 홍보티 안 나게
        - 중복표현 금지
        - 후기 느낌
        - 문단마다 표현 변화
        - 공백 포함 {length}자 내외
        {'- ' + keyword + f" {count}회 이상 포함" if keyword else ''}
        """

    return [
        {"role": "system", "content": system_prompt.strip()},
        {
            "role": "user",
            "content": f"""다음은 블로그 원문입니다. 내용을 읽고 위 지침에 따라 요약해 주세요.

'''{text}'''

요약 조건: 공백 포함 {length}자 이내, {version_count}개 글로 작성.
키워드: '{keyword}' (총 {count}회 이상 포함)

작성해 주세요.
"""
        }
    ]


# ---------------------------------------------------------
# 7) 모델 선택
# ---------------------------------------------------------
def select_model():
    priority = ["gpt-4.1-mini", "gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"]
    for m in priority:
        try:
            openai.Model.retrieve(m)
            return m
        except:
            continue
    return "gpt-3.5-turbo"


# ---------------------------------------------------------
# 8) 메인 API 엔드포인트
# ---------------------------------------------------------
@app.route("/api/summary_advanced", methods=["POST"])
def summary_advanced():
    try:
        data = request.get_json(force=True)
        url_raw = data.get("url", "").strip()
        if not url_raw:
            return jsonify({"error": "URL is required"}), 400

        length = data.get("length", 300)
        keyword = data.get("keyword", "").strip()
        count = int(data.get("count", 1))
        extra = data.get("extra", "").strip()
        version_count = int(data.get("version_count", 1))

        urls = re.split(r'[\n,]+', url_raw)
        urls = [normalize_url(u.strip()) for u in urls if u.strip()]
        if len(urls) > count:
            urls = urls[:count]

        model = select_model()
        results = []

        for u in urls:
            try:
                raw_text = extract_text_from_url(u)
            except Exception as e:
                results.append(f"(크롤링 실패) {u} - {str(e)}")
                continue

            prompt = build_prompt(
                raw_text,
                length,
                keyword,
                count,
                version_count
            )

            try:
                response = openai.ChatCompletion.create(
                    model=model,
                    messages=prompt,
                    temperature=0.7,
                    n=version_count,
                    max_tokens=int(length * 1.5)
                )
                for choice in response.choices:
                    results.append(choice.message.content.strip())
            except Exception as e:
                results.append(f"(요약 실패) {u} - {str(e)}")

        return jsonify({"summary_list": results, "model_used": model})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------
# 9) 서버 실행
# ---------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
