import os
import secrets
from email.header import decode_header
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from pydantic import BaseModel
from dotenv import load_dotenv

from agent import run_agent

load_dotenv()

app = FastAPI(title="مساعد أمير الشخصي")

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN")

REDIRECT_URI = (
    "https://amir-personal-ai-agent.onrender.com/auth/google/callback"
)

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

pending_states = set()
google_tokens = {}


class ChatRequest(BaseModel):
    message: str


@app.get("/")
def root():
    return {
        "name": "Amir Personal AI",
        "status": "online",
        "gmail_login": "/auth/google",
        "gmail_status": "/gmail/status",
        "gmail_messages": "/gmail/messages",
        "privacy": "/privacy",
        "terms": "/terms",
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/privacy", response_class=HTMLResponse)
def privacy():
    return """
    <!doctype html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>سياسة الخصوصية</title>
    </head>
    <body style="font-family:Arial;max-width:800px;margin:40px auto;padding:20px;line-height:1.8">
        <h1>سياسة الخصوصية</h1>
        <p>يستخدم التطبيق Google OAuth للوصول إلى Gmail بإذن المستخدم.</p>
        <p>الوصول إلى Gmail للقراءة فقط.</p>
        <p>لا يقوم التطبيق بإرسال أو حذف أو تعديل الرسائل.</p>
    </body>
    </html>
    """


@app.get("/terms", response_class=HTMLResponse)
def terms():
    return """
    <!doctype html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>شروط الاستخدام</title>
    </head>
    <body style="font-family:Arial;max-width:800px;margin:40px auto;padding:20px;line-height:1.8">
        <h1>شروط الاستخدام</h1>
        <p>هذا التطبيق مساعد شخصي للمستخدمين المصرح لهم.</p>
        <p>الوصول إلى Gmail للقراءة فقط.</p>
    </body>
    </html>
    """


def decode_mime_header(value: str) -> str:
    if not value:
        return ""

    try:
        parts = decode_header(value)
        result = ""

        for part, encoding in parts:
            if isinstance(part, bytes):
                result += part.decode(
                    encoding or "utf-8",
                    errors="replace",
                )
            else:
                result += part

        return result

    except Exception:
        return value


def repair_mojibake(text: str) -> str:
    if not text:
        return ""

    candidates = [text]

    for source_encoding in ["latin1", "cp1252"]:
        try:
            fixed = text.encode(
                source_encoding,
                errors="ignore"
            ).decode(
                "utf-8",
                errors="ignore"
            )

            if fixed:
                candidates.append(fixed)
        except Exception:
            pass

    def score(value: str) -> int:
        bad = (
            value.count("Ø")
            + value.count("Ù")
            + value.count("Ã")
            + value.count("â€")
            + value.count("�")
        )

        arabic = sum(
            1
            for ch in value
            if "\u0600" <= ch <= "\u06ff"
        )

        return arabic * 3 - bad * 5

    return max(candidates, key=score)


def clean_header(value: str) -> str:
    return repair_mojibake(
        decode_mime_header(value)
    )


def get_header(headers: list, name: str) -> str:
    for header in headers:
        if header.get("name", "").lower() == name.lower():
            return clean_header(
                header.get("value", "")
            )

    return ""


async def refresh_access_token() -> str:
    refresh_token = (
        google_tokens.get("refresh_token")
        or GOOGLE_REFRESH_TOKEN
    )

    if not refresh_token:
        raise HTTPException(
            status_code=401,
            detail="Connect Gmail first",
        )

    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(
            status_code=500,
            detail="Google OAuth is not configured",
        )

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )

    if response.status_code != 200:
        raise HTTPException(
            status_code=401,
            detail=response.text,
        )

    token_data = response.json()

    google_tokens["access_token"] = token_data.get(
        "access_token"
    )

    google_tokens["refresh_token"] = refresh_token

    return google_tokens["access_token"]


async def get_access_token() -> str:
    access_token = google_tokens.get("access_token")

    if access_token:
        return access_token

    return await refresh_access_token()


@app.get("/auth/google")
def google_login():
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(
            status_code=500,
            detail="Google OAuth is not configured",
        )

    state = secrets.token_urlsafe(32)
    pending_states.add(state)

    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": GMAIL_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }

    url = (
        "https://accounts.google.com/o/oauth2/v2/auth?"
        + urlencode(params)
    )

    return RedirectResponse(url)


@app.get("/auth/google/callback")
async def google_callback(code: str, state: str):
    if state not in pending_states:
        raise HTTPException(
            status_code=400,
            detail="Invalid OAuth state",
        )

    pending_states.remove(state)

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": REDIRECT_URI,
            },
        )

    if response.status_code != 200:
        raise HTTPException(
            status_code=400,
            detail=response.text,
        )

    token_data = response.json()
    google_tokens.update(token_data)

    return {
        "status": "connected",
        "message": "Gmail connected with read-only access",
        "refresh_token_received": bool(
            token_data.get("refresh_token")
        ),
    }


@app.get("/gmail/status")
async def gmail_status():
    try:
        await get_access_token()

        return {
            "connected": True,
            "persistent_refresh_token": bool(
                GOOGLE_REFRESH_TOKEN
            ),
        }

    except HTTPException:
        return {
            "connected": False,
            "persistent_refresh_token": bool(
                GOOGLE_REFRESH_TOKEN
            ),
        }


async def get_gmail_summaries(limit: int = 5):
    access_token = await get_access_token()

    limit = max(1, min(limit, 10))

    auth_headers = {
        "Authorization": f"Bearer {access_token}"
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        list_response = await client.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            headers=auth_headers,
            params={"maxResults": limit},
        )

        if list_response.status_code == 401:
            access_token = await refresh_access_token()

            auth_headers = {
                "Authorization": f"Bearer {access_token}"
            }

            list_response = await client.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                headers=auth_headers,
                params={"maxResults": limit},
            )

        if list_response.status_code != 200:
            raise HTTPException(
                status_code=list_response.status_code,
                detail=list_response.text,
            )

        items = list_response.json().get(
            "messages",
            [],
        )

        messages = []

        for item in items:
            message_id = item.get("id")

            message_response = await client.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
                headers=auth_headers,
                params={
                    "format": "metadata",
                    "metadataHeaders": [
                        "From",
                        "To",
                        "Subject",
                        "Date",
                    ],
                },
            )

            if message_response.status_code != 200:
                continue

            message_data = message_response.json()
            payload = message_data.get(
                "payload",
                {},
            )

            msg_headers = payload.get(
                "headers",
                [],
            )

            snippet = repair_mojibake(
                message_data.get(
                    "snippet",
                    "",
                )
            )

            messages.append({
                "id": message_id,
                "from": get_header(
                    msg_headers,
                    "From",
                ),
                "to": get_header(
                    msg_headers,
                    "To",
                ),
                "subject": get_header(
                    msg_headers,
                    "Subject",
                ),
                "date": get_header(
                    msg_headers,
                    "Date",
                ),
                "snippet": snippet,
            })

    return messages


@app.get("/gmail/messages")
async def gmail_messages(limit: int = 5):
    messages = await get_gmail_summaries(limit)

    return {
        "count": len(messages),
        "messages": messages,
    }


@app.post("/chat")
async def chat(req: ChatRequest):
    if not os.getenv("OPENAI_API_KEY"):
        return {
            "error": "OPENAI_API_KEY is not configured"
        }

    text = req.message.lower()

    keywords = [
        "gmail",
        "رسائلي",
        "البريد",
        "الايميل",
        "الإيميل",
        "بريدي",
        "لخص رسائلي",
        "لخّص رسائلي",
    ]

    wants_gmail = any(
        word in text
        for word in keywords
    )

    if wants_gmail:
        messages = await get_gmail_summaries(5)

        context = "\n\n".join(
            [
                (
                    f"المرسل: {m['from']}\n"
                    f"العنوان: {m['subject']}\n"
                    f"التاريخ: {m['date']}\n"
                    f"المقتطف: {m['snippet']}"
                )
                for m in messages
            ]
        )

        prompt = f"""
أنت مساعد أمير الشخصي.

طلب المستخدم:
{req.message}

هذه أحدث رسائل Gmail:
{context}

أجب بالعربية بشكل واضح ومختصر.
اذكر أهم الرسائل أولاً.
لا تدّعي إرسال أو حذف أو تعديل أي رسالة.
"""

        return {
            "reply": await run_agent(prompt)
        }

    return {
        "reply": await run_agent(req.message)
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
    )