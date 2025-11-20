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
        return "blog.naver.com" in host
    except Exception:
        return False


# ---------------------------------------------------------
# 3) 네이버 블로그 본문 전용 파서
# ---------------------------------------------------------
def extract_naver_blog_text(html: str) -> str:
    soup = BeautifulSoup(html, 'html.parser')

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
# 5) URL에서 본문 내용 추출
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
# 6) 기존 단일 요약 프롬프트 (fallback 용)
# ---------------------------------------------------------
def build_simple_prompt(text, length, keyword, count, version_count):
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
# 7) JSON 파싱 유틸
# ---------------------------------------------------------
def parse_json_safe(text: str):
    """모델 출력에서 JSON 부분만 안전하게 추출"""
    try:
        # ```json ... ``` 형태 처리
        if "```" in text:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                return json.loads(text[start:end+1])
        return json.loads(text)
    except Exception:
        # 실패 시 None
        return None


# ---------------------------------------------------------
# 8) 자동 문단 구조 엔진 - 섹션 분석
# ---------------------------------------------------------
def analyze_sections(text: str, model: str):
    """
    원문 리뷰를 7개 의미 단위로 분류:
    intro / store_info / atmosphere / menu_intro / taste_review / strengths / conclusion
    """
    system_prompt = """
당신의 역할은 음식점 방문 리뷰를 구조화하는 편집자입니다.

아래 원문에서 내용을 다음 7개 범주로 분류해 주세요.

1. 도입부 (방문 계기, 누구와, 언제, 첫 인상 등)
2. 매장 기본 정보 (상호명, 주소, 영업시간, 연락처, 주차 등)
3. 공간/분위기 묘사 (인테리어, 좌석, 조명, 음악, 분위기, 청결 등)
4. 주문 메뉴 소개 (주문한 메뉴, 선택 이유, 대표 메뉴 등)
5. 맛/식감/향 표현 (첫 맛, 식감, 향, 양, 포만감, 전반적인 맛 평가 등)
6. 매장 장점 정리 (서비스, 친절함, 가격, 구성, 재방문 의사 등)
7. 총평 (전체적인 감상, 다시 가고 싶은지, 자연스러운 마무리 멘트)

각 범주에 해당하는 문장들을 모아서 다음 JSON 형식으로만 출력하세요.

{
  "intro": "",
  "store_info": "",
  "atmosphere": "",
  "menu_intro": "",
  "taste_review": "",
  "strengths": "",
  "conclusion": ""
}

JSON 이외의 설명, 문장, 접두사/접미사는 출력하지 마세요.
"""
    messages = [
        {"role": "system", "content": system_prompt.strip()},
        {
            "role": "user",
            "content": f"다음은 음식점 방문 블로그 리뷰 원문입니다. 위 지침에 따라 내용을 7개 범주로 분류해서 JSON만 출력해 주세요.\n\n'''{text}'''"
        }
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
        raise RuntimeError("섹션 분석 JSON 파싱 실패")
    # 키 누락 방지
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
# 9) 자동 문단 구조 + 톤/스타일 재작성 프롬프트
# ---------------------------------------------------------
def build_structured_rewrite_prompt(
    sections: dict,
    length: int,
    tone: str,
    style: str,
    keyword: str,
    count: int,
    extra: str,
    version_count: int
):
    """
    섹션 JSON + 톤/스타일 정보를 이용해
    광고대행사 표준 리뷰 스타일로 문단 구조를 갖춘 글을 생성하는 프롬프트
    """
    tone_text = tone or "구어체"
    style_text = style or "기본체"

    keyword_rule = ""
    if keyword:
        keyword_rule = f"- '{keyword}' 단어를 자연스럽게 {count}회 이상 포함하세요."

    extra_rule = ""
    if extra:
        extra_rule = f"- 추가 스타일/컨셉: {extra}"

    system_prompt = f"""
당신의 역할은 음식점 리뷰 전문 카피라이터입니다.

목표:
- 원문에서 추출된 내용을 바탕으로, 광고 티가 과하지 않으면서도 읽기 좋은 리뷰를 작성합니다.
- 글은 자동 생성이지만, 사람이 직접 쓴 후기처럼 자연스러워야 합니다.

기본 문체 기준:
- 기본 말투: 부드러운 해요체 기반 구어체
- 문장은 12~22자 정도의 자연스러운 길이
- 과장된 표현(최고, 끝판왕, 존맛, 대박 등) 사용 금지
- 의미 없는 과한 감탄사 연속 사용 금지
- 후기 느낌 + 정보성을 함께 전달

말투(Tone):
- 현재 선택된 톤: {tone_text}
- 이 톤에 맞게 어미, 단어 선택, 문장 분위기를 조정하세요.

스타일(Style):
- 현재 선택된 스타일: {style_text}
- 스타일에 맞게 서사/묘사/간결/화려함 등의 비율을 조절하세요.

글 전체 구성:
1. 도입부
2. 매장 기본 정보 요약
3. 공간/분위기 묘사
4. 주문 메뉴 소개
5. 맛/식감/향 표현 (핵심 문단)
6. 매장 장점 정리
7. 총평/마무리

작성 규칙:
- 위 7개 문단 순서를 유지하세요.
- 각 문단은 2~4문장 정도로 구성하세요.
- 전체 분량은 공백 포함 약 {length}자 내외로 조정하세요.
- 광고 문구(꼭 가보세요, 강력 추천합니다 등)는 사용하지 마세요.
{keyword_rule}
{extra_rule}
"""

    user_prompt = f"""
아래는 원문에서 추출된 섹션별 내용입니다. 이 내용을 기반으로 위 지침에 따라 하나의 자연스러운 리뷰 글을 작성해 주세요.

[섹션 데이터(JSON)]:
{json.dumps(sections, ensure_ascii=False, indent=2)}

요청:
- 7개 문단 구조를 유지하면서, 하나의 글처럼 자연스럽게 이어지도록 작성해 주세요.
- 출력은 리뷰 본문 텍스트만 작성해 주세요. (제목, 해시태그, 설명 문구는 포함하지 마세요.)
- 총 {version_count}개의 서로 다른 버전이 필요합니다. (동일 정보 기반, 표현과 흐름만 다르게 작성)
"""

    return [
        {"role": "system", "content": system_prompt.strip()},
        {"role": "user", "content": user_prompt.strip()}
    ]


# ---------------------------------------------------------
# 10) 모델 선택
# ---------------------------------------------------------
def select_model():
    priority = ["gpt-4.1-mini", "gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"]
    for m in priority:
        try:
            openai.Model.retrieve(m)
            return m
        except Exception:
            continue
    return "gpt-3.5-turbo"


# ---------------------------------------------------------
# 11) 메인 API 엔드포인트
# ---------------------------------------------------------
@app.route("/api/summary_advanced", methods=["POST"])
def summary_advanced():
    try:
        data = request.get_json(force=True)
        url_raw = data.get("url", "").strip()
        if not url_raw:
            return jsonify({"error": "URL is required"}), 400

        length = int(data.get("length", 300))
        keyword = data.get("keyword", "").strip()
        count = int(data.get("count", 1))
        extra = data.get("extra", "").strip()
        version_count = int(data.get("version_count", 1))

        # 새 옵션 (없으면 기본값)
        tone = data.get("tone", "구어체").strip() or "구어체"
        style = data.get("style", "기본체").strip() or "기본체"

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

            try:
                # 1) 섹션 분석
                sections = analyze_sections(raw_text, model)

                # 2) 구조화 + 톤/스타일 적용 재작성
                prompt = build_structured_rewrite_prompt(
                    sections=sections,
                    length=length,
                    tone=tone,
                    style=style,
                    keyword=keyword,
                    count=count,
                    extra=extra,
                    version_count=version_count
                )

                response = openai.ChatCompletion.create(
                    model=model,
                    messages=prompt,
                    temperature=0.7,
                    n=version_count,
                    max_tokens=int(length * 2)
                )

                for choice in response.choices:
                    results.append(choice.message.content.strip())

            except Exception as e:
                # 문제가 생기면 기존 단일 요약 방식으로 fallback
                fallback_prompt = build_simple_prompt(
                    raw_text,
                    length,
                    keyword,
                    count,
                    version_count
                )
                try:
                    fallback_resp = openai.ChatCompletion.create(
                        model=model,
                        messages=fallback_prompt,
                        temperature=0.7,
                        n=version_count,
                        max_tokens=int(length * 1.5)
                    )
                    for choice in fallback_resp.choices:
                        results.append("(fallback)\n" + choice.message.content.strip())
                except Exception as e2:
                    results.append(f"(요약 실패) {u} - {str(e)} / fallback 실패: {str(e2)}")

        return jsonify({
            "summary_list": results,
            "model_used": model,
            "tone_used": tone,
            "style_used": style
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------
# 12) 서버 실행
# ---------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
