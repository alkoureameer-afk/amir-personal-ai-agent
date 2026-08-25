import os
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
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
    }


@app.get("/health")
def health():
    return {"status": "ok"}


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