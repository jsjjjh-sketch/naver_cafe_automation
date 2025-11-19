import os
import math
import openai
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify
from datetime import datetime
from crawler import fetch_and_clean
from openai import OpenAI

app = Flask(__name__)

# --- OpenAI 클라이언트 초기화 ---
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
openai.api_key = os.environ.get("OPENAI_API_KEY")
if not openai.api_key:
    raise RuntimeError("OpenAI API key not found in environment variables.")

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

# --- URL 본문 추출 ---
def extract_text_from_url(url: str) -> str:
    try:
        res = requests.get(url, timeout=10)
        res.raise_for_status()
    except Exception:
        raise

    soup = BeautifulSoup(res.text, 'html.parser')
    for elem in soup(["script", "style"]):
        elem.decompose()
    text = soup.get_text(separator=" ").strip()
    return " ".join(text.split())

@app.route("/")
def home():
    return "Server is running."

@app.route("/api/summary_advanced", methods=["POST"])
def summary_advanced():
    try:
        data = request.get_json(force=True)

        url = data.get("url", "").strip()
        length_input = data.get("length", 300)
        keyword = data.get("keyword", "").strip()
        count = int(data.get("count", 1))
        extra = data.get("extra", "").strip()
        version_count_input = data.get("version_count")

        if not url:
            return jsonify({"error": "URL 누락"}), 400

        urls = [url]
        if count > 1:
            import re
            parts = re.split(r'[\n,]+', url)
            urls = [u.strip() for u in parts if u.strip()]
            if len(urls) < count:
                count = len(urls)
            if len(urls) > count:
                urls = urls[:count]

        summary_results = []
        model_priority = ["gpt-4.1-mini", "gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"]
        selected_model = None
        for model_name in model_priority:
            try:
                openai.Model.retrieve(model_name)
                selected_model = model_name
                break
            except:
                continue
        if not selected_model:
            selected_model = "gpt-3.5-turbo"

        for idx, post_url in enumerate(urls):
            try:
                original_text = fetch_and_clean(post_url)
            except:
                summary_results.append(f"(오류) URL에서 내용을 가져오지 못했습니다: {post_url}")
                continue

            original_length = len(original_text)

            if count > 1:
                base_length = original_length
                target_length = math.ceil(base_length * 1.25)
                versions = 2 if base_length <= 800 else 1
            elif version_count_input is not None:
                try:
                    target_length = int(length_input)
                except:
                    target_length = math.ceil(original_length * 1.25)
                try:
                    versions = int(version_count_input)
                except:
                    versions = 2 if original_length <= 800 else 1
            else:
                try:
                    base_length = int(length_input) if length_input is not None else original_length
                except:
                    base_length = original_length
                target_length = math.ceil(base_length * 1.25)
                versions = 2 if base_length <= 800 else 1

            keyword_text = f"- '{keyword}' 단어를 자연스럽게 {count}회 이상 포함" if keyword else ""
            extra_instruction = ""
            if keyword:
                extra_instruction += f"\n요약문에 반드시 '{keyword}'를 포함해주세요."
            if extra:
                extra_instruction += f"\n추가 요청: {extra}"

            prompt = f"""
아래는 네이버 블로그 글의 내용이야.
이걸 참고해서 네이버 카페용 게시글 5개 버전으로 만들어줘.

{load_prompt_rules(target_length, keyword_text, extra)}

원문:
{original_text}
            """.strip()

            try:
                response = client.chat.completions.create(
                    model=selected_model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.8,
                    max_tokens=int(target_length * 1.2),
                    n=versions,
                )
            except Exception as e:
                summary_results.append(f"(오류) 요약 생성 실패: {post_url} - {str(e)}")
                continue

            for choice in response.choices:
                summary_results.append(choice.message.content.strip())

        return jsonify({
            "summary_list": summary_results,
            "model_used": selected_model,
            "timestamp": datetime.now().isoformat()
        })

    except Exception as e:
        return jsonify({"error": f"gpt_fail: {str(e)}"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)