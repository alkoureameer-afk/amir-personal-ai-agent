import os
from fastapi import FastAPI
from pydantic import BaseModel
from dotenv import load_dotenv
from agent import run_agent

load_dotenv()
app = FastAPI(title="مساعد أمير الشخصي")

class ChatRequest(BaseModel):
    message: str

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/chat")
async def chat(req: ChatRequest):
    if not os.getenv("OPENAI_API_KEY"):
        return {"error": "OPENAI_API_KEY is not configured"}
    return {"reply": await run_agent(req.message)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
