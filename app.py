import os
import re
import json
import requests
import openai
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify

# ---------------------------------------------------------
# 기본 설정
# ---------------------------------------------------------
app = Flask(__name__)

openai.api_key = os.getenv("OPENAI_API_KEY")
if not openai.api_key:
    raise RuntimeError("OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")


# ---------------------------------------------------------
# 유틸: URL 정규화 / 블로그 판별
# ---------------------------------------------------------
def normalize_url(url: str) -> str:
    return url.replace("https://blog.naver.com", "https://m.blog.naver.com")


def is_naver_blog(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        return "blog.naver.com" in host
    except Exception:
        return False


# ---------------------------------------------------------
# 네이버 블로그/포스트 본문 추출
# ---------------------------------------------------------
def extract_naver_blog_text(html: str, base_url: str) -> str:
    """
    네이버 블로그/포스트 본문 추출
    - iframe 기반(realContentUrl) 대응
    - se-main-container / se-viewer / se_component_wrap / post_ct 등 대응
    """
    # 1차 HTML에서 realContentUrl 찾기 (iframe 방식)
    real_match = re.search(r'"realContentUrl"\s*:\s*"([^"]+)"', html)
    if real_match:
        real_url = real_match.group(1).replace("\\", "")
        if real_url.startswith("/"):
            real_url = "https://blog.naver.com" + real_url
        try:
            res2 = requests.get(real_url, timeout=10, headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0 Safari/537.36"
                )
            })
            res2.raise_for_status()
            html = res2.text
        except Exception as e:
            print("[WARN] realContentUrl 요청 실패:", e)

    soup = BeautifulSoup(html, "html.parser")

    # 스크립트/스타일 제거
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    container = None

    # 최신 에디터
    if not container:
        container = soup.find("div", class_=re.compile(r"\bse-main-container\b"))
    # se-viewer
    if not container:
        container = soup.find("div", class_=re.compile(r"\bse-viewer\b"))
    # 구형 에디터
    if not container:
        container = soup.find("div", id=re.compile(r"^(postViewArea|printPost1)$"))
    # component_wrap
    if not container:
        container = soup.find("div", class_=re.compile(r"\bse_component_wrap\b"))
    # 포스트(post_ct)
    if not container:
        container = soup.find("div", class_="post_ct")
    # fallback
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

    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------------------------------------------------------
# 일반 HTML 본문 추출
# ---------------------------------------------------------
def extract_generic_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ---------------------------------------------------------
# URL → 본문 텍스트
# ---------------------------------------------------------
def extract_text_from_url(url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        )
    }
    try:
        res = requests.get(url, timeout=10, headers=headers)
        res.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"크롤링 실패: {e}")

    html = res.text

    if is_naver_blog(url):
        text = extract_naver_blog_text(html, url)
    else:
        text = extract_generic_text(html)

    if not text:
        raise RuntimeError("본문 추출 실패: 내용이 비어 있음")

    return text


# ---------------------------------------------------------
# JSON 파싱 유틸
# ---------------------------------------------------------
def parse_json_safe(text: str):
    try:
        if "```" in text:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                return json.loads(text[start : end + 1])
        return json.loads(text)
    except Exception:
        return None


# ---------------------------------------------------------
# 리뷰 섹션 분석 엔진 (JSON 실패 시 폴백 포함)
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
        {"role": "system", "content": system_prompt.strip()},
        {"role": "user", "content": text},
    ]

    resp = openai.ChatCompletion.create(
        model=model,
        messages=messages,
        temperature=0.1,
        max_tokens=1200,
    )

    content = resp.choices[0].message.content.strip()
    parsed = parse_json_safe(content)

    # JSON 파싱 실패 시 폴백
    if not parsed:
        print("[WARN] analyze_sections: JSON 파싱 실패, 기본 섹션으로 폴백.")
        intro_part = text[:500]
        rest_part = text[500:1500]
        return {
            "intro": intro_part,
            "store_info": "",
            "atmosphere": "",
            "menu_intro": "",
            "taste_review": rest_part,
            "strengths": "",
            "conclusion": "",
        }

    default = {
        "intro": "",
        "store_info": "",
        "atmosphere": "",
        "menu_intro": "",
        "taste_review": "",
        "strengths": "",
        "conclusion": "",
    }
    default.update({k: v for k, v in parsed.items() if isinstance(v, str)})
    return default


# ---------------------------------------------------------
# 블로그용 프롬프트
# ---------------------------------------------------------
def build_blog_prompt(sections, tone, style, length, keyword, count, extra):
    keyword_rule = ""
    if keyword:
        keyword_rule = f'- 키워드 "{keyword}"를 본문에 최소 {count}회 자연스럽게 포함하세요.\n'

    extra_rule = ""
    if extra:
        extra_rule = f'- 추가 요청: "{extra}" 내용을 문맥에 맞게 반영하세요.\n'

    system_prompt = f"""
플랫폼: 네이버 블로그 리뷰 글

목표:
- 공백 포함 {length}자 ±5% 분량
- 묘사와 서사가 적절히 섞인 자연스러운 후기
- 문단 6~7개, 각 문단 3~4문장
- 도입 → 매장정보 → 분위기 → 메뉴 → 맛 → 장점 → 총평 흐름 유지
- 과장 및 광고 문구(대박, 존맛, 인생맛집 등) 사용 금지

톤(Tone): {tone}
스타일(Style): {style}

작성 규칙:
- 가능한 한 한 문장은 12~24자 길이로 유지하세요.
- 실제 방문 후기를 쓰는 것처럼 자연스럽게 서술하세요.
{keyword_rule}{extra_rule}
"""

    user_prompt = f"""
아래는 원문 리뷰에서 추출한 섹션 데이터입니다:

{json.dumps(sections, ensure_ascii=False, indent=2)}

이 내용을 기반으로 네이버 블로그에 올릴 리뷰 글을 작성해 주세요.

조건 요약:
1) 공백 포함 {length}자 ±5% 분량
2) 위 섹션 구조(도입, 정보, 분위기, 메뉴, 맛, 장점, 총평)를 자연스럽게 녹여서 작성
3) 후기처럼 자연스럽고, 광고 티는 나지 않게
"""

    return [
        {"role": "system", "content": system_prompt.strip()},
        {"role": "user", "content": user_prompt.strip()},
    ]


# ---------------------------------------------------------
# 카페용 프롬프트
# ---------------------------------------------------------
def build_cafe_prompt(sections, tone, style, length, keyword, count, extra):
    keyword_rule = ""
    if keyword:
        keyword_rule = f'- 키워드 "{keyword}"를 본문에 최소 {count}회 자연스럽게 포함하세요.\n'

    extra_rule = ""
    if extra:
        extra_rule = f'- 추가 요청: "{extra}" 내용을 문맥에 맞게 반영하세요.\n'

    system_prompt = f"""
플랫폼: 네이버 카페 후기

목표:
- 공백 포함 {length}자 ±5% 분량
- 문단 4~6개, 각 문단 2~3문장
- 간결하고 읽기 쉬운 후기 스타일
- 방문 이유 → 분위기 → 메뉴/맛 → 장점 → 마무리 흐름

톤(Tone): {tone}
스타일(Style): {style}

작성 규칙:
- 문단은 반드시 줄바꿈(빈 줄)으로 구분할 것
- 한 문장은 8~18자로 짧게 유지
- 과장 표현 및 광고성 표현 금지
{keyword_rule}{extra_rule}
"""

    user_prompt = f"""
아래는 원문 리뷰에서 추출한 섹션 데이터입니다:

{json.dumps(sections, ensure_ascii=False, indent=2)}

이 내용을 기반으로 네이버 카페 게시판에 올릴 후기 글을 생성하세요.

반드시 지킬 조건:
1) 문단은 반드시 "빈 줄"을 넣어 명확히 구분할 것
2) 공백 포함 {length}자 ±5% 유지
3) 간결하고 담백한 후기 톤 유지
"""

    return [
        {"role": "system", "content": system_prompt.strip()},
        {"role": "user", "content": user_prompt.strip()},
    ]


# ---------------------------------------------------------
# 길이 측정 / 길이 보정 프롬프트
# ---------------------------------------------------------
def measure_length(text: str) -> int:
    return len(text)


def build_blog_length_fix_prompt(text, target, keyword, count):
    keyword_rule = ""
    if keyword:
        keyword_rule = (
            f'- 키워드 "{keyword}"가 최소 {count}회 이상 포함되도록 유지하세요.\n'
        )

    system_prompt = f"""
아래 블로그 리뷰 글의 내용과 문단 구조를 유지하면서,
공백 포함 {target}자 ±5% 분량이 되도록 자연스럽게 다듬어 주세요.

조건:
- 내용 삭제 금지(의미 축약으로 줄이지 말고 표현만 압축/확장)
- 문단 개수는 그대로 유지
- 문장의 자연스러움을 최우선
{keyword_rule}
"""

    return [
        {"role": "system", "content": system_prompt.strip()},
        {"role": "user", "content": text},
    ]


def build_cafe_length_fix_prompt(text, target, keyword, count):
    keyword_rule = ""
    if keyword:
        keyword_rule = (
            f'- 키워드 "{keyword}"가 최소 {count}회 이상 포함되도록 유지하세요.\n'
        )

    system_prompt = f"""
아래 네이버 카페 후기 글을 공백 포함 {target}자 ±5% 분량이 되도록
자연스럽게 압축하거나 약간만 확장해 주세요.

조건:
- 후기 느낌과 말투는 그대로 유지
- 핵심 내용 삭제 금지
{keyword_rule}
"""

    return [
        {"role": "system", "content": system_prompt.strip()},
        {"role": "user", "content": text},
    ]


# ---------------------------------------------------------
# 모델 선택
# ---------------------------------------------------------
def select_model():
    candidates = ["gpt-4.1-mini", "gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"]
    for m in candidates:
        try:
            openai.Model.retrieve(m)
            print("[DEBUG] Using model:", m)
            return m
        except Exception:
            continue
    print("[WARN] 모든 우선순위 모델 조회 실패, gpt-3.5-turbo 사용")
    return "gpt-3.5-turbo"


# ---------------------------------------------------------
# 메인 API 엔드포인트
# ---------------------------------------------------------
@app.route("/api/summary_advanced", methods=["POST"])
def summary_advanced():
    try:
        data = request.get_json(force=True)
        print("[DEBUG] Received data:", data)

        url_raw = (data.get("url") or "").strip()
        if not url_raw:
            print("[ERROR] Missing URL")
            return jsonify({"error": "URL is required"}), 400

        base_length = int(data.get("length", 1200))
        tone = (data.get("tone") or "구어체").strip() or "구어체"
        style = (data.get("style") or "기본체").strip() or "기본체"
        keyword = (data.get("keyword") or "").strip()
        count = int(data.get("count", 1))
        extra = (data.get("extra") or "").strip()

        url = normalize_url(url_raw)
        model = select_model()

        print("[DEBUG] Normalized URL:", url)

        # 1) 본문 추출
        raw_text = extract_text_from_url(url)
        print("[DEBUG] Extracted text length:", len(raw_text))

        # 2) 섹션 분석
        sections = analyze_sections(raw_text, model)
        print("[DEBUG] Sections extracted.")

        # 3) 블로그 버전 생성
        blog_target = base_length  # 요청 길이 그대로 사용
        blog_prompt = build_blog_prompt(
            sections=sections,
            tone=tone,
            style=style,
            length=blog_target,
            keyword=keyword,
            count=count,
            extra=extra,
        )

        blog_resp = openai.ChatCompletion.create(
            model=model,
            messages=blog_prompt,
            temperature=0.7,
            max_tokens=int(blog_target * 3),
        )
        blog_version = blog_resp.choices[0].message.content.strip()
        blog_len = measure_length(blog_version)
        print(f"[DEBUG] Blog length initial: {blog_len}")

        # 블로그 길이 보정 (±5% 초과 시)
        if blog_target > 0 and abs(blog_len - blog_target) > blog_target * 0.05:
            fix_prompt = build_blog_length_fix_prompt(
                blog_version, blog_target, keyword, count
            )
            fix_resp = openai.ChatCompletion.create(
                model=model,
                messages=fix_prompt,
                temperature=0.3,
                max_tokens=int(blog_target * 3),
            )
            blog_version = fix_resp.choices[0].message.content.strip()
            print(
                f"[DEBUG] Blog length after fix: {measure_length(blog_version)}"
            )

        # 4) 카페 버전 생성
        cafe_target = min(900, base_length) if base_length > 0 else 600
        cafe_prompt = build_cafe_prompt(
            sections=sections,
            tone=tone,
            style=style,
            length=cafe_target,
            keyword=keyword,
            count=count,
            extra=extra,
        )

        cafe_resp = openai.ChatCompletion.create(
            model=model,
            messages=cafe_prompt,
            temperature=0.7,
            max_tokens=int(cafe_target * 3),
        )
        cafe_version = cafe_resp.choices[0].message.content.strip()
        cafe_len = measure_length(cafe_version)
        print(f"[DEBUG] Cafe length initial: {cafe_len}")

        # 카페 길이 보정 (±5% 초과 시)
        if cafe_target > 0 and abs(cafe_len - cafe_target) > cafe_target * 0.05:
            fix_prompt = build_cafe_length_fix_prompt(
                cafe_version, cafe_target, keyword, count
            )
            fix_resp = openai.ChatCompletion.create(
                model=model,
                messages=fix_prompt,
                temperature=0.3,
                max_tokens=int(cafe_target * 3),
            )
            cafe_version = fix_resp.choices[0].message.content.strip()
            print(
                f"[DEBUG] Cafe length after fix: {measure_length(cafe_version)}"
            )

        # 5) 최종 반환
        return jsonify(
            {
                "blog_version": blog_version,
                "cafe_version": cafe_version,
                "tone_used": tone,
                "style_used": style,
                "model_used": model,
            }
        )

    except Exception as e:
        print("[CRITICAL ERROR]", str(e))
        return jsonify({"error": "critical_failure", "detail": str(e)}), 500


# ---------------------------------------------------------
# 실행
# ---------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
