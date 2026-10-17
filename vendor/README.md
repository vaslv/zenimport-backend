# Отдельный пакет zenmoney-python

Закреплён **zenmoney-python==0.7.0**, импорт `zenmoney_api`.
Проект: https://github.com/vaslv/zenmoney-python, MIT.
Wheel не содержит кода backend; исходники SDK сюда не копируются.

До первого GitHub-релиза wheel собран из локального проверенного коммита SDK.
`SHA256SUMS` фиксирует байты, `requirements-sdk.lock.txt` — имя/версию и SHA256.
Docker сначала проверяет manifest, затем устанавливает пакет без индекса/зависимостей
с `--require-hashes` и выполняет pip check. Его зависимости закреплены отдельно
в requirements.runtime.lock.txt; соседний checkout для обычной сборки не нужен.
`pyproject.toml` и тест проверяют то же каноническое имя/версию, старого
zenmoney-api-client в образе нет. Это пакетная зависимость; vendor служит её кешем.

После публикации первого SDK-релиза скачать wheel и SHA256SUMS из GitHub Release,
сверить digest, затем выполнить из корня backend:

```sh
python3 scripts/update_sdk.py --wheel /path/zenmoney_python-0.7.0-py3-none-any.whl --sha256 VERIFIED_DIGEST
./scripts/test-platforms.sh
```

Обновление не обращается в сеть. Скрипт отказывается менять кеш при несовпадении
SHA256, имени или версии зависимости pyproject.toml. Чтобы перейти на другую
версию, сначала явно изменить pin в pyproject.toml, затем поставить проверенный
wheel. После обновления выполнить Docker-проверки и закоммитить wheel/manifest/
lock вместе. Не ставить SDK из рабочей ветки или через соседний PYTHONPATH.

GitHub-репозиторий не является PyPI. Для будущего перехода к загрузке из индекса
нужен отдельный PyPI-релиз и сохранение pin/digest; сейчас сборка не зависит от
наличия ещё не опубликованного пакета в интернете. Уже опубликованный артефакт
той же версии не заменять новой сборкой с другим хешем.

Текущий предрелизный wheel 0.7.0 получен после проверки SDK-коммита `ba17ce3`
(сохранены исходная история и начальная лицензия GitHub). Docker SDK:
260 тестов, mypy strict, покрытие 99%; runtime API при переименовании не менялся.
