from __future__ import annotations

from typing import Any


SUPPORTED_LANGUAGES = ("ru", "en", "kg")
DEFAULT_LANGUAGE = "ru"


def normalize_language(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return DEFAULT_LANGUAGE
    candidate = raw.replace("_", "-").split("-", 1)[0]
    if candidate in SUPPORTED_LANGUAGES:
        return candidate
    return DEFAULT_LANGUAGE


def pick_language(*, query_lang: Any = None, accept_language: Any = None) -> str:
    if query_lang:
        return normalize_language(query_lang)

    if accept_language:
        # Supports values like: "ru-RU,ru;q=0.9,en;q=0.8"
        items = [part.strip() for part in str(accept_language).split(",") if part.strip()]
        for item in items:
            lang_part = item.split(";", 1)[0].strip()
            normalized = normalize_language(lang_part)
            if normalized in SUPPORTED_LANGUAGES:
                return normalized

    return DEFAULT_LANGUAGE


def _catalog() -> dict[str, dict[str, str]]:
    return {
        "validation_error": {
            "ru": "Ошибка валидации.",
            "en": "Validation error.",
            "kg": "Текшерүү катасы.",
        },
        "permission_denied": {
            "ru": "Недостаточно прав.",
            "en": "Insufficient permissions.",
            "kg": "Укук жетишсиз.",
        },
        "authentication_required": {
            "ru": "Требуется авторизация.",
            "en": "Authentication required.",
            "kg": "Авторизация талап кылынат.",
        },
        "not_found": {
            "ru": "Ресурс не найден.",
            "en": "Resource not found.",
            "kg": "Ресурс табылган жок.",
        },
        "error": {
            "ru": "Ошибка.",
            "en": "Error.",
            "kg": "Ката.",
        },
        "user_not_found": {
            "ru": "Пользователь не найден.",
            "en": "User not found.",
            "kg": "Колдонуучу табылган жок.",
        },
        "invalid_role": {
            "ru": "Некорректная роль.",
            "en": "Invalid role.",
            "kg": "Туура эмес роль.",
        },
        "department_mismatch": {
            "ru": "Пользователь из другого отдела.",
            "en": "User belongs to another department.",
            "kg": "Колдонуучу башка бөлүмдө.",
        },
        "assignee_not_found": {
            "ru": "Исполнитель не найден.",
            "en": "Assignee not found.",
            "kg": "Аткаруучу табылган жок.",
        },
        "column_not_found": {
            "ru": "Колонка не найдена.",
            "en": "Column not found.",
            "kg": "Тилке табылган жок.",
        },
        "role_superadmin": {
            "ru": "Суперадмин",
            "en": "Superadmin",
            "kg": "Суперадмин",
        },
        "role_administrator": {
            "ru": "Администратор",
            "en": "Administrator",
            "kg": "Администратор",
        },
        "role_admin": {
            "ru": "Руководитель отдела",
            "en": "Department head",
            "kg": "Бөлүм башчысы",
        },
        "role_department_head": {
            "ru": "Руководитель отдела",
            "en": "Department head",
            "kg": "Бөлүм башчысы",
        },
        "role_projectmanager": {
            "ru": "Тимлид",
            "en": "Team lead",
            "kg": "Тимлид",
        },
        "role_employee": {
            "ru": "Сотрудник",
            "en": "Employee",
            "kg": "Кызматкер",
        },
        "role_intern": {
            "ru": "Стажер",
            "en": "Intern",
            "kg": "Таҗрыйбадан өтүүчү",
        },
        "status_calculated": {
            "ru": "Рассчитано",
            "en": "Calculated",
            "kg": "Эсептелген",
        },
        "status_paid": {
            "ru": "Выплачено",
            "en": "Paid",
            "kg": "Төлөнгөн",
        },
        "status_delayed": {
            "ru": "Задержано",
            "en": "Delayed",
            "kg": "Кечиккен",
        },
        "status_draft": {
            "ru": "Черновик",
            "en": "Draft",
            "kg": "Долбоор",
        },
        "status_locked": {
            "ru": "Заблокировано",
            "en": "Locked",
            "kg": "Бөгөттөлгөн",
        },
        "status_pending": {
            "ru": "Ожидает",
            "en": "Pending",
            "kg": "Күтүүдө",
        },
        "status_sent": {
            "ru": "Отправлен",
            "en": "Sent",
            "kg": "Жөнөтүлгөн",
        },
        "status_accepted": {
            "ru": "Принят",
            "en": "Accepted",
            "kg": "Кабыл алынды",
        },
        "status_revision": {
            "ru": "На доработке",
            "en": "Revision required",
            "kg": "Кайра иштетүүгө",
        },
        "status_rejected": {
            "ru": "Отклонен",
            "en": "Rejected",
            "kg": "Четке кагылды",
        },
        "status_clarification_requested": {
            "ru": "Запрошено уточнение",
            "en": "Clarification requested",
            "kg": "Тактоо суралды",
        },
        "status_in_progress": {
            "ru": "В работе",
            "en": "In progress",
            "kg": "Аткарылууда",
        },
        "status_new": {
            "ru": "Новый",
            "en": "New",
            "kg": "Жаңы",
        },
        "status_approved": {
            "ru": "Одобрен",
            "en": "Approved",
            "kg": "Бекитилди",
        },
    }


def tr(key: str, lang: str | None = None, fallback: str = "") -> str:
    language = normalize_language(lang)
    values = _catalog().get(key, {})
    return values.get(language) or values.get(DEFAULT_LANGUAGE) or fallback or key


def role_label(role_code: str, lang: str | None = None) -> str:
    key = f"role_{str(role_code or '').strip().lower()}"
    return tr(key, lang=lang, fallback=str(role_code or ""))


def status_label(status_code: str, lang: str | None = None) -> str:
    key = f"status_{str(status_code or '').strip().lower()}"
    return tr(key, lang=lang, fallback=str(status_code or ""))


def request_language(request) -> str:
    if not request:
        return DEFAULT_LANGUAGE
    return normalize_language(getattr(request, "LANGUAGE_CODE", DEFAULT_LANGUAGE))
