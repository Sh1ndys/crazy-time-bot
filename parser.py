import asyncio
import aiohttp
import logging
from datetime import datetime, timezone
from config import SLOTYI_API, ALERT_REPEAT_INTERVAL

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.casinotrackpot.com/",
}

# wheelSector values from slotyi.com API
RESULT_MAP = {
    "1": "1",
    "2": "2",
    "5": "5",
    "10": "10",
    "coinflip": "CoinFlip",
    "coin flip": "CoinFlip",
    "pachinko": "Pachinko",
    "cashhunt": "CashHunt",
    "cash hunt": "CashHunt",
    "crazytime": "CrazyTime",
    "crazy time": "CrazyTime",
}


def normalize_result(spin):
    """
    Структура spins от slotyi.com:
    {
      "data": {
        "result": {
          "wheelSector": "CashHunt"  # или "1", "2", "5", "10" и т.д.
        }
      },
      "settledAt": "...",
      "id": "..."
    }
    """
    # Берём data если есть, иначе сам spin
    data = spin.get("data") or spin
    result = data.get("result") or {}
    
    # wheelSector прямо в result
    sector = str(result.get("wheelSector") or "").strip().lower()
    if sector:
        mapped = RESULT_MAP.get(sector)
        if mapped:
            return mapped
    
    # Fallback: ищем в outcome
    outcome = result.get("outcome") or {}
    if isinstance(outcome, dict):
        sector = str(outcome.get("wheelSector") or "").strip().lower()
        if sector:
            mapped = RESULT_MAP.get(sector)
            if mapped:
                return mapped
    
    logger.debug(f"Unknown result structure: {result}")
    return None


class TracksParser:
    def __init__(self, db, analyzer):
        self.db = db
        self.analyzer = analyzer
        self._running = False
        self._last_fetch = "never"
        self._consecutive_errors = 0

    def get_status(self):
        return {
            "running": self._running,
            "last_fetch": self._last_fetch,
            "errors": self._consecutive_errors,
        }

    async def fetch_and_process(self):
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
                        skipped += 1
                        continue
                    ts_raw = spin.get("settledAt") or spin.get("startedAt")
                    try:
                        timestamp = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                    except Exception:
                        timestamp = datetime.now(tz=timezone.utc)
                    spin_id = str(spin.get("id") or spin.get("transmissionId") or f"{result}_{ts_raw}")
                    is_new = self.db.save_spin(spin_id, result, timestamp)
                    if is_new:
                        saved += 1
                        self.db.clear_alert_history(result)
                logger.info(f"Saved: {saved}, skipped: {skipped}, total in DB: {self.db.get_total_spins()}")
                if saved > 0:
                    alerts = self._check_all_thresholds()
            self._last_fetch = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
            self._consecutive_errors = 0
        except Exception as e:
            self._consecutive_errors += 1
            logger.exception(f"Parser error: {e}")
        return alerts

    async def _fetch_new_spins(self):
        last_known_id = self.db.get_last_spin_id()
        last_spin = self.db.get_last_spin()
        last_ts = None
        if last_spin:
            try:
                last_ts = datetime.fromisoformat(
                    last_spin["timestamp"].replace("Z", "+00:00")
                ).timestamp()
            except Exception:
                pass

        data = None
        for attempt in range(3):
            try:
                async with aiohttp.ClientSession(headers=HEADERS) as session:
                    async with session.get(
                        SLOTYI_API,
                        timeout=aiohttp.ClientTimeout(total=30)
                    ) as resp:
                        if resp.status != 200:
                            logger.error(f"HTTP {resp.status} from slotyi.com")
                            return []
                        data = await resp.json(content_type=None)
                break
            except asyncio.TimeoutError:
                logger.warning(f"Timeout attempt {attempt+1}/3")
                if attempt == 2:
                    return []
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Request error: {e}")
                return []

        if not isinstance(data, list):
            logger.warning(f"Unexpected format: {type(data)}, content: {str(data)[:200]}")
            return []

        logger.debug(f"Got {len(data)} spins from API")

        all_new = []
        for spin in data:
            spin_id = str(spin.get("id") or "")
            spin_ts_raw = spin.get("settledAt") or spin.get("startedAt")

            if not last_known_id:
                all_new.append(spin)
                continue

            if spin_id and spin_id == str(last_known_id):
                break

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

        return list(reversed(all_new))

    def _check_all_thresholds(self):
        total = self.db.get_total_spins()
        if total < 200:
            logger.info(f"Skipping alerts: only {total} spins in DB")
            return []
        alerts = []
        absences = self.analyzer.current_absence_all()
        thresholds = self.db.get_all_thresholds()
        for event, absence in absences.items():
            threshold = thresholds.get(event, 300)
            if absence < threshold or absence >= total - 1:
                continue
            notify_at = threshold
            while notify_at <= absence:
                if not self.db.was_alert_sent(event, notify_at):
                    alerts.append(self._format_alert(event, absence, threshold))
                    self.db.mark_alert_sent(event, notify_at)
                    break
                notify_at += ALERT_REPEAT_INTERVAL
        return alerts

    def _format_alert(self, event, absence, threshold):
        icons = {
            "CrazyTime": "⏰", "CashHunt": "💰",
            "Pachinko": "🎯", "CoinFlip": "🪙",
            "10": "🔟", "5": "5️⃣", "2": "2️⃣", "1": "1️⃣",
        }
        icon = icons.get(event, "🎡")
        ratio = absence / threshold
        if ratio >= 2.0: level = "🚨🚨🚨 КРИТИЧНО"
        elif ratio >= 1.5: level = "🚨🚨 ОЧЕНЬ ДОЛГО"
        elif ratio >= 1.2: level = "⚠️⚠️ ДАВНО НЕ БЫЛО"
        else: level = "⚠️ ВНИМАНИЕ"
        return (
            f"{level}\n\n"
            f"{icon} *{event}* не выпадает уже *{absence}* спинов\n"
            f"Порог: {threshold} | Превышение: {ratio:.1f}x"
        )
