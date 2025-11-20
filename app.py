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

    parsed = parse_json_safe(resp.choices[0].message.content.strip())

    if not parsed:
        raise RuntimeError("리뷰 구조 분석 실패")

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
def build_blog_prompt(sections, tone, style, length):
    system_prompt = f"""
플랫폼: 네이버 블로그 리뷰 글
목표:
- 1200~1800자
- 묘사체 + 서사체
- 구어체 기반 자연스러운 후기
- 문단 6~7개, 각 문단 3~4문장

톤(Tone): {tone}
스타일(Style): {style}

주의:
- 과장 금지
- 광고 문구 금지
"""

    user_prompt = f"""
섹션 데이터:
{json.dumps(sections, ensure_ascii=False, indent=2)}

위 내용을 활용해 블로그 리뷰를 작성하세요.
목표 글자수: {length}자
"""

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


# ---------------------------------------------------------
# 카페용 프롬프트
# ---------------------------------------------------------
def build_cafe_prompt(sections, tone, style, length):
    system_prompt = f"""
플랫폼: 네이버 카페 후기
목표:
- 600~900자
- 간결한 후기체
- 문단 4~6개

톤(Tone): {tone}
스타일(Style): {style}

주의:
- 과한 감탄 금지
- 광고 문구 금지
"""

    user_prompt = f"""
섹션 데이터:
{json.dumps(sections, ensure_ascii=False, indent=2)}

위 내용을 바탕으로 카페 후기 스타일로 작성하세요.
목표 글자수: {length}자
"""

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


# ---------------------------------------------------------
# 길이 측정
# ---------------------------------------------------------
def measure_length(text: str) -> int:
    return len(text)


# ---------------------------------------------------------
# 길이 재조정 프롬프트 (블로그)
# ---------------------------------------------------------
def build_blog_length_fix_prompt(text, target):
    return [
        {"role": "system", "content": f"""
아래 글의 내용과 문단 구조를 유지하면서 공백 포함 {target}자로 조정하세요.
- 과장 금지
- 자연스러운 압축 또는 확장
"""},
        {"role": "user", "content": text}
    ]


# ---------------------------------------------------------
# 길이 재조정 프롬프트 (카페)
# ---------------------------------------------------------
def build_cafe_length_fix_prompt(text, target):
    return [
        {"role": "system", "content": f"""
아래 후기 내용을 공백 포함 {target}자로 자연스럽게 조정해 주세요.
- 문단 구조 유지
- 핵심만 부드럽게 압축/확장
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

        url_raw = data.get("url", "").strip()
        if not url_raw:
            return jsonify({"error": "URL required"}), 400

        length = int(data.get("length", 1200))
        tone = data.get("tone", "구어체")
        style = data.get("style", "기본체")

        url = normalize_url(url_raw)
        model = select_model()

        raw_text = extract_text_from_url(url)
        sections = analyze_sections(raw_text, model)

        # -----------------------------------------------
        # 블로그 버전 생성
        # -----------------------------------------------
        blog_prompt = build_blog_prompt(sections, tone, style, max(1200, length))
        blog_resp = openai.ChatCompletion.create(
            model=model,
            messages=blog_prompt,
            temperature=0.7,
            max_tokens=2500
        )
        blog_version = blog_resp.choices[0].message.content.strip()

        # 길이 조절
        blog_len = measure_length(blog_version)
        if abs(blog_len - length) > length * 0.1:
            fix_prompt = build_blog_length_fix_prompt(blog_version, length)
            fix_resp = openai.ChatCompletion.create(
                model=model,
                messages=fix_prompt,
                temperature=0.3,
                max_tokens=2500
            )
            blog_version = fix_resp.choices[0].message.content.strip()

        # -----------------------------------------------
        # 카페 버전 생성
        # -----------------------------------------------
        cafe_target = min(900, length)
        cafe_prompt = build_cafe_prompt(sections, tone, style, cafe_target)
        cafe_resp = openai.ChatCompletion.create(
            model=model,
            messages=cafe_prompt,
            temperature=0.7,
            max_tokens=1500
        )
        cafe_version = cafe_resp.choices[0].message.content.strip()

        # 길이 조절
        cafe_len = measure_length(cafe_version)
        if abs(cafe_len - cafe_target) > cafe_target * 0.1:
            fix_prompt = build_cafe_length_fix_prompt(cafe_version, cafe_target)
            fix_resp = openai.ChatCompletion.create(
                model=model,
                messages=fix_prompt,
                temperature=0.3,
                max_tokens=1500
            )
            cafe_version = fix_resp.choices[0].message.content.strip()

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
