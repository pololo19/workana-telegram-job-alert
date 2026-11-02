from __future__ import annotations

import re

_PUBLISHED_PREFIX_RE = re.compile(
    r"^(?:Publicado|Posted|Publicado em)\s*:\s*",
    re.IGNORECASE,
)

_RELATIVE_DATE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^hace\s+instantes$", re.I), "Just Now"),
    (re.compile(r"^há\s+instantes$", re.I), "Just Now"),
    (re.compile(r"^agora\s+mesmo$", re.I), "Just Now"),
    (re.compile(r"^ayer$", re.I), "yesterday"),
    (re.compile(r"^ontem$", re.I), "yesterday"),
    (re.compile(r"^anteayer$", re.I), "day before yesterday"),
    (re.compile(r"^hace\s+(\d+)\s+minutos?$", re.I), r"\1 minutes ago"),
    (re.compile(r"^há\s+(\d+)\s+minutos?$", re.I), r"\1 minutes ago"),
    (re.compile(r"^hace\s+(\d+)\s+horas?$", re.I), r"\1 hours ago"),
    (re.compile(r"^há\s+(\d+)\s+horas?$", re.I), r"\1 hours ago"),
    (re.compile(r"^hace\s+(\d+)\s+d[ií]as?$", re.I), r"\1 days ago"),
    (re.compile(r"^há\s+(\d+)\s+dias?$", re.I), r"\1 days ago"),
    (re.compile(r"^hace\s+(\d+)\s+semanas?$", re.I), r"\1 weeks ago"),
    (re.compile(r"^há\s+(\d+)\s+semanas?$", re.I), r"\1 weeks ago"),
    (re.compile(r"^hace\s+(\d+)\s+meses?$", re.I), r"\1 months ago"),
    (re.compile(r"^há\s+(\d+)\s+meses?$", re.I), r"\1 months ago"),
    (re.compile(r"^hace\s+casi\s+una\s+hora$", re.I), "About an hour ago"),
    (re.compile(r"^há\s+cerca\s+de\s+uma\s+hora$", re.I), "About an hour ago"),
    (re.compile(r"^hace\s+casi\s+un\s+minuto$", re.I), "About a minute ago"),
)

_LAST_REPLY_PREFIX_RE = re.compile(
    r"^(?:Last reply|Última respuesta|Ultima respuesta|Última mensagem)\s*:\s*",
    re.IGNORECASE,
)

_BUDGET_PREFIX_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^Más de\s", re.I), "More than "),
    (re.compile(r"^Menos de\s", re.I), "Less than "),
    (re.compile(r"^Desde\s", re.I), "From "),
    (re.compile(r"^Hasta\s", re.I), "Up to "),
    (re.compile(r"^Mais de\s", re.I), "More than "),
    (re.compile(r"^Menos de\s", re.I), "Less than "),
    (re.compile(r"^Entre\s", re.I), "Between "),
)

_COUNTRY_NAMES: dict[str, str] = {
    "brasil": "Brazil",
    "españa": "Spain",
    "espana": "Spain",
    "méxico": "Mexico",
    "mexico": "Mexico",
    "perú": "Peru",
    "peru": "Peru",
}

_MONTH_NAMES: dict[str, str] = {
    "enero": "January",
    "febrero": "February",
    "marzo": "March",
    "abril": "April",
    "mayo": "May",
    "junio": "June",
    "julio": "July",
    "agosto": "August",
    "septiembre": "September",
    "setiembre": "September",
    "octubre": "October",
    "noviembre": "November",
    "diciembre": "December",
    "janeiro": "January",
    "fevereiro": "February",
    "março": "March",
    "marco": "March",
    "abril": "April",
    "maio": "May",
    "junho": "June",
    "julho": "July",
    "agosto": "August",
    "setembro": "September",
    "outubro": "October",
    "novembro": "November",
    "dezembro": "December",
}

STAT_LABEL_ALIASES: dict[str, str] = {
    "published projects": "published projects",
    "projects paid": "projects paid",
    "bids": "bids",
    "interested freelancers": "interested freelancers",
    "proyectos publicados": "published projects",
    "proyectos pagados": "projects paid",
    "propuestas": "bids",
    "ofertas": "bids",
    "freelancers interesados": "interested freelancers",
    "projetos publicados": "published projects",
    "projetos pagos": "projects paid",
    "lances": "bids",
    "propostas": "bids",
    "freelancers interessados": "interested freelancers",
}


def normalize_stat_label(label: str) -> str:
    key = label.strip().lower()
    return STAT_LABEL_ALIASES.get(key, key)


def english_relative_time(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    text = _LAST_REPLY_PREFIX_RE.sub("", text).strip()
    if re.match(r"^Posted:\s", text, re.I):
        text = text.split(":", 1)[1].strip()
    text = _PUBLISHED_PREFIX_RE.sub("", text).strip()
    translated = _translate_relative_date(text)
    if translated:
        return _title_relative_date(translated)
    if re.match(r"^\d+\s+\w+\s+ago", text, re.I):
        return text
    if text.lower() in ("just now", "yesterday", "day before yesterday"):
        return _title_relative_date(text)
    return text


def english_published(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return "—"
    relative = english_relative_time(text)
    if relative and relative != text:
        return f"Posted: {relative}"
    if re.match(r"^Posted:\s", text, re.I):
        return text
    body = _PUBLISHED_PREFIX_RE.sub("", text).strip()
    relative = english_relative_time(body)
    if relative:
        return f"Posted: {relative}"
    return text


def english_last_reply(raw: str) -> str:
    relative = english_relative_time(raw)
    if not relative:
        return "Last reply: —"
    return f"Last reply: {relative}"


def english_budget(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return "—"
    for pattern, replacement in _BUDGET_PREFIX_RULES:
        if pattern.search(text):
            return pattern.sub(replacement, text, count=1)
    return text


def english_country(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return "—"
    mapped = _COUNTRY_NAMES.get(text.lower())
    return mapped if mapped else text


def english_member_since(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    if re.match(r"^(?:January|February|March|April|May|June|July|August|"
                r"September|October|November|December)\b", text, re.I):
        return text
    result = text
    for src, dst in _MONTH_NAMES.items():
        result = re.sub(rf"\b{src}\b", dst, result, flags=re.I)
    result = re.sub(r"\bde\b", "", result, flags=re.I)
    return re.sub(r"\s+", " ", result).strip()


def _translate_relative_date(text: str) -> str:
    candidate = text.strip()
    for pattern, replacement in _RELATIVE_DATE_RULES:
        match = pattern.match(candidate)
        if match:
            return pattern.sub(replacement, candidate, count=1)
    return ""


def _title_relative_date(text: str) -> str:
    if not text:
        return text
    if text[0].isdigit():
        return text
    return text[0].upper() + text[1:]
