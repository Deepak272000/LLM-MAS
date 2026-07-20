import requests
from app.config import settings


def classify_email_type(payload: dict) -> str:
    return payload.get("email_type", "order_confirmation")


def build_fallback_subject(email_type: str, order_id: str) -> str:
    if email_type == "order_confirmation":
        return f"Your order {order_id} has been confirmed"
    return f"Update for order {order_id}"


def build_fallback_body(payload: dict) -> str:
    user_name = payload.get("user_name", "Customer")
    order_id = payload.get("order_id", "")
    currency_code = payload.get("currency_code", "USD")
    total = payload.get("total", 0.0)
    items = payload.get("items", [])

    item_lines = []
    for item in items:
        item_lines.append(
            f"- {item.get('name')} x{item.get('quantity')} ({item.get('price')} {currency_code})"
        )

    items_text = "\n".join(item_lines) if item_lines else "- No items listed"

    return (
        f"Hello {user_name},\n\n"
        f"Thank you for your order.\n"
        f"Order ID: {order_id}\n"
        f"Items:\n{items_text}\n\n"
        f"Total: {total} {currency_code}\n\n"
        f"Your order has been received successfully.\n\n"
        f"Regards,\nEmail Service Agent"
    )


def generate_email_content(payload: dict) -> dict:
    email_type = classify_email_type(payload)

    fallback_subject = build_fallback_subject(email_type, payload["order_id"])
    fallback_body = build_fallback_body(payload)

    print("USE_LLM =", settings.USE_LLM)
    print("OLLAMA_BASE_URL =", settings.OLLAMA_BASE_URL)
    print("MODEL_NAME =", settings.MODEL_NAME)

    if not settings.USE_LLM:
     print("LLM disabled, using fallback")
     return {
        "email_type": email_type,
        "subject": fallback_subject,
        "body": fallback_body,
        "llm_used": False,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_llm_calls": 0,
        "total_tokens": 0,
    }
    prompt = f"""
You are an email assistant for an ecommerce system.
Generate a concise and professional customer email.

Email type: {email_type}
Customer: {payload.get("user_name", "Customer")}
Order ID: {payload.get("order_id")}
Items: {payload.get("items", [])}
Total: {payload.get("total", 0.0)} {payload.get("currency_code", "USD")}

Return exactly in this format:
SUBJECT: <subject>
BODY:
<body>
""".strip()

    try:
        print("Calling Ollama...")
        response = requests.post(
            f"{settings.OLLAMA_BASE_URL}/api/generate",
            json={
                "model": settings.MODEL_NAME,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": settings.TEMPERATURE},
            },
            timeout=60,
        )

        print("Ollama status code:", response.status_code)
        response.raise_for_status()

        raw_json = response.json()

        input_tokens = raw_json.get("prompt_eval_count", 0)
        output_tokens = raw_json.get("eval_count", 0)
        total_tokens = input_tokens + output_tokens

        print(f"TOKEN_METRICS input={input_tokens} output={output_tokens} total={total_tokens}")

        with open("token_log.txt", "a") as f:
            f.write(f"{total_tokens}\n")

        print("Ollama JSON keys:", raw_json.keys())

        text = raw_json.get("response", "").strip()
        print("Ollama response text:", text[:200])

        subject = fallback_subject
        body = fallback_body

        if "SUBJECT:" in text and "BODY:" in text:
            subject_part = text.split("SUBJECT:", 1)[1].split("BODY:", 1)[0].strip()
            body_part = text.split("BODY:", 1)[1].strip()

            if subject_part:
                subject = subject_part
            if body_part:
                body = body_part

        return {
            "email_type": email_type,
            "subject": subject,
            "body": body,
            "llm_used": True,
            "total_input_tokens": input_tokens,
            "total_output_tokens": output_tokens,
            "total_llm_calls": 1,
            "total_tokens": total_tokens,
        }

    except Exception as e:
        print("OLLAMA ERROR:", repr(e))
        return {
            "email_type": email_type,
            "subject": fallback_subject,
            "body": fallback_body,
            "llm_used": False,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_llm_calls": 0,
            "total_tokens": 0,
        }