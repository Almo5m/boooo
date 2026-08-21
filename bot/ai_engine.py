import os
import httpx
from collections import defaultdict, deque

BOT_PERSONA_NAME = os.getenv("BOT_PERSONA_NAME", "بو")

# ---- إعدادات مزوّد الذكاء الاصطناعي ----
# غيّر AI_PROVIDER في الـ Variables لـ: groq | openrouter | gemini
AI_PROVIDER = os.getenv("AI_PROVIDER", "groq").lower().strip()

PROVIDERS = {
    "groq": {
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "key_env": "GROQ_API_KEY",
        "default_model": "llama-3.3-70b-versatile",
    },
    "openrouter": {
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "key_env": "OPENROUTER_API_KEY",
        # موديل مجاني على OpenRouter
        "default_model": "meta-llama/llama-3.1-8b-instruct:free",
    },
    "gemini": {
        # جوجل بتوفر endpoint متوافق مع صيغة OpenAI
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "key_env": "GEMINI_API_KEY",
        "default_model": "gemini-3.6-flash",
    },
}

_config = PROVIDERS.get(AI_PROVIDER, PROVIDERS["groq"])
API_URL = _config["url"]
API_KEY = os.getenv(_config["key_env"], "")
MODEL = os.getenv("AI_MODEL", _config["default_model"])

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
    if not API_KEY:
        return (
            f"لسه مفعلتش خاصية الدردشة الذكية 🙏\n"
            f"ضيف {_config['key_env']} في Variables على Railway (المزوّد الحالي: {AI_PROVIDER})."
        )

    history = _history[chat_id]
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for role, content in history:
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": f"{user_name}: {user_message}"})

    headers = {"Authorization": f"Bearer {API_KEY}"}
    if AI_PROVIDER == "openrouter":
        # OpenRouter بيحب تحدد مصدر الطلب (اختياري بس بيحسن حدود الاستخدام المجاني)
        headers["HTTP-Referer"] = "https://railway.app"
        headers["X-Title"] = BOT_PERSONA_NAME

    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.8,
        "max_tokens": 800,
    }
    if AI_PROVIDER == "gemini":
        # موديلات Gemini 2.5+ بتستهلك جزء من max_tokens في "تفكير" داخلي قبل الرد،
        # وده بيقطع الرد نص كلمة. reasoning_effort=none بيقفل التفكير ده تمامًا.
        payload["reasoning_effort"] = "none"

    try:
        async with httpx.AsyncClient(timeout=25) as client:
            resp = await client.post(
                API_URL,
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            reply = data["choices"][0]["message"]["content"].strip()
    except httpx.HTTPStatusError as e:
        # بنطبع تفاصيل الخطأ في الـ logs عشان تقدر تشخصه من Railway
        print(f"[AI ERROR] {AI_PROVIDER} رجع status {e.response.status_code}: {e.response.text[:500]}")
        return "معلش، حصل خطأ وأنا بحاول أرد 😅 جرب تاني كمان شوية."
    except Exception as e:
        print(f"[AI ERROR] {AI_PROVIDER}: {type(e).__name__}: {e}")
        return "معلش، حصل خطأ وأنا بحاول أرد 😅 جرب تاني كمان شوية."

    history.append(("user", f"{user_name}: {user_message}"))
    history.append(("assistant", reply))
    return reply
