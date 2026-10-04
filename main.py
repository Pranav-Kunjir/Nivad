import os
import csv 
import json
import base64
import mimetypes
from groq import Groq
from prompt import build_prompt 
import time


#setting
MODEL = "qwen/qwen3.8-27b"
FORM_SCORE_FIELDS = ("contribution", "warmth", "fit", "network", "effort")

def instagram_profile_url(profile):
    """Normalize an Instagram URL or username to a profile URL."""
    from urllib.parse import urlparse

    profile = profile.strip()
    if not profile:
        raise ValueError("Provide an Instagram username or profile URL.")

    if profile.startswith(("http://", "https://")):
        candidate = profile
    elif profile.startswith(("instagram.com/", "www.instagram.com/")):
        candidate = f"https://{profile}"
    else:
        username = profile.lstrip("@").strip("/")
        if not username or "/" in username:
            raise ValueError("Provide one Instagram username or profile URL.")
        candidate = f"https://www.instagram.com/{username}/"

    parsed_profile = urlparse(candidate)
    if parsed_profile.hostname not in {"instagram.com", "www.instagram.com"}:
        raise ValueError("The profile URL must belong to instagram.com.")
    if not parsed_profile.path.strip("/"):
        raise ValueError("The profile URL must include an Instagram username.")
    return candidate

def capture_instagram_screenshot(
    profile,
    screenshot_path=None,
    session_path=None,
):
    """Open an Instagram profile, save login state, and return its screenshot path."""
    from pathlib import Path
    from playwright.sync_api import sync_playwright

    profile_url = instagram_profile_url(profile)
    username = profile_url.rstrip("/").split("/")[-1]

    session_file = (
        Path(session_path).expanduser()
        if session_path
        else Path(__file__).resolve().parent / ".instagram_session.json"
    )
    screenshot_file = Path(screenshot_path).expanduser() if screenshot_path else Path(
        f"instagram_{username}.png"
    )
    session_file.parent.mkdir(parents=True, exist_ok=True)
    screenshot_file.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context_options = {"storage_state": str(session_file)} if session_file.exists() else {}
        context = browser.new_context(**context_options)
        page = context.new_page()
        try:
            page.goto(profile_url, wait_until="domcontentloaded")
            needs_login = (
                not session_file.exists()
                or "/accounts/login" in page.url
                or page.locator('input[name="username"]').count() > 0
            )
            if needs_login:
                if session_file.exists():
                    page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
                print("Log in to Instagram in the opened browser, then return here and press Enter.")
                input()
                context.storage_state(path=str(session_file))
                session_file.chmod(0o600)
                page.goto(profile_url, wait_until="domcontentloaded")

            page.locator("body").wait_for()
            time.sleep(4)
            page.screenshot(path=str(screenshot_file), full_page=True)
            context.storage_state(path=str(session_file))
            session_file.chmod(0o600)
            return screenshot_file.resolve()
        finally:
            context.close()
            browser.close()

def model_response(prompt, model, image_path=None):
    client = Groq(
        api_key=os.environ.get("GROQ_API_KEY"),
    )

    if image_path:
        image_file = os.fspath(image_path)
        mime_type = mimetypes.guess_type(image_file)[0] or "image/png"
        with open(image_file, "rb") as image:
            encoded_image = base64.b64encode(image.read()).decode("ascii")
        content = [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{encoded_image}"},
            },
        ]
    else:
        content = prompt

    response = client.chat.completions.create(
        messages=[
            {
                "role": "user",
                "content": content,
            }
        ],
        model=model,
    )
    return response

def parse_evaluation(response_text):
    """Parse a JSON object from the model response, including fenced output."""
    response_text = (response_text or "").strip()
    if response_text.startswith("```"):
        response_text = response_text.split("\n", 1)[-1]
        if response_text.endswith("```"):
            response_text = response_text[:-3].strip()

    try:
        result = json.loads(response_text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        start = response_text.find("{")
        if start < 0:
            return None
        try:
            result, _ = decoder.raw_decode(response_text[start:])
        except json.JSONDecodeError:
            return None
    return result if isinstance(result, dict) else None


def calculate_overall_score(evaluation):
    """Return a 0-100 score from the five form rubric scores, or None if incomplete."""
    if not evaluation:
        return None
    scores = [evaluation.get(field) for field in FORM_SCORE_FIELDS]
    if any(
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not 1 <= score <= 5
        for score in scores
    ):
        return None
    average = sum(scores) / len(scores)
    return round((average - 1) * 25, 2)


def save_results_json(results, output_path):
    """Write accumulated applicant data safely to a JSON file."""
    from pathlib import Path

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = output_file.with_suffix(output_file.suffix + ".tmp")
    temporary_file.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n")
    temporary_file.replace(output_file)


def evaluate_responses(filename, model, json_output="evaluation_results.json"):
    results = []
    with open(filename, newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        for row in reader:
            profile = (
                row.get("instagram_url", "").strip()
                or row.get("Instagram (username only, no @)", "").strip()
            )
            profile_url = ""
            screenshot_path = None
            if profile:
                try:
                    profile_url = instagram_profile_url(profile)
                    screenshot_path = capture_instagram_screenshot(profile_url)
                except Exception as error:
                    print(f"Could not capture Instagram for {row.get('Full name', '')}: {error}")

            prompt = build_prompt(
                name=row.get("Full name", ""),
                instagram=profile_url,
                doing=row.get("What do you do, and what are you working on right now?", ""),
                built=row.get("What's something you built, organized, or helped someone with recently?", ""),
                bring=row.get("What would you bring to the room?", ""),
                share=row.get("Would you share the event if you enjoyed it?", ""),
            )
            if not screenshot_path:
                prompt += "\nNo profile screenshot was attached for this applicant."

            response = model_response(prompt, model, screenshot_path)
            raw_response = response.choices[0].message.content or ""
            evaluation = parse_evaluation(raw_response)
            result = {
                "applicant": row,
                "profile_url": profile_url,
                "screenshot_path": str(screenshot_path) if screenshot_path else "",
                "evaluation": evaluation,
                "overall_score": calculate_overall_score(evaluation),
                "raw_response": raw_response,
            }
            results.append(result)
            save_results_json(results, json_output)
            print(row.get("Full name", ""))
            print(raw_response)

    return json_output


def write_ranked_results_csv(json_input="evaluation_results.json", csv_output="evaluation_results.csv"):
    """Export saved JSON results to a ranked, spreadsheet-friendly CSV."""
    from pathlib import Path

    with open(json_input, encoding="utf-8") as file:
        results = json.load(file)

    ranked_results = sorted(
        results,
        key=lambda result: (
            result.get("overall_score") is None,
            -(result.get("overall_score") or 0),
        ),
    )
    csv_file = Path(csv_output)
    csv_file.parent.mkdir(parents=True, exist_ok=True)
    fixed_fields = [
        "rank", "overall_score", "profile_url", "screenshot_path",
        *FORM_SCORE_FIELDS, "profile_signal", "authentic", "activity", "vibe",
        "consistency", "recommendation", "flags", "borderline", "reason",
        "evaluation_json", "raw_response",
    ]
    applicant_fields = list(results[0].get("applicant", {}).keys()) if results else []
    fieldnames = list(dict.fromkeys([*fixed_fields, *applicant_fields]))

    with csv_file.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for rank, result in enumerate(ranked_results, start=1):
            evaluation = result.get("evaluation") or {}
            profile_read = evaluation.get("profile_read") or {}
            output_row = {
                "rank": rank,
                "overall_score": result.get("overall_score"),
                "profile_url": result.get("profile_url", ""),
                "screenshot_path": result.get("screenshot_path", ""),
                "profile_signal": evaluation.get("profile_signal"),
                "authentic": profile_read.get("authentic", ""),
                "activity": profile_read.get("activity", ""),
                "vibe": profile_read.get("vibe", ""),
                "consistency": profile_read.get("consistency", ""),
                "recommendation": evaluation.get("recommendation", ""),
                "flags": json.dumps(evaluation.get("flags", []), ensure_ascii=False),
                "borderline": evaluation.get("borderline", ""),
                "reason": evaluation.get("reason", ""),
                "evaluation_json": json.dumps(evaluation, ensure_ascii=False),
                "raw_response": result.get("raw_response", ""),
                **result.get("applicant", {}),
            }
            output_row.update({field: evaluation.get(field, "") for field in FORM_SCORE_FIELDS})
            writer.writerow(output_row)

    return csv_file.resolve()

if __name__ == "__main__":
    results_json = evaluate_responses("./test.csv", MODEL)
    results_csv = write_ranked_results_csv(results_json)
    print(f"Saved JSON results to {results_json}")
    print(f"Saved ranked CSV results to {results_csv}")
