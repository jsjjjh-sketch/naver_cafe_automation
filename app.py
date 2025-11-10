import os
from flask import Flask, request, jsonify
from datetime import datetime
from openai import OpenAI
from crawler import fetch_and_clean

app = Flask(__name__)

# --- OpenAI 클라이언트 초기화 ---
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# --- 동적 모델 탐색 ---
def get_available_model():
    try:
        available = [m.id for m in client.models.list().data]
        for candidate in ["gpt-4.1-mini", "gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"]:
            if candidate in available:
                return candidate
        return "gpt-4o-mini"
    except Exception:
        return "gpt-4o-mini"

# --- 외부 규칙 불러오기 ---
def load_prompt_rules(length, keyword_text, extra_text):
    try:
        with open("prompt_rules.txt", "r", encoding="utf-8") as f:
            rules = f.read()
        return rules.format(length=length, keyword_text=keyword_text, extra_text=extra_text)
    except Exception as e:
        print(f"[WARN] prompt_rules.txt 불러오기 실패: {e}")
        return f"""
        조건:
        - 자연스러운 구어체
        - ㅎㅎ,ㅠㅠ,ㅋㅋ 와 같은 자음,모음 사용
        - 홍보티 안 나게
        - 리얼 후기 느낌
        - 공백 포함 {length}자 내외로 작성
        {keyword_text}
        {extra_text}
        """

@app.route("/api/summary", methods=["POST"])
def summarize():
    data = request.get_json()
    url = data.get("url")
    length = int(data.get("length", 300))
    keyword = data.get("keyword", "").strip()
    repeat = int(data.get("repeat", 1))
    extra = data.get("extra", "").strip()

    if not url:
        return jsonify({"error": "missing_url"}), 400

    # --- 본문 크롤링 ---
    try:
        raw_text = fetch_and_clean(url)
    except Exception as e:
        return jsonify({"error": f"crawl_fail: {str(e)}"}), 400

    # --- 키워드 조건 구성 ---
    keyword_text = ""
    if keyword:
        keyword_text = f"- '{keyword}' 단어를 자연스럽게 {repeat}회 이상 포함"

    # --- 프롬프트 구성 ---
    prompt = f"""
아래는 네이버 블로그 글의 내용이야.
이를 참고해서 네이버 카페용 게시글을 5개 버전 만들어줘.

{load_prompt_rules(length, keyword_text, extra)}

원문:
{raw_text}
"""

    # --- 모델 호출 ---
    model = get_available_model()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
            max_tokens=800,
        )
        summary_text = response.choices[0].message.content.strip()
    except Exception as e:
        return jsonify({"error": f"gpt_fail: {str(e)}"}), 500

    return jsonify({
        "summary": summary_text,
        "model_used": model,
        "timestamp": datetime.now().isoformat()
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
