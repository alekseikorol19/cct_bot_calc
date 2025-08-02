import sqlite3
import logging
from datetime import datetime
import zoneinfo
import os

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ConversationHandler,
    ContextTypes,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")

# Включаем логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
COUNTRY, PRICE, AGE, ENGINE, RATE1, RATE2 = range(6)

# Кнопки выбора
COUNTRY_OPTIONS = ["Китай", "Япония", "Корея"]
AGE_CATEGORIES = ["До 3 лет", "3–5 лет", "Старше 5 лет"]

# Фиксированные сборы по умолчанию для каждой страны
DEFAULT_FEES = {
    "Китай": {
        "broker": 98000,
        "ussuriysk": 7000,
        "agent": 80000,
        "utilization_under3": 3400,
        "utilization_over3": 5200,
        "transport": 15000,
    },
    "Япония": {
        "broker": 75000,
        "transfer": 5000,
        "agent": 60000,
        "utilization_under3": 3400,
        "utilization_over3": 5200,
        "transport": 105000,
    },
    "Корея": {
        "broker": 100000,
        "transfer": 5000,
        "agent": 80000,
        "utilization_under3": 3400,
        "utilization_over3": 5200,
        "transport": 1700000,
    },
}

DB_PATH = "bot_data.db"
TZ = zoneinfo.ZoneInfo("Asia/Vladivostok")


def init_db():
    """Инициализация SQLite-базы, создание таблиц."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Таблица курсов: хранит по дате и валюте два курса (в рубли и в евро)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS rates (
            date TEXT,
            currency TEXT,
            to_rub REAL,
            to_eur REAL,
            PRIMARY KEY (date, currency)
        )
        """
    )

    # Таблица фиксированных сборов
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS fees (
            country TEXT,
            name TEXT,
            value INTEGER,
            PRIMARY KEY (country, name)
        )
        """
    )

    # Вставляем сборы по умолчанию, если их ещё нет
    for country, fees in DEFAULT_FEES.items():
        for name, value in fees.items():
            cursor.execute(
                "INSERT OR IGNORE INTO fees (country, name, value) VALUES (?, ?, ?)",
                (country, name, value),
            )

    conn.commit()
    conn.close()


def get_today_rate(currency: str):
    """Получить курсы to_rub и to_eur для указанной валюты на сегодня."""
    today_str = datetime.now(TZ).date().isoformat()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT to_rub, to_eur FROM rates WHERE date = ? AND currency = ?", (today_str, currency)
    )
    row = cursor.fetchone()
    conn.close()
    if row:
        return row[0], row[1]
    return None, None


def set_today_rate(currency: str, to_rub: float, to_eur: float):
    """Сохранить или обновить курс валюты для сегодняшнего дня."""
    today_str = datetime.now(TZ).date().isoformat()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO rates (date, currency, to_rub, to_eur)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(date, currency) DO UPDATE SET to_rub = excluded.to_rub, to_eur = excluded.to_eur
        """,
        (today_str, currency, to_rub, to_eur),
    )
    conn.commit()
    conn.close()


def get_fee(country: str, name: str) -> int:
    """Получить значение фиксированного сбора по стране и имени."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT value FROM fees WHERE country = ? AND name = ?", (country, name)
    )
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 0


def set_fee(country: str, name: str, value: int):
    """Установить/обновить фиксированный сбор для страны."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE fees SET value = ? WHERE country = ? AND name = ?", (value, country, name)
    )
    conn.commit()
    conn.close()


async def set_commands(application: Application):
    """Устанавливает список команд бота для автозаполнения в Telegram."""
    commands = [
        BotCommand("start", "Приветственное сообщение"),
        BotCommand("help", "Справка по командам"),
        BotCommand("calculate", "Начать расчёт стоимости"),
        BotCommand("set_rates", "Установить курсы валют"),
        BotCommand("set_fees", "Установить фиксированные сборы"),
        BotCommand("cancel", "Отменить расчёт"),
    ]
    await application.bot.set_my_commands(commands)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я помощник для расчёта стоимости автомобиля «под ключ» до Владивостока.\n"
        "Чтобы начать расчёт, отправьте команду /calculate.\n"
        "Для справки используйте /help."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Доступные команды:\n"
        "/calculate — начать расчёт стоимости авто\n"
        "/set_rates <currency> <to_rub> <to_eur> — установить курсы валют на сегодня\n"
        "/set_fees <country> <name> <value> — установить фиксированный сбор\n"
        "/cancel — отменить текущий расчёт\n"
        "/help — эта справка"
    )


async def calculate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply_keyboard = [COUNTRY_OPTIONS]
    await update.message.reply_text(
        "Выберите страну импорта:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return COUNTRY


async def country_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    country = update.message.text
    if country not in COUNTRY_OPTIONS:
        await update.message.reply_text("Пожалуйста, выберите одну из кнопок: Китай, Япония или Корея.")
        return COUNTRY
    context.user_data["country"] = country
    await update.message.reply_text(
        f"Выбранно: {country}. Теперь введите стоимость автомобиля (целое число) в местной валюте.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return PRICE


async def price_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.replace(" ", "")
    if not text.isdigit():
        await update.message.reply_text("Некорректный ввод. Введите только целое число (например: 150000).")
        return PRICE

    context.user_data["price"] = int(text)
    country = context.user_data["country"]
    # Фиксированные транспортные расходы по стране
    context.user_data["transport"] = get_fee(country, "transport")

    # Спрашиваем возраст автомобиля
    reply_keyboard = [AGE_CATEGORIES]
    await update.message.reply_text(
        "Выберите возраст автомобиля (кнопкой):",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return AGE


async def age_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    age_choice = update.message.text
    if age_choice not in AGE_CATEGORIES:
        await update.message.reply_text("Пожалуйста, выберите одну из кнопок: «До 3 лет», «3–5 лет», «Старше 5 лет».")
        return AGE

    context.user_data["age_category"] = age_choice
    await update.message.reply_text(
        "Введите объём двигателя (целое число, в куб.см), например: 1800", reply_markup=ReplyKeyboardRemove()
    )
    return ENGINE


async def engine_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.replace(" ", "")
    if not text.isdigit():
        await update.message.reply_text("Некорректный ввод. Введите целое число (например: 1800).")
        return ENGINE

    context.user_data["engine_cc"] = int(text)

    # Проверяем, есть ли курсы на сегодня для выбранной валюты
    country = context.user_data["country"]
    currency = "CNY" if country == "Китай" else ("JPY" if country == "Япония" else "KRW")
    to_rub, to_eur = get_today_rate(currency)
    if to_rub is None or to_eur is None:
        # Запрашиваем курс: для Китая — за 1, для Японии — за 100, для Кореи — за 1000
        if currency == "CNY":
            await update.message.reply_text("Укажите текущий курс юаня (CNY→₽), например: 11.05")
        elif currency == "JPY":
            await update.message.reply_text("Укажите курс японской йены за 100 JPY (JPY→₽), например: 75.50")
        else:
            await update.message.reply_text("Укажите курс корейской воны за 1000 KRW (KRW→₽), например: 65.00")
        return RATE1

    # Если курс уже в базе, запоминаем и сразу считаем
    context.user_data["rate_rub"] = to_rub
    context.user_data["rate_eur"] = to_eur
    return await perform_calculation(update, context)


async def rate1_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.replace(",", ".")
    try:
        val = float(text)
    except ValueError:
        await update.message.reply_text("Некорректный ввод. Введите число, например: 75.50")
        return RATE1

    country = context.user_data["country"]
    currency = "CNY" if country == "Китай" else ("JPY" if country == "Япония" else "KRW")
    # Нормируем курс: для JPY делим на 100, для KRW делим на 1000
    if currency == "JPY":
        to_rub = val / 100.0
    elif currency == "KRW":
        to_rub = val / 1000.0
    else:
        to_rub = val
    context.user_data["rate_rub"] = to_rub

    # Теперь спрашиваем курс в евро
    if currency == "CNY":
        await update.message.reply_text("Укажите курс CNY→EUR, например: 0.13")
    elif currency == "JPY":
        await update.message.reply_text("Укажите курс JPY за 100 JPY→EUR, например: 0.0065")
    else:
        await update.message.reply_text("Укажите курс KRW за 1000 KRW→EUR, например: 0.00075")
    return RATE2


async def rate2_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.replace(",", ".")
    try:
        val = float(text)
    except ValueError:
        await update.message.reply_text("Некорректный ввод. Введите число, например: 0.13")
        return RATE2

    country = context.user_data["country"]
    currency = "CNY" if country == "Китай" else ("JPY" if country == "Япония" else "KRW")
    # Нормируем курс: для JPY делим на 100, для KRW делим на 1000
    if currency == "JPY":
        to_eur = val / 100.0
    elif currency == "KRW":
        to_eur = val / 1000.0
    else:
        to_eur = val
    context.user_data["rate_eur"] = to_eur

    # Сохраняем в базу
    set_today_rate(currency, context.user_data["rate_rub"], context.user_data["rate_eur"])
    return await perform_calculation(update, context)


async def perform_calculation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    country = context.user_data["country"]
    price = context.user_data["price"]
    transport = context.user_data["transport"]
    rate_rub = context.user_data["rate_rub"]
    rate_eur = context.user_data["rate_eur"]
    age_category = context.user_data["age_category"]
    engine_cc = context.user_data["engine_cc"]

    # Фиксированные сборы
    broker_fee = get_fee(country, "broker")
    transfer_fee = get_fee(country, "transfer") if country in ["Япония", "Корея"] else get_fee(country, "ussuriysk")
    agent_fee = get_fee(country, "agent")
    utilization_fee = (
        get_fee(country, "utilization_under3")
        if age_category == "До 3 лет"
        else get_fee(country, "utilization_over3")
    )

    # 1) Общая сумма в местной валюте и перевод в рубли
    total_local = price + transport
    total_rub = total_local * rate_rub

    # 2) Перевод в евро через локальную валюту
    total_eur = price * rate_eur

    # 3) Рассчёт таможенной пошлины (EUR)
    tax_eur = 0.0
    if age_category == "До 3 лет":
        if total_eur <= 8500:
            duty_pct = 0.54
            duty_min = 2.5 * engine_cc
        elif total_eur <= 16700:
            duty_pct = 0.48
            duty_min = 3.5 * engine_cc
        elif total_eur <= 42300:
            duty_pct = 0.48
            duty_min = 5.5 * engine_cc
        elif total_eur <= 84500:
            duty_pct = 0.48
            duty_min = 7.5 * engine_cc
        elif total_eur <= 169000:
            duty_pct = 0.48
            duty_min = 15.0 * engine_cc
        else:
            duty_pct = 0.48
            duty_min = 20.0 * engine_cc
        tax_eur = max(total_eur * duty_pct, duty_min)
    elif age_category == "3–5 лет":
        if engine_cc <= 1000:
            duty_eur = 1.5 * engine_cc
        elif engine_cc <= 1500:
            duty_eur = 1.7 * engine_cc
        elif engine_cc <= 1800:
            duty_eur = 2.5 * engine_cc
        elif engine_cc <= 2300:
            duty_eur = 2.7 * engine_cc
        elif engine_cc <= 3000:
            duty_eur = 3.0 * engine_cc
        else:
            duty_eur = 3.6 * engine_cc
        tax_eur = duty_eur
    else:
        if engine_cc <= 1000:
            duty_eur = 3.0 * engine_cc
        elif engine_cc <= 1500:
            duty_eur = 3.2 * engine_cc
        elif engine_cc <= 1800:
            duty_eur = 3.5 * engine_cc
        elif engine_cc <= 2300:
            duty_eur = 4.8 * engine_cc
        elif engine_cc <= 3000:
            duty_eur = 5.0 * engine_cc
        else:
            duty_eur = 5.7 * engine_cc
        tax_eur = duty_eur

    # 4) Конвертация таможни в рубли через EUR→₽ = (rate_rub)/(rate_eur)
    rate_eur_rub = rate_rub / rate_eur
    tax_rub = tax_eur * rate_eur_rub

    # 5) Суммируем всё вместе
    total_rub_with_fees = (
        total_rub + tax_rub + broker_fee + transfer_fee + agent_fee + utilization_fee
    )

    response = (
        f"Расчёт стоимости автомобиля «под ключ» из {country} до Владивостока:\n\n"
        f"1. Стоимость авто (местная валюта): {price:,} + Транспорт: {transport:,} = {total_local:,}\n"
        f"   Курс локальной валюты→₽: {rate_rub:.4f} → {total_rub:,.0f} ₽\n"
        f"   Курс локальной валюты→EUR: {rate_eur:.6f} → {total_eur:,.2f} EUR\n\n"
        f"2. Возраст: {age_category}, объём: {engine_cc} см³\n"
        f"   Таможенная пошлина: {tax_eur:,.2f} EUR → {tax_rub:,.0f} ₽ (через EUR→₽ = {rate_eur_rub:.4f})\n\n"
        f"3. Фиксированные сборы (₽):\n"
        f"   • Брокер: {broker_fee:,} ₽\n"
        f"   • Перегон: {transfer_fee:,} ₽\n"
        f"   • Агент: {agent_fee:,} ₽\n"
        f"   • Утилизационный сбор: {utilization_fee:,} ₽\n\n\n"
        f"Итого: {total_rub_with_fees:,.0f} ₽"
    )

    # Кнопка "Посчитать еще раз":
    retry_markup = ReplyKeyboardMarkup(
        [["/calculate"]], one_time_keyboard=True, resize_keyboard=True
    )
    await update.message.reply_text(response, reply_markup=retry_markup)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Расчёт отменён. При необходимости запустите /calculate заново.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


async def set_rates_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) != 3:
        await update.message.reply_text(
            "Использование: /set_rates <currency> <to_rub> <to_eur>\nНапример: /set_rates JPY 75.50 0.0065"
        )
        return
    currency = args[0].upper()
    if currency not in ["CNY", "JPY", "KRW"]:
        await update.message.reply_text("Неверная валюта. Допустимо: CNY, JPY, KRW.")
        return
    try:
        to_rub = float(args[1].replace(",", "."))
        to_eur = float(args[2].replace(",", "."))
    except ValueError:
        await update.message.reply_text(
            "Неверный формат. Введите числа, например: /set_rates JPY 75.50 0.0065"
        )
        return
    set_today_rate(currency, to_rub, to_eur)
    await update.message.reply_text(
        f"Курс для {currency} на сегодня установлен:\n"
        f"→ ₽: {to_rub:.4f}\n"
        f"→ EUR: {to_eur:.6f}"
    )


async def set_fees_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) != 3:
        await update.message.reply_text(
            "Использование: /set_fees <country> <name> <value>\n"
            "Например: /set_fees Япония broker 80000"
        )
        return
    country = args[0].capitalize()
    name = args[1].lower()
    if country not in DEFAULT_FEES or name not in DEFAULT_FEES[country]:
        await update.message.reply_text(
            "Неверный формат. Проверьте, что указали правильную страну и имя сбора."
        )
        return
    try:
        value = int(args[2])
    except ValueError:
        await update.message.reply_text(
            "Неверный формат суммы. Введите целое число, например: 120000"
        )
        return
    set_fee(country, name, value)
    await update.message.reply_text(f"Сбор '{name}' для {country} обновлён: {value:,} ₽")


def main():
    # Инициализируем базу данных (таблицы, значения по умолчанию)
    init_db()

    # Замените "ВАШ_ТОКЕН_ОТ_BOTFATHER" на реальный токен
    application = Application.builder().token(BOT_TOKEN).post_init(set_commands).build()
    # ConversationHandler для /calculate
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("calculate", calculate_command)],
        states={
            COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, country_handler)],
            PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, price_handler)],
            AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, age_handler)],
            ENGINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, engine_handler)],
            RATE1: [MessageHandler(filters.TEXT & ~filters.COMMAND, rate1_handler)],
            RATE2: [MessageHandler(filters.TEXT & ~filters.COMMAND, rate2_handler)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Регистрируем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("set_rates", set_rates_command))
    application.add_handler(CommandHandler("set_fees", set_fees_command))

    # Запускаем Polling
    application.run_polling()


if __name__ == "__main__":
    main()
