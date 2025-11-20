import os
import re
import json
import openai
import requests
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify

app = Flask(__name__)
openai.api_key = os.getenv("OPENAI_API_KEY")

if not openai.api_key:
    raise RuntimeError("OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")


# ---------------------------------------------------------
# URL 정규화
# ---------------------------------------------------------
def normalize_url(url: str) -> str:
    return url.replace("https://blog.naver.com", "https://m.blog.naver.com")


# ---------------------------------------------------------
# 네이버 블로그 판별
# ---------------------------------------------------------
def is_naver_blog(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        return "blog.naver.com" in host
    except:
        return False


# ---------------------------------------------------------
# 네이버 블로그 본문 파서
# ---------------------------------------------------------
def extract_naver_blog_text(html: str) -> str:
    soup = BeautifulSoup(html, 'html.parser')

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    container = None

    if not container:
        container = soup.find("div", class_=re.compile(r"\bse-main-container\b"))
    if not container:
        container = soup.find("div", id=re.compile(r"^(postViewArea|printPost1)$"))
    if not container:
        container = soup.find("div", id="post-view") or soup.find(
            "div", class_=re.compile(r"\bse_component_wrap\b")
        )
    if not container:
        container = soup.body or soup

    text = container.get_text(separator=" ", strip=True)

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
# 일반 HTML 파서
# ---------------------------------------------------------
def extract_generic_text(html: str) -> str:
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text(separator=" ").strip()
    text = re.sub(r'\s+', ' ', text)
    return text


# ---------------------------------------------------------
# URL에서 본문 추출
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

    if is_naver_blog(url):
        text = extract_naver_blog_text(html)
    else:
        text = extract_generic_text(html)

    if not text:
        raise RuntimeError("본문 추출 실패: 내용이 비어 있음")

    return text


# ---------------------------------------------------------
# JSON 파싱 안전 처리
# ---------------------------------------------------------
def parse_json_safe(text: str):
    try:
        if "```" in text:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                return json.loads(text[start:end+1])
        return json.loads(text)
    except:
        return None


# ---------------------------------------------------------
# 문단 분석 엔진
# ---------------------------------------------------------
def analyze_sections(text: str, model: str):
    system_prompt = """
당신의 역할은 음식점 방문 리뷰를 구조화하는 편집자입니다.

다음 7개 항목으로 원문을 분류하세요.

1. 도입부
2. 매장 기본 정보
3. 공간/분위기 묘사
4. 주문 메뉴 소개
5. 맛/식감/향 표현
6. 매장 장점 정리
7. 총평

JSON 형식으로만 출력하세요:

{
  "intro": "",
  "store_info": "",
  "atmosphere": "",
  "menu_intro": "",
  "taste_review": "",
  "strengths": "",
  "conclusion": ""
}
"""

    messages = [
        {"role": "system", "content": system_prompt.strip()},
        {
            "role": "user",
            "content": f"'''{text}'''"
        },
    ]

    resp = openai.ChatCompletion.create(
        model=model,
        messages=messages,
        temperature=0.1,
        max_tokens=1200,
    )

    content = resp.choices[0].message.content.strip()
    sections = parse_json_safe(content)

    if not sections:
        raise RuntimeError("섹션 분석 실패")

    default_sections = {
        "intro": "",
        "store_info": "",
        "atmosphere": "",
        "menu_intro": "",
        "taste_review": "",
        "strengths": "",
        "conclusion": ""
    }
    default_sections.update({k: v for k, v in sections.items() if isinstance(v, str)})
    return default_sections


# ---------------------------------------------------------
# 블로그 프롬프트
# ---------------------------------------------------------
def build_blog_prompt(sections, tone, style, length):
    system_prompt = f"""
플랫폼: 네이버 블로그 리뷰 글

목표:
- 1200~1800자 자연스러운 후기형 리뷰
- 묘사체 + 서사체 기반
- 정보성 + 감성 균형
- 문단 6~7개
- 각 문단 3~4문장
- 광고성 문구 금지
- 자연스러운 방문기처럼 작성

톤(Tone): {tone}
스타일(Style): {style}

절대 규칙:
- 과장 금지(대박/존맛/최고 등)
- 출력은 텍스트만
"""

    user_prompt = f"""
다음은 원문 분석 섹션입니다:

{json.dumps(sections, ensure_ascii=False, indent=2)}

이 내용을 바탕으로 네이버 블로그용 리뷰를 작성하세요.
목표 글자수: {length}자 내외
"""

    return [
        {"role": "system", "content": system_prompt.strip()},
        {"role": "user", "content": user_prompt.strip()},
    ]


# ---------------------------------------------------------
# 카페 프롬프트
# ---------------------------------------------------------
def build_cafe_prompt(sections, tone, style, length):
    system_prompt = f"""
플랫폼: 네이버 카페 후기

목표:
- 600~900자
- 구어체 + 후기톤
- 문단 4~6개
- 각 문단 2~3문장
- 핵심 위주
- 간결하고 가독성 좋게

톤(Tone): {tone}
스타일(Style): {style}

주의:
- 과한 감탄/광고 문구 금지
- 자연스러운 후기 말투 유지
"""

    user_prompt = f"""
다음은 원문 섹션입니다:

{json.dumps(sections, ensure_ascii=False, indent=2)}

이 내용을 바탕으로 네이버 카페용 리뷰를 작성하세요.
목표 글자수: {length}자 내외
"""

    return [
        {"role": "system", "content": system_prompt.strip()},
        {"role": "user", "content": user_prompt.strip()},
    ]


# ---------------------------------------------------------
# 모델 선택
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
# API 메인 엔드포인트
# ---------------------------------------------------------
@app.route("/api/summary_advanced", methods=["POST"])
def summary_advanced():
    try:
        data = request.get_json(force=True)
        url_raw = data.get("url", "").strip()

        if not url_raw:
            return jsonify({"error": "URL is required"}), 400

        length = int(data.get("length", 1200))
        tone = data.get("tone", "구어체").strip()
        style = data.get("style", "기본체").strip()

        url = normalize_url(url_raw)
        model = select_model()

        raw_text = extract_text_from_url(url)
        sections = analyze_sections(raw_text, model)

        # ---------------------------
        # Blog Version
        # ---------------------------
        blog_prompt = build_blog_prompt(
            sections=sections,
            tone=tone,
            style=style,
            length=max(1200, length)
        )

        blog_resp = openai.ChatCompletion.create(
            model=model,
            messages=blog_prompt,
            temperature=0.7,
            max_tokens=2500
        )
        blog_version = blog_resp.choices[0].message.content.strip()

        # ---------------------------
        # Cafe Version
        # ---------------------------
        cafe_prompt = build_cafe_prompt(
            sections=sections,
            tone=tone,
            style=style,
            length=min(900, length)
        )

        cafe_resp = openai.ChatCompletion.create(
            model=model,
            messages=cafe_prompt,
            temperature=0.7,
            max_tokens=1500
        )
        cafe_version = cafe_resp.choices[0].message.content.strip()

        return jsonify({
            "blog_version": blog_version,
            "cafe_version": cafe_version,
            "tone_used": tone,
            "style_used": style,
            "model_used": model
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------
# 실행
# ---------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
