"""
Парсер данных с slotyi.com/api/crazytime
Публичный API — без токена, без VPN, работает в России.

Структура одного спина:
{
  "id": "18adc06f6b6704098330990c",
  "settledAt": "2026-05-09T01:10:53.096Z",
  "result": {
    "outcome": "WinningNumber",   # или "CoinFlip", "Pachinko", "CashHunt", "CrazyTime"
    "wheelSector": "1"            # "1","2","5","10","CoinFlip","Pachinko","CashHunt","CrazyTime"
  }
}
"""

import asyncio
import aiohttp
import logging
from datetime import datetime, timezone
from config import SLOTYI_API, POLL_INTERVAL, ALERT_REPEAT_INTERVAL
from database import Database
from analyzer import Analyzer

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.casinotrackpot.com/",
    "Origin": "https://www.casinotrackpot.com",
}

# wheelSector из API → наше название
RESULT_MAP = {
    "1":         "1",
    "2":         "2",
    "5":         "5",
    "10":        "10",
    "coinflip":  "CoinFlip",
    "coin flip": "CoinFlip",
    "pachinko":  "Pachinko",
    "cashhunt":  "CashHunt",
    "cash hunt": "CashHunt",
    "crazytime": "CrazyTime",
    "crazy time":"CrazyTime",
}


def normalize_result(spin):
    data = spin.get("data", spin)
    result = data.get("result", {})
    # wheelSector прямо в result
    sector = str(result.get("wheelSector") or "").strip().lower()
    if not sector:
        outcome = result.get("outcome", {})
        sector = str(outcome.get("wheelSector") or "").strip().lower()
    return RESULT_MAP.get(sector)


class TracksParser:
    def __init__(self, db: Database, analyzer: Analyzer):
        self.db = db
        self.analyzer = analyzer
        self._running = False
        self._last_fetch = "никогда"
        self._consecutive_errors = 0

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "last_fetch": self._last_fetch,
            "errors": self._consecutive_errors,
        }

    async def fetch_and_process(self) -> list[str]:
        self._running = True
        alerts = []

        try:
            new_spins = await self._fetch_new_spins()

            if new_spins:
                saved = 0
                skipped = 0
                for spin in new_spins:
                    result = normalize_result(spin)
                    if not result:
                        logger.debug(f"Пропуск: {spin.get('result')}")
                        skipped += 1
                        continue

                    ts_raw = spin.get("settledAt") or spin.get("startedAt")
                    try:
                        timestamp = datetime.fromisoformat(
                            str(ts_raw).replace("Z", "+00:00")
                        )
                    except Exception:
                        timestamp = datetime.now(tz=timezone.utc)

                    spin_id = str(spin.get("id") or spin.get("transmissionId") or f"{result}_{ts_raw}")
                    is_new = self.db.save_spin(spin_id, result, timestamp)
                    if is_new:
                        saved += 1
                        self.db.clear_alert_history(result)

                if saved > 0:
                    logger.info(f"Сохранено: {saved}, пропущено: {skipped}")
                    alerts = self._check_all_thresholds()

            self._last_fetch = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
            self._consecutive_errors = 0

        except aiohttp.ClientError as e:
            self._consecutive_errors += 1
            logger.error(f"Ошибка сети: {e}")
        except Exception as e:
            self._consecutive_errors += 1
            logger.exception(f"Ошибка парсера: {e}")

        return alerts

    async def _fetch_new_spins(self) -> list[dict]:
        """Получить новые спины с slotyi.com"""
        last_known_id = self.db.get_last_spin_id()
        last_spin = self.db.get_last_spin()

        # Получаем timestamp последнего известного спина
        last_ts = None
        if last_spin:
            try:
                last_ts = datetime.fromisoformat(
                    last_spin["timestamp"].replace("Z", "+00:00")
                ).timestamp()
            except Exception:
                pass

        from config import PROXY_URL
        for attempt in range(3):
            try:
                async with aiohttp.ClientSession(headers=HEADERS) as session:
                    async with session.get(
                        SLOTYI_API,
                        proxy=PROXY_URL,
                        timeout=aiohttp.ClientTimeout(total=30)
                    ) as resp:
                        if resp.status != 200:
                            logger.error(f"HTTP {resp.status} от slotyi.com")
                            return []
                        data = await resp.json(content_type=None)
                break
            except asyncio.TimeoutError:
                logger.warning(f"Таймаут slotyi.com (попытка {attempt+1}/3)")
                if attempt == 2:
                    return []
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Ошибка запроса: {e}")
                return []

        # API возвращает список спинов (новые первые)
        if not isinstance(data, list):
            logger.warning(f"Неожиданный формат: {type(data)}")
            return []

        # Фильтруем только новые
        all_new = []
        for spin in data:
            spin_id = str(spin.get("id") or "")
            spin_ts_raw = spin.get("settledAt") or spin.get("startedAt")

            # Если нет данных в БД — берём все
            if not last_known_id:
                all_new.append(spin)
                continue

            # Проверяем по ID
            if spin_id and spin_id == str(last_known_id):
                break

            # Проверяем по времени
            if last_ts and spin_ts_raw:
                try:
                    spin_ts = datetime.fromisoformat(
                        str(spin_ts_raw).replace("Z", "+00:00")
                    ).timestamp()
                    if spin_ts <= last_ts:
                        break
                except Exception:
                    pass

            all_new.append(spin)

        # Возвращаем в хронологическом порядке (старые первыми)
        return list(reversed(all_new))

    def _check_all_thresholds(self) -> list[str]:
        total = self.db.get_total_spins()
        if total < 200:
            logger.info(f"Пропускаем проверку порогов — в БД {total} спинов (нужно 200+)")
            return []

        alerts = []
        absences = self.analyzer.current_absence_all()
        thresholds = self.db.get_all_thresholds()

        for event, absence in absences.items():
            threshold = thresholds.get(event, 300)
            if absence < threshold:
                continue
            if absence >= total - 1:
                continue

            notify_at = threshold
            while notify_at <= absence:
                if not self.db.was_alert_sent(event, notify_at):
                    alerts.append(self._format_alert(event, absence, threshold))
                    self.db.mark_alert_sent(event, notify_at)
                    break
                notify_at += ALERT_REPEAT_INTERVAL

        return alerts

    def _format_alert(self, event: str, absence: int, threshold: int) -> str:
        icons = {
            "CrazyTime": "⏰", "CashHunt": "💰",
            "Pachinko":  "🎯", "CoinFlip": "🪙",
            "10": "🔟", "5": "5️⃣", "2": "2️⃣", "1": "1️⃣",
        }
        icon = icons.get(event, "🎡")
        ratio = absence / threshold
        if ratio >= 2.0:   level = "🚨🚨🚨 КРИТИЧНО"
        elif ratio >= 1.5: level = "🚨🚨 ОЧЕНЬ ДОЛГО"
        elif ratio >= 1.2: level = "⚠️⚠️ ДАВНО НЕ БЫЛО"
        else:              level = "⚠️ ВНИМАНИЕ"
        return (
            f"{level}\n\n"
            f"{icon} *{event}* не выпадает уже *{absence}* спинов\n"
            f"Порог: {threshold} | Превышение: {ratio:.1f}×"
        )
