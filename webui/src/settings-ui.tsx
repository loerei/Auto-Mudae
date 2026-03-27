import { ChangeEvent, useEffect, useState } from "react";

import { DashboardPanel, StatusChip } from "./live-ui";
import { DEFAULT_UI_SETTINGS, normalizeUiSettings } from "./theme";
import {
  FieldError,
  SettingsField,
  SettingsPayload,
  SettingsSchema,
  SettingsSection,
  ThemeMode,
  UnknownSetting
} from "./types";

export const THEME_LABELS: Record<ThemeMode, string> = { system: "System", light: "Light", dark: "Dark" };

type SettingsWorkspaceProps = {
  settings: SettingsPayload | null;
  schema: SettingsSchema | null;
  schemaError?: string | null;
  resolvedTheme: string;
  onPatch: (patch: Partial<SettingsPayload>) => Promise<SettingsPayload>;
  onExport: () => Promise<void>;
  onImport: (text: string) => Promise<void>;
  onNotice: (message: string) => void;
};

export function SettingsWorkspace(props: SettingsWorkspaceProps) {
  const [appDraft, setAppDraft] = useState<Record<string, unknown>>({});
  const [uiDraft, setUiDraft] = useState<Record<string, unknown>>(DEFAULT_UI_SETTINGS);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [importText, setImportText] = useState("");

  useEffect(() => {
    setAppDraft(props.settings?.app_settings ?? {});
    setUiDraft(normalizeUiSettings(props.settings?.ui_settings));
    setFieldErrors({});
  }, [props.settings]);

  const sections = props.schema?.sections ?? [];
  const unknownSettings = props.schema?.unknown_app_settings ?? [];

  const savedAppSettings = props.settings?.app_settings ?? {};
  const savedUiSettings = normalizeUiSettings(props.settings?.ui_settings);

  function getFieldValue(field: SettingsField): unknown {
    if (field.source === "ui_settings") {
      return uiDraft[field.key] ?? field.default;
    }
    return appDraft[field.key] ?? field.default;
  }

  function setFieldValue(field: SettingsField, nextValue: unknown) {
    const errorKey = `${field.source}.${field.key}`;
    setFieldErrors((prev) => {
      if (!(errorKey in prev)) return prev;
      const next = { ...prev };
      delete next[errorKey];
      return next;
    });
    if (field.source === "ui_settings") {
      setUiDraft((prev) => ({ ...prev, [field.key]: nextValue }));
      return;
    }
    setAppDraft((prev) => ({ ...prev, [field.key]: nextValue }));
  }

  function isSectionDirty(section: SettingsSection): boolean {
    return section.fields.some((field) => {
      const current = getFieldValue(field);
      const saved = field.source === "ui_settings" ? savedUiSettings[field.key] : savedAppSettings[field.key];
      return stableSerialize(current) !== stableSerialize(saved ?? field.default);
    });
  }

  function getSectionScopes(section: SettingsSection): string[] {
    return Array.from(new Set(section.fields.map((field) => field.apply_scope))).filter(Boolean);
  }

  function getSectionPatch(section: SettingsSection): Partial<SettingsPayload> {
    const app_settings: Record<string, unknown> = {};
    const ui_settings: Record<string, unknown> = {};
    for (const field of section.fields) {
      const value = cloneValue(getFieldValue(field));
      if (field.source === "ui_settings") {
        ui_settings[field.key] = value;
      } else {
        app_settings[field.key] = value;
      }
    }
    return {
      ...(Object.keys(app_settings).length > 0 ? { app_settings } : {}),
      ...(Object.keys(ui_settings).length > 0 ? { ui_settings } : {})
    };
  }

  function resetSection(section: SettingsSection) {
    for (const field of section.fields) {
      const saved = field.source === "ui_settings" ? savedUiSettings[field.key] : savedAppSettings[field.key];
      setFieldValue(field, cloneValue(saved ?? field.default));
    }
  }

  async function saveSection(section: SettingsSection) {
    try {
      await props.onPatch(getSectionPatch(section));
      setFieldErrors((prev) => {
        const next = { ...prev };
        for (const field of section.fields) {
          delete next[`${field.source}.${field.key}`];
        }
        return next;
      });
      props.onNotice(`${section.title} settings saved.`);
    } catch (error) {
      const nextErrors: Record<string, string> = {};
      for (const item of readFieldErrors(error)) {
        nextErrors[`${item.source}.${item.key}`] = item.message;
      }
      if (Object.keys(nextErrors).length > 0) {
        setFieldErrors((prev) => ({ ...prev, ...nextErrors }));
        props.onNotice(`Could not save ${section.title.toLowerCase()}.`);
        return;
      }
      throw error;
    }
  }

  async function handleImport() {
    await props.onImport(importText);
    setImportText("");
  }

  function handleImportFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    void file.text().then((text) => setImportText(text));
  }

  if (!props.settings || !props.schema) {
    return (
      <section className="panel">
        <p className="muted">{props.schemaError || "Loading structured settings…"}</p>
        {props.schemaError && <p className="muted">The redesigned Settings page needs the matching backend routes. Restart the local daemon and reload the browser.</p>}
      </section>
    );
  }

  return (
    <section className="settings-shell">
      <aside className="panel settings-sidebar">
        <div className="panel-header">
          <div>
            <h3>Categories</h3>
            <p className="muted">Jump between structured sections without leaving the page.</p>
          </div>
        </div>
        <nav className="settings-nav">
          {sections.map((section) => (
            <button key={section.id} type="button" className="sidebar-item" onClick={() => scrollToSettingsSection(section.id)}>
              <span className="sidebar-main">
                <strong>{section.title}</strong>
                <small>{section.fields.length} field{section.fields.length === 1 ? "" : "s"}</small>
              </span>
            </button>
          ))}
          <button type="button" className="sidebar-item" onClick={() => scrollToSettingsSection("unknown")}>
            <span className="sidebar-main">
              <strong>Unknown / Unsupported</strong>
              <small>{unknownSettings.length} item{unknownSettings.length === 1 ? "" : "s"}</small>
            </span>
          </button>
          <button type="button" className="sidebar-item" onClick={() => scrollToSettingsSection("backup")}>
            <span className="sidebar-main">
              <strong>Backup / Restore</strong>
              <small>Export and import portable bundles</small>
            </span>
          </button>
        </nav>
      </aside>

      <div className="settings-main">
        {sections.map((section) => {
          const dirty = isSectionDirty(section);
          const scopes = getSectionScopes(section);
          const hasDangerousFields = section.fields.some((field) => field.dangerous);
          return (
            <DashboardPanel
              key={section.id}
              className="settings-section"
              title={section.title}
              subtitle={section.description}
              actions={
                <div className="panel-actions">
                  {dirty && <StatusChip tone="warning">Unsaved</StatusChip>}
                  <button className="primary" type="button" disabled={!dirty} onClick={() => void saveSection(section)}>
                    Save
                  </button>
                  <button type="button" disabled={!dirty} onClick={() => resetSection(section)}>
                    Reset
                  </button>
                </div>
              }
            >
              <div id={`settings-${section.id}`} className="settings-section-stack">
                <div className="badge-row">
                  {scopes.map((scope) => (
                    <StatusChip key={scope} tone={scope === "Immediate" ? "success" : scope === "Daemon restart" ? "warning" : "neutral"}>
                      {scope}
                    </StatusChip>
                  ))}
                  {hasDangerousFields && <StatusChip tone="warning">Advanced / Sensitive</StatusChip>}
                </div>
                {hasDangerousFields && (
                  <div className="settings-warning">
                    These fields are low-level integration settings. Incorrect values can break connectivity or target the wrong server/channel.
                  </div>
                )}
                <div className="settings-form-grid">
                  {section.fields.map((field) => (
                    <SettingFieldCard
                      key={`${field.source}-${field.key}`}
                      field={field}
                      value={getFieldValue(field)}
                      error={fieldErrors[`${field.source}.${field.key}`]}
                      resolvedTheme={props.resolvedTheme}
                      onChange={(value) => setFieldValue(field, value)}
                    />
                  ))}
                </div>
              </div>
            </DashboardPanel>
          );
        })}

        <DashboardPanel
          className="settings-section"
          title="Unknown / Unsupported"
          subtitle="These keys are preserved in storage but do not yet have first-class controls in the WebUI."
        >
          <div id="settings-unknown" className="settings-section-stack">
            {unknownSettings.length === 0 ? (
              <p className="muted">No unsupported app settings are currently stored.</p>
            ) : (
              <div className="unknown-settings-list">
                {unknownSettings.map((item) => (
                  <UnknownSettingCard key={item.key} item={item} />
                ))}
              </div>
            )}
          </div>
        </DashboardPanel>

        <DashboardPanel className="settings-section" title="Backup / Restore" subtitle="Export a full local bundle or import one after stopping live sessions.">
          <div id="settings-backup" className="settings-section-stack">
            <div className="button-row">
              <button className="primary" type="button" onClick={() => void props.onExport()}>
                Export Backup
              </button>
            </div>
            <input type="file" accept="application/json" onChange={handleImportFile} />
            <textarea className="code-block" value={importText} onChange={(event) => setImportText(event.target.value)} placeholder="Paste exported JSON here" />
            <div className="button-row">
              <button className="primary" type="button" disabled={!importText.trim()} onClick={() => void handleImport()}>
                Import Backup
              </button>
            </div>
          </div>
        </DashboardPanel>
      </div>
    </section>
  );
}

function SettingFieldCard(props: {
  field: SettingsField;
  value: unknown;
  error?: string;
  resolvedTheme: string;
  onChange: (value: unknown) => void;
}) {
  return (
    <section className={`panel inset settings-field-card ${props.field.dangerous ? "danger-zone" : ""}`.trim()}>
      <div className="settings-field-header">
        <div>
          <h4>{props.field.label}</h4>
          {props.field.description && <p className="muted">{props.field.description}</p>}
        </div>
        <div className="badge-row">
          <StatusChip tone="neutral">{props.field.apply_scope}</StatusChip>
          {props.field.key === "theme" && <StatusChip tone="neutral">Resolved: {props.resolvedTheme}</StatusChip>}
        </div>
      </div>
      <SettingEditor field={props.field} value={props.value} onChange={props.onChange} />
      {props.error && <p className="field-error">{props.error}</p>}
    </section>
  );
}

function SettingEditor(props: { field: SettingsField; value: unknown; onChange: (value: unknown) => void }) {
  const { field, value, onChange } = props;
  if (field.editor === "toggle") {
    return (
      <label className="checkbox">
        <input type="checkbox" checked={Boolean(value)} onChange={(event) => onChange(event.target.checked)} />
        <span>Enabled</span>
      </label>
    );
  }
  if (field.editor === "select") {
    return (
      <select value={String(value ?? "")} onChange={(event) => onChange(event.target.value)}>
        {field.options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    );
  }
  if (field.editor === "number") {
    return (
      <input
        type="number"
        min={typeof field.validation.min === "number" ? field.validation.min : undefined}
        max={typeof field.validation.max === "number" ? field.validation.max : undefined}
        step={typeof field.validation.step === "number" ? field.validation.step : field.value_type === "float" ? 0.1 : 1}
        value={typeof value === "number" || typeof value === "string" ? value : ""}
        onChange={(event) => onChange(event.target.value)}
      />
    );
  }
  if (field.editor === "tag_list") {
    return <StringListEditor items={asStringList(value)} onChange={onChange} ordered={false} />;
  }
  if (field.editor === "ordered_list") {
    return <StringListEditor items={asStringList(value)} onChange={onChange} ordered />;
  }
  if (field.editor === "key_value") {
    return <KeyValueEditor field={field} value={asRecord(value)} onChange={onChange} />;
  }
  if (field.editor === "pair_list") {
    return <PairListEditor items={asPairList(value)} onChange={onChange} labels={asStringList(field.validation.pair_labels)} />;
  }
  return <input value={typeof value === "string" || typeof value === "number" ? String(value) : ""} onChange={(event) => onChange(event.target.value)} />;
}

function StringListEditor(props: { items: string[]; onChange: (value: unknown) => void; ordered: boolean }) {
  const [draft, setDraft] = useState("");
  function addItem() {
    const text = draft.trim();
    if (!text) return;
    if (props.items.some((item) => item.toLowerCase() === text.toLowerCase())) {
      setDraft("");
      return;
    }
    props.onChange([...props.items, text]);
    setDraft("");
  }
  return (
    <div className="settings-list-editor">
      <div className="settings-inline-input">
        <input value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="Add item" />
        <button type="button" onClick={addItem}>Add</button>
      </div>
      <div className="settings-chip-list">
        {props.items.map((item, index) => (
          <div key={`${item}-${index}`} className="settings-chip-row">
            <span className="settings-chip">{item}</span>
            {props.ordered && (
              <>
                <button type="button" onClick={() => props.onChange(moveArrayItem(props.items, index, index - 1))} disabled={index === 0}>Up</button>
                <button type="button" onClick={() => props.onChange(moveArrayItem(props.items, index, index + 1))} disabled={index === props.items.length - 1}>Down</button>
              </>
            )}
            <button type="button" className="danger ghost" onClick={() => props.onChange(props.items.filter((_, itemIndex) => itemIndex !== index))}>
              Remove
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

function KeyValueEditor(props: { field: SettingsField; value: Record<string, unknown>; onChange: (value: unknown) => void }) {
  const allowedKeys = asStringList(props.field.validation.allowed_keys);
  const keys = allowedKeys.length > 0 ? allowedKeys : Object.keys(props.value);
  const valueKind = String(props.field.validation.value_kind ?? "string");
  return (
    <div className="settings-kv-table">
      {keys.map((key) => (
        <label className="settings-kv-row" key={key}>
          <span>{key}</span>
          <input
            type={valueKind === "nullable_int" ? "number" : "text"}
            value={props.value[key] == null ? "" : String(props.value[key])}
            onChange={(event) =>
              props.onChange({
                ...props.value,
                [key]: valueKind === "nullable_int" ? event.target.value : event.target.value
              })
            }
          />
        </label>
      ))}
    </div>
  );
}

function PairListEditor(props: { items: Array<[number | string, number | string]>; onChange: (value: unknown) => void; labels: string[] }) {
  const leftLabel = props.labels[0] ?? "Value A";
  const rightLabel = props.labels[1] ?? "Value B";
  return (
    <div className="settings-pair-list">
      {props.items.map((item, index) => (
        <div className="settings-pair-row" key={`${index}-${item[0]}-${item[1]}`}>
          <label className="field">
            <span>{leftLabel}</span>
            <input type="number" min={1} value={item[0]} onChange={(event) => props.onChange(updatePairItem(props.items, index, 0, event.target.value))} />
          </label>
          <label className="field">
            <span>{rightLabel}</span>
            <input type="number" min={1} value={item[1]} onChange={(event) => props.onChange(updatePairItem(props.items, index, 1, event.target.value))} />
          </label>
          <button type="button" className="danger ghost" onClick={() => props.onChange(props.items.filter((_, itemIndex) => itemIndex !== index))}>
            Remove
          </button>
        </div>
      ))}
      <button type="button" onClick={() => props.onChange([...props.items, ["", ""]])}>Add Row</button>
    </div>
  );
}

function UnknownSettingCard(props: { item: UnknownSetting }) {
  return (
    <section className="panel inset unknown-setting-card">
      <div className="settings-field-header">
        <div>
          <h4>{props.item.label}</h4>
          <p className="muted">{props.item.key}</p>
        </div>
        <StatusChip tone="neutral">Read only</StatusChip>
      </div>
      <pre className="code-block">{JSON.stringify(props.item.value, null, 2)}</pre>
    </section>
  );
}

function readFieldErrors(error: unknown): FieldError[] {
  if (!error || typeof error !== "object" || !("fieldErrors" in error)) return [];
  const value = (error as { fieldErrors?: unknown }).fieldErrors;
  return Array.isArray(value) ? (value as FieldError[]) : [];
}

function scrollToSettingsSection(sectionId: string) {
  document.getElementById(`settings-${sectionId}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function moveArrayItem(items: string[], fromIndex: number, toIndex: number): string[] {
  if (toIndex < 0 || toIndex >= items.length) return items;
  const next = [...items];
  const [moved] = next.splice(fromIndex, 1);
  next.splice(toIndex, 0, moved);
  return next;
}

function updatePairItem(items: Array<[number | string, number | string]>, index: number, slot: 0 | 1, nextValue: string): Array<[number | string, number | string]> {
  return items.map((item, itemIndex) => {
    if (itemIndex !== index) return item;
    const next: [number | string, number | string] = [...item] as [number | string, number | string];
    next[slot] = nextValue;
    return next;
  });
}

function cloneValue<T>(value: T): T {
  if (value == null) return value;
  return JSON.parse(JSON.stringify(value)) as T;
}

function stableSerialize(value: unknown): string {
  return JSON.stringify(value) ?? "null";
}

function asStringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function asPairList(value: unknown): Array<[number | string, number | string]> {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is [number | string, number | string] => Array.isArray(item) && item.length === 2)
    .map((item) => [item[0] as number | string, item[1] as number | string]);
}
