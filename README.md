# Svitlobot HA (Power Watchdog)

Кастомна інтеграція для **Home Assistant**, яка відстежує наявність “світла” за станом/доступністю **датчика напруги** (будь-якої `sensor.*`) і, коли світло є, **пінгує SvitloBot** кожні ~30 секунд.

> Інтеграція **не надсилає Telegram-повідомлень**. Є лише ping у SvitloBot за `channel_key`.

---

## Можливості

- Відстеження **online/offline** для сутності напруги (`voltage_entity_id`).
- **Debounce**: підтвердження зміни стану через N секунд (захист від “флапів”).
- **Stale timeout**: якщо датчик не оновлювався N секунд — вважаємо “offline”.
- **Refresh**: опціонально примусово викликає `homeassistant.update_entity` раз на N секунд.
- **SvitloBot ping**: якщо світло є (online) — GET-запит:
  `https://api.svitlobot.in.ua/channelPing?channel_key=<SVITLOBOT_CHANNEL_KEY>`
  приблизно раз на **30 секунд**.

---

## Встановлення

### Через HACS (Custom Repository)

1. Відкрий **HACS → Integrations → ⋮ → Custom repositories**.
2. Додай репозиторій з типом **Integration**.
3. Встанови інтеграцію **Svitlobot HA**.
4. Перезапусти Home Assistant.

### Вручну

1. Скопіюй папку `custom_components/svitlobot_ha` у:
   `config/custom_components/svitlobot_ha`
2. Перезапусти Home Assistant.

---

## Налаштування в Home Assistant

**Settings → Devices & services → Add integration → Svitlobot HA**

Поля конфігурації:

- `voltage_entity_id` — сутність (наприклад `sensor.tuya_socket_voltage`)
- `svitlobot_channel_key` — ключ каналу SvitloBot (можна залишити порожнім, тоді ping не буде)
- `refresh_seconds` — інтервал примусового оновлення сутності (`update_entity`). `0` = вимкнено
- `stale_timeout_seconds` — через скільки секунд без оновлень вважати “offline”. `0` = вимкнено
- `debounce_seconds` — затримка підтвердження зміни стану. `0` = без затримки

---

## Що створює інтеграція

### Binary Sensor

Інтеграція створює `binary_sensor` зі станом:

- **ON** = online (світло є)
- **OFF** = offline (світла немає)

Атрибути сенсора:

- `watched_entity_id` — яка сутність відстежується
- `watched_state` — поточний стан цієї сутності

---

## Логіка “online/offline”

- Якщо стан сутності `unavailable` / `unknown` / `offline` → **offline**
- Якщо `stale_timeout_seconds > 0` і сутність не оновлювалась довше цього часу → **offline**
- Інакше → **online**

---

## Переклади

Файли перекладу:

- `custom_components/svitlobot_ha/translations/en.json`
- `custom_components/svitlobot_ha/translations/uk.json`

Після додавання/зміни перекладів інколи потрібен **повний рестарт** Home Assistant (а також може допомогти очистка кешу браузера).

---

## Траблшутинг

- **Не бачу української мови у формах** → Переконайся, що:
  - мова профілю HA = Українська
  - є `translations/uk.json`
  - зроблено рестарт HA (і за потреби очищено кеш браузера)

- **Ping не відправляється**:
  - перевір, що задано `svitlobot_channel_key`
  - перевір логи Home Assistant: **Settings → System → Logs**

---

## Ліцензія

MIT — див. файл [LICENSE](LICENSE).
