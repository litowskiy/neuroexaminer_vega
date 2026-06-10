"""
Генерация PDF-отчёта по результатам группы.

Шрифт: DejaVu Sans (поддержка кириллицы). На сервере ставится пакетом
fonts-dejavu-core, локально ищем по списку типичных путей.
"""
import io
import logging
from datetime import datetime
from pathlib import Path

from fpdf import FPDF

logger = logging.getLogger(__name__)

_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
]
_FONT_BOLD_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
]


def _find_font(paths: list[str]) -> str | None:
    for p in paths:
        if Path(p).exists():
            return p
    return None


def build_group_report(
    group_name: str,
    teacher_name: str,
    assignments: list[dict],
) -> bytes:
    """
    assignments: [
      {
        "title": str,
        "mode": str,
        "rows": [{"full_name": str, "status": str, "correct": int|None, "total": int|None}],
      }
    ]
    """
    pdf = FPDF()
    font_path = _find_font(_FONT_PATHS)
    if font_path:
        pdf.add_font("Main", "", font_path)
        bold_path = _find_font(_FONT_BOLD_PATHS) or font_path
        pdf.add_font("Main", "B", bold_path)
        font = "Main"
    else:
        logger.warning("Unicode font not found, PDF may render Cyrillic incorrectly.")
        font = "Helvetica"

    pdf.add_page()
    pdf.set_font(font, "B", 16)
    pdf.cell(0, 10, f"Отчёт по группе «{group_name}»", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(font, "", 10)
    pdf.cell(0, 6, f"Преподаватель: {teacher_name}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"Сформирован: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    if not assignments:
        pdf.set_font(font, "", 12)
        pdf.cell(0, 8, "Тестов в группе пока нет.", new_x="LMARGIN", new_y="NEXT")

    for asg in assignments:
        pdf.set_font(font, "B", 13)
        pdf.cell(0, 9, f"Тест: {asg['title']}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font(font, "", 9)
        pdf.cell(0, 5, f"Режим: {asg['mode']}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)

        pdf.set_font(font, "B", 10)
        pdf.cell(90, 7, "ФИО студента", border=1)
        pdf.cell(50, 7, "Статус", border=1)
        pdf.cell(40, 7, "Результат", border=1, new_x="LMARGIN", new_y="NEXT")

        pdf.set_font(font, "", 10)
        for row in asg["rows"]:
            result = ""
            if row["correct"] is not None and row["total"]:
                pct = round(row["correct"] / row["total"] * 100)
                result = f"{row['correct']}/{row['total']} ({pct}%)"
            pdf.cell(90, 7, row["full_name"][:45], border=1)
            pdf.cell(50, 7, row["status"], border=1)
            pdf.cell(40, 7, result, border=1, new_x="LMARGIN", new_y="NEXT")

        pdf.ln(5)

    buf = io.BytesIO()
    pdf.output(buf)
    return buf.getvalue()


def _make_pdf() -> tuple[FPDF, str]:
    pdf = FPDF()
    font_path = _find_font(_FONT_PATHS)
    if font_path:
        pdf.add_font("Main", "", font_path)
        bold_path = _find_font(_FONT_BOLD_PATHS) or font_path
        pdf.add_font("Main", "B", bold_path)
        font = "Main"
    else:
        logger.warning("Unicode font not found, PDF may render Cyrillic incorrectly.")
        font = "Helvetica"
    return pdf, font


LETTERS = ["А", "Б", "В", "Г"]


def build_questions_pdf(doc_title: str, questions: list[dict]) -> bytes:
    """
    Вопросы материала одним PDF.
    questions: [
      {
        "text": str,
        "options": [(text, is_correct)],     # закрытый вопрос
        "reference_answer": str | None,      # открытый эталон
        "tf_answer": bool | None,            # утверждение Верно/Неверно
      }
    ]
    """
    pdf, font = _make_pdf()
    pdf.add_page()
    pdf.set_font(font, "B", 16)
    pdf.multi_cell(0, 8, f"Вопросы по материалу «{doc_title}»", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(font, "", 10)
    pdf.cell(0, 6, f"Сформирован: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    for i, q in enumerate(questions, 1):
        pdf.set_font(font, "B", 11)
        pdf.multi_cell(0, 7, f"{i}. {q['text']}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font(font, "", 10)

        if q.get("tf_answer") is not None:
            answer = "Верно" if q["tf_answer"] else "Неверно"
            pdf.multi_cell(0, 6, f"Тип: Верно/Неверно. Правильный ответ: {answer}", new_x="LMARGIN", new_y="NEXT")
        elif q.get("options"):
            for j, (opt_text, is_correct) in enumerate(q["options"][:4]):
                mark = "  [ПРАВИЛЬНЫЙ]" if is_correct else ""
                pdf.multi_cell(0, 6, f"   {LETTERS[j]}) {opt_text}{mark}", new_x="LMARGIN", new_y="NEXT")
            if q.get("reference_answer"):
                pdf.multi_cell(0, 6, f"Эталонный ответ: {q['reference_answer']}", new_x="LMARGIN", new_y="NEXT")
        elif q.get("reference_answer"):
            pdf.multi_cell(0, 6, f"Эталонный ответ: {q['reference_answer']}", new_x="LMARGIN", new_y="NEXT")

        pdf.ln(3)

    buf = io.BytesIO()
    pdf.output(buf)
    return buf.getvalue()
