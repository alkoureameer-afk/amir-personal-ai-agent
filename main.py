import os
import secrets
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
        <p>مساعد أمير الشخصي هو تطبيق شخصي يستخدم Google OAuth للوصول إلى Gmail بإذن المستخدم.</p>

        <h2>الوصول إلى Gmail</h2>
        <p>
        يطلب التطبيق صلاحية قراءة Gmail فقط
        (gmail.readonly).
        لا يرسل التطبيق رسائل بريد إلكتروني ولا يحذفها ولا يرد عليها.
        </p>

        <h2>استخدام البيانات</h2>
        <p>
        تُستخدم بيانات البريد فقط لتقديم وظائف المساعد الشخصي مثل
        قراءة الرسائل وتصنيفها وتلخيصها للمستخدم.
        </p>

        <h2>مشاركة البيانات</h2>
        <p>
        لا يتم بيع بيانات المستخدم أو مشاركتها مع جهات أخرى لأغراض إعلانية.
        </p>

        <h2>إلغاء الوصول</h2>
        <p>
        يمكن للمستخدم إلغاء صلاحية الوصول في أي وقت من إعدادات حساب Google.
        </p>

        <h2>التواصل</h2>
        <p>
        للاستفسارات المتعلقة بالخصوصية، استخدم بريد الدعم المسجل في شاشة موافقة Google.
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
        صلاحية Gmail المستخدمة هي للقراءة فقط.
        لا يقوم التطبيق بإرسال أو حذف أو الرد على رسائل البريد الإلكتروني.
        </p>

        <p>
        باستخدام التطبيق، فإنك توافق على منح الصلاحيات التي تظهر لك
        بوضوح في شاشة موافقة Google.
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
async def google_callback(
    code: str,
    state: str,
):
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