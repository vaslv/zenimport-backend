# Фиксированная зависимость ZenMoneyAPI

Версия **0.6.0**, исходная ревизия SDK **52ffbc7**. Wheel собран из отдельного
проекта `../ZenMoneyAPI`; проверен 224 автономными тестами SDK и живой матрицей
18 базовых операций в отдельном тестовом аккаунте. Docker проверяет SHA256SUMS.
Старый wheel 0.4 и namespace zenimport_zenmoney больше не используются.

Для обновления: запустить scripts/test.sh и scripts/build.sh в ZenMoneyAPI;
скопировать wheel сюда, обновить SHA256SUMS и путь в Dockerfile. Обновить зависимости
SDK в обоих lock-файлах, затем запустить scripts/test-docker.sh в ZenImport.
При изменении кода SDK должна меняться версия. Для обычной Docker-сборки приложения
соседний checkout не нужен.
