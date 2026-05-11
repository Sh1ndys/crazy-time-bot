import asyncio
import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from config import BOT_TOKEN, CHAT_IDS, ADMIN_IDS
from database import Database
from analyzer import Analyzer, EVENTS, compute_gaps, gap_stats
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

ICONS = {
    "CrazyTime": "⏰", "CashHunt": "💰",
    "Pachinko": "🎯", "CoinFlip": "🪙",
    "10": "🔟", "5": "5️⃣", "2": "2️⃣", "1": "1️⃣",
}


def is_admin(update: Update) -> bool:
    return update.effective_user.id in ADMIN_IDS


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
    chat_id = update.effective_chat.id
    username = update.effective_user.username or update.effective_user.first_name
    already = db.is_subscriber(chat_id)
    db.add_subscriber(chat_id, username)

    if already:
        greeting = f"👋 С возвращением, {username}!\nТы уже подписан на уведомления."
    else:
        greeting = f"✅ {username}, ты подписан на уведомления!\nТеперь будешь получать оповещения о длинных сериях."

    text = (
        f"🎡 *Crazy Time Stats Bot*\n\n"
        f"{greeting}\n\n"
        f"*Команды:*\n"
        f"/stats — промежутки сейчас\n"
        f"/monthly — статистика за месяц\n"
        f"/event — детальная статистика по событию\n"
        f"/top — топ серий невыпадений\n"
        f"/alerts — пороги уведомлений\n"
        f"/unsubscribe — отписаться от уведомлений\n"
        f"/status — статус парсера\n"
    )
    if is_admin(update):
        text += (
            f"\n*Команды администратора:*\n"
            f"/setalert [событие] [число]\n"
            f"/setalert nobonus [число]\n"
            f"/reset — обнулить базу данных\n"
            f"/subscribers — список подписчиков\n"
        )
    keyboard = ReplyKeyboardMarkup(
        [
            [KeyboardButton("📊 Статистика"), KeyboardButton("🏆 Топ")],
            [KeyboardButton("📅 За месяц"), KeyboardButton("🔧 Статус")],
            [KeyboardButton("🎯 По событию"), KeyboardButton("🔔 Пороги")],
            [KeyboardButton("✖️ Множители")],
        ],
        resize_keyboard=True
    )
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=keyboard)


async def unsubscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if db.is_subscriber(chat_id):
        db.remove_subscriber(chat_id)
        await update.message.reply_text("❌ Ты отписан от уведомлений.\nЧтобы подписаться снова — /start")
    else:
        await update.message.reply_text("Ты и так не подписан. Напиши /start чтобы подписаться.")


async def subscribers_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text("❌ Только для администратора.")
        return
    count = db.get_subscribers_count()
    await update.message.reply_text(f"👥 Подписчиков: *{count}*", parse_mode='Markdown')


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
    bonus_absence = analyzer.current_bonus_absence()
    bonus_threshold = thresholds.get("__bonus__", 30)
    bonus_emoji = "🔴" if bonus_absence >= bonus_threshold else ("🟡" if bonus_absence >= bonus_threshold * 0.7 else "🟢")
    lines.append(f"\n{bonus_emoji}🎰 *Без бонусов подряд*: {bonus_absence} (порог: {bonus_threshold})")
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
    lines = ["🏆 *Топ серий невыпадений (за всё время):*\n"]
    for event in EVENTS:
        gaps = compute_gaps(all_spins, event)
        s = gap_stats(gaps)
        icon = ICONS.get(event, "🎡")
        lines.append(f"{icon} *{event}*: макс={s['max']} среднее={s['avg']:.1f}")

    bonus_stats = db.get_bonus_gap_stats()
    current_bonus = analyzer.current_bonus_absence()
    thresholds = db.get_all_thresholds()
    bonus_threshold = thresholds.get("__bonus__", 30)
    lines.append(f"\n🎰 *Серии без любого бонуса:*")
    lines.append(f"  Сейчас: *{current_bonus}* спинов (порог: {bonus_threshold})")
    if bonus_stats["count"] > 0:
        lines.append(f"  Макс: *{bonus_stats['max']}* спинов")
        lines.append(f"  Среднее: *{bonus_stats['avg']:.1f}* спинов")
        if bonus_stats["top5"]:
            lines.append(f"  Топ-5: {', '.join(str(g) for g in bonus_stats['top5'])}")
    else:
        lines.append(f"  _(статистика накапливается)_")
    await update.message.reply_text("\n".join(lines), parse_mode='Markdown')


async def alerts_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    thresholds = db.get_all_thresholds()
    bonus_threshold = thresholds.get("__bonus__", 30)
    lines = ["🔔 *Текущие пороги уведомлений:*\n"]
    for event in EVENTS:
        icon = ICONS.get(event, "🎡")
        threshold = thresholds.get(event, "—")
        lines.append(f"  {icon} *{event}*: {threshold} спинов")
    lines.append(f"\n  🎰 *Без бонусов подряд*: {bonus_threshold} спинов")
    if is_admin(update):
        lines.append("\n_/setalert [событие] [число]_")
        lines.append("_/setalert nobonus [число]_")
    await update.message.reply_text("\n".join(lines), parse_mode='Markdown')


async def set_alert(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text("❌ Только администратор может менять пороги.")
        return
    if len(ctx.args) < 2:
        await update.message.reply_text(
            "Использование:\n/setalert CrazyTime 250\n/setalert nobonus 30"
        )
        return
    if ctx.args[0].lower() == "nobonus":
        try:
            threshold = int(ctx.args[1])
            if threshold < 1:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Порог должен быть положительным числом")
            return
        db.set_threshold("__bonus__", threshold)
        db.clear_alert_history("__bonus__")
        await update.message.reply_text(
            f"✅ Порог серии без бонусов: *{threshold}* спинов",
            parse_mode='Markdown'
        )
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
    recent = db.get_recent_spins(10)
    text = (
        f"🔧 *Статус парсера*\n\n"
        f"{'🟢 работает' if info['running'] else '🔴 остановлен'}\n"
        f"Последний запрос: {info['last_fetch']}\n"
        f"Ошибок подряд: {info['errors']}\n\n"
        f"💾 Всего спинов: *{total}*\n"
        f"👥 Подписчиков: *{db.get_subscribers_count()}*\n\n"
        f"🕐 *Последние 10 спинов:*\n"
    )
    for spin in recent:
        icon = ICONS.get(spin['result'], "🎡")
        ts = spin['timestamp'][:16].replace('T', ' ')
        text += f"  {icon} {spin['result']} — {ts}\n"
    await update.message.reply_text(text, parse_mode='Markdown')


async def reset_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text("❌ Только администратор может сбрасывать базу.")
        return
    keyboard = [[
        InlineKeyboardButton("✅ Да, обнулить", callback_data="reset_confirm"),
        InlineKeyboardButton("❌ Отмена", callback_data="reset_cancel"),
    ]]
    await update.message.reply_text(
        "⚠️ Вы уверены что хотите *удалить всю историю спинов*?",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def reset_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id not in ADMIN_IDS:
        await query.answer("❌ Только администратор.", show_alert=True)
        return
    await query.answer()
    if query.data == "reset_confirm":
        with db._conn() as conn:
            conn.execute("DELETE FROM spins")
            conn.execute("DELETE FROM sent_alerts")
            conn.execute("DELETE FROM bonus_gaps")
        await query.message.edit_text("✅ База данных очищена.")
        logger.info("Database reset by admin")
    else:
        await query.message.edit_text("❌ Отмена.")


async def multipliers_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    stats_data = db.get_multiplier_stats()
    lines = ["🎰 *Статистика множителей бонусов:*\n"]
    for event in ["CoinFlip", "Pachinko", "CashHunt", "CrazyTime"]:
        s = stats_data.get(event, {})
        icon = ICONS.get(event, "🎡")
        if s.get("count", 0) == 0:
            lines.append(f"{icon} *{event}*: _(данные накапливаются)_")
        else:
            lines.append(
                f"{icon} *{event}*:\n"
                f"  Выпадений: {s['count']} | Среднее: {s['avg_all']:.1f}x\n"
                f"  Макс за всё время: *{s['max_all']:.0f}x*\n"
                f"  Макс за 24 часа: *{s['max_24h']:.0f}x*"
            )
    await update.message.reply_text("\n".join(lines), parse_mode='Markdown')


async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "📊 Статистика":
        await stats(update, ctx)
    elif text == "🏆 Топ":
        await top_cmd(update, ctx)
    elif text == "📅 За месяц":
        await monthly(update, ctx)
    elif text == "🔧 Статус":
        await status(update, ctx)
    elif text == "🎯 По событию":
        await event_detail(update, ctx)
    elif text == "🔔 Пороги":
        await alerts_menu(update, ctx)
    elif text == "✖️ Множители":
        await multipliers_cmd(update, ctx)


async def polling_task(app: Application):
    logger.info("Parser task started")
    while True:
        try:
            alerts = await parser.fetch_and_process()
            if alerts:
                # Рассылаем всем подписчикам
                subscribers = db.get_all_subscribers()
                for alert in alerts:
                    for chat_id in subscribers:
                        try:
                            await app.bot.send_message(
                                chat_id=chat_id, text=alert, parse_mode='Markdown'
                            )
                        except Exception as e:
                            logger.error(f"Send error to {chat_id}: {e}")
        except Exception as e:
            logger.exception(f"Parser loop error: {e}")
        await asyncio.sleep(30)


async def post_init(app: Application):
    asyncio.create_task(polling_task(app))


def main():
    db.init()
    db.init_default_thresholds()
    # Автоматически добавляем администратора как подписчика
    for admin_id in ADMIN_IDS:
        db.add_subscriber(admin_id, "admin")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe))
    app.add_handler(CommandHandler("subscribers", subscribers_cmd))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("monthly", monthly))
    app.add_handler(CommandHandler("event", event_detail))
    app.add_handler(CommandHandler("top", top_cmd))
    app.add_handler(CommandHandler("alerts", alerts_menu))
    app.add_handler(CommandHandler("setalert", set_alert))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(CommandHandler("multipliers", multipliers_cmd))
    app.add_handler(CallbackQueryHandler(event_callback, pattern="^event_"))
    app.add_handler(CallbackQueryHandler(reset_callback, pattern="^reset_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, button_handler))
    logger.info("Bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

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
