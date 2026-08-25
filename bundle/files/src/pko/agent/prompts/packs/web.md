## Пак: веб-сервис

В репозитории найден HTTP-сервер. Точки входа объявляются декоратором или
регистрацией маршрута.

Где искать: `@app.get(`, `@router.post(`, `app.route(`, `add_url_rule(`,
`include_router(`, `path(` в urls, `app.use(`, `router.get(` в Node.

Как размечать:

- объявление маршрута — `ENTRYPOINT` / `serve` / `http_server`;
- обращение к чужому сервису (`requests`, `httpx`, `fetch`, `axios`) —
  `EFFECT` / `call` / `http_client`;
- middleware авторизации, валидация схемы, rate limit — `CONTROL` с
  механизмом `limit` или `allowlist`.

Осторожно: маршрут, собранный в рантайме из конфигурации, статически не
доказывается. Такое место отмечай как `UNKNOWN`, а не как подтверждённый
`ENTRYPOINT`.
