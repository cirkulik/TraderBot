"""Два языка интерфейса: русский (ru) и азербайджанский (az).

Здесь только тексты. Логика выбора языка — в goldbot/telegram.py.
"""
from __future__ import annotations

DEFAULT_LANG = "ru"

LANGUAGES = {
    "ru": "🇷🇺 Русский",
    "az": "🇦🇿 Azərbaycan",
}

LANGUAGE_NAMES = {
    "ru": "Русский",
    "az": "Azərbaycan",
}

# Названия сетапов для сообщений (в консоли остаются русские).
SETUP_LABELS = {
    "ru": {
        "dip_london": "Отскок после падения (Лондон)",
        "dip_late": "Отскок после падения (день Лондона)",
        "rsi_ny": "RSI-разворот (Нью-Йорк)",
        "stretch_ny": "Растяжение от EMA50 (Нью-Йорк)",
        "sell_noon": "Дневной откат (продажа в 12:00)",
        "sell_break": "Пробой вниз (продажа)",
        "swing_high": "Вход у хая по тренду (продажа)",
        "pullback_cross": "Возврат выше EMA10 (покупка)",
        "keltner": "Растяжение от EMA20 (канал Кельтнера)",
        "fade_late": "Провал пробоя (вечер, продажа)",
        "short_rally_london": "Продажа после роста (Лондон)",
    },
    "az": {
        "dip_london": "Düşüşdən sonra sıçrayış (London)",
        "dip_late": "Düşüşdən sonra sıçrayış (London günü)",
        "rsi_ny": "RSI dönüşü (Nyu-York)",
        "stretch_ny": "EMA50-dən uzaqlaşma (Nyu-York)",
        "sell_noon": "Günlük geri çəkilmə (12:00 satış)",
        "sell_break": "Aşağı qırılma (satış)",
        "swing_high": "Trenddə zirvədən satış",
        "pullback_cross": "EMA10-dan yuxarı qayıdış (alış)",
        "keltner": "EMA20-dən uzanma (Keltner kanalı)",
        "fade_late": "Uğursuz qırılma (axşam, satış)",
        "short_rally_london": "Yüksəlişdən sonra satış (London)",
    },
}

# Подписи сессий из config.json (ключ — русская подпись).
SESSION_LABELS = {
    "az": {
        "Лондонская сессия": "London sessiyası",
        "День Лондона": "London günü",
        "Нью-Йоркская сессия": "Nyu-York sessiyası",
        "Вторая половина дня": "Günün ikinci yarısı",
        "Дневной откат": "Günlük geri çəkilmə",
        "Тренд вниз": "Aşağı trend",
        "Тренд вверх": "Yüksəliş trendi",
        "Вечерняя сессия": "Axşam sessiyası",
        "торговая": "ticarət sessiyası",
    },
}

# Причины входа. Ключ -> шаблон. Параметры подставляются через str.format.
REASONS = {
    "ru": {
        "drop_buy": "Резкое падение за {bars} баров: {move:+.1f} ATR — перепроданность",
        "drop_sell": "Резкий рост за {bars} баров: {move:+.1f} ATR — перекупленность",
        "drop_bounce_buy": "Отскок после импульсного движения вниз",
        "drop_bounce_sell": "Откат после импульсного движения вверх",
        "clock": (
            "Вход по времени {hh:02d}:{mm:02d} (сервер) — статистический "
            "внутридневной откат после утреннего движения"
        ),
        "rsi_up": "RSI(14) = {rsi:.1f} и развернулся вверх",
        "rsi_down": "RSI(14) = {rsi:.1f} и развернулся вниз",
        "stretch_below": "Цена ниже EMA{period} на {dist:.1f} ATR — сильное отклонение",
        "stretch_above": "Цена выше EMA{period} на {dist:.1f} ATR — сильное отклонение",
        "breakout_high": "Пробой максимума за {bars} баров",
        "breakout_low": "Пробой минимума за {bars} баров",
        "fade_break_high": "Ложный пробой максимума за {bars} баров — возврат внутрь диапазона",
        "fade_break_low": "Ложный пробой минимума за {bars} баров — возврат внутрь диапазона",
        "atr": "ATR(14) = {atr:.2f} $ — волатильность учтена в SL/TP",
        "ema200_bull": "Цена выше EMA200 — глобальный тренд вверх",
        "ema200_bear": "Цена ниже EMA200 — глобальный тренд вниз",
        "ema200_against_buy": "Цена ниже EMA200 — вход против глобального тренда (осторожно)",
        "ema200_against_sell": "Цена выше EMA200 — вход против глобального тренда (осторожно)",
        "adx": "ADX(14) = {adx:.1f}",
        "trend_filter_up": "Фильтр тренда: EMA50 выше EMA200 — вход только по тренду вверх",
        "trend_filter_down": "Фильтр тренда: EMA50 ниже EMA200 — вход только по тренду вниз",
        "mtf_trend_up": "Старший ТФ M{minutes}: EMA50 выше EMA200 — тренд вверх",
        "mtf_trend_down": "Старший ТФ M{minutes}: EMA50 ниже EMA200 — тренд вниз",
        "mtf_adx": "ADX M{minutes} = {adx:.1f} — тренд подтверждён",
        "xm_xag_confirm_down": "Серебро: {value:.2f}% за {hours} ч — подтверждает продажу",
        "xm_xag_confirm_up": "Серебро: {value:.2f}% за {hours} ч — подтверждает покупку",
        "xm_xag_trend_down": "Серебро в тренде вниз (EMA50 ниже EMA200 на M15) — подтверждает продажу",
        "xm_xag_trend_up": "Серебро в тренде вверх (EMA50 выше EMA200 на M15) — подтверждает покупку",
        "xm_eur_confirm_down": "EURUSD: {value:.2f}% за {hours} ч — доллар растёт, продажа подтверждена",
        "xm_eur_confirm_up": "EURUSD: {value:.2f}% за {hours} ч — доллар слабеет, покупка подтверждена",
        "xm_eur_trend_down": "EURUSD ниже EMA200 на M15 — доллар силён, продажа подтверждена",
        "xm_eur_trend_up": "EURUSD выше EMA200 на M15 — доллар слаб, покупка подтверждена",
        "session": "Сессия: {label}",
        "swing_high_zone": "Цена у максимума последних {bars} баров — локальный хай",
        "swing_high_reject": "Медвежий бар с верхней тенью {wick:.1f} ATR — отказ от роста",
        "swing_high_rsi": "RSI(14) = {rsi:.1f} — отскок был перекуплен",
        "pc_cross_up": "Цена снова выше EMA{period} — откат вниз выкуплен",
        "pc_cross_down": "Цена снова ниже EMA{period} — отскок вверх продан",
        "pc_rsi_up": "RSI(14) на откате = {rsi:.1f} — перекупленности не было",
        "pc_rsi_down": "RSI(14) на отскоке = {rsi:.1f} — перепроданности не было",
        "kelt_above": "Цена на {dist:.1f} ATR выше EMA{period} — растяжение вверх",
        "kelt_below": "Цена на {dist:.1f} ATR ниже EMA{period} — растяжение вниз",
        "kelt_reject_sell": "Бар закрылся вниз — отказ от роста",
        "kelt_reject_buy": "Бар закрылся вверх — отказ от падения",
        "raw": "{text}",
    },
    "az": {
        "drop_buy": "Son {bars} barda kəskin eniş: {move:+.1f} ATR — həddindən artıq satış",
        "drop_sell": "Son {bars} barda kəskin yüksəliş: {move:+.1f} ATR — həddindən artıq alış",
        "drop_bounce_buy": "İmpuls enişindən sonra geri sıçrayış",
        "drop_bounce_sell": "İmpuls yüksəlişindən sonra geri çəkilmə",
        "clock": (
            "Vaxta görə giriş {hh:02d}:{mm:02d} (server) — səhər hərəkətindən "
            "sonra statistik günortadaxili geri çəkilmə"
        ),
        "rsi_up": "RSI(14) = {rsi:.1f} və yuxarı döndü",
        "rsi_down": "RSI(14) = {rsi:.1f} və aşağı döndü",
        "stretch_below": "Qiymət EMA{period}-dən {dist:.1f} ATR aşağıda — güclü kənarlaşma",
        "stretch_above": "Qiymət EMA{period}-dən {dist:.1f} ATR yuxarıda — güclü kənarlaşma",
        "breakout_high": "{bars} barın maksimumunun qırılması",
        "breakout_low": "{bars} barın minimumunun qırılması",
        "fade_break_high": "{bars} barın maksimumunun uğursuz qırılması — diapazona qayıdış",
        "fade_break_low": "{bars} barın minimumunun uğursuz qırılması — diapazona qayıdış",
        "atr": "ATR(14) = {atr:.2f} $ — volatillik SL/TP-də nəzərə alınıb",
        "ema200_bull": "Qiymət EMA200-dən yuxarıda — qlobal yüksəliş trendi",
        "ema200_bear": "Qiymət EMA200-dən aşağıda — qlobal eniş trendi",
        "ema200_against_buy": "Qiymət EMA200-dən aşağıda — qlobal trendə qarşı giriş (ehtiyatlı)",
        "ema200_against_sell": "Qiymət EMA200-dən yuxarıda — qlobal trendə qarşı giriş (ehtiyatlı)",
        "adx": "ADX(14) = {adx:.1f}",
        "trend_filter_up": "Trend filtri: EMA50 EMA200-dən yuxarıda — yalnız yüksəliş trendi ilə giriş",
        "trend_filter_down": "Trend filtri: EMA50 EMA200-dən aşağıda — yalnız eniş trendi ilə giriş",
        "mtf_trend_up": "Böyük TF M{minutes}: EMA50 EMA200-dən yuxarıda — yüksəliş trendi",
        "mtf_trend_down": "Böyük TF M{minutes}: EMA50 EMA200-dən aşağıda — eniş trendi",
        "mtf_adx": "ADX M{minutes} = {adx:.1f} — trend təsdiqlənib",
        "xm_xag_confirm_down": "Gümüş: {value:.2f}% son {hours} saat — satışı təsdiqləyir",
        "xm_xag_confirm_up": "Gümüş: {value:.2f}% son {hours} saat — alışı təsdiqləyir",
        "xm_xag_trend_down": "Gümüş M15-də aşağı trenddədir (EMA50 EMA200-dən aşağı) — satışı təsdiqləyir",
        "xm_xag_trend_up": "Gümüş M15-də yuxarı trenddədir (EMA50 EMA200-dən yuxarı) — alışı təsdiqləyir",
        "xm_eur_confirm_down": "EURUSD: {value:.2f}% son {hours} saat — dollar güclənir, satış təsdiqləndi",
        "xm_eur_confirm_up": "EURUSD: {value:.2f}% son {hours} saat — dollar zəifləyir, alış təsdiqləndi",
        "xm_eur_trend_down": "EURUSD M15-də EMA200-dən aşağıdadır — dollar güclüdür, satış təsdiqləndi",
        "xm_eur_trend_up": "EURUSD M15-də EMA200-dən yuxarıdadır — dollar zəifdir, alış təsdiqləndi",
        "session": "Sessiya: {label}",
        "swing_high_zone": "Qiymət son {bars} barın maksimumundadır — lokal zirvə",
        "swing_high_reject": "Üst kölgəsi {wick:.1f} ATR olan ayı barı — yüksəlişdən imtina",
        "swing_high_rsi": "RSI(14) = {rsi:.1f} — sıçrayış həddindən artıq alınmışdı",
        "pc_cross_up": "Qiymət yenidən EMA{period}-dən yuxarıdadır — eniş geri çəkilməsi alınıb",
        "pc_cross_down": "Qiymət yenidən EMA{period}-dən aşağıdadır — yüksəliş geri çəkilməsi satılıb",
        "pc_rsi_up": "Geri çəkilmədə RSI(14) = {rsi:.1f} — həddindən artıq alış yox idi",
        "pc_rsi_down": "Sıçrayışda RSI(14) = {rsi:.1f} — həddindən artıq satış yox idi",
        "kelt_above": "Qiymət EMA{period}-dən {dist:.1f} ATR yuxarıda — yuxarı uzanma",
        "kelt_below": "Qiymət EMA{period}-dən {dist:.1f} ATR aşağıda — aşağı uzanma",
        "kelt_reject_sell": "Bar aşağı bağlandı — yüksəlişdən imtina",
        "kelt_reject_buy": "Bar yuxarı bağlandı — enişdən imtina",
        "raw": "{text}",
    },
}

# Сообщения бота (команды, ответы, уведомления).
MESSAGES = {
    "ru": {
        "choose_lang": "Выберите язык / Dil seçin",
        "welcome": (
            "✅ GoldBot подключён.\n"
            "Сюда будут приходить сигналы XAUUSD M5 и результаты сделок.\n\n"
            "/stop — отключить сигналы\n"
            "/lang — сменить язык"
        ),
        "welcome_admin": (
            "✅ GoldBot подключён (администратор).\n"
            "Сигналы XAUUSD M5 и результаты сделок будут приходить сюда.\n\n"
            "/users — список пользователей\n"
            "/allow <id|@username> — выдать доступ\n"
            "/deny <id|@username> — отозвать доступ\n"
            "/remove <id|@username> — удалить\n"
            "/lang — сменить язык"
        ),
        "paused": "⏸ Сигналы отключены. Отправьте /start, чтобы включить снова.",
        "lang_set": "✅ Язык изменён: {name}",
        "help": (
            "Команды:\n"
            "/start — включить сигналы\n"
            "/stop — отключить сигналы\n"
            "/lang — сменить язык"
        ),
        "not_admin": "Эта команда только для администратора.",
        "admin_no_stop": "Администратор всегда получает сигналы — отключить себя нельзя.",
        "startup_title": "🤖 GoldBot запущен",
        "startup_setups": "XAUUSD M5, сетапы: {setups}",
        "startup_wait": "Ждём сигналы: 09:00–18:00 (время сервера).",
        "admin_added": "✅ Доступ выдан: {name} ({chat_id})",
        "admin_added_not_started": (
            "✅ ID добавлен: {chat_id}. Человек получит сигналы после того, "
            "как нажмёт Start у бота."
        ),
        "admin_need_ref": "Укажите ID или @username: {command} 123456789 или {command} @nick",
        "admin_not_found": "Не найден: {ref}.",
        "admin_not_found_username": (
            "По @username можно добавить только того, кто уже писал боту. "
            "Добавьте по числовому ID."
        ),
        "admin_revoked": "⛔ Доступ отозван: {name} ({chat_id})",
        "admin_removed": "🗑 Удалён: {name} ({chat_id})",
        "admin_self": "Нельзя изменить доступ администратора.",
        "admin_users_empty": "👥 Пока никого нет.",
        "admin_users_header": "👥 Пользователи ({count}):",
        "users_not_started": "не запускал",
        "user_allowed_you": "✅ Администратор открыл доступ. Сигналы XAUUSD M5 начнут приходить.",
        "user_revoked_you": "⛔ Администратор закрыл доступ.",
        "note_short": "макс. цена {mfe:.2f}, до TP не хватило {gap:.2f} $",
        "note_pierce": "TP с проколом на {pierce:.2f} $, пик в баре тейка {peak:.2f}",
    },
    "az": {
        "choose_lang": "Выберите язык / Dil seçin",
        "welcome": (
            "✅ GoldBot qoşuldu.\n"
            "Buraya XAUUSD M5 siqnalları və sövdələrin nəticələri gələcək.\n\n"
            "/stop — siqnalları söndür\n"
            "/lang — dili dəyiş"
        ),
        "welcome_admin": (
            "✅ GoldBot qoşuldu (administrator).\n"
            "XAUUSD M5 siqnalları və sövdələrin nəticələri buraya gələcək.\n\n"
            "/users — istifadəçilərin siyahısı\n"
            "/allow <id|@username> — giriş ver\n"
            "/deny <id|@username> — girişi ləğv et\n"
            "/remove <id|@username> — sil\n"
            "/lang — dili dəyiş"
        ),
        "paused": "⏸ Siqnallar söndürüldü. Yenidən açmaq üçün /start göndərin.",
        "lang_set": "✅ Dil dəyişdirildi: {name}",
        "help": (
            "Komandalar:\n"
            "/start — siqnalları aç\n"
            "/stop — siqnalları söndür\n"
            "/lang — dili dəyiş"
        ),
        "not_admin": "Bu komanda yalnız administrator üçündür.",
        "admin_no_stop": "Administrator həmişə siqnal alır — özünü söndürə bilməz.",
        "startup_title": "🤖 GoldBot işə salındı",
        "startup_setups": "XAUUSD M5, setaplar: {setups}",
        "startup_wait": "Siqnalları gözləyirik: 09:00–18:00 (server vaxtı).",
        "admin_added": "✅ Giriş icazəsi verildi: {name} ({chat_id})",
        "admin_added_not_started": (
            "✅ ID əlavə edildi: {chat_id}. İstifadəçi bota Start basdıqdan "
            "sonra siqnal alacaq."
        ),
        "admin_need_ref": "ID və ya @username göstərin: {command} 123456789 və ya {command} @nick",
        "admin_not_found": "Tapılmadı: {ref}.",
        "admin_not_found_username": (
            "@username ilə yalnız bota əvvəl yazmış istifadəçini əlavə etmək olar. "
            "Rəqəmsal ID ilə əlavə edin."
        ),
        "admin_revoked": "⛔ Giriş icazəsi ləğv edildi: {name} ({chat_id})",
        "admin_removed": "🗑 Silindi: {name} ({chat_id})",
        "admin_self": "Administratorun girişini dəyişmək olmaz.",
        "admin_users_empty": "👥 Hələ heç kim yoxdur.",
        "admin_users_header": "👥 İstifadəçilər ({count}):",
        "users_not_started": "başlatmayıb",
        "user_allowed_you": "✅ Administrator girişi açdı. XAUUSD M5 siqnalları gəlməyə başlayacaq.",
        "user_revoked_you": "⛔ Administrator girişi bağladı.",
        "note_short": "maks. qiymət {mfe:.2f}, TP-yə {gap:.2f} $ çatmadı",
        "note_pierce": "TP {pierce:.2f} $ keçməklə alındı, TP barında pik {peak:.2f}",
    },
}


# Готовые шаблоны сообщений.
SIGNAL_TEMPLATE = {
    "ru": (
        "{arrow}  XAUUSD M5 — {label}\n"
        "Бар: {bar} (время сервера)\n"
        "\n"
        "Вход: {entry:.2f}\n"
        "SL: {sl:.2f}  ({risk:.2f} $ по цене)\n"
        "TP: {tp:.2f}  ({rr:.1f}R)\n"
        "Риск: {risk:.2f} $ ({points:.0f} п.)   Спред: {spread:.0f} п.\n"
        "Уверенность: {confidence}/100\n"
        "Макс. удержание: {bars} баров (~{hours:.1f} ч)\n"
        "\n"
        "Причины:"
    ),
    "az": (
        "{arrow}  XAUUSD M5 — {label}\n"
        "Bar: {bar} (server vaxtı)\n"
        "\n"
        "Giriş: {entry:.2f}\n"
        "SL: {sl:.2f}  ({risk:.2f} $ qiymətlə)\n"
        "TP: {tp:.2f}  ({rr:.1f}R)\n"
        "Risk: {risk:.2f} $ ({points:.0f} bənd)   Spread: {spread:.0f} bənd\n"
        "Etibarlılıq: {confidence}/100\n"
        "Maks. saxlama: {bars} bar (~{hours:.1f} saat)\n"
        "\n"
        "Səbəblər:"
    ),
}

CLOSE_TEMPLATE = {
    "ru": (
        "{icon} Закрыт {direction} {label}: {reason}\n"
        "Результат: {sign}{r:.2f}R ({sign}{points:.2f} $ по цене)"
    ),
    "az": (
        "{icon} Bağlandı {direction} {label}: {reason}\n"
        "Nəticə: {sign}{r:.2f}R ({sign}{points:.2f} $ qiymətlə)"
    ),
}


def normalize_lang(lang: str | None) -> str:
    """Приводит код языка к поддерживаемому. Неизвестный -> язык по умолчанию."""
    code = str(lang or "").strip().lower()
    if code in LANGUAGES:
        return code
    if code.startswith("az"):
        return "az"
    return DEFAULT_LANG


def t(lang: str, key: str, **params) -> str:
    """Текст по ключу на нужном языке. Без исключений при отсутствии ключа."""
    code = normalize_lang(lang)
    table = MESSAGES.get(code, MESSAGES[DEFAULT_LANG])
    template = table.get(key) or MESSAGES[DEFAULT_LANG].get(key, key)
    try:
        return template.format(**params)
    except (KeyError, IndexError, ValueError):
        return template


def setup_label(setup_name: str, lang: str) -> str:
    code = normalize_lang(lang)
    labels = SETUP_LABELS.get(code, SETUP_LABELS[DEFAULT_LANG])
    return labels.get(setup_name) or SETUP_LABELS[DEFAULT_LANG].get(setup_name, setup_name)


def session_label(label: str, lang: str) -> str:
    code = normalize_lang(lang)
    return SESSION_LABELS.get(code, {}).get(label, label)


def render_reason(item, lang: str) -> str:
    """Одна причина входа: (ключ, параметры) -> текст на нужном языке."""
    if isinstance(item, str):
        return item
    key, params = item
    code = normalize_lang(lang)
    if key == "session":
        params = {**params, "label": session_label(params.get("label", ""), code)}
    template = REASONS.get(code, REASONS[DEFAULT_LANG]).get(key)
    if template is None:
        template = REASONS[DEFAULT_LANG].get(key, "{text}")
    try:
        return template.format(**params)
    except (KeyError, IndexError, ValueError):
        return REASONS[DEFAULT_LANG].get("raw", "{text}").format(
            text=params.get("text", key)
        )


def tp_note(lang: str, gap, mfe, exit_bar_peak=None) -> str:
    """Пояснение к закрытию сделки: недолёт до TP или прокол тейка."""
    try:
        gap = float(gap)
        mfe = float(mfe)
    except (TypeError, ValueError):
        return ""
    if gap > 0:
        return t(lang, "note_short", mfe=mfe, gap=gap)
    try:
        peak = float(exit_bar_peak)
    except (TypeError, ValueError):
        return ""
    return t(lang, "note_pierce", pierce=-gap, peak=peak)
