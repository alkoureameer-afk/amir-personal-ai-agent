from agents import Agent, Runner, function_tool

@function_tool
def classify_email(subject: str, sender: str, body: str) -> str:
    """Classify an email without sending, replying, deleting, or modifying it."""
    text = f"{subject}\n{sender}\n{body}".lower()
    if any(x in text for x in ("urgent", "important", "عاجل", "مهم")):
        return "URGENT"
    if any(x in text for x in ("unsubscribe", "sale", "offer", "خصم", "عرض")):
        return "PROMOTION"
    return "NORMAL"

@function_tool
def plan_day(tasks: str) -> str:
    """Return a task list for prioritization. No external side effects."""
    return tasks

agent = Agent(
    name="مساعد أمير الشخصي",
    instructions=(
        "You are a private personal assistant. Help organize the user's day, "
        "summarize and classify email, and use web search when a search tool is connected. "
        "You MUST NOT send/reply/delete email. You MUST NOT send WhatsApp messages to other people. "
        "You MUST NOT place a call unless the user explicitly requests it and identifies the intended person/number. "
        "Require confirmation before sensitive side effects. Never reveal secrets or API keys."
    ),
    tools=[classify_email, plan_day],
)

async def run_agent(message: str) -> str:
    result = await Runner.run(agent, message)
    return result.final_output
