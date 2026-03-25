import { ChangeEvent, FormEvent, useEffect, useMemo, useState } from "react";

type QueueItem = {
  id: number;
  account_id: number;
  mode: string;
  action: string;
  status: string;
  source: string;
  scheduled_for?: number | null;
  session_id?: string | null;
};

type SessionItem = {
  id: string;
  mode: string;
  status: string;
  started_at: number;
  ended_at?: number | null;
  error?: string | null;
  summary?: Record<string, unknown>;
};

type EventItem = {
  id: number;
  created_at: number;
  account_id?: number | null;
  mode?: string | null;
  kind: string;
  level?: string | null;
  message?: string | null;
  payload: Record<string, unknown>;
};

type Account = {
  id: number;
  name: string;
  discord_user_id?: string | null;
  discordusername?: string | null;
  token: string;
  max_power: number;
};

type WishlistItem = {
  id?: number;
  account_id?: number | null;
  name: string;
  priority: number;
  is_star: boolean;
};

type AccountSnapshot = {
  account: Account;
  status: string;
  active_mode?: string | null;
  active_session_id?: string | null;
  paused_mode?: string | null;
  last_mode?: string | null;
  connection_status?: string | null;
  dashboard_state?: string | null;
  last_action?: string | null;
  next_action?: string | null;
  countdown_active?: boolean;
  countdown_remaining?: number | null;
  last_message?: string | null;
  last_error?: string | null;
  queue?: QueueItem[];
  best_candidate?: Record<string, unknown> | null;
  summary?: Record<string, unknown> | null;
};

type OverviewPayload = {
  accounts: AccountSnapshot[];
  queue: QueueItem[];
  recent_sessions: SessionItem[];
  running_count: number;
};

type SettingsPayload = {
  app_settings: Record<string, unknown>;
  ui_settings: Record<string, unknown>;
};

type WishlistPayload = {
  global: WishlistItem[];
  accounts: Record<string, WishlistItem[]>;
};

type AccountHistory = {
  sessions: SessionItem[];
  events: EventItem[];
};

const TABS = ["Overview", "Accounts", "Wishlist", "Logs", "Settings"] as const;
const ACCOUNT_SUBTABS = ["Live", "Main Bot", "Ouro", "History", "Config"] as const;
const MODE_OPTIONS = ["main", "oh", "oc", "oq"] as const;

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

function fmtTime(value?: number | null): string {
  return value ? new Date(value * 1000).toLocaleString() : "n/a";
}

function fmtCountdown(seconds?: number | null): string {
  if (!seconds && seconds !== 0) return "n/a";
  const safe = Math.max(0, Math.floor(seconds));
  const hrs = Math.floor(safe / 3600);
  const mins = Math.floor((safe % 3600) / 60);
  const secs = safe % 60;
  return `${hrs.toString().padStart(2, "0")}:${mins.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
}

function toLocalInputValue(date: Date): string {
  const year = date.getFullYear();
  const month = `${date.getMonth() + 1}`.padStart(2, "0");
  const day = `${date.getDate()}`.padStart(2, "0");
  const hours = `${date.getHours()}`.padStart(2, "0");
  const minutes = `${date.getMinutes()}`.padStart(2, "0");
  return `${year}-${month}-${day}T${hours}:${minutes}`;
}

function parseJsonEditor(text: string): Record<string, unknown> {
  const parsed = JSON.parse(text);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("Expected a JSON object.");
  }
  return parsed as Record<string, unknown>;
}

function emptyWishlistRow(): WishlistItem {
  return { name: "", priority: 2, is_star: false };
}

function WishlistEditor(props: {
  title: string;
  items: WishlistItem[];
  onChange: (items: WishlistItem[]) => void;
  onSave: () => Promise<void>;
}) {
  const { title, items, onChange, onSave } = props;
  const updateRow = (index: number, patch: Partial<WishlistItem>) => {
    onChange(items.map((item, itemIndex) => (itemIndex === index ? { ...item, ...patch } : item)));
  };

  return (
    <section className="panel">
      <div className="panel-header">
        <h3>{title}</h3>
        <div className="panel-actions">
          <button onClick={() => onChange([...items, emptyWishlistRow()])}>Add Row</button>
          <button className="primary" onClick={() => void onSave()}>
            Save
          </button>
        </div>
      </div>
      <div className="wishlist-table">
        <div className="table-row table-head">
          <span>Name</span>
          <span>Priority</span>
          <span>Star</span>
          <span />
        </div>
        {items.map((item, index) => (
          <div className="table-row" key={`${item.name}-${index}`}>
            <input value={item.name} onChange={(event) => updateRow(index, { name: event.target.value })} placeholder="Character or series" />
            <input type="number" min={1} max={3} value={item.priority} onChange={(event) => updateRow(index, { priority: Number(event.target.value) || 2 })} />
            <label className="checkbox">
              <input type="checkbox" checked={item.is_star} onChange={(event) => updateRow(index, { is_star: event.target.checked, priority: event.target.checked ? 3 : item.priority })} />
              <span>Star</span>
            </label>
            <button className="danger ghost" onClick={() => onChange(items.filter((_, itemIndex) => itemIndex !== index))}>
              Remove
            </button>
          </div>
        ))}
      </div>
    </section>
  );
}

export default function App() {
  const [activeTab, setActiveTab] = useState<(typeof TABS)[number]>("Overview");
  const [accountTab, setAccountTab] = useState<(typeof ACCOUNT_SUBTABS)[number]>("Live");
  const [overview, setOverview] = useState<OverviewPayload | null>(null);
  const [logs, setLogs] = useState<EventItem[]>([]);
  const [settings, setSettings] = useState<SettingsPayload | null>(null);
  const [wishlist, setWishlist] = useState<WishlistPayload | null>(null);
  const [history, setHistory] = useState<AccountHistory | null>(null);
  const [selectedAccountId, setSelectedAccountId] = useState<number | null>(null);
  const [notice, setNotice] = useState<string>("Loading...");
  const [lastEventTick, setLastEventTick] = useState<number>(0);
  const [logMode, setLogMode] = useState<string>("");
  const [logLevel, setLogLevel] = useState<string>("");
  const [globalWishlistDraft, setGlobalWishlistDraft] = useState<WishlistItem[]>([]);
  const [accountWishlistDraft, setAccountWishlistDraft] = useState<WishlistItem[]>([]);
  const [appSettingsText, setAppSettingsText] = useState<string>("{}");
  const [uiSettingsText, setUiSettingsText] = useState<string>("{}");
  const [accountForm, setAccountForm] = useState<Account>({ id: 0, name: "", discord_user_id: "", discordusername: "", token: "", max_power: 110 });
  const [queueMode, setQueueMode] = useState<string>("main");
  const [scheduleMode, setScheduleMode] = useState<string>("main");
  const [scheduleAt, setScheduleAt] = useState<string>(toLocalInputValue(new Date(Date.now() + 10 * 60 * 1000)));
  const [importText, setImportText] = useState<string>("");
  const selectedSnapshot = useMemo(() => overview?.accounts.find((item) => item.account.id === selectedAccountId) ?? null, [overview, selectedAccountId]);
  const selectedAccountWishlist = useMemo(() => {
    if (!wishlist || selectedAccountId == null) return [];
    return wishlist.accounts[String(selectedAccountId)] ?? [];
  }, [selectedAccountId, wishlist]);

  async function loadOverview() {
    const payload = await fetchJson<OverviewPayload>("/api/overview");
    setOverview(payload);
    if (selectedAccountId == null && payload.accounts.length > 0) {
      setSelectedAccountId(payload.accounts[0].account.id);
    }
  }

  async function loadLogs() {
    const url = new URL("/api/logs", window.location.origin);
    if (selectedAccountId != null && activeTab === "Accounts") url.searchParams.set("account_id", String(selectedAccountId));
    if (logMode) url.searchParams.set("mode", logMode);
    if (logLevel) url.searchParams.set("level", logLevel);
    const payload = await fetchJson<{ items: EventItem[] }>(url.pathname + url.search);
    setLogs(payload.items);
  }

  async function loadSettings() {
    const payload = await fetchJson<SettingsPayload>("/api/settings");
    setSettings(payload);
    setAppSettingsText(JSON.stringify(payload.app_settings, null, 2));
    setUiSettingsText(JSON.stringify(payload.ui_settings, null, 2));
  }

  async function loadWishlist() {
    const payload = await fetchJson<WishlistPayload>("/api/wishlist");
    setWishlist(payload);
    setGlobalWishlistDraft(payload.global.map((item) => ({ ...item })));
    if (selectedAccountId != null) {
      setAccountWishlistDraft((payload.accounts[String(selectedAccountId)] ?? []).map((item) => ({ ...item })));
    }
  }

  async function loadHistory(accountId: number) {
    const payload = await fetchJson<AccountHistory>(`/api/accounts/${accountId}/history`);
    setHistory(payload);
  }

  async function refreshAll() {
    await Promise.all([loadOverview(), loadSettings(), loadWishlist(), loadLogs()]);
    if (selectedAccountId != null) await loadHistory(selectedAccountId);
  }

  async function sendModeAction(accountId: number, mode: string, action: string) {
    await fetchJson(`/api/accounts/${accountId}/modes/${mode}/${action}`, { method: "POST" });
    setNotice(`Queued ${action} for ${mode}.`);
    await loadOverview();
  }

  async function saveSettings() {
    const payload = { app_settings: parseJsonEditor(appSettingsText), ui_settings: parseJsonEditor(uiSettingsText) };
    const saved = await fetchJson<SettingsPayload>("/api/settings", { method: "PUT", body: JSON.stringify(payload) });
    setSettings(saved);
    setNotice("Settings saved. Restart affected sessions to apply.");
  }

  async function saveGlobalWishlist() {
    const items = globalWishlistDraft.filter((item) => item.name.trim());
    await fetchJson("/api/wishlist/global", { method: "PUT", body: JSON.stringify(items) });
    setNotice("Global wishlist saved.");
    await loadWishlist();
  }

  async function saveAccountWishlist() {
    if (selectedAccountId == null) return;
    const items = accountWishlistDraft.filter((item) => item.name.trim());
    await fetchJson(`/api/accounts/${selectedAccountId}/wishlist`, { method: "PUT", body: JSON.stringify(items) });
    setNotice("Account wishlist saved.");
    await loadWishlist();
  }

  async function saveAccount(event: FormEvent) {
    event.preventDefault();
    const url = accountForm.id ? `/api/accounts/${accountForm.id}` : "/api/accounts";
    const method = accountForm.id ? "PUT" : "POST";
    const result = await fetchJson<{ account: Account }>(url, { method, body: JSON.stringify(accountForm) });
    setNotice(`Saved account ${result.account.name}.`);
    await loadOverview();
  }

  async function enqueueAction() {
    if (selectedAccountId == null) return;
    await fetchJson("/api/queue", { method: "POST", body: JSON.stringify({ account_id: selectedAccountId, mode: queueMode, action: "start" }) });
    setNotice(`Queued ${queueMode}.`);
    await loadOverview();
  }

  async function createSchedule() {
    if (selectedAccountId == null) return;
    await fetchJson("/api/schedules", {
      method: "POST",
      body: JSON.stringify({ account_id: selectedAccountId, mode: scheduleMode, action: "start", run_at: new Date(scheduleAt).toISOString() })
    });
    setNotice(`Scheduled ${scheduleMode} for ${scheduleAt}.`);
    await loadHistory(selectedAccountId);
  }

  async function exportData() {
    const payload = await fetchJson<Record<string, unknown>>("/api/export");
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "mudae-webui-backup.json";
    link.click();
    URL.revokeObjectURL(url);
  }

  async function importData() {
    const payload = JSON.parse(importText);
    await fetchJson("/api/import", { method: "POST", body: JSON.stringify(payload) });
    setNotice("Imported backup bundle.");
    await refreshAll();
  }

  function handleImportFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    void file.text().then((text) => setImportText(text));
  }

  useEffect(() => {
    void refreshAll().catch((error: Error) => setNotice(error.message));
  }, []);

  useEffect(() => {
    if (!settings) return;
    setAppSettingsText(JSON.stringify(settings.app_settings, null, 2));
    setUiSettingsText(JSON.stringify(settings.ui_settings, null, 2));
  }, [settings]);

  useEffect(() => {
    if (!selectedSnapshot) return;
    setAccountForm({
      ...selectedSnapshot.account,
      discord_user_id: selectedSnapshot.account.discord_user_id ?? "",
      discordusername: selectedSnapshot.account.discordusername ?? ""
    });
  }, [selectedSnapshot]);

  useEffect(() => {
    setAccountWishlistDraft(selectedAccountWishlist.map((item) => ({ ...item })));
  }, [selectedAccountWishlist]);

  useEffect(() => {
    if (selectedAccountId != null) void loadHistory(selectedAccountId).catch((error: Error) => setNotice(error.message));
  }, [selectedAccountId]);

  useEffect(() => {
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${protocol}://${window.location.host}/ws/live`);
    socket.onmessage = (event) => {
      const payload = JSON.parse(event.data) as { kind?: string; overview?: OverviewPayload };
      if (payload.kind === "bootstrap" && payload.overview) {
        setOverview(payload.overview);
        return;
      }
      setLastEventTick(Date.now());
    };
    socket.onerror = () => setNotice("WebSocket disconnected; retrying automatically.");
    return () => socket.close();
  }, []);

  useEffect(() => {
    if (!lastEventTick) return;
    void loadOverview();
    if (activeTab === "Logs") void loadLogs();
    if (activeTab === "Wishlist") void loadWishlist();
    if (activeTab === "Accounts" && selectedAccountId != null) void loadHistory(selectedAccountId);
  }, [lastEventTick, activeTab, selectedAccountId]);

  const accountOptions = overview?.accounts ?? [];

  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <p className="eyebrow">Local Daemon + SPA</p>
          <h1>Mudae WebUI</h1>
        </div>
        <div className="header-meta">
          <span>{overview?.running_count ?? 0} active</span>
          <button onClick={() => void refreshAll()}>Refresh</button>
        </div>
      </header>

      <nav className="top-tabs">
        {TABS.map((tab) => (
          <button key={tab} className={tab === activeTab ? "active" : ""} onClick={() => setActiveTab(tab)}>
            {tab}
          </button>
        ))}
      </nav>

      <p className="notice">{notice}</p>

      {activeTab === "Overview" && (
        <section className="grid overview-grid">
          {accountOptions.map((snapshot) => (
            <article className="panel card" key={snapshot.account.id}>
              <div className="card-header">
                <div>
                  <h3>{snapshot.account.name}</h3>
                  <p>{snapshot.account.discordusername || "No username"}</p>
                </div>
                <span className={`status-pill status-${snapshot.status}`}>{snapshot.status}</span>
              </div>
              <dl className="meta-list">
                <div><dt>Mode</dt><dd>{snapshot.active_mode || snapshot.paused_mode || "idle"}</dd></div>
                <div><dt>Connection</dt><dd>{snapshot.connection_status || "n/a"}</dd></div>
                <div><dt>Next</dt><dd>{snapshot.next_action || "n/a"}</dd></div>
                <div><dt>Countdown</dt><dd>{fmtCountdown(snapshot.countdown_remaining)}</dd></div>
              </dl>
              <p className="muted">{snapshot.last_message || snapshot.last_action || "No live events yet."}</p>
              <div className="button-row">
                <button className="primary" onClick={() => void sendModeAction(snapshot.account.id, "main", "start")}>Start Main</button>
                <button onClick={() => void sendModeAction(snapshot.account.id, "oh", "start")}>OH</button>
                <button onClick={() => void sendModeAction(snapshot.account.id, "oc", "start")}>OC</button>
                <button onClick={() => void sendModeAction(snapshot.account.id, "oq", "start")}>OQ</button>
              </div>
              <div className="button-row">
                <button onClick={() => void sendModeAction(snapshot.account.id, snapshot.active_mode || "main", "pause")}>Pause</button>
                <button onClick={() => void sendModeAction(snapshot.account.id, snapshot.active_mode || "main", "resume")}>Resume</button>
                <button onClick={() => void sendModeAction(snapshot.account.id, snapshot.active_mode || "main", "restart")}>Restart</button>
                <button className="danger" onClick={() => void sendModeAction(snapshot.account.id, snapshot.active_mode || "main", "stop")}>Stop</button>
              </div>
            </article>
          ))}
        </section>
      )}

      {activeTab === "Accounts" && (
        <section className="account-layout">
          <aside className="account-sidebar panel">
            <div className="panel-header">
              <h3>Accounts</h3>
              <button onClick={() => setAccountForm({ id: 0, name: "", discord_user_id: "", discordusername: "", token: "", max_power: 110 })}>New</button>
            </div>
            {accountOptions.map((snapshot) => (
              <button key={snapshot.account.id} className={`sidebar-item ${selectedAccountId === snapshot.account.id ? "active" : ""}`} onClick={() => setSelectedAccountId(snapshot.account.id)}>
                <strong>{snapshot.account.name}</strong>
                <span>{snapshot.status}</span>
              </button>
            ))}
          </aside>

          <div className="account-detail">
            <nav className="sub-tabs">
              {ACCOUNT_SUBTABS.map((tab) => (
                <button key={tab} className={tab === accountTab ? "active" : ""} onClick={() => setAccountTab(tab)}>
                  {tab}
                </button>
              ))}
            </nav>

            {!selectedSnapshot ? (
              <section className="panel"><p>Select an account to view details.</p></section>
            ) : (
              <>
                {accountTab === "Live" && (
                  <section className="panel">
                    <div className="panel-header">
                      <h3>{selectedSnapshot.account.name} Live</h3>
                      <span className={`status-pill status-${selectedSnapshot.status}`}>{selectedSnapshot.status}</span>
                    </div>
                    <div className="grid two-col">
                      <div>
                        <p><strong>Active mode:</strong> {selectedSnapshot.active_mode || selectedSnapshot.paused_mode || "idle"}</p>
                        <p><strong>Connection:</strong> {selectedSnapshot.connection_status || "n/a"}</p>
                        <p><strong>Dashboard state:</strong> {selectedSnapshot.dashboard_state || "n/a"}</p>
                        <p><strong>Countdown:</strong> {fmtCountdown(selectedSnapshot.countdown_remaining)}</p>
                        <p><strong>Last action:</strong> {selectedSnapshot.last_action || "n/a"}</p>
                        <p><strong>Next action:</strong> {selectedSnapshot.next_action || "n/a"}</p>
                      </div>
                      <div>
                        <p><strong>Latest message:</strong> {selectedSnapshot.last_message || "n/a"}</p>
                        <p><strong>Last error:</strong> {selectedSnapshot.last_error || "none"}</p>
                        <p><strong>Best candidate:</strong> {selectedSnapshot.best_candidate ? JSON.stringify(selectedSnapshot.best_candidate) : "n/a"}</p>
                        <p><strong>Summary:</strong> {selectedSnapshot.summary ? JSON.stringify(selectedSnapshot.summary) : "n/a"}</p>
                      </div>
                    </div>
                    <div className="button-row">
                      <button className="primary" onClick={() => void sendModeAction(selectedSnapshot.account.id, "main", "start")}>Start Main</button>
                      <button onClick={() => void sendModeAction(selectedSnapshot.account.id, selectedSnapshot.active_mode || "main", "pause")}>Pause</button>
                      <button onClick={() => void sendModeAction(selectedSnapshot.account.id, selectedSnapshot.active_mode || "main", "resume")}>Resume</button>
                      <button onClick={() => void sendModeAction(selectedSnapshot.account.id, selectedSnapshot.active_mode || "main", "restart")}>Restart</button>
                      <button className="danger" onClick={() => void sendModeAction(selectedSnapshot.account.id, selectedSnapshot.active_mode || "main", "stop")}>Stop</button>
                    </div>
                    <div className="grid two-col">
                      <section className="panel inset">
                        <div className="panel-header"><h4>Queue</h4></div>
                        <div className="inline-form">
                          <select value={queueMode} onChange={(event) => setQueueMode(event.target.value)}>
                            {MODE_OPTIONS.map((mode) => <option key={mode} value={mode}>{mode.toUpperCase()}</option>)}
                          </select>
                          <button onClick={() => void enqueueAction()}>Queue Run</button>
                        </div>
                        <ul className="data-list">
                          {(selectedSnapshot.queue ?? []).map((item) => <li key={item.id}>#{item.id} {item.mode.toUpperCase()} {item.action} [{item.status}]</li>)}
                          {(selectedSnapshot.queue ?? []).length === 0 && <li>No pending queue items.</li>}
                        </ul>
                      </section>
                      <section className="panel inset">
                        <div className="panel-header"><h4>One-Time Schedule</h4></div>
                        <div className="inline-form">
                          <select value={scheduleMode} onChange={(event) => setScheduleMode(event.target.value)}>
                            {MODE_OPTIONS.map((mode) => <option key={mode} value={mode}>{mode.toUpperCase()}</option>)}
                          </select>
                          <input type="datetime-local" value={scheduleAt} onChange={(event) => setScheduleAt(event.target.value)} />
                          <button onClick={() => void createSchedule()}>Schedule</button>
                        </div>
                      </section>
                    </div>
                  </section>
                )}

                {accountTab === "Main Bot" && (
                  <section className="panel">
                    <div className="panel-header"><h3>Main Bot Control</h3></div>
                    <p>Config saves immediately, but restart the session to apply changes.</p>
                    <div className="button-row">
                      <button className="primary" onClick={() => void sendModeAction(selectedSnapshot.account.id, "main", "start")}>Start Main</button>
                      <button onClick={() => void sendModeAction(selectedSnapshot.account.id, "main", "restart")}>Restart Main</button>
                      <button onClick={() => void sendModeAction(selectedSnapshot.account.id, "main", "pause")}>Pause Main</button>
                      <button className="danger" onClick={() => void sendModeAction(selectedSnapshot.account.id, "main", "stop")}>Stop Main</button>
                    </div>
                  </section>
                )}

                {accountTab === "Ouro" && (
                  <section className="panel">
                    <div className="panel-header"><h3>Standalone Ouro Modes</h3></div>
                    <p>Main bot may still call integrated auto-Ouro. These buttons launch standalone OH/OC/OQ workers.</p>
                    <div className="button-row">
                      <button onClick={() => void sendModeAction(selectedSnapshot.account.id, "oh", "start")}>Start OH</button>
                      <button onClick={() => void sendModeAction(selectedSnapshot.account.id, "oc", "start")}>Start OC</button>
                      <button onClick={() => void sendModeAction(selectedSnapshot.account.id, "oq", "start")}>Start OQ</button>
                    </div>
                  </section>
                )}

                {accountTab === "History" && (
                  <section className="grid two-col">
                    <section className="panel">
                      <div className="panel-header"><h3>Recent Sessions</h3></div>
                      <ul className="data-list">
                        {(history?.sessions ?? []).map((session) => (
                          <li key={session.id}>
                            <strong>{session.mode.toUpperCase()}</strong> [{session.status}]<br />
                            <span>{fmtTime(session.started_at)}</span>
                            {session.error && <span className="error-text"> {session.error}</span>}
                          </li>
                        ))}
                        {(history?.sessions ?? []).length === 0 && <li>No session history yet.</li>}
                      </ul>
                    </section>
                    <section className="panel">
                      <div className="panel-header"><h3>Recent Events</h3></div>
                      <ul className="data-list">
                        {(history?.events ?? []).map((item) => (
                          <li key={item.id}>
                            <strong>{item.kind}</strong> {item.message ? `· ${item.message}` : ""}<br />
                            <span>{fmtTime(item.created_at)}</span>
                          </li>
                        ))}
                        {(history?.events ?? []).length === 0 && <li>No account events yet.</li>}
                      </ul>
                    </section>
                  </section>
                )}

                {accountTab === "Config" && (
                  <section className="grid two-col">
                    <form className="panel" onSubmit={(event) => void saveAccount(event)}>
                      <div className="panel-header"><h3>Account Config</h3></div>
                      <label>Name<input value={accountForm.name} onChange={(event) => setAccountForm({ ...accountForm, name: event.target.value })} /></label>
                      <label>Discord User ID<input value={accountForm.discord_user_id ?? ""} onChange={(event) => setAccountForm({ ...accountForm, discord_user_id: event.target.value })} /></label>
                      <label>Discord Username<input value={accountForm.discordusername ?? ""} onChange={(event) => setAccountForm({ ...accountForm, discordusername: event.target.value })} /></label>
                      <label>Token<textarea value={accountForm.token} onChange={(event) => setAccountForm({ ...accountForm, token: event.target.value })} /></label>
                      <label>Max Power<input type="number" min={1} value={accountForm.max_power} onChange={(event) => setAccountForm({ ...accountForm, max_power: Number(event.target.value) || 110 })} /></label>
                      <div className="button-row"><button className="primary" type="submit">Save Account</button></div>
                    </form>
                    <WishlistEditor title={`Wishlist: ${selectedSnapshot.account.name}`} items={accountWishlistDraft} onChange={setAccountWishlistDraft} onSave={saveAccountWishlist} />
                  </section>
                )}
              </>
            )}
          </div>
        </section>
      )}

      {activeTab === "Wishlist" && (
        <section className="grid two-col">
          <WishlistEditor title="Global Wishlist" items={globalWishlistDraft} onChange={setGlobalWishlistDraft} onSave={saveGlobalWishlist} />
          <section className="panel">
            <div className="panel-header"><h3>Aggregate View</h3></div>
            <ul className="data-list">
              {Object.entries(wishlist?.accounts ?? {}).flatMap(([accountId, items]) =>
                items.map((item, index) => (
                  <li key={`${accountId}-${item.name}-${index}`}>
                    <strong>Account {accountId}</strong> · {item.name} · priority {item.priority} {item.is_star ? "★" : ""}
                  </li>
                ))
              )}
              {Object.keys(wishlist?.accounts ?? {}).length === 0 && <li>No per-account wishlist entries yet.</li>}
            </ul>
          </section>
        </section>
      )}

      {activeTab === "Logs" && (
        <section className="panel">
          <div className="panel-header">
            <h3>Structured Logs</h3>
            <div className="inline-form">
              <select value={logMode} onChange={(event) => setLogMode(event.target.value)}>
                <option value="">All Modes</option>
                {MODE_OPTIONS.map((mode) => <option key={mode} value={mode}>{mode.toUpperCase()}</option>)}
              </select>
              <select value={logLevel} onChange={(event) => setLogLevel(event.target.value)}>
                <option value="">All Levels</option>
                {["INFO", "WARN", "ERROR", "SUCCESS"].map((level) => <option key={level} value={level}>{level}</option>)}
              </select>
              <button onClick={() => void loadLogs()}>Apply</button>
            </div>
          </div>
          <div className="log-list">
            {logs.map((item) => (
              <article className="log-item" key={item.id}>
                <header><strong>{item.kind}</strong><span>{item.level || "INFO"}</span><time>{fmtTime(item.created_at)}</time></header>
                <p>{item.message || "(no message)"}</p>
                <pre>{JSON.stringify(item.payload, null, 2)}</pre>
              </article>
            ))}
            {logs.length === 0 && <p>No log events match the current filters.</p>}
          </div>
        </section>
      )}

      {activeTab === "Settings" && (
        <section className="grid two-col">
          <section className="panel">
            <div className="panel-header"><h3>App Settings</h3></div>
            <textarea className="code-block" value={appSettingsText} onChange={(event) => setAppSettingsText(event.target.value)} />
            <div className="panel-header"><h3>UI Settings</h3></div>
            <textarea className="code-block" value={uiSettingsText} onChange={(event) => setUiSettingsText(event.target.value)} />
            <div className="button-row">
              <button className="primary" onClick={() => void saveSettings()}>Save Settings</button>
              <button onClick={() => void exportData()}>Export Backup</button>
            </div>
          </section>
          <section className="panel">
            <div className="panel-header"><h3>Import Backup</h3></div>
            <input type="file" accept="application/json" onChange={handleImportFile} />
            <textarea className="code-block" value={importText} onChange={(event) => setImportText(event.target.value)} placeholder="Paste exported JSON here" />
            <button className="primary" onClick={() => void importData()}>Import Backup</button>
          </section>
        </section>
      )}
    </div>
  );
}
