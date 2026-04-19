# Server-side route-based split tunneling / Серверное сплит-туннелирование IP-маршрутизацией

## Description / Описание

### English

Fetch Russian IPv4 prefixes from the RIPE database and generate routes of the form `ip route <CIDR> dev <EGRESS INTERFACE>` when the default route points into the tunnel. Monitor prefix changes with a Telegram bot that reports the affected ASN and owning organization.

Built with Cursor under human supervision and testing. If that wounds your feelings, I regret to inform you—with the deepest sympathy—that I could not care less.

### Russian

Выгрузка российских подсетей с базы RIPE, генерация маршрутов формата `ip route <CIDR> dev <ВЫХОДНОЙ ИНТЕРФЕЙС>` при наличии дефолтного маршрута внутрь тоннеля. Мониторинг изменения подсетей с оповещением Telegram-ботом с информацией о затронутой AS и организации-владельце.

Написано с использованием Cursor под надзором и тестированием человека. Если это задевает ваши глубокие чувства, я с глубочайшим сожалением и сочувствием вынужден сообщить, что мне насрать.

## Supposed use case / Предполагаемое использование

### English

Cascade tunneling via an intermediate server in Russia: traffic path [Client] → WG tunnel → [Server in Russia] → tunnel → [Server abroad]. The author uses WireGuard on both hops, with clients and the foreign server as peers of the Russian server. All tunneled traffic uses a separate routing table `wireguard2x` with a default route into the foreign tunnel.

Example of supposed configuration provided in [wireguard-example](./wireguard-example/) How long this setup stays viable is hard to predict; changes to [update-cidrs-in-route-table.py](./update-cidrs-in-route-table.py) are expected to stay small thanks to the dedicated routing table.

### Russian

Каскадное туннелирование через промежуточный сервер в РФ. Иными словами, маршрут [Клиент] -> WG-тоннель -> [Сервер в России] -> Тоннель -> [Сервер за пределами России]. Автором на обоих стыках используется WireGuard-тоннель, подключая клиентов и зарубежный сервер как пиры к серверу в России. Весь туннелируемый трафик ходит внутри отдельной таблицы маршрутизации `wireguard2x` с дефолтным маршрутом внутрь тоннеля зарубеж.

Пример предполагаемой конфигурации предоставлен в [wireguard-example](./wireguard-example/). К сожалению, время жизни такой схемы предсказать сложно, но предполагается, что изменения в [update-cidrs-in-route-table.py](./update-cidrs-in-route-table.py) будут минимальными за счет использования отдельной таблицы маршрутизации.

## Installation / Установка

### English

1. Create and fill these files:

- `/etc/split-tunneling/creds`
    - `IPINFO_TOKEN` — token from [ipinfo.io](https://ipinfo.io/). Free tier is enough; no rate limits hit in practice.
    - `TELEGRAM_BOT_TOKEN` — bot token from [@botfather](https://t.me/botfather).
    - `TELEGRAM_CHAT_ID` — chat ID; e.g. from `curl https://api.telegram.org/bot<BOT_TOKEN>/getUpdates`.

    Example:

    ```ini
    IPINFO_TOKEN=fjakljwflkawj
    TELEGRAM_BOT_TOKEN=botAKakFJIXJLKJ31JKLA
    TELEGRAM_CHAT_ID=3910831
    ```

- `/etc/split-tunneling/split-tunneling.ini`
    - `routing_table` — policy routing table name. Default: `wireguard2x`
    - `egress_dev` — egress interface for split traffic (outside the tunnel). Default: `eth0`

    Example:

    ```ini
    routing_table=wireguard2x
    egress_dev=eth0
    ```

2. `chmod 600 /etc/split-tunneling/creds` — lock down secrets.

3. `python -m pip install requests` — required for HTTP APIs.

4. Install scripts to `/usr/local/bin` (adjust paths in the unit file if you use another directory):

- [get-iana-cidrs.py](./get-iana-cidrs.py) — downloads RIPE prefixes; writes `/etc/split-tunneling/raw-list`, enriches with IPinfo (ASN/org), writes `/etc/split-tunneling/rich-list.csv`.
- [diff-and-report-tg.py](./diff-and-report-tg.py) — diffs the new rich list vs the previous snapshot and posts changes to Telegram.
- [update-cidrs-in-route-table.py](./update-cidrs-in-route-table.py) — reads `raw-list`, merges `include-list`, subtracts `exclude-list`, collapses CIDRs, incrementally adds/removes routes.

5. Install systemd units under `/etc/systemd/system`:

- [systemd/split-tunneling.service](./systemd/split-tunneling.service) — oneshot: runs all three scripts in order.
- [systemd/split-tunneling.timer](./systemd/split-tunneling.timer) — daily timer that starts the service.

6. **(Optional)** Edit:

- `/etc/split-tunneling/include-list` — extra prefixes to send via the egress table (non-Russian ranges you still want split out).
- `/etc/split-tunneling/exclude-list` — prefixes to exclude even if they appear in the Russian feed.

7. First run: `systemctl start split-tunneling`

8. On tunnel bring-up, run `/usr/local/bin/update-cidrs-in-route-table.py --force` (e.g. WireGuard `PostUp`). `--force` installs all planned routes and ignores the saved effective list—useful when the routing table was flushed.

### Русский

1. Заполнить следующие файлы:

- `/etc/split-tunneling/creds`
    - `IPINFO_TOKEN` - токен для [ipinfo.io](https://ipinfo.io/). Предоставляет всю необходимую информацию бесплатно. Рейт-лимиты не найдены, скрипты в них не упираются. 
    - `TELEGRAM_BOT_TOKEN` - бот-токен для Telegram. Получается в [@botfather](t.me/botfather).
    - `TELEGRAM_CHAT_ID` - ID диалога в Telegram. Можно найти в выводе запроса `curl https://api.telegram.org/bot<ТОКЕН БОТА>*/getUpdates`.
    Пример:
    ```ini
    IPINFO_TOKEN=fjakljwflkawj
    TELEGRAM_BOT_TOKEN=botAKakFJIXJLKJ31JKLA
    TELEGRAM_CHAT_ID=3910831
    ```

- `/etc/split-tunneling/split-tunneling.ini`
    - `routing_table` - название таблицы маршрутизации. Дефолт: `wireguard2x`
    - `egress_dev` - выходной интерфейс в обход тоннеля. Дефолт: `eth0`
    Пример:
    ```ini
    routing_table=wireguard2x
    egress_dev=eth0
    ```

2. `chmod 600 /etc/split-tunneling/creds` - ограничить доступ к файлу с секретами.

3. `python -m pip install requests` - установить requests для походов по API

4. Положить файлы в `/usr/local/bin` (в случае иного пути требуется поправить Unit-файлы):
- [get-iana-cidrs.py](./get-iana-cidrs.py) - отвечает за выгрузку адресов с базы данных RIPE. Сохраняет сырой список сетей (`/etc/split-tunneling/raw-list`), обогощает его информацией об AS и организации из IPInfo и сохраняет в `/etc/split-tunneling/rich-list.csv`.
- [diff-and-report-tg.py](./diff-and-report-tg.py) - сравнивает свежую выгрузку с предыдущей, отправляет информацию об изменениях в Telegram.
- [update-cidrs-in-route-table.py](./update-cidrs-in-route-table.py) - берет список сетей (`/etc/split-tunneling/raw-list`), объединяет с дополнительными сетями (`/etc/split-tunneling/include-list`), вычитает исключения (`/etc/split-tunneling/exclude-list`), упрощает CIDR-ы (объединяет соседние сети), инкрементально удаляет ненужные и добавляет новые маршруты.

5. Положить файлы в `/etc/systemd/system`:
- [systemd/split-tunneling.service](./systemd/split-tunneling.service) - `one-shot` сервис, вызывающий все скрипты.
- [systemd/split-tunneling.timer](./systemd/split-tunneling.timer) - таймер, вызывающий сервис каждый день ночью.

6. **(Опционально)** Добавьте CIDR нужных вам сетей в:
- `/etc/split-tunneling/include-list` - чтобы пустить не-русскую подсеть в обход тоннеля
- `/etc/split-tunneling/exclude-list` - чтобы пустить русскую подсеть в тоннель

7. Выгрузите подсети в первый раз: `systemctl start split-tunneling`

8. Добавьте вызов `/usr/local/bin/update-cidrs-in-route-table.py --force` к подъему вашего тоннеля. Флаг `--force` добавляет маршруты игнорируя предыдущее состояние таблицы маршрутизации. Например, в `PostUp` конфигурации WireGuard.

## Acknowledgements / Благодарности

### English

- [Cascade tunneling implementation](https://serverfault.com/questions/1080901/chaining-wireguard-servers-can-ping-both-from-client-but-cant-access-internet/1081164#1081164) — chained WireGuard / routing discussion
- [RIPE Data API](https://stat.ripe.net/docs/data-api/ripestat-data-api) — RIPEstat data API
- [IPInfo API](https://ipinfo.io/) — IP geolocation and ASN metadata
- [Cursor](https://cursor.com/agents) — coding assistant used during development

### Russian

- [Реализация каскадного туннелирования](https://serverfault.com/questions/1080901/chaining-wireguard-servers-can-ping-both-from-client-but-cant-access-internet/1081164#1081164) — обсуждение цепочки WireGuard и маршрутизации
- [RIPE Data API](https://stat.ripe.net/docs/data-api/ripestat-data-api) — API данных RIPEstat
- [IPInfo API](https://ipinfo.io/) — геолокация IP и метаданные ASN
- [Cursor](https://cursor.com/agents) — ассистент при разработке

## License / Лицензия

### English

See [LICENSE.md](./LICENSE.md).

### Russian

Полный текст: [LICENSE.md](./LICENSE.md).
