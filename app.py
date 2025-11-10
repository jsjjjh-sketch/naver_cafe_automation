import os
from flask import Flask, request, jsonify
from datetime import datetime
from openai import OpenAI
from crawler import fetch_and_clean

app = Flask(__name__)

# --- OpenAI 클라이언트 초기화 ---
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# --- 모델 자동 탐색 ---
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
        - 크롤링한 링크의 원문 말투를 최대한 정확하게 반영
        - ㅎㅎ, ㅋㅋ,ㅠㅠ 와 같은 자음, 모음을 사용한 경우 반영
        - 공백 포함 {length}자 내외로 작성
        {keyword_text}
        {extra_text}
        """


@app.route("/")
def home():
    return "Server is running."


# --- 고급 요약 엔드포인트 ---
@app.route("/api/summary_advanced", methods=["POST"])
def summary_advanced():
    try:
        data = request.get_json()

        url = data.get("url", "").strip()
        length = int(data.get("length", 300))
        keyword = data.get("keyword", "").strip()
        count = int(data.get("count", 1))
        extra = data.get("extra", "").strip()

        if not url:
            return jsonify({"error": "URL 누락"}), 400

        # --- 본문 크롤링 ---
        try:
            raw_text = fetch_and_clean(url)
        except Exception as e:
            return jsonify({"error": f"crawl_fail: {str(e)}"}), 400

        if not raw_text or len(raw_text.strip()) < 50:
            return jsonify({"error": "본문 추출 실패"}), 400

        # --- 키워드 조건 ---
        keyword_text = ""
        if keyword:
            keyword_text = f"- '{keyword}' 단어를 자연스럽게 {count}회 이상 포함"

        # --- 프롬프트 구성 ---
        prompt = f"""
아래는 네이버 블로그 글의 내용이야.
이걸 참고해서 네이버 카페용 게시글 5개 버전으로 만들어줘.

{load_prompt_rules(length, keyword_text, extra)}

원문:
{raw_text}
        """

        # --- 모델 자동 선택 ---
        model = get_available_model()

        # --- GPT 호출 ---
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
            max_tokens=1000,
        )

        summary = response.choices[0].message.content.strip()

        return jsonify({
            "summary_versions": summary,
            "model_used": model,
            "timestamp": datetime.now().isoformat()
        })

    except Exception as e:
        return jsonify({"error": f"gpt_fail: {str(e)}"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
