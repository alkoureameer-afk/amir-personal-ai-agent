# مساعد أمير الشخصي

Private personal AI agent foundation using OpenAI Agents SDK.

## Safety defaults
- Email: read/classify/summarize only; no send/reply/delete tools.
- WhatsApp: inbound command endpoint; no outbound-to-third-party tool in the core agent.
- Calls: explicit request only; provider integration is isolated.
- Web search and email/WhatsApp/voice providers are adapter points.

## Run
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY='YOUR_EXISTING_KEY'
python main.py
```

Never commit the key.
