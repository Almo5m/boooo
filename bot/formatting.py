"""
تحويل ردود الذكاء الاصطناعي (Markdown + LaTeX) لتنسيق HTML بيفهمه تليجرام.

تليجرام مش بيدعم LaTeX ولا Markdown جوه وضع HTML، فبنعمل:
  1) المعادلات ($..$ و $$..$$ و \\[..\\] و \\(..\\)) بتتحول لرموز يونيكود مقروءة.
  2) علامات Markdown (**عريض**، *مائل*، `كود`، عناوين، قوائم) بتتحول لوسوم HTML.
  3) أي نص تاني بيتعمله HTML-escape عشان رمز زي < أو & مايكسرش الرسالة.
"""
import re
import html

GREEK_AND_SYMBOLS = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ", "phi": "φ", "varphi": "φ", "chi": "χ",
    "psi": "ψ", "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ",
    "Pi": "Π", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
    "cdot": "·", "times": "×", "div": "÷", "pm": "±", "mp": "∓", "approx": "≈", "neq": "≠",
    "ne": "≠", "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "equiv": "≡", "propto": "∝",
    "rightarrow": "→", "to": "→", "leftarrow": "←", "Rightarrow": "⇒", "Leftarrow": "⇐",
    "leftrightarrow": "↔", "Leftrightarrow": "⇔", "infty": "∞", "circ": "°", "degree": "°",
    "sum": "Σ", "prod": "Π", "int": "∫", "partial": "∂", "nabla": "∇", "angle": "∠",
    "perp": "⊥", "parallel": "∥", "therefore": "∴", "because": "∵", "in": "∈", "notin": "∉",
    "subset": "⊂", "cup": "∪", "cap": "∩", "ldots": "…", "dots": "…", "cdots": "⋯",
    "quad": " ", "qquad": "  ", "hbar": "ħ", "ell": "ℓ",
}

SUPERSCRIPT = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶", "7": "⁷",
    "8": "⁸", "9": "⁹", "+": "⁺", "-": "⁻", "−": "⁻", "=": "⁼", "(": "⁽", ")": "⁾",
    "n": "ⁿ", "i": "ⁱ",
}
SUBSCRIPT = {
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄", "5": "₅", "6": "₆", "7": "₇",
    "8": "₈", "9": "₉", "+": "₊", "-": "₋", "−": "₋", "=": "₌", "(": "₍", ")": "₎",
    "a": "ₐ", "e": "ₑ", "h": "ₕ", "i": "ᵢ", "j": "ⱼ", "k": "ₖ", "l": "ₗ", "m": "ₘ",
    "n": "ₙ", "o": "ₒ", "p": "ₚ", "r": "ᵣ", "s": "ₛ", "t": "ₜ", "u": "ᵤ", "v": "ᵥ", "x": "ₓ",
}

_SIMPLE_TOKEN = re.compile(r"^[\w.°]+$")


def _wrap_if_complex(s: str) -> str:
    s = s.strip()
    return s if _SIMPLE_TOKEN.match(s) else f"({s})"


def _script(content: str, table: dict, marker: str) -> str:
    content = content.strip()
    if content and all(ch in table for ch in content.replace(" ", "")):
        return "".join(table[ch] for ch in content.replace(" ", ""))
    return f"{marker}{content}" if content.isalnum() else f"{marker}({content})"


def latex_to_text(s: str) -> str:
    """بتحول تعبير LaTeX لنص عادي بيستخدم رموز يونيكود."""
    s = re.sub(r"\\(left|right|big|Big|bigg|Bigg)\s*", "", s)
    s = re.sub(r"\\[,;:!]", " ", s)
    s = s.replace("\\\\", " ")
    s = re.sub(r"\\(?:text|mathrm|mathbf|mathit|textbf|operatorname|mathbb|boldsymbol)\s*\{([^{}]*)\}", r"\1", s)

    for _ in range(10):  # كسور وجذور متداخلة: بنبدأ من الأعمق ونكرر لحد ما مفيش تغيير
        before = s
        s = re.sub(r"\\sqrt\s*\[(\d+)\]\s*\{([^{}]*)\}",
                   lambda m: _script(m.group(1), SUPERSCRIPT, "^") + "√" + _wrap_if_complex(m.group(2)), s)
        s = re.sub(r"\\sqrt\s*\{([^{}]*)\}", lambda m: "√" + _wrap_if_complex(m.group(1)), s)
        s = re.sub(
            r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}",
            lambda m: f"{_wrap_if_complex(latex_to_text(m.group(1)))}/{_wrap_if_complex(latex_to_text(m.group(2)))}",
            s,
        )
        if s == before:
            break

    s = re.sub(r"\\([%&#_$])", r"\1", s)
    s = re.sub(r"\\([A-Za-z]+)", lambda m: GREEK_AND_SYMBOLS.get(m.group(1), m.group(1)), s)

    s = re.sub(r"\^\s*\{([^{}]*)\}", lambda m: _script(m.group(1), SUPERSCRIPT, "^"), s)
    s = re.sub(r"\^\s*([A-Za-z0-9+\-−])", lambda m: _script(m.group(1), SUPERSCRIPT, "^"), s)
    s = re.sub(r"_\s*\{([^{}]*)\}", lambda m: _script(m.group(1), SUBSCRIPT, "_"), s)
    s = re.sub(r"_\s*([A-Za-z0-9])", lambda m: _script(m.group(1), SUBSCRIPT, "_"), s)

    s = s.replace("{", "").replace("}", "")
    return re.sub(r"[ \t]{2,}", " ", s).strip()


def format_ai_reply(text: str) -> str:
    """بتحول رد الـ AI الخام لنص HTML آمن وجاهز للإرسال في تليجرام."""
    placeholders: list[str] = []

    def stash(rendered: str) -> str:
        placeholders.append(rendered)
        return f"\x00{len(placeholders) - 1}\x00"

    # 1) كتل الكود (قبل أي حاجة تانية عشان محتواها مايتغيرش)
    text = re.sub(
        r"```[^\n]*\n?(.*?)```",
        lambda m: stash(f"<pre>{html.escape(m.group(1).strip(chr(10)), quote=False)}</pre>"),
        text, flags=re.DOTALL,
    )
    text = re.sub(r"`([^`\n]+)`", lambda m: stash(f"<code>{html.escape(m.group(1), quote=False)}</code>"), text)

    # 2) المعادلات: كتل مستقلة الأول، وبعدين الداخلية
    def block_math(m):
        return "\n" + stash(f"<code>{html.escape(latex_to_text(m.group(1)), quote=False)}</code>") + "\n"

    def inline_math(m):
        return stash(f"<i>{html.escape(latex_to_text(m.group(1)), quote=False)}</i>")

    text = re.sub(r"\$\$(.+?)\$\$", block_math, text, flags=re.DOTALL)
    text = re.sub(r"\\\[(.+?)\\\]", block_math, text, flags=re.DOTALL)
    text = re.sub(r"\\\((.+?)\\\)", inline_math, text, flags=re.DOTALL)
    text = re.sub(r"\$(?!\s)([^$\n]+?)(?<!\s)\$", inline_math, text)

    # 3) escape لباقي النص
    text = html.escape(text, quote=False)

    # 4) Markdown → HTML
    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[\*\-]\s+", "• ", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*-{3,}\s*$", "──────────", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*([^\n*]+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__([^\n_]+?)__", r"<b>\1</b>", text)
    text = re.sub(r"(?<![\*\w])\*([^\n*]+?)\*(?![\*\w])", r"<i>\1</i>", text)
    text = re.sub(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', text)

    # 5) رجّع العناصر المحجوزة
    for _ in range(3):
        text = re.sub(r"\x00(\d+)\x00", lambda m: placeholders[int(m.group(1))], text)

    return re.sub(r"\n{3,}", "\n\n", text).strip()


def to_plain(formatted_html: str) -> str:
    """نسخة نص عادي من الرد المنسّق (للاحتياطي لو تليجرام رفض الـ HTML)."""
    return html.unescape(re.sub(r"</?[a-z][^>]*>", "", formatted_html))
