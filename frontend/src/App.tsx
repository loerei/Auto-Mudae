import { Component, ErrorInfo, FormEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";

import { AnimatePresence, MotionConfig, motion, useReducedMotion } from "framer-motion";

import { DashboardPanel, LiveDashboard, LogPayloadView, OverviewCard, StatusChip, fmtTime } from "./live-ui";
import { buildFadeUp, buildHighlightFlash, buildStaggerContainer, getTabTransition } from "./motion";
import { LEGACY_SECTION_GROUPS, SETTINGS_COMPAT_FIELD_META } from "./settings-compat";
import { normalizeUiSettings, useResolvedTheme } from "./theme";
import { SettingsWorkspace, THEME_LABELS } from "./settings-ui";
import {
  Account,
  AccountHistory,
  AccountSnapshot,
  EventItem,
  FieldError,
  LiveEventMessage,
  OverviewPayload,
  SessionItem,
  SettingsSchema,
  SettingsPayload,
  WishlistItem,
  WishlistPayload
} from "./types";

const TABS = ["Overview", "Accounts", "Wishlist", "Logs", "Settings"] as const;
const ACCOUNT_SUBTABS = ["Live", "Main Bot", "Ouro", "History", "Config"] as const;

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init
  });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  return (await response.json()) as T;
}

async function readErrorMessage(response: Response): Promise<string> {
  const text = await response.text();
  if (!text) {
    return `Request failed: ${response.status}`;
  }
  try {
    const payload = JSON.parse(text) as { detail?: unknown; message?: unknown };
    if (typeof payload.detail === "string" && payload.detail.trim()) return payload.detail;
    if (typeof payload.message === "string" && payload.message.trim()) return payload.message;
  } catch {
    // Fall back to the raw text for non-JSON responses.
  }
  return text;
}

function toLocalInputValue(date: Date): string {
  const year = date.getFullYear();
  const month = `${date.getMonth() + 1}`.padStart(2, "0");
  const day = `${date.getDate()}`.padStart(2, "0");
  const hours = `${date.getHours()}`.padStart(2, "0");
  const minutes = `${date.getMinutes()}`.padStart(2, "0");
  return `${year}-${month}-${day}T${hours}:${minutes}`;
}

function emptyWishlistRow(): WishlistItem {
  return { name: "", priority: 2, is_star: false };
}

function closeWebUiTabBestEffort(): void {
  window.setTimeout(() => {
    try {
      window.close();
    } catch {
      // Ignore close failures; browsers often block closing tabs not opened by script.
    }
    window.setTimeout(() => {
      if (!document.hidden) {
        window.location.replace("about:blank");
      }
    }, 180);
  }, 220);
}

function normalizeSettingsSchemaPayload(payload: unknown): SettingsSchema {
  const raw = payload && typeof payload === "object" ? (payload as Record<string, unknown>) : {};
  const rawSections = Array.isArray(raw.sections) ? raw.sections : [];
  const sections = rawSections
    .map((item, index) => normalizeSettingsSection(item, index))
    .filter((section): section is SettingsSchema["sections"][number] => section != null);
  const unknownAppSettings = Array.isArray(raw.unknown_app_settings)
    ? raw.unknown_app_settings
        .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
        .map((item) => ({
          source: item.source === "ui_settings" ? "ui_settings" : "app_settings",
          key: typeof item.key === "string" ? item.key : "unknown",
          label: typeof item.label === "string" ? item.label : typeof item.key === "string" ? item.key : "Unknown key",
          value: "value" in item ? item.value : null
        }))
    : [];
  const sources =
    raw.sources && typeof raw.sources === "object"
      ? Object.fromEntries(Object.entries(raw.sources as Record<string, unknown>).map(([key, value]) => [key, typeof value === "string" ? value : String(value ?? "")]))
      : {};
  return {
    sections,
    unknown_app_settings: unknownAppSettings,
    sources
  };
}

function normalizeSettingsSection(rawSection: unknown, index: number): SettingsSchema["sections"][number] | null {
  if (!rawSection || typeof rawSection !== "object") return null;
  const section = rawSection as Record<string, unknown>;
  const sectionId = typeof section.id === "string" && section.id.trim() ? section.id : `section_${index}`;
  const usingLegacySectionSchema = !Array.isArray(section.groups);
  const fields = Array.isArray(section.fields)
    ? section.fields
        .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
        .map((field) => normalizeSettingsField(field, sectionId, usingLegacySectionSchema))
    : [];
  const groups = Array.isArray(section.groups)
    ? section.groups
        .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
        .map((group, groupIndex) => normalizeSettingsGroup(group, fields, sectionId, groupIndex))
    : [];
  const normalizedGroups = groups.length > 0 ? groups : buildLegacySettingsGroups(sectionId, fields);
  return {
    id: sectionId,
    title: typeof section.title === "string" && section.title.trim() ? section.title : sectionId,
    description: typeof section.description === "string" ? section.description : "",
    groups: normalizedGroups,
    fields,
    section_apply_scope: typeof section.section_apply_scope === "string" ? section.section_apply_scope : undefined,
    dangerous: Boolean(section.dangerous)
  };
}

function normalizeSettingsGroup(
  rawGroup: Record<string, unknown>,
  fields: SettingsSchema["sections"][number]["fields"],
  sectionId: string,
  index: number
): SettingsSchema["sections"][number]["groups"][number] {
  const groupId = typeof rawGroup.id === "string" && rawGroup.id.trim() ? rawGroup.id : `${sectionId}.group_${index}`;
  const groupFieldKeys = Array.isArray(rawGroup.fields)
    ? new Set(
        rawGroup.fields
          .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
          .map((field) => (typeof field.key === "string" ? field.key : ""))
          .filter(Boolean)
      )
    : null;
  const groupFields = groupFieldKeys ? fields.filter((field) => groupFieldKeys.has(field.key)) : fields.filter((field) => field.group === groupId);
  return {
    id: groupId,
    title: typeof rawGroup.title === "string" && rawGroup.title.trim() ? rawGroup.title : "Settings Group",
    description: typeof rawGroup.description === "string" ? rawGroup.description : "",
    fields: groupFields,
    apply_scope: typeof rawGroup.apply_scope === "string" ? rawGroup.apply_scope : undefined,
    layout_hint: typeof rawGroup.layout_hint === "string" ? rawGroup.layout_hint : undefined,
    default_collapsed: Boolean(rawGroup.default_collapsed),
    dangerous: Boolean(rawGroup.dangerous)
  };
}

function buildLegacySettingsGroups(sectionId: string, fields: SettingsSchema["sections"][number]["fields"]): SettingsSchema["sections"][number]["groups"] {
  const definitions = LEGACY_SECTION_GROUPS[sectionId];
  if (!definitions || definitions.length === 0) {
    return [
      {
        id: `${sectionId}.legacy`,
        title: "Section Settings",
        description: "This section is being rendered from a compatibility schema.",
        fields,
        layout_hint: "rows",
        default_collapsed: false,
        dangerous: false
      }
    ];
  }
  const leftovers = new Set(fields.map((field) => field.key));
  const groups = definitions
    .map((definition) => {
      const groupFields = fields.filter((field) => definition.fieldKeys.includes(field.key));
      for (const field of groupFields) leftovers.delete(field.key);
      if (groupFields.length === 0) return null;
      return {
        id: definition.id,
        title: definition.title,
        description: definition.description,
        fields: groupFields,
        apply_scope: coalesceSectionScope(groupFields),
        layout_hint: definition.layoutHint,
        default_collapsed: Boolean(definition.defaultCollapsed),
        dangerous: Boolean(definition.dangerous)
      };
    })
    .filter((group): group is NonNullable<typeof group> => group != null);
  if (leftovers.size > 0) {
    groups.push({
      id: `${sectionId}.legacy_misc`,
      title: "Additional Settings",
      description: "Settings from the legacy schema that do not map to a known group yet.",
      fields: fields.filter((field) => leftovers.has(field.key)),
      apply_scope: coalesceSectionScope(fields.filter((field) => leftovers.has(field.key))),
      layout_hint: "rows",
      default_collapsed: false,
      dangerous: false
    });
  }
  return groups;
}

function normalizeSettingsField(
  rawField: Record<string, unknown>,
  sectionId: string,
  useCompatibilityMetadata = false
): SettingsSchema["sections"][number]["fields"][number] {
  const key = typeof rawField.key === "string" && rawField.key.trim() ? rawField.key : `field_${Math.random().toString(36).slice(2, 8)}`;
  const compat = useCompatibilityMetadata ? SETTINGS_COMPAT_FIELD_META[key] : undefined;
  const validation = rawField.validation && typeof rawField.validation === "object" ? { ...(rawField.validation as Record<string, unknown>) } : {};
  if (compat?.pairLabels && (!Array.isArray(validation.pair_labels) || validation.pair_labels.length < 2 || String(validation.pair_labels[0]) === "Target ID")) {
    validation.pair_labels = compat.pairLabels;
  }
  return {
    key,
    source: rawField.source === "ui_settings" ? "ui_settings" : "app_settings",
    section: typeof rawField.section === "string" && rawField.section.trim() ? rawField.section : sectionId,
    group: typeof rawField.group === "string" && rawField.group.trim() ? rawField.group : `${sectionId}.legacy`,
    label: compat?.label ?? (typeof rawField.label === "string" && rawField.label.trim() ? rawField.label : key),
    short_label: typeof rawField.short_label === "string" ? rawField.short_label : undefined,
    description: typeof rawField.description === "string" ? rawField.description : "",
    help_text: compat?.helpText ?? (typeof rawField.help_text === "string" ? rawField.help_text : undefined),
    editor: typeof rawField.editor === "string" ? rawField.editor : "text",
    value_type: typeof rawField.value_type === "string" ? rawField.value_type : "string",
    default: "default" in rawField ? rawField.default : null,
    value: "value" in rawField ? rawField.value : null,
    options: Array.isArray(rawField.options)
      ? rawField.options
          .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
          .map((item) => ({
            value: typeof item.value === "string" ? item.value : String(item.value ?? ""),
            label: typeof item.label === "string" ? item.label : String(item.value ?? "")
          }))
      : [],
    validation,
    apply_scope: typeof rawField.apply_scope === "string" ? rawField.apply_scope : "Next session",
    dangerous: Boolean(rawField.dangerous),
    editable: rawField.editable !== false,
    unit: compat?.unit ?? (typeof rawField.unit === "string" ? rawField.unit : undefined),
    placeholder: typeof rawField.placeholder === "string" ? rawField.placeholder : undefined,
    control_width: compat?.controlWidth ?? (typeof rawField.control_width === "string" ? rawField.control_width : undefined),
    layout_hint: compat?.layoutHint ?? (typeof rawField.layout_hint === "string" ? rawField.layout_hint : undefined),
    show_apply_scope: Boolean(rawField.show_apply_scope)
  };
}

function coalesceSectionScope(fields: SettingsSchema["sections"][number]["fields"]): string | undefined {
  const scopes = Array.from(new Set(fields.map((field) => field.apply_scope).filter(Boolean)));
  if (scopes.length === 1) return scopes[0];
  if (scopes.length === 0) return undefined;
  return "Mixed";
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

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function formatSessionSummary(session: SessionItem): string {
  const summary = session.summary ?? {};
  const parts: string[] = [];
  const rollsTotal = asNumber(summary.rolls_total);
  const claimsTotal = asNumber(summary.claims_total);
  const kakeraTotal = asNumber(summary.kakera_total);
  const totalBalance = asNumber(summary.total_balance);
  if (rollsTotal != null) parts.push(`${rollsTotal} rolls`);
  if (claimsTotal != null) parts.push(`${claimsTotal} claims`);
  if (kakeraTotal != null) parts.push(`${kakeraTotal} kakera`);
  if (totalBalance != null) parts.push(`balance ${totalBalance}`);
  return parts.join(" · ") || "No summary captured.";
}

function PageHeader(props: {
  eyebrow: string;
  title: string;
  description: string;
  actions?: ReactNode;
  meta?: ReactNode;
}) {
  return (
    <section className="page-header">
      <div className="page-copy">
        <p className="eyebrow">{props.eyebrow}</p>
        <h2>{props.title}</h2>
        <p className="muted">{props.description}</p>
      </div>
      {props.actions && <div className="page-actions">{props.actions}</div>}
      {props.meta && <div className="page-meta">{props.meta}</div>}
    </section>
  );
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
          <button type="button" onClick={() => props.onChange([...props.items, emptyWishlistRow()])}>Add Row</button>
          <button className="primary" type="button" onClick={() => void props.onSave()}>
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
            <button className="danger ghost" type="button" onClick={() => props.onChange(props.items.filter((_, itemIndex) => itemIndex !== index))}>
              Remove
            </button>
          </div>
        ))}
      </div>
    </section>
  );
}

class SettingsErrorBoundary extends Component<{ children: ReactNode }, { hasError: boolean }> {
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(): { hasError: boolean } {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("SettingsErrorBoundary", error, info);
  }

  render() {
    if (this.state.hasError) {
      return (
        <section className="panel">
          <div className="panel-header">
            <h3>Settings Unavailable</h3>
            <button type="button" onClick={() => window.location.reload()}>
              Reload
            </button>
          </div>
          <p className="muted">
            The Settings workspace hit an unexpected render error. Reload the page first. If it keeps happening, restart the local daemon so the
            frontend and backend schema stay in sync.
          </p>
        </section>
      );
    }
    return this.props.children;
  }
}

export default function App() {
  const reducedMotion = useReducedMotion();
  const [activeTab, setActiveTab] = useState<(typeof TABS)[number]>("Overview");
  const [accountTab, setAccountTab] = useState<(typeof ACCOUNT_SUBTABS)[number]>("Live");
  const [overview, setOverview] = useState<OverviewPayload | null>(null);
  const [logs, setLogs] = useState<EventItem[]>([]);
  const [settings, setSettings] = useState<SettingsPayload | null>(null);
  const [settingsSchema, setSettingsSchema] = useState<SettingsSchema | null>(null);
  const [settingsSchemaError, setSettingsSchemaError] = useState<string | null>(null);
  const [wishlist, setWishlist] = useState<WishlistPayload | null>(null);
  const [history, setHistory] = useState<AccountHistory | null>(null);
  const [selectedAccountId, setSelectedAccountId] = useState<number | null>(null);
  const [notice, setNotice] = useState<string>("Loading...");
  const [logMode, setLogMode] = useState<string>("");
  const [logLevel, setLogLevel] = useState<string>("");
  const [globalWishlistDraft, setGlobalWishlistDraft] = useState<WishlistItem[]>([]);
  const [accountWishlistDraft, setAccountWishlistDraft] = useState<WishlistItem[]>([]);
  const [accountForm, setAccountForm] = useState<Account>({ id: 0, name: "", discord_user_id: "", discordusername: "", token: "", max_power: 110 });
  const [queueMode, setQueueMode] = useState<string>("main");
  const [scheduleMode, setScheduleMode] = useState<string>("main");
  const [scheduleAt, setScheduleAt] = useState<string>(toLocalInputValue(new Date(Date.now() + 10 * 60 * 1000)));
  const [pendingLogCount, setPendingLogCount] = useState(0);
  const [isShuttingDown, setIsShuttingDown] = useState(false);

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
  const savedUiSettings = useMemo(() => normalizeUiSettings(settings?.ui_settings), [settings]);
  const themeMode = savedUiSettings.theme;
  const resolvedTheme = useResolvedTheme(themeMode);
  const totalAccounts = overview?.accounts.length ?? 0;
  const queuedCount = overview?.queue.length ?? 0;
  const totalWishlistCount = useMemo(
    () => (wishlist?.global.length ?? 0) + Object.values(wishlist?.accounts ?? {}).reduce((sum, items) => sum + items.length, 0),
    [wishlist]
  );
  const tabVariants = getTabTransition(reducedMotion);
  const overviewVariants = buildStaggerContainer(reducedMotion, 0.05);
  const logListVariants = buildStaggerContainer(reducedMotion, 0.03);

  useEffect(() => {
    document.documentElement.dataset.theme = resolvedTheme;
    document.documentElement.style.colorScheme = resolvedTheme;
  }, [resolvedTheme]);

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
  }

  async function loadSettingsSchema() {
    const response = await fetch("/api/settings/schema", {
      headers: { "Content-Type": "application/json" }
    });
    if (!response.ok) {
      const baseMessage = await readErrorMessage(response);
      const message =
        response.status === 404
          ? "This daemon is missing /api/settings/schema. Restart run_webui.bat so the backend matches the redesigned Settings page."
          : baseMessage;
      setSettingsSchema(null);
      setSettingsSchemaError(message);
      throw new Error(message);
    }
    const payload = normalizeSettingsSchemaPayload(await response.json());
    setSettingsSchema(payload);
    setSettingsSchemaError(null);
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

  async function refreshAll(showNotice = true) {
    await Promise.all([loadOverview(), loadSettings(), loadSettingsSchema(), loadWishlist(), loadLogs()]);
    if (selectedAccountId != null) await loadHistory(selectedAccountId);
    if (showNotice) {
      setNotice(`Refreshed at ${new Date().toLocaleTimeString()}.`);
    }
  }

  async function patchSettings(patch: Partial<SettingsPayload>) {
    const response = await fetch("/api/settings", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch)
    });
    if (!response.ok) {
      let payload: { message?: string; field_errors?: FieldError[] } | null = null;
      try {
        payload = (await response.json()) as { message?: string; field_errors?: FieldError[] };
      } catch {
        payload = null;
      }
      const fallbackMessage =
        response.status === 404 || response.status === 405
          ? "This daemon is older than the current WebUI build. Restart run_webui.bat so Settings PATCH support is available."
          : `Request failed: ${response.status}`;
      const error = new Error(payload?.message || fallbackMessage) as Error & { fieldErrors?: FieldError[] };
      error.fieldErrors = payload?.field_errors ?? [];
      throw error;
    }
    const saved = (await response.json()) as SettingsPayload;
    setSettings(saved);
    await loadSettingsSchema();
    return saved;
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

  async function quickToggleTheme() {
    const order: typeof themeMode[] = ["system", "light", "dark"];
    const nextTheme = order[(order.indexOf(themeMode) + 1 + order.length) % order.length];
    await patchSettings({ ui_settings: { theme: nextTheme } });
    setNotice(`Theme set to ${THEME_LABELS[nextTheme]}.`);
  }

  async function shutdownWebUi() {
    setIsShuttingDown(true);
    try {
      await fetchJson<{ ok: boolean; message: string }>("/api/shutdown", { method: "POST" });
      setNotice("Shutting down local daemon and closing WebUI tab...");
      closeWebUiTabBestEffort();
    } catch (error) {
      setIsShuttingDown(false);
      throw error;
    }
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

  async function importData(text: string) {
    const payload = JSON.parse(text);
    await fetchJson("/api/import", { method: "POST", body: JSON.stringify(payload) });
    setNotice("Imported backup bundle.");
    await refreshAll(false);
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
    void refreshAll(false)
      .then(() => setNotice("Connected to local daemon."))
      .catch((error: Error) => setNotice(error.message));
  }, []);

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
        <header className="app-header panel">
          <div className="brand-block">
            <p className="eyebrow">Local Daemon + SPA</p>
            <h1>Mudae WebUI</h1>
            <p className="muted">A cleaner operational workspace for multi-account sessions, wishlists, logs, and settings.</p>
          </div>
          <div className="header-actions">
            <div className="badge-row">
              <StatusChip tone="neutral">Accounts: {totalAccounts}</StatusChip>
              <StatusChip tone={overview?.running_count ? "success" : "neutral"} pulseKey={overview?.running_count ?? 0}>
                Active: {overview?.running_count ?? 0}
              </StatusChip>
              <StatusChip tone={queuedCount ? "warning" : "neutral"} pulseKey={queuedCount}>
                Queue: {queuedCount}
              </StatusChip>
              <StatusChip tone="neutral" pulseKey={themeMode}>
                Theme: {THEME_LABELS[themeMode]}
              </StatusChip>
            </div>
            <div className="button-row">
              <button type="button" onClick={() => void quickToggleTheme().catch((error: Error) => setNotice(error.message))}>
                Cycle Theme
              </button>
              <button type="button" onClick={() => void refreshAll().catch((error: Error) => setNotice(error.message))}>
                Refresh
              </button>
              <button
                className="danger"
                type="button"
                disabled={isShuttingDown}
                onClick={() => void shutdownWebUi().catch((error: Error) => setNotice(error.message))}
              >
                {isShuttingDown ? "Shutting Down..." : "Shutdown WebUI"}
              </button>
            </div>
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
            <motion.section key="overview" className="page-stack" variants={tabVariants} initial="hidden" animate="visible" exit="exit">
              <PageHeader
                eyebrow="Control Room"
                title="Overview"
                description="Keep this page summary-only. Jump into Accounts > Live for queue, schedules, and detailed operations."
                meta={
                  <div className="badge-row">
                    <StatusChip tone="neutral">Wishlists: {totalWishlistCount}</StatusChip>
                    <StatusChip tone="neutral">Recent sessions: {overview?.recent_sessions.length ?? 0}</StatusChip>
                    <StatusChip tone="neutral">Resolved theme: {resolvedTheme}</StatusChip>
                  </div>
                }
              />
              <motion.div className="grid overview-grid" variants={overviewVariants} initial="hidden" animate="visible">
                {accountOptions.map((snapshot, index) => (
                  <OverviewCard
                    key={snapshot.account.id}
                    snapshot={snapshot}
                    index={index}
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
              </motion.div>
              {accountOptions.length === 0 && (
                <section className="panel empty-state">
                  <h3>No accounts configured yet</h3>
                  <p>Open the Accounts tab to create the first account and connect the live dashboard.</p>
                </section>
              )}
            </motion.section>
          )}

          {activeTab === "Accounts" && (
            <motion.section key="accounts" className="page-stack" variants={tabVariants} initial="hidden" animate="visible" exit="exit">
              <PageHeader
                eyebrow="Workspace"
                title="Accounts"
                description="Use the sidebar for account switching, then move between Live, Main Bot, Ouro, History, and Config."
                meta={
                  selectedSnapshot ? (
                    <div className="badge-row">
                      <StatusChip tone={selectedSnapshot.status} pulseKey={selectedSnapshot.status}>{selectedSnapshot.status}</StatusChip>
                      <StatusChip tone={selectedSnapshot.connection_status === "Connected" ? "success" : "warning"} pulseKey={selectedSnapshot.connection_status || "n/a"}>
                        {selectedSnapshot.connection_status || "n/a"}
                      </StatusChip>
                      <StatusChip tone={selectedSnapshot.queue?.length ? "warning" : "neutral"} pulseKey={selectedSnapshot.queue?.length ?? 0}>
                        Queue: {selectedSnapshot.queue?.length ?? 0}
                      </StatusChip>
                    </div>
                  ) : undefined
                }
              />
              <div className="account-layout">
                <aside className="account-sidebar panel">
                  <div className="panel-header">
                    <div>
                      <h3>Accounts</h3>
                      <p className="muted">Pick one account as the active workspace.</p>
                    </div>
                    <button type="button" onClick={() => setAccountForm({ id: 0, name: "", discord_user_id: "", discordusername: "", token: "", max_power: 110 })}>New</button>
                  </div>
                  <div className="sidebar-stack">
                    {accountOptions.map((snapshot) => (
                      <button key={snapshot.account.id} className={`sidebar-item ${selectedAccountId === snapshot.account.id ? "active" : ""}`} onClick={() => setSelectedAccountId(snapshot.account.id)}>
                        <span className="sidebar-main">
                          <strong>{snapshot.account.name}</strong>
                          <small>{snapshot.account.discordusername || "No username"}</small>
                        </span>
                        <StatusChip tone={snapshot.status} pulseKey={snapshot.status}>{snapshot.status}</StatusChip>
                      </button>
                    ))}
                    {accountOptions.length === 0 && <p className="muted">No accounts yet.</p>}
                  </div>
                </aside>

                <div className="account-detail">
                  {!selectedSnapshot ? (
                    <section className="panel empty-state">
                      <h3>Select an account</h3>
                      <p>The account workspace, live dashboard, and config editor appear here.</p>
                    </section>
                  ) : (
                    <>
                      <section className="panel workspace-hero">
                        <div>
                          <p className="eyebrow">Account Workspace</p>
                          <h3>{selectedSnapshot.account.name}</h3>
                          <p className="muted">{selectedSnapshot.account.discordusername || "No Discord username saved."}</p>
                        </div>
                        <div className="badge-row">
                          <StatusChip tone="neutral">{selectedSnapshot.active_mode || selectedSnapshot.paused_mode || "idle"}</StatusChip>
                          <StatusChip tone="neutral">Next: {selectedSnapshot.next_action || "n/a"}</StatusChip>
                          <StatusChip tone="neutral">Queued: {selectedSnapshot.queue?.length ?? 0}</StatusChip>
                        </div>
                      </section>

                      <nav className="sub-tabs">
                        {ACCOUNT_SUBTABS.map((tab) => (
                          <button key={tab} className={tab === accountTab ? "active" : ""} onClick={() => setAccountTab(tab)}>
                            {tab}
                          </button>
                        ))}
                      </nav>

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
                          <div className="panel-header">
                            <div>
                              <h3>Main Bot Control</h3>
                              <p className="muted">Direct controls for the primary session loop.</p>
                            </div>
                          </div>
                          <div className="button-row">
                            <button className="primary" onClick={() => void sendModeAction(selectedSnapshot.account.id, "main", "start")}>Start Main</button>
                            <button onClick={() => void sendModeAction(selectedSnapshot.account.id, "main", "restart")}>Restart Main</button>
                            <button onClick={() => void sendModeAction(selectedSnapshot.account.id, "main", "pause")}>Pause Main</button>
                            <button className="danger" onClick={() => void sendModeAction(selectedSnapshot.account.id, "main", "stop")}>Stop Main</button>
                          </div>
                        </section>
                      )}

                      {accountTab === "Ouro" && (
                        <section className="grid two-col">
                          <DashboardPanel title="Standalone Ouro Runs">
                            <p className="muted">Launch OH, OC, or OQ separately from the integrated Main Bot loop.</p>
                            <div className="button-row">
                              <button onClick={() => void sendModeAction(selectedSnapshot.account.id, "oh", "start")}>Start OH</button>
                              <button onClick={() => void sendModeAction(selectedSnapshot.account.id, "oc", "start")}>Start OC</button>
                              <button onClick={() => void sendModeAction(selectedSnapshot.account.id, "oq", "start")}>Start OQ</button>
                            </div>
                          </DashboardPanel>
                          <DashboardPanel title="Current Ouro Capacity">
                            <div className="badge-row">
                              <StatusChip tone="neutral">$oh: {selectedSnapshot.oh_left ?? "n/a"}</StatusChip>
                              <StatusChip tone="neutral">$oc: {selectedSnapshot.oc_left ?? "n/a"}</StatusChip>
                              <StatusChip tone="neutral">$oq: {selectedSnapshot.oq_left ?? "n/a"}</StatusChip>
                              <StatusChip tone="neutral">Spheres: {selectedSnapshot.sphere_balance ?? "n/a"}</StatusChip>
                            </div>
                          </DashboardPanel>
                        </section>
                      )}

                      {accountTab === "History" && (
                        <section className="grid two-col">
                          <DashboardPanel title="Recent Sessions">
                            <ul className="data-list">
                              {(history?.sessions ?? []).map((session, index) => (
                                <motion.li key={session.id} variants={buildFadeUp(reducedMotion, 8)} initial="hidden" animate="visible" exit="exit" transition={{ delay: reducedMotion ? 0 : index * 0.03 }}>
                                  <div className="panel-header">
                                    <strong>{session.mode.toUpperCase()}</strong>
                                    <StatusChip tone={session.status}>{session.status}</StatusChip>
                                  </div>
                                  <p>{fmtTime(session.started_at)}{session.ended_at ? ` → ${fmtTime(session.ended_at)}` : ""}</p>
                                  <p className="muted">{formatSessionSummary(session)}</p>
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
                                  <div className="panel-header">
                                    <strong>{item.kind}</strong>
                                    <span className="muted">{item.level || "INFO"}</span>
                                  </div>
                                  <p>{item.message || "(no message)"}</p>
                                  <p className="muted">{fmtTime(item.created_at)}</p>
                                </motion.li>
                              ))}
                              {(history?.events ?? []).length === 0 && <li>No account events yet.</li>}
                            </ul>
                          </DashboardPanel>
                        </section>
                      )}

                      {accountTab === "Config" && (
                        <section className="grid two-col">
                          <form className="panel form-panel" onSubmit={(event) => void saveAccount(event)}>
                            <div className="panel-header">
                              <div>
                                <h3>Account Config</h3>
                                <p className="muted">Edit saved identity and token values here.</p>
                              </div>
                            </div>
                            <div className="field-grid">
                              <label className="field">
                                <span>Name</span>
                                <input value={accountForm.name} onChange={(event) => setAccountForm({ ...accountForm, name: event.target.value })} />
                              </label>
                              <label className="field">
                                <span>Discord User ID</span>
                                <input value={accountForm.discord_user_id ?? ""} onChange={(event) => setAccountForm({ ...accountForm, discord_user_id: event.target.value })} />
                              </label>
                              <label className="field">
                                <span>Discord Username</span>
                                <input value={accountForm.discordusername ?? ""} onChange={(event) => setAccountForm({ ...accountForm, discordusername: event.target.value })} />
                              </label>
                              <label className="field">
                                <span>Max Power</span>
                                <input type="number" min={1} value={accountForm.max_power} onChange={(event) => setAccountForm({ ...accountForm, max_power: Number(event.target.value) || 110 })} />
                              </label>
                              <label className="field field-span-2">
                                <span>Token</span>
                                <textarea value={accountForm.token} onChange={(event) => setAccountForm({ ...accountForm, token: event.target.value })} />
                              </label>
                            </div>
                            <div className="button-row"><button className="primary" type="submit">Save Account</button></div>
                          </form>
                          <WishlistEditor title={`Wishlist: ${selectedSnapshot.account.name}`} items={accountWishlistDraft} onChange={setAccountWishlistDraft} onSave={saveAccountWishlist} />
                        </section>
                      )}
                    </>
                  )}
                </div>
              </div>
            </motion.section>
          )}

          {activeTab === "Wishlist" && (
            <motion.section key="wishlist" className="page-stack" variants={tabVariants} initial="hidden" animate="visible" exit="exit">
              <PageHeader
                eyebrow="Library"
                title="Wishlist"
                description="Keep global and per-account wishlists here instead of mixing them into the live dashboard."
                meta={
                  <div className="badge-row">
                    <StatusChip tone="neutral">Global: {wishlist?.global.length ?? 0}</StatusChip>
                    <StatusChip tone="neutral">Per-account: {totalWishlistCount - (wishlist?.global.length ?? 0)}</StatusChip>
                  </div>
                }
              />
              <section className="grid two-col">
                <WishlistEditor title="Global Wishlist" items={globalWishlistDraft} onChange={setGlobalWishlistDraft} onSave={saveGlobalWishlist} />
                <DashboardPanel title="Per-Account Coverage">
                  <ul className="data-list">
                    {accountOptions.map((snapshot) => {
                      const items = wishlist?.accounts[String(snapshot.account.id)] ?? [];
                      return (
                        <li key={snapshot.account.id}>
                          <div className="panel-header">
                            <strong>{snapshot.account.name}</strong>
                            <StatusChip tone={items.length ? "warning" : "neutral"}>{items.length} item{items.length === 1 ? "" : "s"}</StatusChip>
                          </div>
                          <p className="muted">{items.length > 0 ? items.map((item) => `${item.name}${item.is_star ? " ★" : ""}`).join(", ") : "No per-account wishlist entries yet."}</p>
                        </li>
                      );
                    })}
                    {accountOptions.length === 0 && <li>No accounts available for per-account wishlists yet.</li>}
                  </ul>
                </DashboardPanel>
              </section>
            </motion.section>
          )}

          {activeTab === "Logs" && (
            <motion.section key="logs" className="page-stack" variants={tabVariants} initial="hidden" animate="visible" exit="exit">
              <PageHeader
                eyebrow="Observability"
                title="Logs"
                description="Structured events stay here so the Live dashboard can stay focused on current operational state."
              />
              <section className="panel">
                <div className="panel-header">
                  <div>
                    <h3>Structured Logs</h3>
                    <p className="muted">Filter by mode or level, then keep the feed at the live edge for real-time inserts.</p>
                  </div>
                  <div className="inline-form">
                    <select value={logMode} onChange={(event) => setLogMode(event.target.value)}>
                      <option value="">All Modes</option>
                      {["main", "oh", "oc", "oq"].map((mode) => <option key={mode} value={mode}>{mode.toUpperCase()}</option>)}
                    </select>
                    <select value={logLevel} onChange={(event) => setLogLevel(event.target.value)}>
                      <option value="">All Levels</option>
                      {["INFO", "WARN", "ERROR", "SUCCESS"].map((level) => <option key={level} value={level}>{level}</option>)}
                    </select>
                    <button type="button" onClick={() => void loadLogs().catch((error: Error) => setNotice(error.message))}>Apply</button>
                  </div>
                </div>
                <div className="log-toolbar">
                  <p className="muted">If you scroll away from the live edge, incoming events are buffered until you choose to reveal them.</p>
                  {pendingLogCount > 0 && (
                    <button className="primary new-events-indicator" type="button" onClick={() => flushPendingLogs()}>
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
              </section>
            </motion.section>
          )}

          {activeTab === "Settings" && (
            <motion.section key="settings" className="page-stack" variants={tabVariants} initial="hidden" animate="visible" exit="exit">
              <PageHeader
                eyebrow="Preferences"
                title="Settings"
                description="Structured settings are grouped by category, with advanced integration fields isolated from routine runtime controls."
                meta={
                  <div className="badge-row">
                    <StatusChip tone="neutral">Theme: {THEME_LABELS[themeMode]}</StatusChip>
                    <StatusChip tone="neutral">Resolved: {resolvedTheme}</StatusChip>
                    <StatusChip tone="neutral">Schema-driven</StatusChip>
                  </div>
                }
              />
              <SettingsErrorBoundary>
                <SettingsWorkspace
                  settings={settings}
                  schema={settingsSchema}
                  schemaError={settingsSchemaError}
                  resolvedTheme={resolvedTheme}
                  onPatch={patchSettings}
                  onExport={exportData}
                  onImport={importData}
                  onNotice={setNotice}
                />
              </SettingsErrorBoundary>
            </motion.section>
          )}
        </AnimatePresence>
      </div>
    </MotionConfig>
  );
}
