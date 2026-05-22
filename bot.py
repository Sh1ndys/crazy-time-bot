import asyncio
import logging
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

# Фиксированные множители для симулятора
SIM_FIXED_MULT = {"CashHunt": 25.0, "CrazyTime": 50.0}
BONUS_EVENTS = {"CoinFlip", "Pachinko", "CashHunt", "CrazyTime"}


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


def get_main_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📊 Статистика"), KeyboardButton("🏆 Топ")],
            [KeyboardButton("📅 За месяц"), KeyboardButton("🔧 Статус")],
            [KeyboardButton("🎯 По событию"), KeyboardButton("🔔 Пороги")],
            [KeyboardButton("✖️ Множители"), KeyboardButton("🎮 Симулятор")],
        ],
        resize_keyboard=True
    )


# ── ОСНОВНЫЕ КОМАНДЫ ──────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    username = update.effective_user.username or update.effective_user.first_name
    already = db.is_subscriber(chat_id)
    db.add_subscriber(chat_id, username)
    greeting = (
        f"👋 С возвращением, {username}!\nТы уже подписан на уведомления."
        if already else
        f"✅ {username}, ты подписан на уведомления!\nТеперь будешь получать оповещения о длинных сериях."
    )
    text = (
        f"🎡 *Crazy Time Stats Bot*\n\n{greeting}\n\n"
        f"*Команды:*\n"
        f"/stats — промежутки сейчас\n"
        f"/monthly — статистика за месяц\n"
        f"/event — детальная статистика по событию\n"
        f"/top — топ серий невыпадений\n"
        f"/multipliers — статистика множителей\n"
        f"/alerts — пороги уведомлений\n"
        f"/sim — симулятор ставок\n"
        f"/unsubscribe — отписаться\n"
        f"/status — статус парсера\n"
    )
    if is_admin(update):
        text += (
            f"\n*Команды администратора:*\n"
            f"/setalert [событие] [число]\n"
            f"/setalert nobonus [число]\n"
            f"/simset [параметр] [значение]\n"
            f"/simreset — сбросить симулятор\n"
            f"/reset — обнулить базу данных\n"
            f"/subscribers — список подписчиков\n"
        )
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=get_main_keyboard())


async def unsubscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if db.is_subscriber(chat_id):
        db.remove_subscriber(chat_id)
        await update.message.reply_text("❌ Ты отписан от уведомлений. Напиши /start чтобы подписаться снова.")
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
        await update.message.reply_text("Выберите событие:", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    name = normalize_event(ctx.args[0])
    if not name:
        await update.message.reply_text(f"❌ Неизвестное событие: {ctx.args[0]}")
        return
    await send_event_stats(update.message, name)


async def event_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await send_event_stats(query.message, query.data.replace("event_", ""))


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
        lines.append(f"  Макс за всё время: *{bonus_stats['max']}* спинов")
        lines.append(f"  Среднее: *{bonus_stats['avg']:.1f}* спинов")
        lines.append(f"  Всего серий: *{bonus_stats['count']}*")
        if bonus_stats["top5"]:
            lines.append(f"  Топ-5: {', '.join(str(g) for g in bonus_stats['top5'])}")
    else:
        lines.append("  _(статистика накапливается)_")
    await update.message.reply_text("\n".join(lines), parse_mode='Markdown')


async def multipliers_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    mult_stats = db.get_multiplier_stats()
    thresholds = db.get_all_thresholds()
    bonus_threshold = thresholds.get("__bonus__", 30)
    after_stats = db.get_after_series_stats(min_series=bonus_threshold)

    lines = ["✖️ *Статистика множителей бонусов:*\n"]
    for event in ["CoinFlip", "Pachinko", "CashHunt", "CrazyTime"]:
        s = mult_stats.get(event, {})
        icon = ICONS.get(event, "🎡")
        if s.get("count", 0) == 0:
            lines.append(f"{icon} *{event}*: _(данные накапливаются)_")
        else:
            fixed = SIM_FIXED_MULT.get(event)
            mult_note = f" _(фикс. ×{fixed:.0f})_" if fixed else ""
            lines.append(
                f"{icon} *{event}*{mult_note}:\n"
                f"  Выпадений: {s['count']} | Среднее: {s['avg_all']:.1f}x\n"
                f"  Макс за всё время: *{s['max_all']:.0f}x*\n"
                f"  Макс за 24 часа: *{s['max_24h']:.0f}x*"
            )

    lines.append(f"\n📈 *После серий {bonus_threshold}+ спинов без бонусов:*")
    if after_stats["total"] == 0:
        lines.append("  _(статистика накапливается)_")
    else:
        lines.append(f"  Всего таких серий: *{after_stats['total']}*\n")
        lines.append("  *За всё время:*")
        for r in after_stats["all"]:
            icon = ICONS.get(r['event'], "🎡")
            avg_m = f" | ср.множитель {r['avg_mult']:.1f}x" if r['avg_mult'] else ""
            lines.append(f"  {icon} {r['event']}: *{r['cnt']}* раз{avg_m}")
        if after_stats["24h"]:
            lines.append("\n  *За 24 часа:*")
            for r in after_stats["24h"]:
                icon = ICONS.get(r['event'], "🎡")
                max_m = f" | макс {r['max_mult']:.0f}x" if r['max_mult'] else ""
                lines.append(f"  {icon} {r['event']}: *{r['cnt']}* раз{max_m}")
        else:
            lines.append("  _За 24 часа серий не было_")

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
        await update.message.reply_text("Использование:\n/setalert CrazyTime 250\n/setalert nobonus 30")
        return
    if ctx.args[0].lower() == "nobonus":
        try:
            threshold = int(ctx.args[1])
        except ValueError:
            await update.message.reply_text("❌ Порог должен быть числом")
            return
        db.set_threshold("__bonus__", threshold)
        db.clear_alert_history("__bonus__")
        await update.message.reply_text(f"✅ Порог серии без бонусов: *{threshold}* спинов", parse_mode='Markdown')
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
    await update.message.reply_text(f"✅ Порог для *{name}*: *{threshold}* спинов", parse_mode='Markdown')


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
            conn.execute("DELETE FROM bonus_multipliers")
            conn.execute("DELETE FROM after_series_bonuses")
        await query.message.edit_text("✅ База данных очищена.")
        logger.info("Database reset by admin")
    else:
        await query.message.edit_text("❌ Отмена.")


# ── СИМУЛЯТОР ─────────────────────────────────────────────────────────────────

async def sim_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    balance = db.sim_get_balance()
    stake = float(db.sim_get("stake"))
    num_bets = int(db.sim_get("num_bets"))
    strategy = db.sim_get("strategy")
    mg_mult = float(db.sim_get("martingale_mult"))
    mg_steps = int(db.sim_get("martingale_steps"))
    enabled = db.sim_get("enabled") == "1"
    active_bets = db.sim_get_active_bets()
    hist = db.sim_get_history_stats()

    lines = [
        f"🎮 *Симулятор ставок*\n",
        f"{'🟢 Включён' if enabled else '🔴 Выключен'}\n",
        f"💰 *Баланс:* {balance:,.0f} ₽\n",
        f"⚙️ *Настройки:*",
        f"  Ставка: {stake:,.0f} ₽",
        f"  Кол-во ставок: {num_bets}",
        f"  Стратегия: {'Мартингейл' if strategy == 'martingale' else 'Стандарт (без увеличения)'}",
    ]
    if strategy == "martingale":
        lines.append(f"  Множитель мартингейла: ×{mg_mult}")
        lines.append(f"  Шагов мартингейла: {mg_steps}")

    if active_bets:
        lines.append(f"\n🎯 *Активные ставки:*")
        for bet in active_bets:
            icon = ICONS.get(bet['event'], "🎡")
            event_name = "все бонусы" if bet['event'] == "__bonus__" else bet['event']
            lines.append(f"  {icon} {event_name}: ставка {bet['current_stake']:,.0f} ₽, осталось {bet['bets_remaining']} спинов")
    else:
        lines.append("\n_Активных ставок нет_")

    h = hist["all"]
    if h["total"]:
        wins = h["wins"] or 0
        total = h["total"]
        profit = h["total_profit"] or 0
        lines.append(f"\n📊 *Статистика за всё время:*")
        lines.append(f"  Побед: {wins}/{total} ({wins/total*100:.0f}%)")
        lines.append(f"  Итого: {'✅ +' if profit >= 0 else '❌ '}{profit:,.0f} ₽")
        if h["best_win"]:
            lines.append(f"  Лучшая ставка: +{h['best_win']:,.0f} ₽")
        if h["worst_loss"]:
            lines.append(f"  Худшая ставка: {h['worst_loss']:,.0f} ₽")

    h24 = hist["24h"]
    if h24["total"]:
        profit24 = h24["total_profit"] or 0
        lines.append(f"\n  *За 24 часа:* {'✅ +' if profit24 >= 0 else '❌ '}{profit24:,.0f} ₽ ({h24['wins'] or 0}/{h24['total']})")

    if is_admin(update):
        lines.append(f"\n_Настройка: /simset параметр значение_")
        lines.append(f"_Параметры: balance, stake, num-bets, strategy (flat/martingale), mgale-mult, mgale-steps, enabled (0/1)_")

    await update.message.reply_text("\n".join(lines), parse_mode='Markdown')


async def simset_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text("❌ Только администратор.")
        return
    if len(ctx.args) < 2:
        await update.message.reply_text(
            "Использование: /simset [параметр] [значение]\n\n"
            "Параметры:\n"
            "  balance 100000 — начальный баланс\n"
            "  stake 100 — размер ставки\n"
            "  num_bets 10 — количество ставок\n"
            "  strategy flat|martingale — стратегия\n"
            "  mgale_mult 2 — множитель мартингейла\n"
            "  mgale_steps 3 — шагов мартингейла\n"
            "  enabled 0|1 — вкл/выкл симулятор"
        )
        return
    key = ctx.args[0].lower()
    val = ctx.args[1]
    valid_keys = {"balance", "stake", "num_bets", "strategy", "mgale_mult", "mgale_steps", "enabled"}
    if key not in valid_keys:
        await update.message.reply_text(f"❌ Неизвестный параметр: {key}")
        return
    # Валидация
    try:
        if key in {"balance", "stake", "mgale_mult"}:
            float(val)
        elif key in {"num_bets", "mgale_steps", "enabled"}:
            int(val)
        elif key == "strategy" and val not in {"flat", "martingale"}:
            raise ValueError
    except ValueError:
        await update.message.reply_text(f"❌ Неверное значение для {key}: {val}")
        return
    db.sim_set(key, val)
    await update.message.reply_text(f"✅ Симулятор: *{key}* = *{val}*", parse_mode='Markdown')


async def simreset_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text("❌ Только администратор.")
        return
    keyboard = [[
        InlineKeyboardButton("✅ Сбросить", callback_data="simreset_confirm"),
        InlineKeyboardButton("❌ Отмена", callback_data="simreset_cancel"),
    ]]
    await update.message.reply_text(
        "⚠️ Сбросить историю ставок и активные ставки симулятора?\n_(баланс и настройки сохранятся)_",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def simreset_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id not in ADMIN_IDS:
        await query.answer("❌ Только администратор.", show_alert=True)
        return
    await query.answer()
    if query.data == "simreset_confirm":
        db.sim_reset()
        await query.message.edit_text("✅ Симулятор сброшен. История и активные ставки удалены.")
    else:
        await query.message.edit_text("❌ Отмена.")


# ── ЛОГИКА СИМУЛЯТОРА ─────────────────────────────────────────────────────────

def sim_get_multiplier(event: str, raw_mult: float | None) -> float:
    if event in SIM_FIXED_MULT:
        return SIM_FIXED_MULT[event]
    return raw_mult if raw_mult else 1.0


async def sim_process_spin(app, result: str, raw_mult: float | None, thresholds: dict, absences: dict, bonus_absence: int):
    """Обрабатываем один спин для симулятора."""
    if db.sim_get("enabled") != "1":
        return

    stake_base = float(db.sim_get("stake") or "100")
    num_bets = int(db.sim_get("num_bets") or "10")
    strategy = db.sim_get("strategy") or "flat"
    mg_mult = float(db.sim_get("mgale_mult") or "2.0")
    mg_steps = int(db.sim_get("martingale_steps") or "3")
    notifications = []

    # 1. Проверяем нужно ли открыть новые ставки (пороги только что пересечены)
    # Для отдельных событий
    for event in list(EVENTS):
        threshold = thresholds.get(event, 9999)
        absence = absences.get(event, 0)
        if absence >= threshold and not db.sim_has_active_bet(event):
            db.sim_add_active_bet(event, stake_base, num_bets)
            icon = ICONS.get(event, "🎡")
            notifications.append(
                f"🎮 *Симулятор запускает ставки*\n"
                f"{icon} *{event}* не выпадал {absence} спинов (порог {threshold})\n"
                f"Ставка: {stake_base:,.0f} ₽ × {num_bets} спинов"
            )

    # Для серии без бонусов
    bonus_threshold = thresholds.get("__bonus__", 30)
    if bonus_absence >= bonus_threshold and not db.sim_has_active_bet("__bonus__"):
        db.sim_add_active_bet("__bonus__", stake_base, num_bets)
        notifications.append(
            f"🎮 *Симулятор запускает ставки*\n"
            f"🎰 *Без бонусов* уже {bonus_absence} спинов (порог {bonus_threshold})\n"
            f"Ставка на все 4 бонуса: {stake_base*4:,.0f} ₽/спин × {num_bets} спинов"
        )

    # 2. Обрабатываем активные ставки
    active_bets = db.sim_get_active_bets()
    balance = db.sim_get_balance()

    for bet in active_bets:
        bet_event = bet['event']
        current_stake = bet['current_stake']
        bets_remaining = bet['bets_remaining']
        mg_step = bet['martingale_step']
        total_spent = bet.get('total_spent', 0)
        bet_id = bet['id']

        # Определяем попадание
        if bet_event == "__bonus__":
            hit = result in BONUS_EVENTS
            actual_stake = current_stake * 4  # ставим на все 4
        else:
            hit = result == bet_event
            actual_stake = current_stake

        # Списываем ставку
        balance -= actual_stake
        total_spent += actual_stake

        if hit:
            # Выигрыш — прибыль с учётом ВСЕХ потраченных ставок на эту серию
            mult = sim_get_multiplier(result, raw_mult)
            winnings = current_stake * mult
            real_profit = winnings - total_spent
            balance += winnings
            db.sim_set_balance(balance)
            db.sim_add_history(bet_event, total_spent, "win", real_profit, mult)
            db.sim_remove_active_bet(bet_id)
            icon = ICONS.get(result, "🎡")
            profit_str = f"+{real_profit:,.0f}" if real_profit >= 0 else f"{real_profit:,.0f}"
            notifications.append(
                f"✅ *Симулятор: ВЫИГРЫШ!*\n"
                f"{icon} *{result}* выпал с множителем ×{mult:.0f}\n"
                f"Потрачено: {total_spent:,.0f} ₽ → Выигрыш: {winnings:,.0f} ₽\n"
                f"Чистая прибыль: *{profit_str} ₽*\n"
                f"💰 Баланс: *{balance:,.0f} ₽*"
            )
        else:
            bets_remaining -= 1
            if bets_remaining <= 0:
                # Ставки закончились
                if strategy == "martingale" and mg_step < mg_steps:
                    # Следующий шаг мартингейла
                    new_stake = current_stake * mg_mult
                    db.sim_update_active_bet(bet_id, num_bets, new_stake, mg_step + 1, total_spent)
                    db.sim_set_balance(balance)
                    event_name = "все бонусы" if bet_event == "__bonus__" else bet_event
                    notifications.append(
                        f"🔄 *Симулятор: мартингейл шаг {mg_step+1}*\n"
                        f"_{event_name}_ не выпал за {num_bets} ставок\n"
                        f"Новая ставка: {new_stake:,.0f} ₽ (×{mg_mult})\n"
                        f"Потрачено за серию: {total_spent:,.0f} ₽\n"
                        f"💰 Баланс: *{balance:,.0f} ₽*"
                    )
                else:
                    # Стоп — записываем реальный убыток
                    db.sim_add_history(bet_event, total_spent, "loss", -total_spent, None)
                    db.sim_remove_active_bet(bet_id)
                    db.sim_set_balance(balance)
                    event_name = "все бонусы" if bet_event == "__bonus__" else bet_event
                    notifications.append(
                        f"❌ *Симулятор: серия проиграна*\n"
                        f"_{event_name}_ не выпал за все ставки\n"
                        f"Убыток: -{total_spent:,.0f} ₽\n"
                        f"💰 Баланс: *{balance:,.0f} ₽*"
                    )
            else:
                db.sim_update_active_bet(bet_id, bets_remaining, current_stake, mg_step, total_spent)
                db.sim_set_balance(balance)

    # Отправляем уведомления
    for note in notifications:
        for chat_id in ADMIN_IDS:
            try:
                await app.bot.send_message(chat_id=chat_id, text=note, parse_mode='Markdown')
            except Exception as e:
                logger.error(f"Sim notification error: {e}")


# ── КНОПКИ ────────────────────────────────────────────────────────────────────

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
    elif text == "🎮 Симулятор":
        await sim_cmd(update, ctx)


# ── POLLING ───────────────────────────────────────────────────────────────────

async def polling_task(app: Application):
    logger.info("Parser task started")
    while True:
        try:
            alerts, spin_data = await parser.fetch_and_process()
            subscribers = db.get_all_subscribers()

            if alerts:
                for alert in alerts:
                    for chat_id in subscribers:
                        try:
                            await app.bot.send_message(chat_id=chat_id, text=alert, parse_mode='Markdown')
                        except Exception as e:
                            logger.error(f"Send error to {chat_id}: {e}")

            if spin_data:
                thresholds = db.get_all_thresholds()
                absences = analyzer.current_absence_all()
                bonus_absence = analyzer.current_bonus_absence()
                for result, multiplier, timestamp in spin_data:
                    try:
                        await sim_process_spin(app, result, multiplier, thresholds, absences, bonus_absence)
                    except Exception as e:
                        logger.error(f"Sim process error: {e}")

        except Exception as e:
            logger.exception(f"Parser loop error: {e}")
        await asyncio.sleep(30)


async def post_init(app: Application):
    asyncio.create_task(polling_task(app))


def main():
    db.init()
    db.init_default_thresholds()
    db.sim_init_defaults()
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
    app.add_handler(CommandHandler("multipliers", multipliers_cmd))
    app.add_handler(CommandHandler("alerts", alerts_menu))
    app.add_handler(CommandHandler("setalert", set_alert))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(CommandHandler("sim", sim_cmd))
    app.add_handler(CommandHandler("simset", simset_cmd))
    app.add_handler(CommandHandler("simreset", simreset_cmd))
    app.add_handler(CallbackQueryHandler(event_callback, pattern="^event_"))
    app.add_handler(CallbackQueryHandler(reset_callback, pattern="^reset_"))
    app.add_handler(CallbackQueryHandler(simreset_callback, pattern="^simreset_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, button_handler))
    logger.info("Bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
