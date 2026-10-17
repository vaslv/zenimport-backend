# Порядок публикации трёх проектов

1. https://github.com/vaslv/zenmoney-python — независимый пакет **zenmoney-python 0.7.0**,
   импорт `zenmoney_api`, MIT. Подготовленные wheel/sdist/SHA256SUMS и CI находятся
   в SDK. Сначала публикуются его коммиты и первый проверенный GitHub Release.
2. https://github.com/vaslv/zenimport-backend — backend 0.1.0, MIT. Зависит от
   отдельного SDK-пакета с точной версией/digest; включает Compose для установки
   из готового образа, без SDK checkout. До релиза SDK использован локальный wheel.
3. https://github.com/vaslv/zenimport-chrome — MV3 0.1.9, MIT, с PRIVACY.md,
   воспроизводимым ZIP и CI. Настройки подключения вводятся пользователем в popup.

## Что уже подготовлено локально

- Remotes origin указывают на созданные репозитории; начальная MIT-лицензия и
  история GitHub сохраняются обычным локальным merge, без force/rewrite.
- CI SDK/Chrome/backend выполняет проверки; нет push из workflow, создания тегов,
  автопубликации PyPI/GHCR/Chrome Web Store или выполнения живых финансовых тестов.
- Backend SDK pin согласован между pyproject, wheel, SHA256 и requirements-sdk.lock.txt.
  pip устанавливает пакет с проверкой digest и не ищет его в ещё не опубликованном индексе.
- Docker runtime содержит только сервис/SDK/минимальный helper настройки; .env,
  финансовая история, SQLite, тесты, браузер и live runner исключены.
- Image-only compose.server.yaml хранит SQLite в постоянном явно названном томе,
  открывает HTTP только на loopback. Настройка HTTPS описана отдельно.

## После отдельного разрешения на публикацию

Для SDK выполнить порядок из `ZenMoneyAPI/docs/publication.md`. Пока на GitHub
есть только исходная лицензия; локальная подготовка сама ничего туда не отправила.
После SDK-релиза скачать его wheel/manifest и обновить кеш через
`scripts/update_sdk.py --wheel ... --sha256 ...`, повторить проверки backend.
Версия из pyproject и хеш должны соответствовать опубликованному пакету;
обычный backend build не обращается к соседнему SDK-проекту.

Проверить, что origin/main не изменился, затем по отдельному разрешению отправить
коммиты backend без force и дождаться успешного CI. Git-теги и их push согласуются
отдельно. Версия образа должна соответствовать выбранному тегу/коммиту сервиса.

Для публикации образа в GHCR нужен доступ GitHub Packages, репозиторий
`ghcr.io/vaslv/zenimport-backend` и отдельное разрешение выполнить registry push.
Предпочтительны короткоживущие CI credentials/защищённое окружение; не сохранять
registry-токен в коде/Dockerfile/build args. После авторизации можно создать
multi-platform image из проверенного коммита:

```sh
# Только после отдельного разрешения registry push и настройки входа в GHCR:
docker buildx build --platform linux/amd64,linux/arm64 --target final \
  --build-arg VCS_REF=VERIFIED_COMMIT \
  --build-arg SOURCE_URL=https://github.com/vaslv/zenimport-backend \
  --tag ghcr.io/vaslv/zenimport-backend:0.1.0 --push .
```

Этот пример сейчас не выполнялся. Multi-platform registry manifest создаётся
публикацией; локально проверяются оба исполняемых образа отдельно. После push
проверить manifest/digest и установить **по digest**, а не latest. Для открытого
скачивания настроить публичную видимость пакета GHCR. Сам публичный GitHub-репозиторий
не гарантирует публичность container package. На сервере проверить HTTPS,
восстановление согласованной БД и одну повторную синхронизацию.

Для Chrome после отдельного разрешения push/тег/Release приложить только
проверенный ZIP, его SHA256 и описание приватности. Store отдельно не планируется.

## Ограничения

CI-файлы проверены локально, но ещё не выполнялись на GitHub: push не разрешён.
Готовность локальных сборок не означает уже опубликованный SDK/образ или
настроенный внешний сервер. Живые рабочие операции в этом этапе не используются.
Секреты, .local, .env, серверные базы и приватные evidence не включаются в Git,
Docker context, Python-архивы и ZIP расширения.
