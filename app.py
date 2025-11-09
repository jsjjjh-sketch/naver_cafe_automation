from flask import Flask, request, jsonify
from crawler import extract_text_from_url
from openai import OpenAI
import os

app = Flask(__name__)

# ✅ 환경 변수에서 OpenAI API 키 로드
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

@app.route("/")
def home():
    return "Server is running."

# ✅ 고급 자동 요약 API (Apps Script 전용)
@app.route("/api/summary_advanced", methods=["POST"])
def summary_advanced():
    try:
        data = request.get_json()

        url = data.get("url", "")
        length = int(data.get("length", 300))
        keyword = data.get("keyword", "")
        count = int(data.get("count", 0))
        extra = data.get("extra", "")

        if not url:
            return jsonify({"error": "URL 누락"}), 400

        # ✅ URL에서 본문 추출
        blog_text = extract_text_from_url(url)
        if not blog_text or len(blog_text.strip()) < 50:
            return jsonify({"error": "본문 추출 실패"}), 400

        # ✅ GPT 프롬프트 구성
        prompt = f"""
다음 블로그 글을 네이버 카페용 구어체로 요약해줘.
조건:
- 글자 수 약 {length}자
- {'키워드 "' + keyword + '"를 ' + str(count) + '회 이상 자연스럽게 포함해줘.' if keyword else ''}
- 추가로 "{extra}" 내용을 문맥상 자연스럽게 포함
- 핵심 내용만 남기고 간결하게 작성
- ㅎㅎ, ㅠㅠ,ㅋㅋ 와 같은 자음/모음이 있는 경우 요약에 반영
- 출력은 한글로만, 문체는 부드럽고 자연스럽게

본문:
{blog_text}
        """

        # ✅ GPT 요청
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )

        summary = completion.choices[0].message.content.strip()

        # ✅ 응답 구성 (Google Sheet용)
        return jsonify({
            "summary_versions": summary
        })

    except Exception as e:
        return jsonify({"error": f"gpt_fail: {str(e)}"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
