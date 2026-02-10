Ниже — подробное ТЗ на PyPI-библиотеку “локальный singleton-daemon + прокси-объект” под твой пример:

```python
svc = local_singleton("myname", factory=MyObj, idle_ttl=2.0, serializer="pickle")
obj = svc.proxy()
obj.method(...)
# obj ведёт себя как MyObj, но фактически вызовы идут в один daemon на localhost
# при obj.__del__ / gc — дисконнект (best-effort)
```

Я буду максимально честен: **самая рискованная часть** здесь — обещание “obj как настоящий MyObj” (в т.ч. магические методы, атрибуты, исключения, `isinstance`, `__repr__`, `dir`, дескрипторы). Это можно сделать **достаточно похоже**, но не 100% идентично в Python без тяжёлых трюков. В ТЗ ниже я фиксирую реалистичный контракт.

---

# 1) Назначение и цели

## 1.1 Проблема

Несколько независимых процессов на одной машине создают “singleton” объект, но на деле получают **несколько экземпляров**. Нужно, чтобы на **весь localhost и на пользователя** (или на machine-wide — опционально) был **ровно один владелец состояния**, а все процессы работали с ним как с обычным объектом.

## 1.2 Цели

1. **Один daemon-процесс** на `localhost` на заданное имя `name`, хранящий единственный экземпляр объекта.
2. Клиентский код получает **proxy**, поддерживающий вызов методов и (ограниченно) доступ к атрибутам.
3. **Автозапуск** daemon при первом обращении: “подключись или подними”.
4. **Строгая последовательность** выполнения операций (single-threaded semantics), независимо от количества клиентов.
5. **Жизненный цикл**: daemon завершает работу при отсутствии клиентов (idle shutdown) с `idle_ttl`.
6. Поддержка **Windows + Linux/macOS**.
7. Простая сериализация: числа/строки/маленькие `np.ndarray`.

## 1.3 Нецели (важно зафиксировать)

* Не делать безопасный сетевой RPC для недоверенной среды (если `serializer="pickle"`, это **только доверенные локальные процессы**).
* Не делать распределённую систему / multi-host.
* Не гарантировать 100% “прозрачность” всех Python-магических операций (см. ограничения).

---

# 2) Публичный API

## 2.1 Верхнеуровневые функции/классы

### `local_singleton(...) -> LocalSingletonService`

```python
def local_singleton(
    name: str,
    factory: Callable[[], Any] | str | None = None,
    *,
    scope: Literal["user", "machine"] = "user",
    idle_ttl: float = 2.0,
    serializer: Literal["pickle", "msgpack"] = "pickle",
    numpy: Literal["pickle", "raw"] = "pickle",
    version: int = 1,
    connect_timeout: float = 0.5,
    start_timeout: float = 3.0,
    logger: logging.Logger | None = None,
) -> LocalSingletonService:
    ...
```

**Пояснения:**

* `name`: логическое имя сервиса (часть ключа singleton).
* `factory`:

  * либо callable `() -> obj` (предпочтительно),
  * либо строка `"pkg.module:FactoryOrClass"` (чтобы daemon мог импортировать без pickling кода),
  * либо `None` (тогда требуется `service.ensure_started(factory=...)` перед `proxy()`).
* `scope`:

  * `"user"` — singleton на пользователя (по умолчанию, безопаснее),
  * `"machine"` — один на машину (требует аккуратных прав/каталогов).
* `idle_ttl`: seconds; daemon гасится если нет клиентов `idle_ttl` секунд.
* `serializer`:

  * `"pickle"` — минимальные зависимости, быстро, но небезопасно для недоверенных клиентов,
  * `"msgpack"` — безопаснее по типам, но требует ограничения типов + доп.пакет.
* `numpy`:

  * `"pickle"` — массивы через pickle,
  * `"raw"` — `(dtype, shape, bytes)` для скорости/контроля (только C-contiguous; иначе копия).
* `version`: протокол/контракт (для совместимости между релизами).
* `connect_timeout`, `start_timeout`: таймауты подключения/старта.
* `logger`: опционально.

### `LocalSingletonService.proxy() -> Proxy`

```python
svc = local_singleton(...)
obj = svc.proxy()
```

* Возвращает proxy-объект.
* Proxy поддерживает:

  * вызов методов `obj.foo(1,2)`
  * чтение простых атрибутов `obj.x` (опционально)
  * установку атрибутов `obj.x = 1` (опционально)
  * `obj.close()` закрывает соединение (если реализовано)
* Proxy также является context manager:

```python
with svc.proxy() as obj:
    obj.method(...)
```

### `LocalSingletonService.ensure_started()`

Явное поднятие (редко нужно, но полезно для “warm up”):

```python
svc.ensure_started()
```

### `LocalSingletonService.ping() -> dict`

Диагностика: pid, uptime, active_clients, version, serializer.

### `LocalSingletonService.shutdown(force=False)`

Мягкое завершение daemon’а (если клиент хочет).

---

# 3) Поведение proxy “как MyObj”

## 3.1 Минимальный контракт “как объект”

Обязательные свойства:

* `obj.method(*args, **kwargs)` вызывает реальный метод на daemon’е.
* Исключения пробрасываются клиенту как `RemoteError` (с сохранением текста/типа по мере возможности).
* `repr(obj)` осмысленный (например `"<Proxy MyObj via myname>"`).
* `obj.__del__` делает best-effort disconnect:

  * **не гарантировать**, что `__del__` всегда вызовется (GC/циклы/интерпретатор завершает процесс).
  * Поэтому обязательны также `close()` и `with`.

## 3.2 Ограничения (нужно явно в README)

* `isinstance(obj, MyObj)` **не гарантируется**.
* Магические методы (`__len__`, `__iter__`, `__getitem__`, операторы) поддерживать **только если явно проксировать** (см. optional).
* Доступ к атрибутам:

  * если включить, то `__getattr__`/`__setattr__` будут RPC — это может быть неожиданно по производительности/семантике.

## 3.3 Optional “расширенная прозрачность”

Опциональная опция `proxy_magic=True`:

* проксировать ограниченный набор магических методов:

  * `__len__`, `__iter__` (через генератор плохо; лучше запретить), `__getitem__`, `__setitem__`
  * `__call__`
  * arithmetic operators — обычно не надо
    В ТЗ можно заложить как v2, не как MVP.

---

# 4) IPC и протокол

## 4.1 Transport

* TCP `127.0.0.1` (кроссплатформенно, проще).
* Binding на **эпhemeral port**, порт публикуется в runtime-файле.
* Для Unix можно доп. вариант UDS, но для Windows будет отдельная ветка — в MVP не нужно.

## 4.2 Runtime discovery

Daemon публикует файл `runtime.json|runtime.bin` в каталоге:

* user scope:

  * Linux: `~/.cache/<pkg>/` или `$XDG_RUNTIME_DIR/<pkg>/`
  * Windows: `%LOCALAPPDATA%\<pkg>\`
* machine scope:

  * Linux: `/var/run/<pkg>/` (нужны права)
  * Windows: `C:\ProgramData\<pkg>\` (нужны права)

Содержимое runtime (минимум):

* `protocol_version`
* `pid`
* `host` (всегда 127.0.0.1)
* `port`
* `auth_token` (cookie)
* `service_name`
* `serializer`
* `started_at`

## 4.3 Anti-race locking

Чтобы два процесса не подняли два daemon’а одновременно:

* lock-файл (эксклюзивная блокировка):

  * POSIX: `fcntl.flock`
  * Windows: `msvcrt.locking`
* Алгоритм connect-or-spawn:

  1. попробовать connect
  2. взять lock
  3. повторить connect
  4. если нет — стартовать daemon
  5. дождаться готовности, connect

## 4.4 Auth (обязательно)

* Cookie/токен хранится в отдельном файле `auth.bin` с правами “только владелец” (где возможно).
* Клиент читает cookie и делает handshake:

  * `HELLO(protocol_version, token)`
  * сервер отвечает `OK(pid, server_info)` или `ERR`.
* Это защита от “случайных” подключений и от конфликтов между пользователями на одной машине.

## 4.5 Message framing

* Length-prefixed frames: `[u32 len][payload]`.
* Payload:

  * `pickle` или `msgpack` в зависимости от настройки.
* Типы сообщений:

  * `HELLO`
  * `CALL(method_name, args, kwargs)`
  * `GETATTR(name)` / `SETATTR(name, value)` (если включено)
  * `PING`
  * `CLOSE`
  * `SHUTDOWN(force?)`

## 4.6 Сериализация numpy

* MVP: pickle.
* Опция `numpy="raw"`:

  * массив кодируется как `{ "__ndarray__": True, "dtype": "...", "shape": [...], "data": bytes }`
  * требование C-contiguous; иначе `np.ascontiguousarray` (копия).
  * ограничить размер (например `max_bytes`, дефолт 4–16 MB) — чтобы случайно не гонять гигабайты.

---

# 5) Семантика “строго последовательно”

## 5.1 Требование

Все операции над singleton-объектом выполняются **строго по одной**, независимо от числа клиентов и потоков. Это “single-threaded actor”.

## 5.2 Реализация

* Сервер принимает несколько TCP-сессий (каждая — отдельный handler thread).
* Handler:

  * читает запрос,
  * кладёт задачу в `exec_queue` (FIFO),
  * ждёт ответ через `reply_queue`/`Future`,
  * отправляет ответ клиенту.
* Единственный executor-thread:

  * достаёт задачи из `exec_queue`,
  * вызывает метод реального объекта,
  * кладёт результат/ошибку в reply.

## 5.3 Порядок при гонках

В MVP: порядок FIFO относительно попадания в `exec_queue`. Это достаточно для твоего 100 rps.
Опционально: глобальный sequence number (атомарный счётчик) для более строгой упорядоченности.

---

# 6) Жизненный цикл daemon “умри когда нет клиентов”

## 6.1 Определение “клиент подключён”

Клиент = активная TCP-сессия (одно соединение на proxy / на процесс, см. ниже).

## 6.2 Idle shutdown

* Сервер ведёт счётчик `active_connections`.
* При `active_connections == 0` стартует отсчёт.
* Если ноль держится `idle_ttl` секунд — сервер graceful shutdown:

  * прекращает accept,
  * дожидается завершения handler’ов (или закрывает),
  * удаляет runtime-file,
  * выходит.

## 6.3 Клиентское соединение

Варианты:

* **На proxy-объект** (просто и прозрачно): каждый `svc.proxy()` открывает отдельную TCP-сессию; `obj.close()`/`__del__` закрывают.
* **На процесс** (оптимизация): один shared connection per `(name, pid)` + refcount proxies. Это сложнее, но экономит сокеты. При 100 rps не нужно; можно как v2.

В MVP рекомендую “на proxy”, плюс `LocalSingletonService.proxy(shared=True)` как опциональная оптимизация позже.

## 6.4 **del** / финализация

* `Proxy.__del__` делает `close()` best-effort.
* Использовать `weakref.finalize` чтобы повысить шанс закрытия.
* В README: **не полагаться** на `__del__`, использовать `with` или `close()` для детерминизма.

---

# 7) Ошибки, исключения, устойчивость

## 7.1 Типы ошибок

* `ConnectionError`: нет daemon, runtime битый, порт занят, timeout.
* `ProtocolMismatchError`: версии не совпали.
* `AuthenticationError`: cookie не подходит.
* `RemoteError`: исключение внутри метода.
* `SerializationError`: не сериализуется аргумент/результат.
* `ServerCrashedError`: сервер упал во время вызова.

## 7.2 Поведение при падении daemon

* На следующем вызове proxy:

  * если сокет умер — попытка reconnect-or-spawn,
  * повтор вызова **не делается автоматически** (иначе можно нарушить семантику “вызвал один раз”); повтор пусть делает пользователь.

## 7.3 Stale runtime

Если runtime-file есть, но `pid` не жив / connect не удаётся:

* клиент удаляет runtime-file (под lock) и стартует новый daemon.

---

# 8) Безопасность

## 8.1 Pickle (обязательное предупреждение)

* `serializer="pickle"` допускается **только** для доверенных локальных процессов.
* Сервер слушает только `127.0.0.1`.
* Cookie-файл должен иметь максимально строгие права.

## 8.2 Msgpack (опционально)

* Безопаснее по типам, но:

  * нужно явное кодирование numpy,
  * ограничения на типы,
  * и всё равно остаётся вопрос DoS через большие payload.

В MVP можно оставить только pickle, но документация должна быть честной.

---

# 9) Логи и наблюдаемость

* `logger` на клиенте и сервере.
* Уровни:

  * INFO: старт/стоп daemon, подключение, idle shutdown
  * DEBUG: протокол, размеры сообщений, latency
* Диагностический метод `svc.ping()`:

  * pid, uptime, active, exec_queue_depth, protocol_version, serializer

---

# 10) CLI/entrypoints

## 10.1 Daemon entrypoint

Обязателен модульный запуск:

```bash
python -m localsingleton.daemon --name myname --runtime <path> --auth <hex> --factory pkg:Factory --idle-ttl 2.0
```

Клиент запускает daemon через `subprocess.Popen([...])` с переменными окружения либо аргументами.

## 10.2 Отладочные команды (nice-to-have)

* `localsingleton status myname`
* `localsingleton shutdown myname`

---

# 11) Структура пакета

Предложение:

* `localsingleton/__init__.py`
* `localsingleton/api.py` (local_singleton, LocalSingletonService)
* `localsingleton/proxy.py`
* `localsingleton/daemon.py`
* `localsingleton/transport.py` (framing)
* `localsingleton/serialization.py` (pickle/msgpack + numpy codecs)
* `localsingleton/platform.py` (paths, locking, process spawn flags)
* `localsingleton/errors.py`
* `localsingleton/version.py`

---

# 12) Тестирование (обязательные сценарии)

Тесты должны гоняться на **Linux + Windows** в CI.

## 12.1 Unit

1. Framing encode/decode.
2. Serializer roundtrip (ints, floats, strings, small dict, numpy small).
3. Proxy error mapping.
4. Lock correctness (smoke).

## 12.2 Integration (самое важное)

1. **connect-or-spawn race**:

   * запустить N процессов одновременно, каждый делает `svc.proxy().ping()`.
   * assert: daemon один (один pid), все получили ответ.
2. **strict sequential**:

   * объект с состоянием `counter`, метод `inc()` возвращает значение.
   * запустить M процессов, каждый делает K раз `inc(1)`.
   * собрать результаты, assert: все значения от 1..M*K без дыр и повторов.
3. **idle shutdown**:

   * подключиться, сделать вызов, закрыть proxy.
   * подождать `idle_ttl + delta`.
   * assert: новый connect приводит к новому pid (или runtime отсутствует).
4. **client crash**:

   * запустить процесс, создать proxy и “убить” процесс без close (os._exit).
   * server должен корректно уменьшить active_connections и погаситься.
5. **stale runtime**:

   * создать runtime-file с несуществующим pid/портом.
   * connect должен удалить stale и поднять новый daemon.
6. **numpy**:

   * передача маленького ndarray туда/обратно.

## 12.3 Performance (не бенч как gate, но sanity)

* 100 rps суммарно без деградации и утечек.

---

# 13) Документация (README)

Обязательно:

* Quickstart (как в примере).
* Ограничения прозрачности (`isinstance`, магические методы).
* Жизненный цикл, почему `with` лучше чем `__del__`.
* Безопасность pickle.
* Раздел “Troubleshooting” (stale runtime, antivirus on Windows, порт занят).
* “FAQ: почему не multiprocessing.Manager / Pyro5”.

---

# 14) Версионирование и совместимость

* `protocol_version` отдельный от `package_version`.
* При несовпадении protocol:

  * клиент должен уметь “вежливо” отказаться и поднять новый daemon (или выдать ошибку — настраиваемо).
* В runtime хранить `protocol_version` и `package_version`.

---

# 15) MVP-объём и план работ

## MVP (реально выпустить v0.1)

* Только TCP localhost
* Только pickle serializer
* Factory как `"module:callable"` (строкой) + опционально callable (но тогда клиент должен передать строку для daemon)
* Proxy: методы + `close()` + context manager
* Strict sequential executor
* connect-or-spawn + lock
* idle shutdown
* Windows/Linux CI + интеграционные тесты

## v0.2+

* msgpack serializer
* numpy raw codec
* shared connection per-process
* расширенная прозрачность (магические методы по whitelist)
* CLI status/shutdown

---

# 16) Самый важный риск (и как его снять)

**Риск:** “obj как MyObj” → пользователи начнут ожидать полной идентичности (магия, атрибуты, итераторы, свойства, контекстные менеджеры и т.п.).
**Снятие:** в контракте жёстко объявить: “proxy гарантирует корректные удалённые вызовы обычных методов; остальное — опционально/ограниченно”.

---

