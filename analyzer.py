"""
Аналитика: промежутки между выпадениями, статистика по месяцам
"""

import logging
from datetime import datetime, timezone
from database import Database

logger = logging.getLogger(__name__)

EVENTS = ["1", "2", "5", "10", "CoinFlip", "Pachinko", "CashHunt", "CrazyTime"]


def compute_gaps(spins: list[str], event: str) -> list[int]:
    """
    Вычислить все промежутки (в спинах) между последовательными выпадениями события.

    Промежуток = количество спинов между двумя соседними выпадениями события.
    Если событие выпало два раза подряд — промежуток = 0.
    Первый промежуток считается от начала данных.
    """
    gaps = []
    counter = 0
    first_hit = False

    for result in spins:
        if result == event:
            if first_hit:
                gaps.append(counter)
            else:
                first_hit = True
            counter = 0
        else:
            if first_hit:
                counter += 1

    return gaps


def gap_stats(gaps: list[int]) -> dict:
    """Посчитать мин/макс/среднее для списка промежутков"""
    if not gaps:
        return {"min": 0, "max": 0, "avg": 0.0, "count": 0}
    return {
        "min": min(gaps),
        "max": max(gaps),
        "avg": sum(gaps) / len(gaps),
        "count": len(gaps),
    }


class Analyzer:
    def __init__(self, db: Database):
        self.db = db

    def current_absence_all(self) -> dict[str, int]:
        """
        Для каждого события — сколько спинов прошло с последнего выпадения.
        Использует только последние 5000 спинов (достаточно для любого события).
        """
        recent = self.db.get_recent_spins(5000)  # убывающий порядок (новые первые)
        result = {}

        for event in EVENTS:
            count = 0
            for spin in recent:
                if spin["result"] == event:
                    break
                count += 1
            else:
                # Не встретили событие в последних 5000 — возвращаем 5000+
                count = len(recent)
            result[event] = count

        return result

    def current_absence(self, event: str) -> int:
        """Сколько спинов прошло без конкретного события"""
        all_abs = self.current_absence_all()
        return all_abs.get(event, 0)

    def monthly_stats(self) -> dict[str, dict]:
        """
        Статистика за текущий месяц:
        для каждого события — кол-во выпадений, мин/макс/среднее промежутки.
        """
        now = datetime.now(tz=timezone.utc)
        spins = self.db.get_spins_in_month(now.year, now.month)

        if not spins:
            return {}

        result = {}
        for event in EVENTS:
            gaps = compute_gaps(spins, event)
            hits = spins.count(event)
            s = gap_stats(gaps)
            result[event] = {
                "count": hits,
                "min":   s["min"],
                "max":   s["max"],
                "avg":   s["avg"],
                "gaps":  gaps,
            }

        return result

    def event_detail_stats(self, event: str) -> dict | None:
        """
        Полная статистика по одному событию:
        - за всё время: мин/макс/среднее промежутки, топ-5 максимальных
        - за текущий месяц: то же самое
        - текущее невыпадение
        """
        all_spins = self.db.get_all_spins_ordered()
        if not all_spins:
            return None

        # За всё время
        all_gaps = compute_gaps(all_spins, event)
        all_s = gap_stats(all_gaps)
        top_gaps = sorted(all_gaps, reverse=True)[:5]
        total_hits = all_spins.count(event)

        # За текущий месяц
        now = datetime.now(tz=timezone.utc)
        month_spins = self.db.get_spins_in_month(now.year, now.month)
        month_gaps = compute_gaps(month_spins, event)
        month_s = gap_stats(month_gaps)
        month_hits = month_spins.count(event)

        # Текущее невыпадение
        current_absence = self.current_absence(event)

        return {
            "event":           event,
            "total_hits":      total_hits,
            "all_min":         all_s["min"],
            "all_max":         all_s["max"],
            "all_avg":         all_s["avg"],
            "top_gaps":        top_gaps,
            "month_hits":      month_hits,
            "month_min":       month_s["min"],
            "month_max":       month_s["max"],
            "month_avg":       month_s["avg"],
            "current_absence": current_absence,
        }

    def all_events_summary(self) -> list[dict]:
        """Сводная таблица по всем событиям"""
        all_spins = self.db.get_all_spins_ordered()
        summary = []

        for event in EVENTS:
            gaps = compute_gaps(all_spins, event)
            s = gap_stats(gaps)
            absence = self.current_absence(event)
            summary.append({
                "event":   event,
                "hits":    all_spins.count(event),
                "min":     s["min"],
                "max":     s["max"],
                "avg":     s["avg"],
                "absence": absence,
            })

        return summary
