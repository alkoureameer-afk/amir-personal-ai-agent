import base64
import os
import re
from contextvars import ContextVar

import httpx
from agents import Agent, Runner, function_tool


GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

GITHUB_REPOSITORY = os.getenv(
    "GITHUB_REPOSITORY",
    "alkoureameer-afk/amir-personal-ai-agent",
)

github_write_approved = ContextVar(
    "github_write_approved",
    default=False,
)


def github_headers():
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    return headers


def extract_current_user_message(prompt: str) -> str:
    markers = [
        (
            "طلب المستخدم الحالي:\n",
            "\n\nأحدث رسائل Gmail:",
        ),
        (
            "رسالة المستخدم الحالية:\n",
            "\n\nأجب بالعربية",
        ),
    ]

    for start_marker, end_marker in markers:
        if start_marker in prompt:
            current = prompt.split(start_marker, 1)[1]

            if end_marker in current:
                current = current.split(end_marker, 1)[0]

            return current.strip()

    return prompt.strip()


def normalize_arabic_text(text: str) -> str:
    text = text.lower().strip()

    text = re.sub(
        r"[\u0640\u064b-\u065f\u0670]",
        "",
        text,
    )

    text = (
        text
        .replace("أ", "ا")
        .replace("إ", "ا")
        .replace("آ", "ا")
    )

    text = re.sub(
        r"[^\w\u0600-\u06ff]+",
        " ",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    ).strip()

    return text


def user_explicitly_approved_github_write(
    current_message: str,
) -> bool:
    text = normalize_arabic_text(
        current_message
    )

    blocked_phrases = {
        "لا تنفذ",
        "لا تعدل",
        "لا تغير",
        "لا تعمل تعديل",
    }

    if any(
        phrase in text
        for phrase in blocked_phrases
    ):
        return False

    approved_phrases = {
        "موافق نفذ",
        "موافق نفذ التعديل",
        "نفذ التعديل",
        "نفذ الان",
        "ابدأ التنفيذ",
        "ابدأ بالتنفيذ",
    }

    return any(
        phrase in text
        for phrase in approved_phrases
    )


def path_is_safe_for_edit(
    path: str,
) -> bool:
    clean = (
        path
        .strip()
        .lstrip("/")
        .lower()
    )

    blocked_names = {
        ".env",
        ".env.local",
        ".env.production",
        "secrets.json",
        "credentials.json",
    }

    if clean in blocked_names:
        return False

    if clean.startswith(".git/"):
        return False

    return True


@function_tool
def classify_email(
    subject: str,
    sender: str,
    body: str,
) -> str:
    text = (
        f"{subject}\n"
        f"{sender}\n"
        f"{body}"
    ).lower()

    if any(
        x in text
        for x in (
            "urgent",
            "important",
            "عاجل",
            "مهم",
        )
    ):
        return "URGENT"

    if any(
        x in text
        for x in (
            "unsubscribe",
            "sale",
            "offer",
            "خصم",
            "عرض",
        )
    ):
        return "PROMOTION"

    return "NORMAL"


@function_tool
def plan_day(
    tasks: str,
) -> str:
    return tasks


@function_tool
async def github_repository_info() -> str:
    if not GITHUB_TOKEN:
        return (
            "GitHub is not configured: "
            "GITHUB_TOKEN is missing."
        )

    repo_url = (
        "https://api.github.com/repos/"
        f"{GITHUB_REPOSITORY}"
    )

    async with httpx.AsyncClient(
        timeout=30.0
    ) as client:
        repo_response = await client.get(
            repo_url,
            headers=github_headers(),
        )

        if repo_response.status_code != 200:
            return (
                "GitHub repository request failed: "
                f"{repo_response.status_code} "
                f"{repo_response.text[:500]}"
            )

        repo_data = repo_response.json()

        default_branch = repo_data.get(
            "default_branch",
            "main",
        )

        commit_response = await client.get(
            (
                f"{repo_url}/commits/"
                f"{default_branch}"
            ),
            headers=github_headers(),
        )

        if commit_response.status_code != 200:
            return (
                f"Repository: {GITHUB_REPOSITORY}\n"
                f"Default branch: {default_branch}\n"
                "Could not read latest commit."
            )

        commit_data = (
            commit_response.json()
        )

    sha = commit_data.get(
        "sha",
        "",
    )

    message = (
        commit_data
        .get("commit", {})
        .get("message", "")
    )

    author = (
        commit_data
        .get("commit", {})
        .get("author", {})
        .get("name", "")
    )

    date = (
        commit_data
        .get("commit", {})
        .get("author", {})
        .get("date", "")
    )

    return (
        f"Repository: {GITHUB_REPOSITORY}\n"
        f"Default branch: {default_branch}\n"
        f"Latest commit SHA: {sha[:12]}\n"
        f"Latest commit message: {message}\n"
        f"Commit author: {author}\n"
        f"Commit date: {date}"
    )


@function_tool
async def github_list_files(
    path: str = "",
) -> str:
    if not GITHUB_TOKEN:
        return (
            "GitHub is not configured: "
            "GITHUB_TOKEN is missing."
        )

    clean_path = (
        path
        .strip()
        .lstrip("/")
    )

    url = (
        "https://api.github.com/repos/"
        f"{GITHUB_REPOSITORY}/contents"
    )

    if clean_path:
        url += f"/{clean_path}"

    async with httpx.AsyncClient(
        timeout=30.0
    ) as client:
        response = await client.get(
            url,
            headers=github_headers(),
        )

    if response.status_code == 404:
        return (
            f"Path not found: "
            f"{clean_path or '/'}"
        )

    if response.status_code != 200:
        return (
            "GitHub directory request failed: "
            f"{response.status_code} "
            f"{response.text[:500]}"
        )

    data = response.json()

    if not isinstance(
        data,
        list,
    ):
        return (
            "Path is a file, "
            f"not a directory: {clean_path}"
        )

    lines = []

    for item in data[:100]:
        item_type = item.get(
            "type",
            "unknown",
        )

        item_path = item.get(
            "path",
            "",
        )

        lines.append(
            f"{item_type}: {item_path}"
        )

    return (
        "\n".join(lines)
        or "Directory is empty."
    )


@function_tool
async def github_read_file(
    path: str,
) -> str:
    if not GITHUB_TOKEN:
        return (
            "GitHub is not configured: "
            "GITHUB_TOKEN is missing."
        )

    clean_path = (
        path
        .strip()
        .lstrip("/")
    )

    if not clean_path:
        return (
            "A repository file path "
            "is required."
        )

    url = (
        "https://api.github.com/repos/"
        f"{GITHUB_REPOSITORY}/contents/"
        f"{clean_path}"
    )

    async with httpx.AsyncClient(
        timeout=30.0
    ) as client:
        response = await client.get(
            url,
            headers={
                **github_headers(),
                "Accept": (
                    "application/"
                    "vnd.github.raw+json"
                ),
            },
        )

    if response.status_code == 404:
        return (
            f"File not found: "
            f"{clean_path}"
        )

    if response.status_code != 200:
        return (
            "GitHub file request failed: "
            f"{response.status_code} "
            f"{response.text[:500]}"
        )

    file_content = response.text

    if len(file_content) > 16000:
        file_content = (
            file_content[:16000]
            + "\n\n[File truncated]"
        )

    return (
        f"File: {clean_path}\n\n"
        f"{file_content}"
    )


@function_tool
async def github_update_file(
    path: str,
    new_content: str,
    commit_message: str,
) -> str:
    if not github_write_approved.get():
        return (
            "GitHub write blocked: "
            "the current user message does not "
            "contain explicit execution approval. "
            "Ask the user to say "
            "'موافق، نفّذ' after reviewing "
            "the proposed change."
        )

    if not GITHUB_TOKEN:
        return (
            "GitHub is not configured: "
            "GITHUB_TOKEN is missing."
        )

    clean_path = (
        path
        .strip()
        .lstrip("/")
    )

    if not clean_path:
        return (
            "GitHub write blocked: "
            "file path is required."
        )

    if not path_is_safe_for_edit(
        clean_path
    ):
        return (
            "GitHub write blocked: "
            "this file is treated as a "
            "secret/protected configuration file."
        )

    if not new_content:
        return (
            "GitHub write blocked: "
            "new file content is empty."
        )

    if not commit_message.strip():
        commit_message = (
            f"Update {clean_path} "
            "via Amir Personal AI Agent"
        )

    repo_url = (
        "https://api.github.com/repos/"
        f"{GITHUB_REPOSITORY}"
    )

    file_url = (
        f"{repo_url}/contents/"
        f"{clean_path}"
    )

    async with httpx.AsyncClient(
        timeout=30.0
    ) as client:
        repo_response = await client.get(
            repo_url,
            headers=github_headers(),
        )

        if repo_response.status_code != 200:
            return (
                "GitHub repository request failed: "
                f"{repo_response.status_code} "
                f"{repo_response.text[:500]}"
            )

        default_branch = (
            repo_response
            .json()
            .get(
                "default_branch",
                "main",
            )
        )

        current_response = (
            await client.get(
                file_url,
                headers=github_headers(),
                params={
                    "ref": default_branch
                },
            )
        )

        if current_response.status_code == 404:
            return (
                "GitHub write blocked: "
                "this tool only updates "
                "existing files. "
                f"File not found: {clean_path}"
            )

        if current_response.status_code != 200:
            return (
                "Could not read current GitHub "
                "file metadata: "
                f"{current_response.status_code} "
                f"{current_response.text[:500]}"
            )

        current_data = (
            current_response.json()
        )

        current_sha = (
            current_data.get("sha")
        )

        if not current_sha:
            return (
                "GitHub write blocked: "
                "current file SHA is missing."
            )

        payload = {
            "message": (
                commit_message
                .strip()[:200]
            ),
            "content": (
                base64.b64encode(
                    new_content.encode(
                        "utf-8"
                    )
                ).decode("ascii")
            ),
            "sha": current_sha,
            "branch": default_branch,
        }

        update_response = (
            await client.put(
                file_url,
                headers=github_headers(),
                json=payload,
            )
        )

    if (
        update_response.status_code
        not in (200, 201)
    ):
        return (
            "GitHub update failed: "
            f"{update_response.status_code} "
            f"{update_response.text[:700]}"
        )

    result = (
        update_response.json()
    )

    commit_sha = (
        result
        .get("commit", {})
        .get("sha", "")
    )

    return (
        "GitHub update completed successfully.\n"
        f"File: {clean_path}\n"
        f"Branch: {default_branch}\n"
        f"Commit: {commit_sha[:12]}\n"
        f"Message: {commit_message.strip()}"
    )


agent = Agent(
    name="مساعد أمير الشخصي",

    instructions=(
        "You are a private personal AI assistant. "
        "Answer primarily in Arabic. "

        "Help organize the user's day and summarize "
        "and classify email. "

        "You have GitHub tools for the configured "
        "Amir Personal AI Agent repository. "

        "Use read-only GitHub tools to inspect "
        "repository information, directories, and "
        "files whenever the user asks about GitHub, "
        "code, bugs, commits, branches, or deployment "
        "related code. "

        "Never claim you inspected a GitHub file "
        "unless you actually used a GitHub read tool. "

        "For code changes, first inspect the relevant "
        "files, explain the problem, and clearly propose "
        "the exact change. "

        "DO NOT modify GitHub unless the user's CURRENT "
        "message itself contains explicit execution "
        "approval such as 'موافق، نفّذ' or "
        "'نفّذ التعديل'. "

        "A previous approval in conversation history "
        "is not valid for a new write. "

        "A plain 'نعم' is not sufficient. "

        "When explicit approval is present, use "
        "github_update_file only for the change the user "
        "already reviewed and approved. "

        "After a successful update, report the changed "
        "path and commit SHA. "

        "Never delete GitHub files. "
        "Never edit secret credential files. "

        "You MUST NOT send, reply to, delete, "
        "or modify email. "

        "You MUST NOT send WhatsApp messages "
        "to other people. "

        "You MUST NOT place a call unless the user "
        "explicitly requests it and identifies the "
        "intended person or number. "

        "Require confirmation before sensitive "
        "side effects. "

        "Never reveal secrets, API keys, access tokens, "
        "refresh tokens, passwords, or environment "
        "variables."
    ),

    tools=[
        classify_email,
        plan_day,
        github_repository_info,
        github_list_files,
        github_read_file,
        github_update_file,
    ],
)


async def run_agent(
    message: str,
) -> str:
    current_user_message = (
        extract_current_user_message(
            message
        )
    )

    approval = (
        user_explicitly_approved_github_write(
            current_user_message
        )
    )

    token = (
        github_write_approved.set(
            approval
        )
    )

    try:
        result = await Runner.run(
            agent,
            message,
        )

        return result.final_output

    finally:
        github_write_approved.reset(
            token
        )