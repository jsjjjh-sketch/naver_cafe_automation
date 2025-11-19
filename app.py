import os
import math
import re
import openai
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify

app = Flask(__name__)
openai.api_key = os.getenv("OPENAI_API_KEY")

if not openai.api_key:
    raise RuntimeError("OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")

def normalize_url(url: str) -> str:
    return url.replace("https://blog.naver.com", "https://m.blog.naver.com")

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
        - 자연스러운 구어체 (사람에게 설명하듯이)
        - 홍보티 안 나게
        - 중복표현 사용불가
        - 문체는 카페 후기 느낌
        - 문단마다 표현 다르게
        - 과한 이모티콘은 2~3개 이내
        - ㅎㅎ,ㅠㅠ,ㅋㅋ 등의 감정표현 문자 사용
        - 문장 끝에는 '~했어요' 식 표현 사용
        - 공백 포함 {length}자 내외로 작성
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

작성 부탁드립니다."
        }
    ]

def extract_text_from_url(url):
    try:
        res = requests.get(url, timeout=10)
        res.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"크롤링 실패: {e}")

    soup = BeautifulSoup(res.text, 'html.parser')
    for tag in soup(["script", "style"]):
        tag.decompose()

    text = soup.get_text(separator=" ").strip()
    return re.sub(r'\s+', ' ', text)

def select_model():
    priority = ["gpt-4.1-mini", "gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"]
    for m in priority:
        try:
            openai.Model.retrieve(m)
            return m
        except:
            continue
    return "gpt-3.5-turbo"

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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
