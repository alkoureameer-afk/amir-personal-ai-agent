import os
import secrets
import base64
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
        "name": "مساعد أمير الشخصي",
        "status": "online",
        "gmail_login": "/auth/google",
        "health": "/health",
        "gmail_status": "/gmail/status",
        "gmail_inbox": "/gmail/inbox",
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
        <title>سياسة الخصوصية - مساعد أمير الشخصي</title>
    </head>
    <body style="font-family:Arial;max-width:800px;margin:40px auto;padding:20px;line-height:1.8">
        <h1>سياسة الخصوصية</h1>
        <p>مساعد أمير الشخصي يستخدم Google OAuth للوصول إلى Gmail بإذن المستخدم.</p>

        <h2>الوصول إلى Gmail</h2>
        <p>
        يطلب التطبيق صلاحية قراءة Gmail فقط.
        لا يرسل رسائل ولا يحذفها ولا يرد عليها.
        </p>

        <h2>استخدام البيانات</h2>
        <p>
        تُستخدم بيانات البريد فقط لقراءة الرسائل وتصنيفها وتلخيصها للمستخدم.
        </p>

        <h2>مشاركة البيانات</h2>
        <p>
        لا يتم بيع بيانات المستخدم أو مشاركتها لأغراض إعلانية.
        </p>
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
        <title>شروط الاستخدام - مساعد أمير الشخصي</title>
    </head>
    <body style="font-family:Arial;max-width:800px;margin:40px auto;padding:20px;line-height:1.8">
        <h1>شروط الاستخدام</h1>
        <p>
        هذا التطبيق مساعد شخصي خاص ويستخدم فقط من قبل المستخدمين المصرح لهم.
        </p>
        <p>
        صلاحية Gmail المستخدمة للقراءة فقط.
        </p>
    </body>
    </html>
    """


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

    async with httpx.AsyncClient() as client:
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
    }


@app.get("/gmail/status")
def gmail_status():
    return {
        "connected": bool(
            google_tokens.get("access_token")
        )
    }


def decode_base64url(data: str) -> str:
    if not data:
        return ""

    padding = "=" * (-len(data) % 4)

    try:
        decoded = base64.urlsafe_b64decode(data + padding)
        return decoded.decode("utf-8", errors="replace")
    except Exception:
        return ""


def find_message_body(payload: dict) -> str:
    mime_type = payload.get("mimeType", "")
    body = payload.get("body", {})
    data = body.get("data")

    if mime_type == "text/plain" and data:
        return decode_base64url(data)

    parts = payload.get("parts", [])

    for part in parts:
        text = find_message_body(part)
        if text:
            return text

    if data:
        return decode_base64url(data)

    return ""


def get_header(headers: list, name: str) -> str:
    for header in headers:
        if header.get("name", "").lower() == name.lower():
            return header.get("value", "")

    return ""


@app.get("/gmail/inbox")
async def gmail_inbox():
    access_token = google_tokens.get("access_token")

    if not access_token:
        raise HTTPException(
            status_code=401,
            detail="Connect Gmail first",
        )

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            headers=headers,
            params={"maxResults": 10},
        )

    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.text,
        )

    return response.json()


@app.get("/gmail/messages")
async def gmail_messages(limit: int = 10):
    access_token = google_tokens.get("access_token")

    if not access_token:
        raise HTTPException(
            status_code=401,
            detail="Connect Gmail first",
        )

    if limit < 1:
        limit = 1

    if limit > 20:
        limit = 20

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        list_response = await client.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            headers=headers,
            params={"maxResults": limit},
        )

        if list_response.status_code != 200:
            raise HTTPException(
                status_code=list_response.status_code,
                detail=list_response.text,
            )

        items = list_response.json().get("messages", [])

        messages = []

        for item in items:
            message_id = item.get("id")

            message_response = await client.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
                headers=headers,
                params={"format": "full"},
            )

            if message_response.status_code != 200:
                continue

            message_data = message_response.json()
            payload = message_data.get("payload", {})
            msg_headers = payload.get("headers", [])

            body = find_message_body(payload)

            messages.append({
                "id": message_id,
                "threadId": message_data.get("threadId"),
                "from": get_header(msg_headers, "From"),
                "to": get_header(msg_headers, "To"),
                "subject": get_header(msg_headers, "Subject"),
                "date": get_header(msg_headers, "Date"),
                "snippet": message_data.get("snippet", ""),
                "body": body[:5000],
            })

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