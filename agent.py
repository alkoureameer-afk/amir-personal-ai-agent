import os

import httpx
from agents import Agent, Runner, function_tool


GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

GITHUB_REPOSITORY = os.getenv(
    "GITHUB_REPOSITORY",
    "alkoureameer-afk/amir-personal-ai-agent",
)


def github_headers():
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    return headers


@function_tool
def classify_email(
    subject: str,
    sender: str,
    body: str,
) -> str:
    """
    Classify an email without sending,
    replying, deleting, or modifying it.
    """

    text = f"{subject}\n{sender}\n{body}".lower()

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
def plan_day(tasks: str) -> str:
    """
    Return a task list for prioritization.
    No external side effects.
    """

    return tasks


@function_tool
async def github_repository_info() -> str:
    """
    Read information about the configured GitHub repository,
    including repository name, default branch and latest commit.
    This tool never modifies GitHub.
    """

    if not GITHUB_TOKEN:
        return "GitHub is not configured: GITHUB_TOKEN is missing."

    repo_url = (
        f"https://api.github.com/repos/{GITHUB_REPOSITORY}"
    )

    async with httpx.AsyncClient(timeout=30.0) as client:
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

        commit_data = commit_response.json()

    sha = commit_data.get("sha", "")

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
async def github_read_file(path: str) -> str:
    """
    Read a UTF-8 text file from the configured GitHub repository.
    Use repository-relative paths such as main.py or agent.py.
    This tool never modifies GitHub.
    """

    if not GITHUB_TOKEN:
        return "GitHub is not configured: GITHUB_TOKEN is missing."

    clean_path = path.strip().lstrip("/")

    if not clean_path:
        return "A repository file path is required."

    url = (
        f"https://api.github.com/repos/"
        f"{GITHUB_REPOSITORY}/contents/{clean_path}"
    )

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            url,
            headers={
                **github_headers(),
                "Accept": (
                    "application/vnd.github.raw+json"
                ),
            },
        )

    if response.status_code == 404:
        return f"File not found: {clean_path}"

    if response.status_code != 200:
        return (
            "GitHub file request failed: "
            f"{response.status_code} "
            f"{response.text[:500]}"
        )

    content = response.text

    if len(content) > 12000:
        content = (
            content[:12000]
            + "\n\n[File truncated]"
        )

    return (
        f"File: {clean_path}\n\n"
        f"{content}"
    )


agent = Agent(
    name="مساعد أمير الشخصي",

    instructions=(
        "You are a private personal AI assistant. "
        "Answer primarily in Arabic. "

        "Help organize the user's day and summarize "
        "and classify email. "

        "You have read-only GitHub tools for the configured "
        "Amir Personal AI Agent repository. "
        "When the user asks about the repository, branch, "
        "commit, code, or GitHub files, use the GitHub tools "
        "instead of claiming that GitHub is unavailable. "

        "Never claim you inspected a GitHub file unless "
        "you actually used a GitHub tool. "

        "GitHub access in this version is read-only. "
        "Do not claim to edit, commit, push, delete, or merge code. "

        "You MUST NOT send, reply to, delete, or modify email. "
        "You MUST NOT send WhatsApp messages to other people. "

        "You MUST NOT place a call unless the user explicitly "
        "requests it and identifies the intended person or number. "

        "Require confirmation before sensitive side effects. "
        "Never reveal secrets, API keys, access tokens, "
        "refresh tokens, passwords, or environment variables."
    ),

    tools=[
        classify_email,
        plan_day,
        github_repository_info,
        github_read_file,
    ],
)


async def run_agent(message: str) -> str:
    result = await Runner.run(
        agent,
        message,
    )

    return result.final_output