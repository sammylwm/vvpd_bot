# Запуск в Docker

```bash
docker compose up -d --build
```

Адрес API расписания задаётся через `API_PATH` в `src/settings/.env`.
Для адреса `http://schedule-api:8000` контейнер API должен быть подключён
к сети `poly-shared` и иметь в ней имя сервиса или сетевой alias `schedule-api`.
Бот подключён к этой сети и к стандартной сети проекта для доступа к `db`.
Сеть `poly-shared` внешняя: её должен заранее создать проект API.

После изменения сетевых настроек пересоздайте контейнер бота:

```bash
docker compose up -d --force-recreate bot
```

Проверить разрешение имени API из контейнера бота:

```bash
docker compose exec bot python -c "import socket; print(socket.getaddrinfo('schedule-api', 8000))"
```
