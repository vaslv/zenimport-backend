# Локальная готовность к первому выпуску — 17 октября 2026

Публикация ещё не выполнялась. Пользователь подтвердил MIT и адреса трёх
репозиториев; по его уточнению имя SDK — **zenmoney-python**, импорт zenmoney_api.
Начальные GitHub-лицензии сохранены, remotes настроены, история объединяется
обычными локальными merge без переписывания и без force/push/тегов.

| Проект | Проверка | Результат |
|---|---|---|
| zenmoney-python 0.7.0 | установленный wheel, mypy strict, unit/mock HTTP | 260 passed, 99% coverage |
| SDK-артефакты | wheel из sdist, Twine strict, metadata/LICENSE/py.typed, SHA256 | passed |
| Backend 0.1.0 + SDK 0.7.0 | Linux ARM64 | 492 passed; lifecycle/HTTP/SIGKILL/graceful/snapshot passed |
| Backend 0.1.0 + SDK 0.7.0 | Linux AMD64 под эмуляцией | 492 passed; те же lifecycle-проверки passed |
| compose.server.yaml, готовый образ | обе архитектуры | приватный bootstrap, no-overwrite, UID/read-only root, localhost bind, runtime layout/SDK, OCI metadata, volume recreate passed |
| Chrome 0.1.9 | Docker unit/browser | 31 unit + 10 browser passed; 1 ожидаемый skip модуля без backend |
| Chrome + текущий backend/SDK | Docker integration | 17 passed |
| Chrome ZIP | две сборки, содержимое | одинаковый SHA256, LICENSE/PRIVACY, нет локальных настроек |
| GitHub workflows | actionlint | passed; на GitHub ещё не запускались |
| Caddy example | validate в контейнере без сети | Valid configuration |

ARM64 выполнялся нативно в OrbStack на ARM-хосте, AMD64 — через эмуляцию.
Все финансовые тесты этого этапа используют искусственные данные/ответы SDK.
Рабочие контейнеры, SQLite, банковский кабинет, токены/данные ZenMoney и профиль
пользователя не изменялись. Проверка известных локальных токенов читала их только
для локального сравнения с Git-объектами; значения не печатались и не передавались
сканеру/сети. Gitleaks 8.30.1 сканирует Git-историю и подготовленные diff с redaction.

Backend устанавливает **отдельный пакет** zenmoney-python==0.7.0 с SHA256,
no-index/no-deps/require-hashes и pip check. До первого релиза использован локальный
SDK-wheel из коммита ba17ce3. После публикации SDK загрузить wheel и manifest
именно из проверенного GitHub Release и повторить обновление через update_sdk.py.
GitHub не является PyPI; для этого пути публикация в PyPI не обязательна.

CI- и gitleaks-образы закреплены SHA. Для проверки использованы:

- actionlint: `rhysd/actionlint@sha256:b1934ee5f1c509618f2508e6eb47ee0d3520686341fec936f3b79331f9315667`;
- gitleaks: `ghcr.io/gitleaks/gitleaks@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f`;
- Caddy: `caddy@sha256:685ae3d7c34d9b5f8caeddc5d14051db7bbd88ff1f5a2fa0c16bd8ca328fb7af`.

Приватные логи сохранены в `.local/publication/` каждого проекта и не включаются
в Git/артефакты. В готовый backend не включены .env, SQLite, тесты, браузер, SDK
checkout и live runner. В Python/Chrome-архивы не входят локальные токены и история.

Первым выпускается SDK, затем backend/GHCR и Chrome. Публикация требует отдельных
явных разрешений, включая push и Git-теги. Публичный registry manifest, реальный
удалённый сервер/DNS/HTTPS и мобильный UI не объявляются уже проверенными.
При реальном переезде сохранять журнал доставки через перенос БД; испытание
пустой базы не является гарантией сохранения неизвестных ей прежних удалений.

[Порядок выпуска](publication.md) · [Удалённый сервер](server-deployment.md)
