## Пак: команды и расписание

В репозитории найдена командная строка или запуск по расписанию.

Где искать: `ArgumentParser(`, `add_argument(`, `@click.command`,
`@app.command`, секция `[project.scripts]`, `crontab`, `schedule.every(`,
`CronTrigger`, `add_job(`, `beat_schedule`, `DAG(`.

Как размечать:

- команда CLI — `ENTRYPOINT` / `serve` / `cli`;
- запуск по расписанию — `ENTRYPOINT` / `serve` / `cron`;
- шаг сценария команды — `STEP` / `cli`;
- `--timeout`, `--limit`, число попыток — `CONTROL` / `limit`.

Осторожно: наличие файла с `main()` само по себе точкой входа не является —
нужна регистрация команды или запись в манифесте.
