from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

BUILT_IN_TOPICS: dict[str, str] = {
    "python": "Python",
    "sql": "SQL",
    "git": "Git",
    "linux": "Linux",
    "algorithms": "Алгоритмы",
    "django": "Django",
    "fastapi": "FastAPI",
}

ANSWER_LETTERS = ["А", "Б", "В", "Г"]

MIX_FORMATS: dict[str, str] = {
    "closed": "Закрытые (тест)",
    "open": "Открытые",
    "self_eval": "Правильно / Неправильно",
}


def main_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Загрузить материал", callback_data="upload"))
    builder.row(InlineKeyboardButton(text="Встроенные темы", callback_data="topics"))
    builder.row(InlineKeyboardButton(text="Мои материалы", callback_data="my_materials"))
    builder.row(InlineKeyboardButton(text="Статистика", callback_data="stats"))
    return builder.as_markup()


def topics_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key, label in BUILT_IN_TOPICS.items():
        builder.row(InlineKeyboardButton(text=label, callback_data=f"topic:{key}"))
    builder.row(InlineKeyboardButton(text="Назад", callback_data="back_to_menu"))
    return builder.as_markup()


def materials_keyboard(documents: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for doc in documents:
        builder.row(InlineKeyboardButton(
            text=doc.filename[:40], callback_data=f"material:{doc.id}",
        ))
    builder.row(InlineKeyboardButton(text="Назад", callback_data="back_to_menu"))
    return builder.as_markup()


def mode_selection_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Закрытые вопросы (тест)", callback_data="mode:closed"))
    builder.row(InlineKeyboardButton(text="Открытые вопросы", callback_data="mode:open"))
    builder.row(InlineKeyboardButton(text="Правильно / Неправильно", callback_data="mode:self_eval"))
    builder.row(InlineKeyboardButton(text="Смешанный режим", callback_data="mode:mixed"))
    builder.row(InlineKeyboardButton(text="Марафон — все вопросы", callback_data="mode:marathon"))
    builder.row(InlineKeyboardButton(text="Назад", callback_data="back_to_menu"))
    return builder.as_markup()


def mixed_formats_keyboard(selected: list[str]) -> InlineKeyboardMarkup:
    """Клавиатура выбора форматов для смешанного режима. selected — уже выбранные ключи."""
    builder = InlineKeyboardBuilder()
    for key, label in MIX_FORMATS.items():
        mark = "[+]" if key in selected else "[ ]"
        builder.row(InlineKeyboardButton(
            text=f"{mark} {label}", callback_data=f"mix_toggle:{key}",
        ))
    builder.row(InlineKeyboardButton(text="Подтвердить", callback_data="mix_confirm"))
    builder.row(InlineKeyboardButton(text="Назад", callback_data="mix_back"))
    return builder.as_markup()


def count_selection_keyboard(available: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    btns = [
        InlineKeyboardButton(text=str(n), callback_data=f"count:{n}")
        for n in (5, 10, 15, 20) if n <= available
    ]
    if not btns:
        btns = [InlineKeyboardButton(
            text=f"Все {available}", callback_data=f"count:{available}"
        )]
    builder.row(*btns)
    builder.row(InlineKeyboardButton(text="Назад", callback_data="setup_back_to_mode"))
    return builder.as_markup()


def options_count_keyboard() -> InlineKeyboardMarkup:
    """Выбор количества вариантов ответа для закрытых вопросов (2 / 3 / 4)."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="2 варианта", callback_data="opts:2"),
        InlineKeyboardButton(text="3 варианта", callback_data="opts:3"),
        InlineKeyboardButton(text="4 варианта", callback_data="opts:4"),
    )
    builder.row(InlineKeyboardButton(text="Назад", callback_data="setup_back_to_mode"))
    return builder.as_markup()


def tf_keyboard(question_id: int) -> InlineKeyboardMarkup:
    """Кнопки для режима 'Верно/Неверно' — утверждения True/False."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Верно", callback_data=f"tf:{question_id}:true"),
        InlineKeyboardButton(text="Неверно", callback_data=f"tf:{question_id}:false"),
    )
    builder.row(InlineKeyboardButton(text="Завершить тест", callback_data="cancel_quiz"))
    return builder.as_markup()


def answer_options_keyboard(options: list, question_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for i, opt in enumerate(options[:4]):
        builder.row(InlineKeyboardButton(
            text=f"{ANSWER_LETTERS[i]}) {opt.text[:50]}",
            callback_data=f"answer:{question_id}:{opt.id}",
        ))
    builder.row(InlineKeyboardButton(text="Завершить тест", callback_data="cancel_quiz"))
    return builder.as_markup()


def self_eval_keyboard(question_id: int) -> InlineKeyboardMarkup:
    """Самооценка для открытых вопросов (после показа эталонного ответа)."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Правильно", callback_data=f"self_eval:{question_id}:correct"),
        InlineKeyboardButton(text="Неправильно", callback_data=f"self_eval:{question_id}:wrong"),
    )
    builder.row(InlineKeyboardButton(text="Завершить тест", callback_data="cancel_quiz"))
    return builder.as_markup()


def know_dontknow_keyboard(question_id: int) -> InlineKeyboardMarkup:
    """Режим 'Правильно / Неправильно': пользователь сам оценивает знание вопроса."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Знаю", callback_data=f"know:{question_id}:yes"),
        InlineKeyboardButton(text="Не знаю", callback_data=f"know:{question_id}:no"),
    )
    builder.row(InlineKeyboardButton(text="Завершить тест", callback_data="cancel_quiz"))
    return builder.as_markup()


def next_question_keyboard() -> InlineKeyboardMarkup:
    """Показывается после ответа 'Не знаю' вместе с эталонным ответом."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Следующий вопрос", callback_data="next_question"))
    builder.row(InlineKeyboardButton(text="Завершить тест", callback_data="cancel_quiz"))
    return builder.as_markup()


def cancel_quiz_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Завершить тест", callback_data="cancel_quiz"))
    return builder.as_markup()
