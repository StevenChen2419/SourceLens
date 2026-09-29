"""Manual live endpoint check; makes real embedding, Search, and GPT requests."""

import json

import httpx

QUESTIONS = [
    ("How many vacation days do full-time employees receive?", "supported"),
    ("How many days each week am I allowed to do my job from home?", "supported"),
    ("What is the capital of Japan?", "insufficient_evidence"),
]


def main() -> int:
    failed = False
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=240.0) as client:
        for question, expected in QUESTIONS:
            try:
                response = client.post("/api/answers", json={"question": question})
                response.raise_for_status()
                body = response.json()
            except (httpx.HTTPError, ValueError):
                print(f"Request failed for: {question}. Check the server and Azure configuration.")
                failed = True
                continue
            print(json.dumps(body, indent=2, ensure_ascii=False))
            valid = body.get("status") == expected
            if expected == "supported":
                valid = valid and bool(body.get("citations"))
            else:
                from app.services.answers import INSUFFICIENT_EVIDENCE
                valid = valid and body.get("answer") == INSUFFICIENT_EVIDENCE and body.get("citations") == []
            if not valid:
                print(f"FAILED expected behavior: {expected}")
                failed = True
    print("Manually compare the first two answers and cited pages with your PDF; status checks do not establish correctness.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
