# 🎡 Crazy Time Stats Bot

Telegram бот для отслеживания статистики спинов Crazy Time.
Парсит данные с tracksino.com в реальном времени.

---

## 📋 Что умеет бот

- Парсит все спины с tracksino.com каждые 30 секунд
- Ведёт полную историю в SQLite базе данных
- Считает промежутки между выпадениями каждого события:
  - **Минимальный** промежуток (включая 0 — два выпадения подряд)
  - **Максимальный** промежуток за всё время и за месяц
  - **Среднее арифметическое** всех промежутков
- Отправляет уведомления в Telegram при превышении порогов
- Команды для просмотра статистики прямо в боте

---

## 🚀 Установка

### 1. Требования
- Python 3.11+
- pip

### 2. Клонируйте / скачайте файлы бота

### 3. Установите зависимости
```bash
pip install -r requirements.txt
```

### 4. Создайте Telegram бота
1. Напишите [@BotFather](https://t.me/BotFather) в Telegram
2. Отправьте `/newbot`
3. Следуйте инструкциям, получите **токен**

### 5. Узнайте ваш chat_id
1. Напишите [@userinfobot](https://t.me/userinfobot)
2. Он пришлёт ваш `id` — это и есть chat_id

### 6. Настройте config.py
```python
BOT_TOKEN = "1234567890:AAFxxxxxxxxxxxxx"  # токен от BotFather
CHAT_IDS  = [123456789]                    # ваш chat_id
```

### 7. Запустите бота
```bash
python bot.py
```

---

## 📱 Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Приветствие и список команд |
| `/stats` | Текущее кол-во спинов без каждого события |
| `/monthly` | Статистика промежутков за текущий месяц |
| `/event [название]` | Детальная статистика по событию |
| `/alerts` | Просмотр текущих порогов уведомлений |
| `/setalert [событие] [число]` | Установить порог |
| `/status` | Статус парсера и БД |
| `/help` | Справка |

### Примеры использования:
```
/event CrazyTime
/event CoinFlip
/setalert CrazyTime 200
/setalert 10 60
```

---

## 🎯 Названия событий

| Название в боте | Что означает |
|-----------------|-------------|
| `1` | Число 1 (×23 сегмента на колесе) |
| `2` | Число 2 (×15 сегментов) |
| `5` | Число 5 (×7 сегментов) |
| `10` | Число 10 (×4 сегмента) |
| `CoinFlip` | Бонус Coin Flip (×4) |
| `Pachinko` | Бонус Pachinko (×2) |
| `CashHunt` | Бонус Cash Hunt (×2) |
| `CrazyTime` | Бонус Crazy Time (×1) |

---

## ⚙️ Настройка порогов по умолчанию

В `config.py` → `DEFAULT_THRESHOLDS`:

```python
DEFAULT_THRESHOLDS = {
    "CrazyTime": 200,  # уведомить если не выпадал 200 спинов
    "CashHunt":  150,
    "Pachinko":  150,
    "CoinFlip":  100,
    "10":         80,
    "5":          50,
    "2":          30,
    "1":          20,
}
```

После первого порога уведомления повторяются каждые 10 спинов
(настраивается в `ALERT_REPEAT_INTERVAL`).

---

## 🔄 Запуск как сервис (Linux)

Создайте файл `/etc/systemd/system/crazytime-bot.service`:

```ini
[Unit]
Description=Crazy Time Telegram Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/crazy_time_bot
ExecStart=/usr/bin/python3 /home/ubuntu/crazy_time_bot/bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Затем:
```bash
sudo systemctl daemon-reload
sudo systemctl enable crazytime-bot
sudo systemctl start crazytime-bot
sudo systemctl status crazytime-bot
```

---

## ⚠️ Важно

Tracksino API может изменить структуру ответа. Если парсер перестал работать,
проверьте `/status` и логи в `bot.log`. Возможно потребуется обновить 
`_extract_spins()` в `parser.py`.

---

## 📊 Как считаются промежутки

**Промежуток** = количество спинов между двумя последовательными выпадениями одного события.

Пример для CrazyTime:
```
... 1, 2, CrazyTime, 5, 1, 2, 10, CrazyTime, 1, CrazyTime ...
промежутки:              [3]                  [1]
```
- Промежуток 3: после первого CrazyTime было 3 спина (5, 1, 2) до следующего
- Промежуток 1: после второго CrazyTime был 1 спин (1) до следующего
- Промежуток 0: CrazyTime два раза подряд
