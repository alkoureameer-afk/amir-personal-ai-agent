import os
import secrets
from collections import deque
from email.header import decode_header
from urllib.parse import urlencode

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel
from dotenv import load_dotenv

from agent import run_agent


load_dotenv()

app = FastAPI(title="Amir Personal AI")


# =========================================================
# Environment variables
# =========================================================

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN")

WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
WHATSAPP_VERIFY_TOKEN = os.getenv(
    "WHATSAPP_VERIFY_TOKEN",
    "amir_personal_ai_verify_2026",
)

REDIRECT_URI = (
    "https://amir-personal-ai-agent.onrender.com/auth/google/callback"
)

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


# =========================================================
# Temporary application memory
# =========================================================

pending_states = set()
google_tokens = {}
one_time_refresh_tokens = {}


# =========================================================
# Conversation memory
# =========================================================

MAX_CONVERSATION_MESSAGES = 20

conversation_memory = {}


def get_conversation_history(user_id: str):
    if not user_id:
        return []

    if user_id not in conversation_memory:
        conversation_memory[user_id] = deque(
            maxlen=MAX_CONVERSATION_MESSAGES
        )

    return conversation_memory[user_id]


def remember_conversation(
    user_id: str,
    role: str,
    content: str,
):
    if not user_id or not content:
        return

    history = get_conversation_history(user_id)

    history.append(
        {
            "role": role,
            "content": content,
        }
    )


def build_conversation_context(
    user_id: str,
) -> str:
    history = get_conversation_history(user_id)

    if not history:
        return ""

    lines = []

    for item in history:
        role = item.get("role")

        if role == "user":
            label = "المستخدم"
        else:
            label = "المساعد"

        lines.append(
            f"{label}: {item.get('content', '')}"
        )

    return "\n".join(lines)


# =========================================================
# WhatsApp duplicate protection
# =========================================================

MAX_PROCESSED_WHATSAPP_MESSAGES = 1000

processed_whatsapp_message_ids = set()
processed_whatsapp_message_order = deque()


def remember_whatsapp_message(
    message_id: str,
) -> bool:
    """
    Returns True if this is a new WhatsApp message.
    Returns False if we already processed this message.
    """

    if not message_id:
        return True

    if message_id in processed_whatsapp_message_ids:
        return False

    processed_whatsapp_message_ids.add(
        message_id
    )

    processed_whatsapp_message_order.append(
        message_id
    )

    while (
        len(processed_whatsapp_message_order)
        > MAX_PROCESSED_WHATSAPP_MESSAGES
    ):
        oldest_id = (
            processed_whatsapp_message_order.popleft()
        )

        processed_whatsapp_message_ids.discard(
            oldest_id
        )

    return True


# =========================================================
# Models
# =========================================================

class ChatRequest(BaseModel):
    message: str


# =========================================================
# Basic routes
# =========================================================

@app.get("/")
def root():
    return {
        "name": "Amir Personal AI",
        "status": "online",
        "gmail_login": "/auth/google",
        "gmail_status": "/gmail/status",
        "gmail_messages": "/gmail/messages",
        "whatsapp_webhook": "/whatsapp/webhook",
        "health": "/health",
    }


@app.get("/health")
def health():
    return {
        "status": "ok"
    }


# =========================================================
# Privacy / Terms
# =========================================================

@app.get(
    "/privacy",
    response_class=HTMLResponse,
)
def privacy():
    return """
    <!doctype html>
    <html lang="ar" dir="rtl">

    <head>
        <meta charset="utf-8">

        <meta
            name="viewport"
            content="width=device-width, initial-scale=1"
        >

        <title>
            سياسة الخصوصية
        </title>
    </head>

    <body style="
        font-family:Arial;
        max-width:800px;
        margin:40px auto;
        padding:20px;
        line-height:1.8;
    ">

        <h1>
            سياسة الخصوصية
        </h1>

        <p>
            يستخدم التطبيق Google OAuth
            للوصول إلى Gmail بإذن المستخدم.
        </p>

        <p>
            الوصول إلى Gmail للقراءة فقط.
        </p>

        <p>
            يستخدم التطبيق WhatsApp Cloud API
            لاستقبال والرد على الرسائل.
        </p>

    </body>
    </html>
    """


@app.get(
    "/terms",
    response_class=HTMLResponse,
)
def terms():
    return """
    <!doctype html>
    <html lang="ar" dir="rtl">

    <head>
        <meta charset="utf-8">

        <meta
            name="viewport"
            content="width=device-width, initial-scale=1"
        >

        <title>
            شروط الاستخدام
        </title>
    </head>

    <body style="
        font-family:Arial;
        max-width:800px;
        margin:40px auto;
        padding:20px;
        line-height:1.8;
    ">

        <h1>
            شروط الاستخدام
        </h1>

        <p>
            هذا التطبيق مساعد شخصي
            للمستخدمين المصرح لهم.
        </p>

    </body>
    </html>
    """


# =========================================================
# Gmail helpers
# =========================================================

def decode_mime_header(
    value: str,
) -> str:
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


def repair_mojibake(
    text: str,
) -> str:
    if not text:
        return ""

    candidates = [
        text
    ]

    for source_encoding in [
        "latin1",
        "cp1252",
    ]:
        try:
            fixed = (
                text
                .encode(
                    source_encoding,
                    errors="ignore",
                )
                .decode(
                    "utf-8",
                    errors="ignore",
                )
            )

            if fixed:
                candidates.append(
                    fixed
                )

        except Exception:
            pass

    def score(
        value: str,
    ) -> int:

        bad = (
            value.count("Ø")
            + value.count("Ù")
            + value.count("Ã")
            + value.count("â€")
            + value.count("�")
        )

        arabic = sum(
            1
            for char in value
            if "\u0600" <= char <= "\u06ff"
        )

        return (
            arabic * 3
            - bad * 5
        )

    return max(
        candidates,
        key=score,
    )


def clean_header(
    value: str,
) -> str:
    return repair_mojibake(
        decode_mime_header(
            value
        )
    )


def get_header(
    headers: list,
    name: str,
) -> str:
    for header in headers:

        if (
            header
            .get("name", "")
            .lower()
            == name.lower()
        ):
            return clean_header(
                header.get(
                    "value",
                    "",
                )
            )

    return ""


# =========================================================
# Google OAuth
# =========================================================

async def refresh_access_token() -> str:

    refresh_token = (
        google_tokens.get(
            "refresh_token"
        )
        or GOOGLE_REFRESH_TOKEN
    )

    if not refresh_token:
        raise HTTPException(
            status_code=401,
            detail="Connect Gmail first",
        )

    if (
        not GOOGLE_CLIENT_ID
        or not GOOGLE_CLIENT_SECRET
    ):
        raise HTTPException(
            status_code=500,
            detail=(
                "Google OAuth is not configured"
            ),
        )

    async with httpx.AsyncClient(
        timeout=30.0
    ) as client:

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

    access_token = token_data.get(
        "access_token"
    )

    if not access_token:
        raise HTTPException(
            status_code=401,
            detail=(
                "Google did not return "
                "an access token"
            ),
        )

    google_tokens[
        "access_token"
    ] = access_token

    google_tokens[
        "refresh_token"
    ] = refresh_token

    return access_token


async def get_access_token() -> str:

    access_token = google_tokens.get(
        "access_token"
    )

    if access_token:
        return access_token

    return await refresh_access_token()


@app.get("/auth/google")
def google_login():

    if (
        not GOOGLE_CLIENT_ID
        or not GOOGLE_CLIENT_SECRET
    ):
        raise HTTPException(
            status_code=500,
            detail=(
                "Google OAuth is not configured"
            ),
        )

    state = secrets.token_urlsafe(32)

    pending_states.add(
        state
    )

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
        "https://accounts.google.com/"
        "o/oauth2/v2/auth?"
        + urlencode(params)
    )

    return RedirectResponse(
        url
    )


@app.get(
    "/auth/google/callback"
)
async def google_callback(
    code: str,
    state: str,
):
    if state not in pending_states:
        raise HTTPException(
            status_code=400,
            detail="Invalid OAuth state",
        )

    pending_states.remove(
        state
    )

    async with httpx.AsyncClient(
        timeout=30.0
    ) as client:

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

    google_tokens.update(
        token_data
    )

    refresh_token = token_data.get(
        "refresh_token"
    )

    if refresh_token:

        one_time_code = (
            secrets.token_urlsafe(32)
        )

        one_time_refresh_tokens[
            one_time_code
        ] = refresh_token

        return RedirectResponse(
            url=(
                "/auth/google/"
                "refresh-token-once"
                f"?code={one_time_code}"
            )
        )

    return {
        "status": "connected",
        "message": (
            "Gmail connected "
            "with read-only access"
        ),
        "refresh_token_received": False,
    }


@app.get(
    "/auth/google/refresh-token-once",
    response_class=HTMLResponse,
)
def show_refresh_token_once(
    code: str,
):

    refresh_token = (
        one_time_refresh_tokens.pop(
            code,
            None,
        )
    )

    if not refresh_token:
        raise HTTPException(
            status_code=404,
            detail=(
                "Token already viewed "
                "or invalid"
            ),
        )

    return f"""
    <!doctype html>
    <html lang="ar" dir="rtl">

    <head>

        <meta charset="utf-8">

        <meta
            name="viewport"
            content="width=device-width, initial-scale=1"
        >

        <title>
            Google Refresh Token
        </title>

    </head>

    <body style="
        font-family:Arial;
        max-width:800px;
        margin:40px auto;
        padding:20px;
    ">

        <h2>
            تم ربط Gmail بنجاح ✅
        </h2>

        <p>
            انسخ القيمة التالية إلى Render
            كـ GOOGLE_REFRESH_TOKEN:
        </p>

        <textarea
            style="width:100%;height:180px;"
            readonly
        >{refresh_token}</textarea>

        <p>
            لا ترسل هذه القيمة لأي شخص.
        </p>

    </body>
    </html>
    """


# =========================================================
# Gmail API
# =========================================================

@app.get(
    "/gmail/status"
)
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


async def get_gmail_summaries(
    limit: int = 5,
):

    access_token = (
        await get_access_token()
    )

    limit = max(
        1,
        min(
            limit,
            10,
        ),
    )

    auth_headers = {
        "Authorization": (
            f"Bearer {access_token}"
        )
    }

    async with httpx.AsyncClient(
        timeout=20.0
    ) as client:

        list_response = await client.get(
            (
                "https://gmail.googleapis.com/"
                "gmail/v1/users/me/messages"
            ),
            headers=auth_headers,
            params={
                "maxResults": limit
            },
        )

        if (
            list_response.status_code
            == 401
        ):

            access_token = (
                await refresh_access_token()
            )

            auth_headers = {
                "Authorization": (
                    f"Bearer {access_token}"
                )
            }

            list_response = await client.get(
                (
                    "https://gmail.googleapis.com/"
                    "gmail/v1/users/me/messages"
                ),
                headers=auth_headers,
                params={
                    "maxResults": limit
                },
            )

        if (
            list_response.status_code
            != 200
        ):
            raise HTTPException(
                status_code=(
                    list_response.status_code
                ),
                detail=list_response.text,
            )

        items = (
            list_response
            .json()
            .get(
                "messages",
                [],
            )
        )

        messages = []

        for item in items:

            message_id = item.get(
                "id"
            )

            message_response = (
                await client.get(
                    (
                        "https://gmail.googleapis.com/"
                        "gmail/v1/users/me/messages/"
                        f"{message_id}"
                    ),
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
            )

            if (
                message_response.status_code
                != 200
            ):
                continue

            message_data = (
                message_response.json()
            )

            payload = (
                message_data.get(
                    "payload",
                    {},
                )
            )

            msg_headers = (
                payload.get(
                    "headers",
                    [],
                )
            )

            snippet = repair_mojibake(
                message_data.get(
                    "snippet",
                    "",
                )
            )

            messages.append(
                {
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
                }
            )

    return messages


@app.get(
    "/gmail/messages"
)
async def gmail_messages(
    limit: int = 5,
):

    messages = (
        await get_gmail_summaries(
            limit
        )
    )

    return {
        "count": len(messages),
        "messages": messages,
    }


# =========================================================
# AI Agent
# =========================================================

async def build_agent_reply(
    user_message: str,
    user_id: str = "default",
) -> str:

    text = user_message.lower()

    conversation_context = (
        build_conversation_context(
            user_id
        )
    )

    gmail_keywords = [
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
        keyword in text
        for keyword in gmail_keywords
    )

    if wants_gmail:

        messages = (
            await get_gmail_summaries(5)
        )

        gmail_context = (
            "\n\n".join(
                [
                    (
                        f"المرسل: {message['from']}\n"
                        f"العنوان: {message['subject']}\n"
                        f"التاريخ: {message['date']}\n"
                        f"المقتطف: {message['snippet']}"
                    )
                    for message in messages
                ]
            )
        )

        prompt = f"""
أنت المساعد الشخصي الذكي لأمير.

تحدث مع المستخدم بشكل طبيعي ومفيد.
تذكر سياق المحادثة السابقة.

المحادثة السابقة:
{conversation_context or "لا توجد محادثة سابقة."}

طلب المستخدم الحالي:
{user_message}

هذه أحدث رسائل Gmail:
{gmail_context}

أجب بالعربية بشكل واضح ومختصر.
اذكر أهم الرسائل أولاً.
لا تدّعي إرسال أو حذف أو تعديل أي رسالة.
"""

    else:

        prompt = f"""
أنت المساعد الشخصي الذكي لأمير.

تحدث بالعربية بشكل طبيعي وواضح.

استخدم سياق المحادثة السابقة عند الحاجة.

إذا ذكر المستخدم اسمه أو معلومات عنه،
فتذكرها من المحادثة السابقة.

لا تقل إنك لا تتذكر شيئًا إذا كانت المعلومة
موجودة في سياق المحادثة.

المحادثة السابقة:
{conversation_context or "لا توجد محادثة سابقة."}

رسالة المستخدم الحالية:
{user_message}

أجب على الرسالة الحالية
مع مراعاة السياق السابق.
"""

    reply = await run_agent(
        prompt
    )

    remember_conversation(
        user_id,
        "user",
        user_message,
    )

    remember_conversation(
        user_id,
        "assistant",
        reply,
    )

    return reply


@app.post(
    "/chat"
)
async def chat(
    req: ChatRequest,
):

    if not os.getenv(
        "OPENAI_API_KEY"
    ):
        return {
            "error": (
                "OPENAI_API_KEY "
                "is not configured"
            )
        }

    return {
        "reply": (
            await build_agent_reply(
                req.message,
                user_id="web-chat",
            )
        )
    }


# =========================================================
# WhatsApp Webhook verification
# =========================================================

@app.get(
    "/whatsapp/webhook"
)
def verify_whatsapp_webhook(
    hub_mode: str = None,
    hub_verify_token: str = None,
    hub_challenge: str = None,
):

    if (
        hub_mode == "subscribe"
        and hub_verify_token
        == WHATSAPP_VERIFY_TOKEN
    ):

        return PlainTextResponse(
            content=(
                hub_challenge or ""
            ),
            status_code=200,
        )

    raise HTTPException(
        status_code=403,
        detail=(
            "Webhook verification failed"
        ),
    )


# =========================================================
# Send WhatsApp message
# =========================================================

async def send_whatsapp_message(
    to_number: str,
    text: str,
):

    if not WHATSAPP_ACCESS_TOKEN:

        raise HTTPException(
            status_code=500,
            detail=(
                "WHATSAPP_ACCESS_TOKEN "
                "is not configured"
            ),
        )

    if not WHATSAPP_PHONE_NUMBER_ID:

        raise HTTPException(
            status_code=500,
            detail=(
                "WHATSAPP_PHONE_NUMBER_ID "
                "is not configured"
            ),
        )

    url = (
        "https://graph.facebook.com/v21.0/"
        f"{WHATSAPP_PHONE_NUMBER_ID}/messages"
    )

    headers = {
        "Authorization": (
            f"Bearer {WHATSAPP_ACCESS_TOKEN}"
        ),
        "Content-Type": (
            "application/json"
        ),
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {
            "body": text[:4000],
        },
    }

    async with httpx.AsyncClient(
        timeout=30.0
    ) as client:

        response = await client.post(
            url,
            headers=headers,
            json=payload,
        )

    if response.status_code >= 300:

        raise HTTPException(
            status_code=(
                response.status_code
            ),
            detail=response.text,
        )

    return response.json()


# =========================================================
# Process WhatsApp message in background
# =========================================================

async def process_whatsapp_message(
    from_number: str,
    user_text: str,
    message_id: str,
):

    try:

        print(
            "Processing WhatsApp message:",
            message_id,
            "from:",
            from_number,
        )

        reply = await build_agent_reply(
            user_text,
            user_id=from_number,
        )

        await send_whatsapp_message(
            from_number,
            reply,
        )

        print(
            "WhatsApp reply sent successfully:",
            message_id,
        )

    except Exception as exc:

        print(
            "WhatsApp message processing error:",
            message_id,
            exc,
        )


# =========================================================
# Receive WhatsApp messages
# =========================================================

@app.post(
    "/whatsapp/webhook"
)
async def whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
):

    try:

        data = await request.json()

        entries = data.get(
            "entry",
            [],
        )

        for entry in entries:

            changes = entry.get(
                "changes",
                [],
            )

            for change in changes:

                value = change.get(
                    "value",
                    {},
                )

                # Ignore delivery/read/status updates.
                # Only process incoming messages.
                messages = value.get(
                    "messages",
                    [],
                )

                if not messages:
                    continue

                for message in messages:

                    if (
                        message.get("type")
                        != "text"
                    ):
                        continue

                    message_id = (
                        message.get(
                            "id",
                            "",
                        )
                    )

                    # Prevent duplicates
                    if (
                        message_id
                        and not remember_whatsapp_message(
                            message_id
                        )
                    ):

                        print(
                            "Duplicate WhatsApp message ignored:",
                            message_id,
                        )

                        continue

                    from_number = (
                        message.get(
                            "from"
                        )
                    )

                    user_text = (
                        message
                        .get(
                            "text",
                            {},
                        )
                        .get(
                            "body",
                            "",
                        )
                        .strip()
                    )

                    if not from_number:
                        continue

                    if not user_text:
                        continue

                    # Process after returning 200 OK to Meta
                    background_tasks.add_task(
                        process_whatsapp_message,
                        from_number,
                        user_text,
                        message_id,
                    )

    except Exception as exc:

        print(
            "WhatsApp webhook parsing error:",
            exc,
        )

    return {
        "status": "ok"
    }


# =========================================================
# Run locally / Render
# =========================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "8000",
            )
        ),
    )