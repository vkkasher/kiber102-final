"use client";
import { decodeImportFile } from "../../lib/import-file";
import { FormEvent, useEffect, useState } from "react";
import { api, date, Event, statusName } from "../../lib/api";
import {
  ArrowRight,
  ErrorNotice,
  Shell,
  SourceLink,
} from "../../components/ui";
type EditableEvent = Partial<Event> &
  Pick<Event, "id" | "title" | "category" | "city" | "date_start" | "status">;
type Tab =
  "events" | "import" | "sources" | "audit" | "traces" | "metrics" | "feedback";
type ImportResult = {
  added: number;
  updated: number;
  rejected: number;
  duplicate: number;
  requires_review: number;
  details: unknown[];
};
export default function Admin() {
  const [token, setToken] = useState(""),
    [username, setUsername] = useState(""),
    [role, setRole] = useState("moderator"),
    [password, setPassword] = useState(""),
    [tab, setTab] = useState<Tab>("events"),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [message, setMessage] = useState(""),
    [items, setItems] = useState<Event[]>([]),
    [raw, setRaw] = useState<unknown>(null),
    [edit, setEdit] = useState<EditableEvent | null>(null),
    [content, setContent] = useState(""),
    [format, setFormat] = useState("json"),
    [fileInfo, setFileInfo] = useState(""),
    [result, setResult] = useState<ImportResult | null>(null),
    [refresh, setRefresh] = useState(0),
    [query, setQuery] = useState(""),
    [search, setSearch] = useState(""),
    [page, setPage] = useState(1),
    [total, setTotal] = useState(0),
    [status, setStatus] = useState(""),
    [verifyNote, setVerifyNote] = useState(""),
    [verifyFetch, setVerifyFetch] = useState(true);
  useEffect(() => {
    setToken(sessionStorage.getItem("ayda_admin") || "");
    setRole(sessionStorage.getItem("ayda_admin_role") || "moderator");
  }, []);
  useEffect(() => {
    if (!token || tab === "import") return;
    let live = true;
    setBusy(true);
    setError("");
    const path =
      tab === "metrics"
        ? "/metrics"
        : `/admin/${tab}${tab === "events" ? `?page_size=20&page=${page}&q=${encodeURIComponent(search)}` + (status ? "&status=" + encodeURIComponent(status) : "") : ""}`;
    api<unknown>(path, { headers: { Authorization: `Bearer ${token}` } })
      .then((r) => {
        if (!live) return;
        if (tab === "events") {
          setItems(
            Array.isArray(r) ? r : (r as { items: Event[] }).items || [],
          );
          setTotal(
            Array.isArray(r) ? r.length : (r as { total: number }).total || 0,
          );
        } else setRaw(r);
      })
      .catch((e) => {
        if (live) setError(e.message);
      })
      .finally(() => {
        if (live) setBusy(false);
      });
    return () => {
      live = false;
    };
  }, [token, tab, refresh, status, page, search]);
  async function login(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const r = await api<{ token: string; role: string }>("/admin/login", {
        method: "POST",
        body: JSON.stringify({ password, ...(username ? { username } : {}) }),
      });
      sessionStorage.setItem("ayda_admin", r.token);
      setToken(r.token);
      setTab("events");
      setEdit(null);
      setRaw(null);
      setRole(r.role);
      sessionStorage.setItem("ayda_admin_role", r.role);
      setPassword("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const auth = { Authorization: `Bearer ${token}` };
  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!edit) return;
    setBusy(true);
    setError("");
    setMessage("");
    const fd = new FormData(e.currentTarget);
    const patch: Record<string, unknown> = {};
    for (const [k, v] of fd) {
      if (["age_limit", "price_min", "price_max", "duration_min"].includes(k))
        patch[k] = v === "" ? null : Number(v);
      else patch[k] = v === "" && k !== "description" ? null : v;
    }
    patch.is_free = fd.get("is_free") === "on";
    for (const k of edit.id ? Object.keys(patch) : []) {
      const before = edit[k as keyof Event];
      const after = patch[k];
      if (
        (k === "date_start" || k === "date_end") && before && after
          ? +new Date(String(before)) === +new Date(String(after))
          : before === after
      )
        delete patch[k];
    }
    if (!Object.keys(patch).length) {
      setBusy(false);
      setMessage("Изменений нет.");
      return;
    }
    if (
      patch.status === "published" &&
      !edit.verified_at &&
      !confirm(
        "У события ещё нет времени проверки. Сервер проверит возможность публикации. Продолжить?",
      )
    ) {
      setBusy(false);
      return;
    }
    try {
      await api(edit.id ? `/admin/events/${edit.id}` : "/admin/events", {
        method: edit.id ? "PATCH" : "POST",
        headers: auth,
        body: JSON.stringify(patch),
      });
      setEdit(null);
      setRefresh((v) => v + 1);
      setMessage(
        edit.id
          ? "Изменения сохранены."
          : "Черновик создан. Проверьте источник перед публикацией.",
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function verify(id: string) {
    if (verifyNote.trim().length < 5) {
      setError(
        "Опишите результат сверки с первоисточником, не менее 5 символов",
      );
      return;
    }
    setBusy(true);
    setError("");
    try {
      const r = await api<Event>(`/admin/events/${id}/verify`, {
        method: "POST",
        headers: auth,
        body: JSON.stringify({ note: verifyNote, fetch: verifyFetch }),
      });
      setRefresh((v) => v + 1);
      setEdit(r && r.id ? r : edit);
      setMessage(
        "Проверка источника выполнена. Посмотрите результат перед публикацией.",
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function importData(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const body = JSON.stringify({ format, content });
      if (new TextEncoder().encode(body).byteLength > 2_000_000)
        throw new Error(
          "Данные импорта слишком большие. Разделите файл на части до 2 МБ",
        );
      const r = await api<ImportResult>("/admin/import", {
        method: "POST",
        headers: auth,
        body,
      });
      setResult(r);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Shell>
      <div className="inner-page admin-page">
        {!token ? (
          <form className="login-form" onSubmit={login}>
            <h1>Вход для модератора</h1>
            <p>Импорт, проверка источников и публикация событий.</p>
            <label>
              Учётная запись, если настроена
              <input
                autoComplete="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                maxLength={100}
              />
            </label>
            <label>
              Пароль
              <input
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </label>
            <button className="button" disabled={busy}>
              {busy ? "Проверяем…" : "Войти"}
              <ArrowRight size={17} />
            </button>
            {error && (
              <p role="alert" style={{ color: "var(--error)" }}>
                {error}
              </p>
            )}
          </form>
        ) : (
          <>
            <div className="admin-toolbar">
              <div>
                <h1 style={{ fontSize: 36 }}>Редакция афиши</h1>
                <p>
                  Роль:{" "}
                  {role === "viewer"
                    ? "Просмотр"
                    : role === "admin"
                      ? "Администратор"
                      : "Модератор"}
                </p>
              </div>
              <button
                className="button secondary"
                onClick={() => {
                  sessionStorage.removeItem("ayda_admin");
                  setToken("");
                  setTab("events");
                  setItems([]);
                  setRaw(null);
                  setEdit(null);
                }}
              >
                Выйти
              </button>
            </div>
            <div
              className="tab-list"
              role="tablist"
              aria-label="Разделы модерации"
            >
              {(
                [
                  ["events", "События"],
                  ["import", "Импорт"],
                  ["sources", "Источники"],
                  ["feedback", "Обращения"],
                  ["audit", "Журнал"],
                  ["traces", "Трассы агента"],
                  ["metrics", "Метрики"],
                ] as [Tab, string][]
              )
                .filter(([key]) => role !== "viewer" || key !== "import")
                .map(([key, title]) => (
                  <button
                    role="tab"
                    id={`admin-tab-${key}`}
                    aria-controls="admin-panel"
                    tabIndex={tab === key ? 0 : -1}
                    onKeyDown={(event) => {
                      if (
                        !["ArrowRight", "ArrowLeft", "Home", "End"].includes(
                          event.key,
                        )
                      )
                        return;
                      event.preventDefault();
                      const tabs = Array.from(
                        event.currentTarget.parentElement!.querySelectorAll<HTMLButtonElement>(
                          '[role="tab"]',
                        ),
                      );
                      const current = tabs.indexOf(event.currentTarget);
                      const index =
                        event.key === "Home"
                          ? 0
                          : event.key === "End"
                            ? tabs.length - 1
                            : (current +
                                (event.key === "ArrowRight" ? 1 : -1) +
                                tabs.length) %
                              tabs.length;
                      tabs[index].click();
                      tabs[index].focus();
                    }}
                    aria-selected={tab === key}
                    key={key}
                    onClick={() => {
                      setTab(key);
                      setEdit(null);
                      setError("");
                      setRaw(null);
                      setMessage("");
                    }}
                  >
                    {title}
                  </button>
                ))}
            </div>
            {message && (
              <div className="notice" role="status">
                {message}
              </div>
            )}
            {error && (
              <ErrorNotice
                message={error}
                retry={() => setRefresh((v) => v + 1)}
              />
            )}
            <section
              role="tabpanel"
              id="admin-panel"
              aria-labelledby={`admin-tab-${tab}`}
              aria-label={
                {
                  events: "События",
                  import: "Импорт",
                  sources: "Источники",
                  audit: "Журнал",
                  traces: "Трассы агента",
                  metrics: "Метрики",
                  feedback: "Обращения",
                }[tab]
              }
            >
              {tab === "events" ? (
                <>
                  <div className="admin-toolbar">
                    <form
                      onSubmit={(e) => {
                        e.preventDefault();
                        setSearch(query);
                        setPage(1);
                      }}
                    >
                      <label>
                        Найти событие
                        <input
                          value={query}
                          onChange={(e) => setQuery(e.target.value)}
                          placeholder="Название, площадка или ID"
                          maxLength={300}
                        />
                      </label>
                      <button className="button secondary" disabled={busy}>
                        Искать
                      </button>
                    </form>
                    <label>
                      Статус
                      <select
                        value={status}
                        onChange={(e) => {
                          setStatus(e.target.value);
                          setPage(1);
                        }}
                      >
                        <option value="">Все статусы</option>
                        {Object.entries(statusName)
                          .filter(
                            ([key]) => !["pending", "review"].includes(key),
                          )
                          .map(([key, label]) => (
                            <option value={key} key={key}>
                              {label}
                            </option>
                          ))}
                      </select>
                    </label>
                    <button
                      className="button secondary"
                      onClick={() => setRefresh((v) => v + 1)}
                      disabled={busy}
                    >
                      Обновить
                    </button>
                  </div>
                  {role !== "viewer" && (
                    <button
                      className="button"
                      onClick={() => {
                        setEdit({
                          id: "",
                          title: "",
                          category: "другое",
                          city: "",
                          venue: "",
                          date_start: "",
                          source_url: "",
                          status: "draft",
                        });
                        setError("");
                      }}
                    >
                      Создать событие
                    </button>
                  )}
                  <nav
                    className="pagination"
                    aria-label="Страницы событий модерации"
                  >
                    <button
                      className="button secondary"
                      disabled={busy || page === 1}
                      onClick={() => setPage(page - 1)}
                    >
                      Предыдущая
                    </button>
                    <span>
                      Страница {page} из {Math.max(1, Math.ceil(total / 20))} ·
                      Всего {total}
                    </span>
                    <button
                      className="button secondary"
                      disabled={busy || page * 20 >= total}
                      onClick={() => setPage(page + 1)}
                    >
                      Следующая
                    </button>
                  </nav>
                  {edit && role !== "viewer" && (
                    <form className="admin-editor" onSubmit={save}>
                      <h2>
                        {edit.id ? "Редактирование события" : "Новое событие"}
                      </h2>
                      <div className="editor-fields" key={edit.id}>
                        {(
                          [
                            "title",
                            "category",
                            "city",
                            "venue",
                            "address",
                            "date_start",
                            "date_end",
                            "price_min",
                            "price_max",
                            "age_limit",
                            "duration_min",
                            "language",
                            "org_link",
                            "source_url",
                            "poster_url",
                            "poster_rights",
                          ] as const
                        ).map((key) => (
                          <label key={key}>
                            {
                              (
                                {
                                  title: "Название",
                                  category: "Категория",
                                  city: "Город",
                                  venue: "Площадка",
                                  address: "Адрес",
                                  date_start:
                                    "Начало (ISO 8601 с часовым поясом)",
                                  date_end: "Окончание (ISO 8601)",
                                  price_min: "Цена от, ₽",
                                  price_max: "Цена до, ₽",
                                  age_limit: "Возраст",
                                  duration_min: "Длительность, мин.",
                                  language: "Язык",
                                  org_link: "Ссылка организатора",
                                  source_url: "Источник",
                                  poster_url:
                                    "URL разрешённого постера, необязательно",
                                  poster_rights:
                                    "Лицензия или разрешение на постер",
                                } as Record<string, string>
                              )[key]
                            }
                            <input
                              name={key}
                              required={[
                                "title",
                                "category",
                                "city",
                                "venue",
                                "date_start",
                                "source_url",
                              ].includes(key)}
                              type={
                                [
                                  "price_min",
                                  "price_max",
                                  "age_limit",
                                  "duration_min",
                                ].includes(key)
                                  ? "number"
                                  : "text"
                              }
                              min="0"
                              defaultValue={edit[key] ?? ""}
                            />
                          </label>
                        ))}
                        <label className="checkbox-label">
                          <input
                            name="is_free"
                            type="checkbox"
                            defaultChecked={edit.is_free}
                          />
                          Бесплатное событие
                        </label>
                        <label>
                          Статус
                          <select
                            name="status"
                            defaultValue={edit.status}
                            disabled={!edit.id}
                          >
                            {Object.entries(statusName)
                              .filter(
                                ([k]) => !["pending", "review"].includes(k),
                              )
                              .map(([k, v]) => (
                                <option key={k} value={k}>
                                  {v}
                                </option>
                              ))}
                          </select>
                        </label>
                        <label className="wide">
                          Описание
                          <textarea
                            name="description"
                            defaultValue={edit.description || ""}
                            rows={5}
                          />
                        </label>
                      </div>
                      <div className="button-row">
                        <button className="button" disabled={busy}>
                          {edit.id ? "Сохранить изменения" : "Создать черновик"}
                        </button>
                        <button
                          type="button"
                          className="button secondary"
                          onClick={() => setEdit(null)}
                        >
                          Закрыть
                        </button>
                      </div>
                      {edit.id && (
                        <div className="verification-panel">
                          <h3>Проверка источника</h3>
                          <label>
                            Что сверено с оригиналом
                            <textarea
                              value={verifyNote}
                              onChange={(e) => setVerifyNote(e.target.value)}
                              placeholder="Дата, точный сеанс, место, возраст и цена"
                              minLength={5}
                              maxLength={2000}
                            />
                          </label>
                          <label className="checkbox-label">
                            <input
                              type="checkbox"
                              checked={verifyFetch}
                              onChange={(e) => setVerifyFetch(e.target.checked)}
                            />
                            Автоматически проверить официальный источник
                          </label>
                          {!verifyFetch && (
                            <p>
                              <small>
                                Ручная сверка означает, что вы лично проверили
                                сведения на странице источника.
                              </small>
                            </p>
                          )}
                          <button
                            type="button"
                            className="button secondary"
                            disabled={busy || verifyNote.trim().length < 5}
                            onClick={() => verify(edit.id)}
                          >
                            Проверить источник
                          </button>
                        </div>
                      )}
                      <small>
                        Публикуйте только после проверки содержания, даты, цены
                        и первоисточника. Все изменения записываются в журнал.
                      </small>
                    </form>
                  )}
                  {busy ? (
                    <p role="status">Загружаем…</p>
                  ) : items.length ? (
                    <div className="admin-table-wrap">
                      <table className="admin-table">
                        <thead>
                          <tr>
                            <th>Событие</th>
                            <th>Дата и город</th>
                            <th>Статус</th>
                            <th>Источник</th>
                            <th>Действие</th>
                          </tr>
                        </thead>
                        <tbody>
                          {items.map((e) => (
                            <tr key={e.id}>
                              <td>
                                <strong>{e.title}</strong>
                                <br />
                                <small>{e.category}</small>
                              </td>
                              <td>
                                {date(e.date_start)}
                                <br />
                                {e.city}
                              </td>
                              <td>
                                <span className="status">
                                  {statusName[e.status] || e.status}
                                </span>
                                <br />
                                <small>
                                  {e.verified_at
                                    ? "Проверено " + date(e.verified_at)
                                    : "Проверки нет"}
                                </small>
                              </td>
                              <td>
                                <SourceLink href={e.source_url}>
                                  Открыть
                                </SourceLink>
                              </td>
                              <td>
                                <button
                                  disabled={role === "viewer"}
                                  onClick={() => setEdit(e)}
                                >
                                  Редактировать
                                </button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <div className="notice">
                      Событий с таким статусом нет. Импортируйте JSON или CSV во
                      вкладке «Импорт».
                    </div>
                  )}
                </>
              ) : tab === "import" ? (
                <>
                  <div className="page-intro">
                    <h2>Импорт из источника</h2>
                    <p>
                      Сохраняется оригинал записи. Новые события проходят
                      модерацию перед публикацией. Поля JSON должны
                      соответствовать модели события API.
                    </p>
                  </div>
                  <form className="form-panel" onSubmit={importData}>
                    <label>
                      Формат
                      <select
                        value={format}
                        onChange={(e) => setFormat(e.target.value)}
                      >
                        <option value="json">JSON</option>
                        <option value="csv">CSV</option>
                      </select>
                    </label>
                    <label>
                      Файл JSON или CSV
                      <input
                        type="file"
                        accept=".json,.csv,application/json,text/csv"
                        onChange={async (e) => {
                          const file = e.target.files?.[0];
                          if (!file) return;
                          try {
                            if (file.size > 2_000_000)
                              throw new Error(
                                "Размер файла не должен превышать 2 МБ",
                              );
                            const extension = file.name
                              .split(".")
                              .pop()
                              ?.toLowerCase();
                            if (extension !== "csv" && extension !== "json")
                              throw new Error("Выберите файл .csv или .json");
                            const decoded = decodeImportFile(
                              await file.arrayBuffer(),
                              extension,
                            );
                            setContent(decoded.content);
                            setFormat(extension);
                            setFileInfo(`${file.name} · ${decoded.encoding}`);
                            setError("");
                            setResult(null);
                          } catch (error) {
                            setError(
                              `${(error as Error).message}. Прежнее содержимое не изменено.`,
                            );
                          }
                        }}
                      />
                    </label>
                    {fileInfo && <p role="status">Загружен файл: {fileInfo}</p>}
                    <p>
                      CSV поддерживает UTF-8 и Windows-1251, разделители
                      запятую, точку с запятой и табуляцию. JSON должен быть в
                      UTF-8. До 1000 событий и 2 МБ за загрузку.
                    </p>
                    <label>
                      Содержимое
                      <textarea
                        rows={12}
                        value={content}
                        onChange={(e) => setContent(e.target.value)}
                        required
                        placeholder="Вставьте данные источника или выберите файл"
                        spellCheck={false}
                      />
                    </label>
                    <button
                      className="button"
                      disabled={busy || !content.trim()}
                    >
                      {busy ? "Импортируем…" : "Проверить и импортировать"}
                    </button>
                  </form>
                  {result && (
                    <div className="notice" role="status">
                      <h3>Импорт завершён</h3>
                      <p>
                        Добавлено {result.added || 0}, обновлено{" "}
                        {result.updated || 0}, отклонено {result.rejected || 0},
                        дублей {result.duplicate || 0}, на проверке{" "}
                        {result.requires_review || 0}.
                      </p>
                      <pre className="code-block">
                        {JSON.stringify(result.details, null, 2)}
                      </pre>
                    </div>
                  )}
                </>
              ) : busy ? (
                <p role="status">Загружаем…</p>
              ) : (
                <AdminRecords
                  value={raw}
                  tab={tab}
                  token={token}
                  role={role}
                  onChange={() => setRefresh((v) => v + 1)}
                />
              )}
            </section>
          </>
        )}
      </div>
    </Shell>
  );
}
function RawItems({ value }: { value: unknown }) {
  if (value == null) return null;
  const values = Array.isArray(value)
    ? value
    : typeof value === "object" && value && "items" in value
      ? (value as { items: unknown[] }).items
      : [value];
  if (!values.length) return <div className="notice">Записей пока нет.</div>;
  return (
    <div className="raw-list">
      {values.map((item, i) => {
        const content = JSON.stringify(item, null, 2) || "";
        return (
          <article className="raw-item" key={i}>
            <textarea
              readOnly
              aria-label={`Данные JSON записи ${i + 1}`}
              value={content}
              rows={Math.min(15, Math.max(3, content.split("\n").length))}
            />
          </article>
        );
      })}
    </div>
  );
}

function AdminRecords({
  value,
  tab,
  token,
  role,
  onChange,
}: {
  value: unknown;
  tab: Tab;
  token: string;
  role: string;
  onChange: () => void;
}) {
  const [error, setError] = useState("");
  if (value == null) return null;
  const records = (Array.isArray(value) ? value : [value]) as Record<
    string,
    unknown
  >[];
  async function sourceStatus(id: string, status: string) {
    setError("");
    try {
      await api(`/admin/sources/${id}`, {
        method: "PATCH",
        headers: { Authorization: `Bearer ${token}` },
        body: JSON.stringify({ status }),
      });
      onChange();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  if (!records.length) return <div className="notice">Записей пока нет.</div>;
  if (tab === "sources")
    return (
      <>
        {error && (
          <div className="notice error" role="alert">
            {error}
          </div>
        )}
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th>Источник</th>
                <th>Последняя проверка</th>
                <th>Статус</th>
                <th>Ошибка</th>
              </tr>
            </thead>
            <tbody>
              {records.map((r) => (
                <tr key={String(r.id)}>
                  <td>
                    <SourceLink href={String(r.url)}>
                      {String(r.url)}
                    </SourceLink>
                  </td>
                  <td>
                    {r.last_checked_at
                      ? date(String(r.last_checked_at))
                      : "Не проверялся"}
                  </td>
                  <td>
                    <label>
                      <span className="sr-only">
                        Статус источника {String(r.url)}
                      </span>
                      <select
                        value={String(r.status)}
                        disabled={role !== "admin"}
                        onChange={(e) =>
                          sourceStatus(String(r.id), e.target.value)
                        }
                      >
                        {[
                          ["awaiting_review", "Ожидает проверки"],
                          ["verified", "Проверен автоматически"],
                          ["manually_verified", "Проверен вручную"],
                          ["error", "Ошибка"],
                          ["disabled", "Отключён"],
                          ["blocked", "Заблокирован"],
                        ].map(([v, t]) => (
                          <option value={v} key={v}>
                            {t}
                          </option>
                        ))}
                      </select>
                    </label>
                  </td>
                  <td>{String(r.last_error || "Нет")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </>
    );
  if (tab === "feedback")
    return (
      <div className="raw-list">
        {error && <p role="alert">{error}</p>}
        {records.map((r) => (
          <article className="raw-item" key={String(r.id)}>
            <h2>Обращение {String(r.id)}</h2>
            <label>
              Статус обращения
              <select
                aria-label="Статус обращения"
                value={String(r.status || "pending")}
                disabled={role === "viewer"}
                onChange={async (e) => {
                  try {
                    await api(`/admin/feedback/${r.id}`, {
                      method: "PATCH",
                      headers: { Authorization: `Bearer ${token}` },
                      body: JSON.stringify({
                        status: e.target.value,
                        note: "Статус изменён в очереди обращений",
                      }),
                    });
                    onChange();
                  } catch (e) {
                    setError((e as Error).message);
                  }
                }}
              >
                {[
                  ["pending", "Получено"],
                  ["reviewing", "На рассмотрении"],
                  ["resolved", "Решено"],
                  ["rejected", "Отклонено"],
                ].map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <p style={{ whiteSpace: "pre-wrap", fontSize: 14 }}>
              {String(r.message || "")}
            </p>
            <small>
              {r.created_at ? date(String(r.created_at), true) : ""}
              {r.contact ? ` · Контакт: ${r.contact}` : ""}
              {r.event_id ? ` · Событие: ${r.event_id}` : ""}
            </small>
          </article>
        ))}
      </div>
    );
  if (tab === "metrics") {
    const labels: Record<string, string> = {
      requests: "Запросы",
      errors_5xx: "Серверные ошибки",
      search_latency_p95_ms: "Поиск p95, мс",
      search_samples: "Измерений поиска",
    };
    return (
      <div className="notice">
        <h2 style={{ fontSize: 23 }}>Текущий процесс API</h2>
        <p>
          Счётчики сбрасываются при перезапуске. Это эксплуатационные измерения,
          не оценка качества рекомендаций.
        </p>
        <dl className="detail-data">
          {Object.entries(records[0] || {}).map(([k, v]) => (
            <div key={k}>
              <dt>{labels[k] || k}</dt>
              <dd>{v == null ? "Недостаточно данных" : String(v)}</dd>
            </div>
          ))}
        </dl>
      </div>
    );
  }
  return <RawItems value={value} />;
}
