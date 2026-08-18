import os
import httpx
from collections import defaultdict, deque

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
BOT_PERSONA_NAME = os.getenv("BOT_PERSONA_NAME", "مستر روبوت")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# ذاكرة محادثة بسيطة في الذاكرة (آخر ٦ رسائل لكل جروب) - بتتصفر لما البوت يعيد التشغيل
_history = defaultdict(lambda: deque(maxlen=6))

SYSTEM_PROMPT = f"""إنت اسمك "{BOT_PERSONA_NAME}"، عضو حقيقي جوه جروب طلاب الصف الثالث الثانوي على تليجرام، مش بوت رسمي ومش مساعد آلي بيرد بشكل جامد.

طريقة كلامك:
- بتتكلم عامية مصرية طبيعية، زي أي حد في الجروب، مش فصحى.
- ردودك قصيرة ومباشرة (سطر أو اتنين غالبًا)، إلا لو حد طلب شرح مفصل.
- ودود ومشجع، بتحب تساعد في المذاكرة والاستفسارات الدراسية.
- تقدر تمزح بخفة لو الموضوع مناسب، بس من غير مبالغة.
- لو حد سألك سؤال دراسي، جاوب بشكل مفيد ومختصر وسهل الفهم.
- متكررش نفسك ومتقولش "أنا نموذج لغوي" أو "أنا ذكاء اصطناعي" — إنت عضو في الجروب بس بتساعد.
- لو مش عارف إجابة حاجة، قول بصراحة إنك مش متأكد بدل ما تختلق معلومة.
"""


async def get_ai_reply(chat_id: int, user_name: str, user_message: str) -> str:
    if not GROQ_API_KEY:
        return "لسه مفعلتش خاصية الدردشة الذكية، ضيف GROQ_API_KEY في إعدادات المشروع الأول 🙏"

    history = _history[chat_id]
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for role, content in history:
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": f"{user_name}: {user_message}"})

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                json={
                    "model": GROQ_MODEL,
                    "messages": messages,
                    "temperature": 0.8,
                    "max_tokens": 400,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            reply = data["choices"][0]["message"]["content"].strip()
    except Exception:
        return "معلش، حصل خطأ وأنا بحاول أرد 😅 جرب تاني كمان شوية."

    history.append(("user", f"{user_name}: {user_message}"))
    history.append(("assistant", reply))
    return reply
