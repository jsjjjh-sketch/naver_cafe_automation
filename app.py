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
# 네이버 블로그 본문 추출
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

    return re.sub(r'\s+', ' ', text).strip()


# ---------------------------------------------------------
# 일반 HTML 텍스트 추출
# ---------------------------------------------------------
def extract_generic_text(html: str) -> str:
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return re.sub(r'\s+', ' ', soup.get_text(separator=" ").strip())


# ---------------------------------------------------------
# URL에서 본문 텍스트 추출
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
    return extract_naver_blog_text(html) if is_naver_blog(url) else extract_generic_text(html)


# ---------------------------------------------------------
# JSON 파싱
# ---------------------------------------------------------
def parse_json_safe(text: str):
    try:
        if "```" in text:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1:
                return json.loads(text[start:end+1])
        return json.loads(text)
    except:
        return None


# ---------------------------------------------------------
# 자동 문단 구조 분석 엔진
# ---------------------------------------------------------
def analyze_sections(text: str, model: str):
    system_prompt = """
당신의 역할은 음식점 방문 리뷰를 구조화하는 편집자입니다.

아래 원문을 7개 문단 카테고리로 분류하세요:

1. 도입부
2. 매장 기본 정보
3. 공간/분위기 묘사
4. 주문 메뉴 소개
5. 맛/식감/향 표현
6. 매장 장점 정리
7. 총평

JSON으로만 출력하세요:
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
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text},
    ]

    resp = openai.ChatCompletion.create(
        model=model,
        messages=messages,
        temperature=0.1,
        max_tokens=1200
    )

    content = resp.choices[0].message.content.strip()
    parsed = parse_json_safe(content)

    # ✅ JSON 파싱 실패 시에도 시스템이 죽지 않도록 폴백 처리
    if not parsed:
        print("[WARN] analyze_sections: JSON 파싱 실패, 기본 섹션으로 대체합니다.")
        # 원문 앞 500자 정도를 도입부로, 나머지를 맛 표현으로 사용
        intro_part = text[:500]
        rest_part = text[500:1500]

        return {
            "intro": intro_part,
            "store_info": "",
            "atmosphere": "",
            "menu_intro": "",
            "taste_review": rest_part,
            "strengths": "",
            "conclusion": ""
        }

    default = {
        "intro": "",
        "store_info": "",
        "atmosphere": "",
        "menu_intro": "",
        "taste_review": "",
        "strengths": "",
        "conclusion": ""
    }
    default.update({k: v for k, v in parsed.items() if isinstance(v, str)})
    return default



# ---------------------------------------------------------
# 블로그용 프롬프트
# ---------------------------------------------------------
def build_blog_prompt(sections, tone, style, length, keyword, count, extra):
    system_prompt = f"""
플랫폼: 네이버 블로그 리뷰 글

요구사항:
- 공백 포함 {length}자 ±5% 범위로 맞춰 작성
- 키워드 "{keyword}" 를 최소 {count}회 자연스럽게 포함
- 추가 요소: "{extra}" 는 내용의 흐름에 맞게 자연스럽게 반영
- 문단 6~7개, 각 문단 3~4문장
- 도입 → 분위기 → 메뉴 → 맛 표현 → 장점 → 총평의 구조 유지
- 광고 문구 금지
- 문장을 자연스럽게 이어가기

톤(Tone): {tone}
스타일(Style): {style}
"""

    user_prompt = f"""
아래는 원문에서 추출한 리뷰 섹션입니다:

{json.dumps(sections, ensure_ascii=False, indent=2)}

위 내용을 기반으로 자연스럽고 읽기 좋은 블로그 리뷰 글을 만들어주세요.

반드시 지킬 조건:
1) 키워드 "{keyword}"를 최소 {count}회 포함
2) 공백 포함 {length}자 ±5% 유지
3) 문단은 6~7개
4) 내용 삭제 금지, 맥락 유지

추가 요청: "{extra}"
"""

    return [
        {"role": "system", "content": system_prompt.strip()},
        {"role": "user", "content": user_prompt.strip()},
    ]


# ---------------------------------------------------------
# 카페용 프롬프트
# ---------------------------------------------------------
def build_cafe_prompt(sections, tone, style, length, keyword, count, extra):
    system_prompt = f"""
플랫폼: 네이버 카페 후기

요구사항:
- 공백 포함 {length}자 ±5%
- 키워드 "{keyword}" 최소 {count}회 포함
- 문단 4~6개
- 간결하고 후기 느낌 표현
- 추가 요청 "{extra}" 자연스럽게 반영
"""

    user_prompt = f"""
아래는 원문에서 추출된 주요 내용입니다:

{json.dumps(sections, ensure_ascii=False, indent=2)}

위 내용을 기반으로 카페 후기 스타일의 글을 작성해주세요.

반드시 지킬 조건:
- 키워드 "{keyword}" 최소 {count}회 삽입
- {length}자 ±5%
- 후기처럼 자연스럽고 쉬운 표현
"""

    return [
        {"role": "system", "content": system_prompt.strip()},
        {"role": "user", "content": user_prompt.strip()},
    ]


# ---------------------------------------------------------
# 길이 측정
# ---------------------------------------------------------
def measure_length(text: str) -> int:
    return len(text)


# ---------------------------------------------------------
# 길이 재조정 프롬프트 (블로그)
# ---------------------------------------------------------
def build_blog_length_fix_prompt(text, target, keyword, count):
    return [
        {"role": "system", "content": f"""
아래 글을 공백 포함 {target}자 ±5%로 수정하세요.
키워드 "{keyword}"는 최소 {count}회 유지해야 합니다.
문단 구조와 내용 흐름은 변경하지 마세요.
삭제 금지, 의미 축약 금지. 표현만 자연스럽게 압축/확장하세요.
"""},
        {"role": "user", "content": text}
    ]


# ---------------------------------------------------------
# 길이 재조정 프롬프트 (카페)
# ---------------------------------------------------------
def build_cafe_length_fix_prompt(text, target, keyword, count):
    return [
        {"role": "system", "content": f"""
아래 후기를 공백 포함 {target}자 ±5%로 재작성해주세요.
키워드 "{keyword}"는 최소 {count}회 유지하세요.
후기 톤을 유지하며 부드럽게 압축/확장하세요.
내용 삭제 금지.
"""},
        {"role": "user", "content": text}
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
        print("[DEBUG] Received data:", data)

        url_raw = data.get("url", "").strip()
        if not url_raw:
            print("[ERROR] Missing URL")
            return jsonify({"error": "URL is required"}), 400

        length = int(data.get("length", 1200))
        tone = data.get("tone", "구어체")
        style = data.get("style", "기본체")

        url = normalize_url(url_raw)
        model = select_model()

        print("[DEBUG] Model selected:", model)
        print("[DEBUG] Normalized URL:", url)

        # 본문 추출
        raw_text = extract_text_from_url(url)
        print("[DEBUG] Extracted text length:", len(raw_text))

        # 섹션 분석
        sections = analyze_sections(raw_text, model)
        print("[DEBUG] Sections extracted:", sections)

        # 블로그 버전 생성
try:
    blog_prompt = build_blog_prompt(
        sections=sections,
        tone=tone,
        style=style,
        length=max(1200, length),
        keyword=data.get("keyword", ""),
        count=data.get("count", 1),
        extra=data.get("extra", "")
    )

    blog_resp = openai.ChatCompletion.create(
        model=model,
        messages=blog_prompt,
        temperature=0.7,
        max_tokens=2500
    )
    blog_version = blog_resp.choices[0].message.content.strip()
    print("[DEBUG] blog_version created")

except Exception as e:
    print("[ERROR] Blog version generation failed:", str(e))
    return jsonify({"error": "blog_generation_error", "detail": str(e)}), 500


        # 카페 버전 생성
try:
    cafe_target = min(900, length)

    cafe_prompt = build_cafe_prompt(
        sections=sections,
        tone=tone,
        style=style,
        length=cafe_target,
        keyword=data.get("keyword", ""),
        count=data.get("count", 1),
        extra=data.get("extra", "")
    )

    cafe_resp = openai.ChatCompletion.create(
        model=model,
        messages=cafe_prompt,
        temperature=0.7,
        max_tokens=1500
    )
    cafe_version = cafe_resp.choices[0].message.content.strip()
    print("[DEBUG] cafe_version created")

except Exception as e:
    print("[ERROR] Cafe version generation failed:", str(e))
    return jsonify({"error": "cafe_generation_error", "detail": str(e)}), 500



# ---------------------------------------------------------
# 실행
# ---------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
