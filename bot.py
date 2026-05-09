"""
Crazy Time Telegram Bot
Парсит данные с tracksino.com и ведёт статистику по всем событиям
"""

import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)
from config import BOT_TOKEN, CHAT_IDS
from database import Database
from analyzer import Analyzer
from parser import TracksParser

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

db = Database()
analyzer = Analyzer(db)
parser = TracksParser(db, analyzer)


# ── КОМАНДЫ ──────────────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "🎡 *Crazy Time Stats Bot*\n\n"
        "Я слежу за всеми спинами колеса и считаю статистику.\n\n"
        "*Команды:*\n"
        "/stats — текущая статистика невыпадений\n"
        "/monthly — статистика за текущий месяц\n"
        "/event [название] — детальная статистика по событию\n"
        "/alerts — настройки уведомлений\n"
        "/setalert [событие] [порог] — установить порог\n"
        "/status — статус парсера\n"
        "/help — справка\n\n"
        "Доступные события: `1`, `2`, `5`, `10`, `CoinFlip`, `Pachinko`, `CashHunt`, `CrazyTime`"
    )
    await update.message.reply_text(text, parse_mode='Markdown')


async def stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Текущее количество спинов без каждого события"""
    rows = analyzer.current_absence_all()
    thresholds = db.get_all_thresholds()

    lines = ["📊 *Текущие промежутки (спинов без события):*\n"]
    for event, absence in rows.items():
        threshold = thresholds.get(event, 300)
        emoji = "🔴" if absence >= threshold else ("🟡" if absence >= threshold * 0.7 else "🟢")
        bar_filled = min(int(absence / threshold * 10), 10)
        bar = "█" * bar_filled + "░" * (10 - bar_filled)
        lines.append(f"{emoji} *{event}*\n   [{bar}] {absence} спинов (порог: {threshold})")

    total = db.get_total_spins()
    lines.append(f"\n📈 Всего спинов в БД: *{total}*")
    await update.message.reply_text("\n".join(lines), parse_mode='Markdown')


async def monthly(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Статистика за текущий месяц"""
    stats_data = analyzer.monthly_stats()

    if not stats_data:
        await update.message.reply_text("⚠️ Недостаточно данных за текущий месяц.")
        return

    lines = ["📅 *Статистика за текущий месяц:*\n"]
    lines.append(f"{'Событие':<12} {'Кол-во':>6} {'Мин':>5} {'Макс':>6} {'Среднее':>8}\n{'─'*45}")

    for event, s in stats_data.items():
        lines.append(
            f"*{event:<12}* {s['count']:>6}   {s['min']:>5}   {s['max']:>6}   {s['avg']:>7.1f}"
        )

    await update.message.reply_text(
        "```\n" + "\n".join(lines) + "\n```",
        parse_mode='Markdown'
    )


async def event_detail(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Детальная статистика по конкретному событию"""
    EVENTS = ['1', '2', '5', '10', 'CoinFlip', 'Pachinko', 'CashHunt', 'CrazyTime']

    if not ctx.args:
        keyboard = [[InlineKeyboardButton(e, callback_data=f"event_{e}")] for e in EVENTS]
        await update.message.reply_text(
            "Выберите событие:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    event_name = ctx.args[0]
    normalized = normalize_event(event_name)
    if not normalized:
        await update.message.reply_text(f"❌ Неизвестное событие: {event_name}")
        return

    await send_event_stats(update.message, normalized)


async def event_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    event_name = query.data.replace("event_", "")
    await send_event_stats(query.message, event_name)


async def send_event_stats(message, event: str):
    s = analyzer.event_detail_stats(event)
    if not s:
        await message.reply_text(f"⚠️ Нет данных по событию *{event}*", parse_mode='Markdown')
        return

    text = (
        f"🎯 *{event}* — полная статистика\n\n"
        f"📊 *За всё время:*\n"
        f"  Всего выпадений: *{s['total_hits']}*\n"
        f"  Минимальный промежуток: *{s['all_min']}* спинов\n"
        f"  Максимальный промежуток: *{s['all_max']}* спинов\n"
        f"  Среднее между выпадениями: *{s['all_avg']:.1f}* спинов\n\n"
        f"📅 *За текущий месяц:*\n"
        f"  Выпадений: *{s['month_hits']}*\n"
        f"  Мин. промежуток: *{s['month_min']}* спинов\n"
        f"  Макс. промежуток: *{s['month_max']}* спинов\n"
        f"  Среднее: *{s['month_avg']:.1f}* спинов\n\n"
        f"⏱ *Сейчас не выпадает:* *{s['current_absence']}* спинов"
    )

    # Топ-5 самых длинных промежутков
    if s.get('top_gaps'):
        text += "\n\n🏆 *Топ-5 максимальных промежутков:*\n"
        for i, gap in enumerate(s['top_gaps'], 1):
            text += f"  {i}. {gap} спинов\n"

    await message.reply_text(text, parse_mode='Markdown')


async def alerts_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Показать текущие пороги уведомлений"""
    thresholds = db.get_all_thresholds()
    lines = ["🔔 *Текущие пороги уведомлений:*\n"]
    for event, threshold in thresholds.items():
        lines.append(f"  *{event}*: {threshold} спинов")
    lines.append("\n_Изменить: /setalert [событие] [число]_")
    lines.append("_Пример: /setalert CrazyTime 250_")
    await update.message.reply_text("\n".join(lines), parse_mode='Markdown')


async def set_alert(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Установить порог уведомления для события"""
    if len(ctx.args) < 2:
        await update.message.reply_text(
            "Использование: /setalert [событие] [порог]\n"
            "Пример: /setalert CrazyTime 250"
        )
        return

    event_name = normalize_event(ctx.args[0])
    if not event_name:
        await update.message.reply_text(f"❌ Неизвестное событие: {ctx.args[0]}")
        return

    try:
        threshold = int(ctx.args[1])
        if threshold < 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Порог должен быть положительным числом")
        return

    db.set_threshold(event_name, threshold)
    await update.message.reply_text(
        f"✅ Порог для *{event_name}* установлен: *{threshold}* спинов",
        parse_mode='Markdown'
    )


async def status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Статус парсера"""
    info = parser.get_status()
    total = db.get_total_spins()
    last = db.get_last_spin()

    text = (
        f"🔧 *Статус парсера*\n\n"
        f"{'🟢' if info['running'] else '🔴'} Парсер: {'работает' if info['running'] else 'остановлен'}\n"
        f"📡 Последний запрос: {info['last_fetch']}\n"
        f"⚠️ Ошибок подряд: {info['errors']}\n\n"
        f"💾 *База данных:*\n"
        f"  Всего спинов: *{total}*\n"
        f"  Последний спин: *{last['result'] if last else 'нет'}* ({last['timestamp'] if last else '—'})"
    )
    await update.message.reply_text(text, parse_mode='Markdown')


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *Справка*\n\n"
        "*Статистика промежутков* — это количество спинов между двумя последовательными выпадениями события.\n\n"
        "Например, если CrazyTime выпал, потом не выпадал 45 спинов и снова выпал — промежуток = 45.\n\n"
        "*Мин* — наименьший промежуток (включая 0, если выпало два раза подряд)\n"
        "*Макс* — наибольший промежуток\n"
        "*Среднее* — среднее арифметическое всех промежутков\n\n"
        "*События на колесе:*\n"
        "1️⃣ `1` (×23 на колесе)\n"
        "2️⃣ `2` (×15)\n"
        "5️⃣ `5` (×7)\n"
        "🔟 `10` (×4)\n"
        "🪙 `CoinFlip` (×4)\n"
        "🎯 `Pachinko` (×2)\n"
        "💰 `CashHunt` (×2)\n"
        "⏰ `CrazyTime` (×1)"
    )
    await update.message.reply_text(text, parse_mode='Markdown')


# ── ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ───────────────────────────────────────────────────

def normalize_event(name: str) -> str | None:
    """Нормализовать название события к стандартному виду"""
    mapping = {
        '1': '1', 'number1': '1',
        '2': '2', 'number2': '2',
        '5': '5', 'number5': '5',
        '10': '10', 'number10': '10',
        'coinflip': 'CoinFlip', 'coin_flip': 'CoinFlip', 'coin flip': 'CoinFlip', 'cf': 'CoinFlip',
        'pachinko': 'Pachinko', 'pach': 'Pachinko',
        'cashhunt': 'CashHunt', 'cash_hunt': 'CashHunt', 'cash hunt': 'CashHunt', 'ch': 'CashHunt',
        'crazytime': 'CrazyTime', 'crazy_time': 'CrazyTime', 'crazy time': 'CrazyTime', 'ct': 'CrazyTime',
    }
    return mapping.get(name.lower())


# ── ФОНОВАЯ ЗАДАЧА ────────────────────────────────────────────────────────────

async def polling_task(app: Application):
    """Фоновый цикл парсинга — запускается вместе с ботом"""
    logger.info("Запуск фонового парсера...")
    while True:
        try:
            alerts = await parser.fetch_and_process()
            for alert in alerts:
                for chat_id in CHAT_IDS:
                    try:
                        await app.bot.send_message(
                            chat_id=chat_id,
                            text=alert,
                            parse_mode='Markdown'
                        )
                    except Exception as e:
                        logger.error(f"Ошибка отправки уведомления: {e}")
        except Exception as e:
            logger.error(f"Ошибка в polling_task: {e}")

        await asyncio.sleep(30)  # Проверяем каждые 30 секунд


# ── ЗАПУСК ────────────────────────────────────────────────────────────────────

async def post_init(app: Application):
    """Запуск фоновых задач после инициализации бота"""
    asyncio.create_task(polling_task(app))


def main():
    db.init()
    db.init_default_thresholds()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("monthly", monthly))
    app.add_handler(CommandHandler("event", event_detail))
    app.add_handler(CommandHandler("alerts", alerts_menu))
    app.add_handler(CommandHandler("setalert", set_alert))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(event_callback, pattern="^event_"))

    logger.info("Бот запущен")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
