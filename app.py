import os
HEAD
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



import math
import openai
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify

app = Flask(__name__)

# Set OpenAI API key from environment (ensure OPENAI_API_KEY is set in env variables)
openai.api_key = os.environ.get("OPENAI_API_KEY")
if not openai.api_key:
    raise RuntimeError("OpenAI API key not found in environment variables.")

# Define a helper function to extract main text content from a URL
def extract_text_from_url(url: str) -> str:
    """Fetches the webpage at `url` and returns its text content (with HTML tags removed)."""
    try:
        res = requests.get(url, timeout=10)
        res.raise_for_status()
    except Exception as e:
        # If any HTTP/network error occurs, propagate exception to be handled by caller
        raise

    soup = BeautifulSoup(res.text, 'html.parser')
    # Remove script and style elements
    for elem in soup(["script", "style"]):
        elem.decompose()
    # Get text and collapse whitespace
    text = soup.get_text(separator=" ").strip()
    text = " ".join(text.split())  # collapse consecutive whitespace and newlines
    return text

@app.route("/api/summary_advanced", methods=["POST"])
def summary_advanced():
    """API endpoint to summarize blog content with a casual tone/style."""
    data = request.get_json(force=True)  # get JSON payload from POST

    url = data.get("url", "").strip()
    length_input = data.get("length")      # expected to be an integer (character count)
    keyword = data.get("keyword", "").strip()
    count = data.get("count", 1)
    extra = data.get("extra", "").strip()
    version_count_input = data.get("version_count")  # if provided by client (Apps Script)

    # Validate required fields
    if not url:
        return jsonify({"error": "URL is required"}), 400
    try:
        count = int(count)
    except:
        count = 1
    if count < 1:
        count = 1

    # If multiple posts are requested from one URL, split the URL string if it contains multiple URLs
    urls = [url]
    if count > 1:
        # If the URL field contains multiple URLs separated by newline or comma, split them
        import re
        parts = re.split(r'[\n,]+', url)
        urls = [u.strip() for u in parts if u.strip()]
        # Adjust count if the number of URLs found differs
        if len(urls) < count:
            count = len(urls)
        if len(urls) > count:
            urls = urls[:count]

    summary_results = []  # will store summary text(s) for each post

    # Determine the best available OpenAI model in priority order
    model_priority = ["gpt-4.1-mini", "gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"]
    selected_model = None
    for model_name in model_priority:
        try:
            openai.Model.retrieve(model_name)
            selected_model = model_name
            break
        except Exception as e:
            # If model not available or any error, try next
            continue
    if not selected_model:
        selected_model = "gpt-3.5-turbo"  # fallback to GPT-3.5 if none of the above are accessible

    # Loop through each URL (each post) to summarize
    for idx, post_url in enumerate(urls):
        # Extract text content from the URL
        try:
            original_text = extract_text_from_url(post_url)
        except Exception as e:
            # If extraction fails, append an error message for this post and continue
            summary_results.append(f"(오류) URL에서 내용을 가져오지 못했습니다: {post_url}")
            continue

        # Determine character length of the original text (including spaces)
        original_length = len(original_text)

        # Decide target summary length and number of versions (summary variants) for this post
        if count > 1:
            # If multiple posts are being processed in one request, ignore client-provided length/version and calculate per post
            base_length = original_length
            target_length = math.ceil(base_length * 1.25)  # original length + 25%
            versions = 2 if base_length <= 800 else 1
        elif version_count_input is not None:
            # If client provided version_count, assume `length_input` is already the target length (original + 25%)
            try:
                target_length = int(length_input)
            except:
                target_length = math.ceil(original_length * 1.25)
            try:
                versions = int(version_count_input)
            except:
                versions = 2 if original_length <= 800 else 1
        else:
            # If no version_count provided, calculate based on original length
            try:
                base_length = int(length_input) if length_input is not None else original_length
            except:
                base_length = original_length
            target_length = math.ceil(base_length * 1.25)
            versions = 2 if base_length <= 800 else 1

        # Construct the prompt for the OpenAI model (in Korean, with style guidelines)
        style_guidelines = (
            f"글자수는 공백 포함 약 {target_length}자 내외로 해주세요.\n"
            f"문체는 블로그 원문 기반\n"
            f"문장은 블로그와 유사하게 구성\n"
            f"자음/모음(ㅎㅎ, ㅠㅠ, ㅋㅋ) 자연스럽게 사용\n"
            f"편한 언니에게 소개하듯 자연스럽게 작성\n"
            f"네이버 카페 자유게시판/수다게시판 스타일\n"
        )
        # If a keyword is provided, instruct the model to include it
        extra_instruction = ""
        if keyword:
            extra_instruction += f"\n요약문에 반드시 '{keyword}'를 포함해주세요."
        if extra:
            extra_instruction += f"\n추가 요청: {extra}"

        prompt = f"{original_text}\n\n위 글을 요약해줘.\n{style_guidelines}{extra_instruction}".strip()

        # Call the OpenAI ChatCompletion API to get the summary(s)
        try:
            response = openai.ChatCompletion.create(
                model=selected_model,
                messages=[{"role": "user", "content": prompt}],
                n=versions,           # number of summary versions to generate
                temperature=0.7,      # moderate creativity for a more natural tone
                max_tokens=int(target_length * 1.2)  # limit to roughly 120% of target_length in tokens
            )
        except Exception as e:
            # If the API call fails for this post, record an error message and continue
            summary_results.append(f"(오류) 요약 생성 실패: {post_url} - {str(e)}")
            continue

        # Extract the generated summary text(s) from the API response
        for choice in response.choices:
            summary_text = choice.message.content.strip()
            summary_results.append(summary_text)

    # Return the summaries as a JSON object with a list
    return jsonify({"summary_list": summary_results})

# If running this app directly (for example, in development), start the Flask server
"6a3e011" (initial commit)
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
