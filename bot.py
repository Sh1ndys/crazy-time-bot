import asyncio
import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from config import BOT_TOKEN, CHAT_IDS
from database import Database
from analyzer import Analyzer
from parser import TracksParser

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

db = Database()
analyzer = Analyzer(db)
parser = TracksParser(db, analyzer)

EVENTS = ['1', '2', '5', '10', 'CoinFlip', 'Pachinko', 'CashHunt', 'CrazyTime']
ICONS = {
    "CrazyTime": "⏰", "CashHunt": "💰",
    "Pachinko": "🎯", "CoinFlip": "🪙",
    "10": "🔟", "5": "5️⃣", "2": "2️⃣", "1": "1️⃣",
}

def normalize_event(name: str) -> str | None:
    mapping = {
        '1': '1', '2': '2', '5': '5', '10': '10',
        'coinflip': 'CoinFlip', 'coin flip': 'CoinFlip', 'cf': 'CoinFlip',
        'pachinko': 'Pachinko', 'pach': 'Pachinko',
        'cashhunt': 'CashHunt', 'cash hunt': 'CashHunt', 'ch': 'CashHunt',
        'crazytime': 'CrazyTime', 'crazy time': 'CrazyTime', 'ct': 'CrazyTime',
    }
    return mapping.get(name.lower())


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "🎡 *Crazy Time Stats Bot*\n\n"
        "*Команды:*\n"
        "/stats — промежутки сейчас\n"
        "/monthly — статистика за месяц\n"
        "/event — детальная статистика по событию\n"
        "/top — топ серий невыпадений\n"
        "/alerts — пороги уведомлений\n"
        "/setalert [событие] [число] — установить порог\n"
        "/status — статус парсера\n"
        "/reset — обнулить базу данных\n\n"
        "События: `1` `2` `5` `10` `CoinFlip` `Pachinko` `CashHunt` `CrazyTime`"
    )
    await update.message.reply_text(text, parse_mode='Markdown')


async def stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = analyzer.current_absence_all()
    thresholds = db.get_all_thresholds()
    total = db.get_total_spins()
    lines = ["📊 *Текущие промежутки (спинов без события):*\n"]
    for event, absence in rows.items():
        threshold = thresholds.get(event, 300)
        icon = ICONS.get(event, "🎡")
        emoji = "🔴" if absence >= threshold else ("🟡" if absence >= threshold * 0.7 else "🟢")
        lines.append(f"{emoji}{icon} *{event}*: {absence} спинов (порог: {threshold})")
    lines.append(f"\n📈 Всего спинов в БД: *{total}*")
    await update.message.reply_text("\n".join(lines), parse_mode='Markdown')


async def monthly(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    stats_data = analyzer.monthly_stats()
    if not stats_data:
        await update.message.reply_text("⚠️ Недостаточно данных за текущий месяц.")
        return
    lines = ["📅 *Статистика за текущий месяц:*\n"]
    for event, s in stats_data.items():
        icon = ICONS.get(event, "🎡")
        lines.append(
            f"{icon} *{event}*: выпало {s['count']}x\n"
            f"   мин={s['min']} макс={s['max']} среднее={s['avg']:.1f}\n"
        )
    await update.message.reply_text("\n".join(lines), parse_mode='Markdown')


async def event_detail(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        keyboard = [[InlineKeyboardButton(f"{ICONS.get(e,'')} {e}", callback_data=f"event_{e}")] for e in EVENTS]
        await update.message.reply_text(
            "Выберите событие:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    name = normalize_event(ctx.args[0])
    if not name:
        await update.message.reply_text(f"❌ Неизвестное событие: {ctx.args[0]}")
        return
    await send_event_stats(update.message, name)


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
    icon = ICONS.get(event, "🎡")
    text = (
        f"{icon} *{event}* — полная статистика\n\n"
        f"📊 *За всё время:*\n"
        f"  Выпадений: *{s['total_hits']}*\n"
        f"  Мин. промежуток: *{s['all_min']}* спинов\n"
        f"  Макс. промежуток: *{s['all_max']}* спинов\n"
        f"  Среднее: *{s['all_avg']:.1f}* спинов\n\n"
        f"📅 *За текущий месяц:*\n"
        f"  Выпадений: *{s['month_hits']}*\n"
        f"  Мин: *{s['month_min']}* Макс: *{s['month_max']}* Среднее: *{s['month_avg']:.1f}*\n\n"
        f"⏱ *Сейчас не выпадает:* *{s['current_absence']}* спинов"
    )
    if s.get('top_gaps'):
        text += "\n\n🏆 *Топ-5 максимальных промежутков:*\n"
        for i, gap in enumerate(s['top_gaps'], 1):
            text += f"  {i}. {gap} спинов\n"
    await message.reply_text(text, parse_mode='Markdown')


async def top_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    all_spins = db.get_all_spins_ordered()
    if not all_spins:
        await update.message.reply_text("⚠️ Нет данных.")
        return
    from analyzer import compute_gaps, gap_stats
    lines = ["🏆 *Топ серий невыпадений (за всё время):*\n"]
    for event in EVENTS:
        gaps = compute_gaps(all_spins, event)
        s = gap_stats(gaps)
        icon = ICONS.get(event, "🎡")
        lines.append(f"{icon} *{event}*: макс={s['max']} среднее={s['avg']:.1f}")
    await update.message.reply_text("\n".join(lines), parse_mode='Markdown')


async def alerts_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    thresholds = db.get_all_thresholds()
    lines = ["🔔 *Текущие пороги уведомлений:*\n"]
    for event, threshold in thresholds.items():
        icon = ICONS.get(event, "🎡")
        lines.append(f"  {icon} *{event}*: {threshold} спинов")
    lines.append("\n_Изменить: /setalert [событие] [число]_")
    await update.message.reply_text("\n".join(lines), parse_mode='Markdown')


async def set_alert(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if len(ctx.args) < 2:
        await update.message.reply_text("Использование: /setalert CrazyTime 250")
        return
    name = normalize_event(ctx.args[0])
    if not name:
        await update.message.reply_text(f"❌ Неизвестное событие: {ctx.args[0]}")
        return
    try:
        threshold = int(ctx.args[1])
    except ValueError:
        await update.message.reply_text("❌ Порог должен быть числом")
        return
    db.set_threshold(name, threshold)
    await update.message.reply_text(
        f"✅ Порог для *{name}* установлен: *{threshold}* спинов",
        parse_mode='Markdown'
    )


async def status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    info = parser.get_status()
    total = db.get_total_spins()
    last = db.get_last_spin()
    text = (
        f"🔧 *Статус парсера*\n\n"
        f"{'🟢 работает' if info['running'] else '🔴 остановлен'}\n"
        f"Последний запрос: {info['last_fetch']}\n"
        f"Ошибок подряд: {info['errors']}\n\n"
        f"💾 Всего спинов: *{total}*\n"
        f"Последний: *{last['result'] if last else 'нет'}*"
    )
    await update.message.reply_text(text, parse_mode='Markdown')


async def reset_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("✅ Да, обнулить", callback_data="reset_confirm"),
            InlineKeyboardButton("❌ Отмена", callback_data="reset_cancel"),
        ]
    ]
    await update.message.reply_text(
        "⚠️ Вы уверены что хотите *удалить всю историю спинов*?\n"
        "Статистика будет собираться заново с нуля.",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def reset_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "reset_confirm":
        with db._conn() as conn:
            conn.execute("DELETE FROM spins")
            conn.execute("DELETE FROM sent_alerts")
        await query.message.edit_text("✅ База данных очищена. Статистика собирается заново.")
        logger.info("Database reset by user")
    else:
        await query.message.edit_text("❌ Отмена. База данных не изменена.")


async def polling_task(app: Application):
    logger.info("Parser task started")
    while True:
        try:
            alerts = await parser.fetch_and_process()
            for alert in alerts:
                for chat_id in CHAT_IDS:
                    try:
                        await app.bot.send_message(chat_id=chat_id, text=alert, parse_mode='Markdown')
                    except Exception as e:
                        logger.error(f"Send error: {e}")
        except Exception as e:
            logger.exception(f"Parser loop error: {e}")
        await asyncio.sleep(30)


async def post_init(app: Application):
    asyncio.create_task(polling_task(app))


def main():
    db.init()
    db.init_default_thresholds()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("monthly", monthly))
    app.add_handler(CommandHandler("event", event_detail))
    app.add_handler(CommandHandler("top", top_cmd))
    app.add_handler(CommandHandler("alerts", alerts_menu))
    app.add_handler(CommandHandler("setalert", set_alert))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(CallbackQueryHandler(event_callback, pattern="^event_"))
    app.add_handler(CallbackQueryHandler(reset_callback, pattern="^reset_"))
    logger.info("Bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
