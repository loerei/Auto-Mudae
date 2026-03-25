import { ChangeEvent, FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { AnimatePresence, MotionConfig, motion, useReducedMotion } from "framer-motion";

import { DashboardPanel, LiveDashboard, LogPayloadView, OverviewCard, StatusChip, fmtTime } from "./live-ui";
import { buildFadeUp, buildHighlightFlash, buildStaggerContainer, getTabTransition } from "./motion";
import { Account, AccountHistory, AccountSnapshot, EventItem, LiveEventMessage, OverviewPayload, SettingsPayload, WishlistItem, WishlistPayload } from "./types";

const TABS = ["Overview", "Accounts", "Wishlist", "Logs", "Settings"] as const;
const ACCOUNT_SUBTABS = ["Live", "Main Bot", "Ouro", "History", "Config"] as const;

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

function matchesLogFilters(item: EventItem, logMode: string, logLevel: string): boolean {
  if (logMode && item.mode !== logMode) return false;
  if (logLevel && item.level !== logLevel) return false;
  return true;
}

function recalcRunningCount(accounts: AccountSnapshot[]): number {
  return accounts.filter((snapshot) => ["running", "queued", "pausing", "stopping"].includes(snapshot.status)).length;
}

function mergeAccountState(prev: OverviewPayload | null, state: AccountSnapshot): OverviewPayload | null {
  if (!prev) return null;
  const accounts = prev.accounts.some((item) => item.account.id === state.account.id)
    ? prev.accounts.map((item) => (item.account.id === state.account.id ? { ...item, ...state } : item))
    : [...prev.accounts, state];
  return { ...prev, accounts, running_count: recalcRunningCount(accounts) };
}

function WishlistEditor(props: {
  title: string;
  items: WishlistItem[];
  onChange: (items: WishlistItem[]) => void;
  onSave: () => Promise<void>;
}) {
  const updateRow = (index: number, patch: Partial<WishlistItem>) => {
    props.onChange(props.items.map((item, itemIndex) => (itemIndex === index ? { ...item, ...patch } : item)));
  };

  return (
    <section className="panel">
      <div className="panel-header">
        <h3>{props.title}</h3>
        <div className="panel-actions">
          <button onClick={() => props.onChange([...props.items, emptyWishlistRow()])}>Add Row</button>
          <button className="primary" onClick={() => void props.onSave()}>
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
        {props.items.map((item, index) => (
          <div className="table-row" key={`${item.name}-${index}`}>
            <input value={item.name} onChange={(event) => updateRow(index, { name: event.target.value })} placeholder="Character or series" />
            <input type="number" min={1} max={3} value={item.priority} onChange={(event) => updateRow(index, { priority: Number(event.target.value) || 2 })} />
            <label className="checkbox">
              <input type="checkbox" checked={item.is_star} onChange={(event) => updateRow(index, { is_star: event.target.checked, priority: event.target.checked ? 3 : item.priority })} />
              <span>Star</span>
            </label>
            <button className="danger ghost" onClick={() => props.onChange(props.items.filter((_, itemIndex) => itemIndex !== index))}>
              Remove
            </button>
          </div>
        ))}
      </div>
    </section>
  );
}

export default function App() {
  const reducedMotion = useReducedMotion();
  const [activeTab, setActiveTab] = useState<(typeof TABS)[number]>("Overview");
  const [accountTab, setAccountTab] = useState<(typeof ACCOUNT_SUBTABS)[number]>("Live");
  const [overview, setOverview] = useState<OverviewPayload | null>(null);
  const [logs, setLogs] = useState<EventItem[]>([]);
  const [settings, setSettings] = useState<SettingsPayload | null>(null);
  const [wishlist, setWishlist] = useState<WishlistPayload | null>(null);
  const [history, setHistory] = useState<AccountHistory | null>(null);
  const [selectedAccountId, setSelectedAccountId] = useState<number | null>(null);
  const [notice, setNotice] = useState<string>("Loading...");
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
  const [pendingLogCount, setPendingLogCount] = useState(0);

  const logListRef = useRef<HTMLDivElement | null>(null);
  const isLogAtLiveEdgeRef = useRef(true);
  const pendingLogEventsRef = useRef<EventItem[]>([]);
  const selectedAccountIdRef = useRef<number | null>(null);
  const activeTabRef = useRef<(typeof TABS)[number]>("Overview");
  const logFilterRef = useRef({ mode: "", level: "" });

  const selectedSnapshot = useMemo(() => overview?.accounts.find((item) => item.account.id === selectedAccountId) ?? null, [overview, selectedAccountId]);
  const selectedAccountWishlist = useMemo(() => {
    if (!wishlist || selectedAccountId == null) return [];
    return wishlist.accounts[String(selectedAccountId)] ?? [];
  }, [selectedAccountId, wishlist]);
  const tabVariants = getTabTransition(reducedMotion);
  const overviewVariants = buildStaggerContainer(reducedMotion, 0.05);
  const logListVariants = buildStaggerContainer(reducedMotion, 0.03);

  async function loadOverview() {
    const payload = await fetchJson<OverviewPayload>("/api/overview");
    setOverview(payload);
    if (selectedAccountId == null && payload.accounts.length > 0) {
      setSelectedAccountId(payload.accounts[0].account.id);
    }
  }

  async function loadLogs() {
    const url = new URL("/api/logs", window.location.origin);
    if (logMode) url.searchParams.set("mode", logMode);
    if (logLevel) url.searchParams.set("level", logLevel);
    const payload = await fetchJson<{ items: EventItem[] }>(url.pathname + url.search);
    pendingLogEventsRef.current = [];
    setPendingLogCount(0);
    setLogs(payload.items);
    requestAnimationFrame(() => {
      if (logListRef.current) {
        logListRef.current.scrollTop = 0;
      }
      isLogAtLiveEdgeRef.current = true;
    });
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
    setNotice(`Requested ${action} for ${mode}.`);
    await loadOverview();
  }

  async function forceStopAccount(accountId: number) {
    await fetchJson(`/api/accounts/${accountId}/force-stop`, { method: "POST" });
    setNotice("Force stop requested. Pending queue cleared.");
    await loadOverview();
    if (selectedAccountId === accountId) {
      await loadHistory(accountId);
    }
  }

  async function clearAccountQueue(accountId: number) {
    const result = await fetchJson<{ cleared: number }>(`/api/accounts/${accountId}/queue`, { method: "DELETE" });
    setNotice(`Cleared ${result.cleared} queue item${result.cleared === 1 ? "" : "s"}.`);
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

  function flushPendingLogs(scrollToTop = true) {
    if (pendingLogEventsRef.current.length === 0) return;
    const buffered = pendingLogEventsRef.current;
    pendingLogEventsRef.current = [];
    setPendingLogCount(0);
    setLogs((prev) => [...buffered, ...prev].slice(0, 200));
    if (scrollToTop) {
      requestAnimationFrame(() => {
        logListRef.current?.scrollTo({ top: 0, behavior: reducedMotion ? "auto" : "smooth" });
      });
    }
  }

  function handleLogScroll() {
    const element = logListRef.current;
    if (!element) return;
    isLogAtLiveEdgeRef.current = element.scrollTop <= 24;
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
    if (selectedAccountId != null) {
      void loadHistory(selectedAccountId).catch((error: Error) => setNotice(error.message));
    }
  }, [selectedAccountId]);

  useEffect(() => {
    selectedAccountIdRef.current = selectedAccountId;
  }, [selectedAccountId]);

  useEffect(() => {
    activeTabRef.current = activeTab;
  }, [activeTab]);

  useEffect(() => {
    logFilterRef.current = { mode: logMode, level: logLevel };
  }, [logLevel, logMode]);

  useEffect(() => {
    if (activeTab !== "Logs") return;
    requestAnimationFrame(() => handleLogScroll());
  }, [activeTab, logs.length]);

  useEffect(() => {
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${protocol}://${window.location.host}/ws/live`);
    socket.onmessage = (event) => {
      const payload = JSON.parse(event.data) as LiveEventMessage;
      if (payload.kind === "bootstrap" && payload.overview) {
        setOverview(payload.overview);
        if (selectedAccountIdRef.current == null && payload.overview.accounts.length > 0) {
          setSelectedAccountId(payload.overview.accounts[0].account.id);
        }
        return;
      }
      if (payload.kind === "account_state" && payload.state) {
        setOverview((prev) => mergeAccountState(prev, payload.state));
        if (selectedAccountIdRef.current == null) {
          setSelectedAccountId(payload.state.account.id);
        }
        return;
      }
      if (payload.kind === "event" && payload.event) {
        const filters = logFilterRef.current;
        if (matchesLogFilters(payload.event, filters.mode, filters.level)) {
          if (activeTabRef.current === "Logs" && !isLogAtLiveEdgeRef.current) {
            pendingLogEventsRef.current = [payload.event, ...pendingLogEventsRef.current].slice(0, 200);
            setPendingLogCount(pendingLogEventsRef.current.length);
          } else {
            setLogs((prev) => [payload.event, ...prev].slice(0, 200));
          }
        }
        if (selectedAccountIdRef.current != null && payload.event.account_id === selectedAccountIdRef.current) {
          setHistory((prev) => (prev ? { ...prev, events: [payload.event, ...prev.events].slice(0, 200) } : prev));
        }
        return;
      }
      if (payload.kind === "account_exit" && typeof payload.account_id === "number" && payload.account_id === selectedAccountIdRef.current) {
        void loadHistory(payload.account_id).catch((error: Error) => setNotice(error.message));
      }
    };
    socket.onerror = () => setNotice("WebSocket disconnected; retrying automatically.");
    return () => socket.close();
  }, [reducedMotion]);

  const accountOptions = overview?.accounts ?? [];

  return (
    <MotionConfig reducedMotion="user">
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

        <AnimatePresence mode="wait" initial={false}>
      {activeTab === "Overview" && (
        <motion.section key="overview" className="grid overview-grid" variants={overviewVariants} initial="hidden" animate="visible" exit="exit">
          {accountOptions.map((snapshot) => (
            <OverviewCard
              key={snapshot.account.id}
              snapshot={snapshot}
              onOpen={() => {
                setSelectedAccountId(snapshot.account.id);
                setActiveTab("Accounts");
                setAccountTab("Live");
              }}
              onAction={(mode, action) => void sendModeAction(snapshot.account.id, mode, action)}
              onForceStop={() => void forceStopAccount(snapshot.account.id)}
              onClearQueue={() => void clearAccountQueue(snapshot.account.id)}
            />
          ))}
        </motion.section>
      )}

      {activeTab === "Accounts" && (
        <motion.section key="accounts" className="account-layout" variants={tabVariants} initial="hidden" animate="visible" exit="exit">
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
                  <LiveDashboard
                    snapshot={selectedSnapshot}
                    queueMode={queueMode}
                    setQueueMode={setQueueMode}
                    scheduleMode={scheduleMode}
                    setScheduleMode={setScheduleMode}
                    scheduleAt={scheduleAt}
                    setScheduleAt={setScheduleAt}
                    onAction={(mode, action) => void sendModeAction(selectedSnapshot.account.id, mode, action)}
                    onForceStop={() => void forceStopAccount(selectedSnapshot.account.id)}
                    onClearQueue={() => void clearAccountQueue(selectedSnapshot.account.id)}
                    onQueue={() => void enqueueAction()}
                    onSchedule={() => void createSchedule()}
                  />
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
                    <div className="badge-row">
                      <StatusChip tone="neutral">$oh: {selectedSnapshot.oh_left ?? "n/a"}</StatusChip>
                      <StatusChip tone="neutral">$oc: {selectedSnapshot.oc_left ?? "n/a"}</StatusChip>
                      <StatusChip tone="neutral">$oq: {selectedSnapshot.oq_left ?? "n/a"}</StatusChip>
                      <StatusChip tone="neutral">Spheres: {selectedSnapshot.sphere_balance ?? "n/a"}</StatusChip>
                    </div>
                  </section>
                )}

                {accountTab === "History" && (
                  <section className="grid two-col">
                    <DashboardPanel title="Recent Sessions">
                      <ul className="data-list">
                        {(history?.sessions ?? []).map((session, index) => (
                          <motion.li key={session.id} variants={buildFadeUp(reducedMotion, 8)} initial="hidden" animate="visible" exit="exit" transition={{ delay: reducedMotion ? 0 : index * 0.03 }}>
                            <strong>{session.mode.toUpperCase()}</strong> <StatusChip tone={session.status}>{session.status}</StatusChip>
                            <p>{fmtTime(session.started_at)}</p>
                            {session.summary && Object.keys(session.summary).length > 0 && <p className="muted">{JSON.stringify(session.summary)}</p>}
                            {session.error && <p className="error-text">{session.error}</p>}
                          </motion.li>
                        ))}
                        {(history?.sessions ?? []).length === 0 && <li>No session history yet.</li>}
                      </ul>
                    </DashboardPanel>
                    <DashboardPanel title="Event Timeline">
                      <ul className="data-list">
                        {(history?.events ?? []).map((item, index) => (
                          <motion.li key={item.id} variants={buildFadeUp(reducedMotion, 8)} initial="hidden" animate="visible" exit="exit" transition={{ delay: reducedMotion ? 0 : index * 0.02 }}>
                            <strong>{item.kind}</strong> {item.message ? `· ${item.message}` : ""}
                            <p>{fmtTime(item.created_at)}</p>
                          </motion.li>
                        ))}
                        {(history?.events ?? []).length === 0 && <li>No account events yet.</li>}
                      </ul>
                    </DashboardPanel>
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
        </motion.section>
      )}

      {activeTab === "Wishlist" && (
        <motion.section key="wishlist" className="grid two-col" variants={tabVariants} initial="hidden" animate="visible" exit="exit">
          <WishlistEditor title="Global Wishlist" items={globalWishlistDraft} onChange={setGlobalWishlistDraft} onSave={saveGlobalWishlist} />
          <DashboardPanel title="Aggregate View">
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
          </DashboardPanel>
        </motion.section>
      )}

      {activeTab === "Logs" && (
        <motion.section key="logs" className="panel" variants={tabVariants} initial="hidden" animate="visible" exit="exit">
          <div className="panel-header">
            <h3>Structured Logs</h3>
            <div className="inline-form">
              <select value={logMode} onChange={(event) => setLogMode(event.target.value)}>
                <option value="">All Modes</option>
                {["main", "oh", "oc", "oq"].map((mode) => <option key={mode} value={mode}>{mode.toUpperCase()}</option>)}
              </select>
              <select value={logLevel} onChange={(event) => setLogLevel(event.target.value)}>
                <option value="">All Levels</option>
                {["INFO", "WARN", "ERROR", "SUCCESS"].map((level) => <option key={level} value={level}>{level}</option>)}
              </select>
              <button onClick={() => void loadLogs()}>Apply</button>
            </div>
          </div>
          <div className="log-toolbar">
            <p className="muted">Newest events stay at the live edge. If you scroll away, incoming events are buffered.</p>
            {pendingLogCount > 0 && (
              <button className="primary new-events-indicator" onClick={() => flushPendingLogs()}>
                Show {pendingLogCount} new event{pendingLogCount === 1 ? "" : "s"}
              </button>
            )}
          </div>
          <motion.div ref={logListRef} className="log-list" onScroll={handleLogScroll} variants={logListVariants} initial="hidden" animate="visible">
            <AnimatePresence initial={false}>
            {logs.map((item) => (
              <motion.article className="log-item" key={item.id} variants={buildHighlightFlash(reducedMotion)} initial="hidden" animate="visible" exit="exit">
                <header><strong>{item.kind}</strong><span>{item.level || "INFO"}</span><time>{fmtTime(item.created_at)}</time></header>
                <p>{item.message || "(no message)"}</p>
                <LogPayloadView item={item} />
              </motion.article>
            ))}
            </AnimatePresence>
            {logs.length === 0 && <p>No log events match the current filters.</p>}
          </motion.div>
        </motion.section>
      )}

      {activeTab === "Settings" && (
        <motion.section key="settings" className="grid two-col" variants={tabVariants} initial="hidden" animate="visible" exit="exit">
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
        </motion.section>
      )}
        </AnimatePresence>
      </div>
    </MotionConfig>
  );
}
