import os
import requests
import asyncio
from datetime import datetime, date
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_PROBLEMS_DB_ID = "88be90a6768e4c9da2819565e1a69f62"
ADMIN_CHAT_ID = 188483198
SHEETS_ID = "1SOKanELXstuJ0W75fsWpbmYRibk-mWkHLF5XHz4KHYc"

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json"
}

# Храним уже обработанные строки чтобы не дублировать
processed_rows = set()

def get_csi_rows():
    """Читает все строки из Google Sheets CSI+NPS"""
    url = f"https://docs.google.com/spreadsheets/d/{SHEETS_ID}/gviz/tq?tqx=out:csv&sheet=Sheet1"
    res = requests.get(url)
    if res.status_code != 200:
        return []

    lines = res.text.strip().split("\n")
    rows = []
    for line in lines[1:]:
        cols = [c.strip().strip('"') for c in line.split(",")]
        if len(cols) >= 7 and cols[0]:
            rows.append(cols)
    return rows

def find_worst_score(cols):
    """Находит худшую оценку и категорию"""
    categories = [
        (1, "Общее"),
        (2, "Кальян"),
        (3, "Напитки"),
        (4, "Еда"),
        (5, "Команда"),
    ]
    worst_score = 10
    worst_cat = "Общее"
    for idx, cat in categories:
        try:
            score = float(cols[idx].replace(",", "."))
            if score < worst_score:
                worst_score = score
                worst_cat = cat
        except:
            pass
    return worst_score, worst_cat

def create_notion_problem(cols, worst_score, worst_cat):
    """Создаёт задачу в Notion"""
    comment = cols[7].strip() if len(cols) > 7 else ""
    visit_date = cols[0].split(" ")[0] if cols[0] else date.today().isoformat()

    # Конвертируем дату из формата DD.MM.YYYY в YYYY-MM-DD
    try:
        parts = visit_date.split(".")
        if len(parts) == 3:
            visit_date = f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"
    except:
        visit_date = date.today().isoformat()

    page_data = {
        "parent": {"database_id": NOTION_PROBLEMS_DB_ID},
        "properties": {
            "Проблема": {
                "title": [{"text": {"content": f"Низкая оценка — {worst_cat} ({worst_score}/10)"}}]
            },
            "Категория": {"select": {"name": worst_cat}},
            "Оценка гостя": {"number": worst_score},
            "Комментарий гостя": {
                "rich_text": [{"text": {"content": comment or "Без комментария"}}]
            },
            "Дата отзыва": {"date": {"start": visit_date}},
            "Статус": {"select": {"name": "Новая"}}
        }
    }

    res = requests.post(
        "https://api.notion.com/v1/pages",
        headers=NOTION_HEADERS,
        json=page_data
    )
    return res.status_code == 200

async def check_reviews(context):
    """Проверяет новые отзывы каждый час"""
    rows = get_csi_rows()

    for cols in rows:
        row_id = cols[0]  # Временная метка как уникальный ключ
        if row_id in processed_rows:
            continue

        processed_rows.add(row_id)
        worst_score, worst_cat = find_worst_score(cols)

        # Уведомляем если оценка ниже 7
        if worst_score < 7:
            comment = cols[7].strip() if len(cols) > 7 else ""

            # Все оценки
            scores_text = (
                f"😊 Вечер: {cols[1]}/10\n"
                f"🪄 Кальян: {cols[2]}/10\n"
                f"🍹 Напитки: {cols[3]}/10\n"
                f"🍽 Еда: {cols[4]}/10\n"
                f"👨‍💼 Команда: {cols[5]}/10\n"
                f"🎯 NPS: {cols[6]}/10"
            )

            text = (
                f"🚨 *Негативный отзыв!*\n\n"
                f"{scores_text}\n"
                f"👎 Проблемная категория: *{worst_cat}* ({worst_score}/10)\n"
            )
            if comment:
                text += f"💬 Комментарий: _{comment}_\n"

            # Создаём задачу в Notion
            created = create_notion_problem(cols, worst_score, worst_cat)
            if created:
                text += f"\n✅ Задача создана в Notion — не забудь указать причину и срок!"
            else:
                text += f"\n⚠️ Не удалось создать задачу в Notion."

            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=text,
                parse_mode="Markdown"
            )

async def problems(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает открытые проблемы из Notion"""
    res = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_PROBLEMS_DB_ID}/query",
        headers=NOTION_HEADERS,
        json={
            "filter": {
                "or": [
                    {"property": "Статус", "select": {"equals": "Новая"}},
                    {"property": "Статус", "select": {"equals": "В работе"}}
                ]
            },
            "sorts": [{"property": "Дата отзыва", "direction": "descending"}]
        }
    )

    results = res.json().get("results", [])

    if not results:
        await update.message.reply_text("✅ Открытых проблем нет!")
        return

    text = f"⚠️ *Открытые проблемы ({len(results)}):*\n\n"

    for p in results:
        props = p["properties"]

        title = props["Проблема"]["title"][0]["plain_text"] if props["Проблема"]["title"] else "—"
        status = props["Статус"]["select"]["name"] if props["Статус"]["select"] else "—"
        category = props["Категория"]["select"]["name"] if props["Категория"]["select"] else "—"
        score = props["Оценка гостя"]["number"] if props["Оценка гостя"]["number"] else "—"
        deadline = props["Срок исполнения"]["date"]["start"] if props["Срок исполнения"]["date"] else "не указан"
        responsible = props["Ответственный"]["rich_text"][0]["plain_text"] if props["Ответственный"]["rich_text"] else "не назначен"

        status_icon = "🔴" if status == "Новая" else "🟡"
        text += (
            f"{status_icon} *{category}* — {score}/10\n"
            f"   👤 {responsible} · 📅 {deadline}\n\n"
        )

    await update.message.reply_text(text, parse_mode="Markdown")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚨 *Бот мониторинга отзывов*\n\n"
        "Автоматически проверяю Google Forms каждый час.\n"
        "При оценке ниже 7 — сразу пришлю уведомление.\n\n"
        "/problems — открытые проблемы",
        parse_mode="Markdown"
    )

app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("problems", problems))

# Проверка каждый час (3600 секунд)
app.job_queue.run_repeating(check_reviews, interval=3600, first=10)

app.run_polling()
