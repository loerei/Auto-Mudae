import { ChangeEvent, useEffect, useMemo, useState } from "react";

import { DashboardPanel, StatusChip } from "./live-ui";
import { DEFAULT_UI_SETTINGS, normalizeUiSettings } from "./theme";
import {
  FieldError,
  SettingsField,
  SettingsGroup,
  SettingsPayload,
  SettingsSchema,
  SettingsSection,
  ThemeMode,
  UnknownSetting
} from "./types";

export const THEME_LABELS: Record<ThemeMode, string> = { system: "System", light: "Light", dark: "Dark" };

const SETTINGS_HASH_PREFIX = "#settings:";
const SETTINGS_VIEW_STORAGE_KEY = "mudae.settings.active-view";
const SIMPLE_FIELD_EDITORS = new Set(["toggle", "select", "number", "text"]);
const UTILITY_VIEWS = [
  { id: "unknown", title: "Unknown / Unsupported", description: "Read-only keys preserved in storage but not yet promoted to full WebUI controls." },
  { id: "backup", title: "Backup / Restore", description: "Export or import a full local settings bundle." }
] as const;

type SettingsViewId = string;

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
  const [activeViewId, setActiveViewId] = useState<SettingsViewId>("appearance");
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({});

  useEffect(() => {
    setAppDraft(props.settings?.app_settings ?? {});
    setUiDraft(normalizeUiSettings(props.settings?.ui_settings));
    setFieldErrors({});
  }, [props.settings]);

  const sections = props.schema?.sections ?? [];
  const unknownSettings = props.schema?.unknown_app_settings ?? [];
  const usingLegacySchema = useMemo(() => sections.some((section) => !Array.isArray((section as { groups?: unknown }).groups)), [sections]);

  const savedAppSettings = props.settings?.app_settings ?? {};
  const savedUiSettings = normalizeUiSettings(props.settings?.ui_settings);

  useEffect(() => {
    if (!sections.length) return;
    setCollapsedGroups((current) => {
      const next = { ...current };
      let changed = false;
      for (const section of sections) {
        for (const group of getSectionGroups(section)) {
          const key = makeGroupStateKey(section.id, group.id);
          if (!(key in next)) {
            next[key] = Boolean(group.default_collapsed);
            changed = true;
          }
        }
      }
      return changed ? next : current;
    });
  }, [sections]);

  useEffect(() => {
    if (!sections.length) return;
    setActiveViewId((current) => {
      if (isValidSettingsViewId(current, sections)) return current;
      return getPreferredSettingsView(sections);
    });
  }, [sections]);

  useEffect(() => {
    if (!sections.length || !isValidSettingsViewId(activeViewId, sections)) return;
    persistSettingsView(activeViewId);
  }, [activeViewId, sections]);

  useEffect(() => {
    if (!sections.length || typeof window === "undefined") return;
    const handleHashChange = () => {
      const candidate = readHashViewId();
      if (candidate && isValidSettingsViewId(candidate, sections)) {
        setActiveViewId(candidate);
      }
    };
    window.addEventListener("hashchange", handleHashChange);
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, [sections]);

  const activeSection = useMemo(() => sections.find((section) => section.id === activeViewId) ?? null, [activeViewId, sections]);

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

  function getSectionErrorCount(section: SettingsSection): number {
    return section.fields.reduce((count, field) => count + (fieldErrors[`${field.source}.${field.key}`] ? 1 : 0), 0);
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
    setAppDraft((prev) => {
      const next = { ...prev };
      for (const field of section.fields) {
        if (field.source === "app_settings") {
          const saved = savedAppSettings[field.key];
          next[field.key] = cloneValue(saved ?? field.default);
        }
      }
      return next;
    });
    setUiDraft((prev) => {
      const next = { ...prev };
      for (const field of section.fields) {
        if (field.source === "ui_settings") {
          const saved = savedUiSettings[field.key];
          next[field.key] = cloneValue(saved ?? field.default);
        }
      }
      return next;
    });
    setFieldErrors((prev) => {
      const next = { ...prev };
      for (const field of section.fields) {
        delete next[`${field.source}.${field.key}`];
      }
      return next;
    });
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

  function toggleGroup(sectionId: string, groupId: string) {
    const key = makeGroupStateKey(sectionId, groupId);
    setCollapsedGroups((prev) => ({ ...prev, [key]: !prev[key] }));
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
            <h3>Settings</h3>
            <p className="muted">Work through one focused section at a time instead of a single wall of fields.</p>
          </div>
        </div>

        <div className="settings-sidebar-stack">
          <section className="settings-sidebar-group">
            <p className="eyebrow">Sections</p>
            <nav className="settings-nav">
              {sections.map((section) => {
                const dirty = isSectionDirty(section);
                const errorCount = getSectionErrorCount(section);
                const active = activeViewId === section.id;
                return (
                  <button
                    key={section.id}
                    type="button"
                    className={`sidebar-item ${active ? "active" : ""}`.trim()}
                    onClick={() => setActiveSettingsView(section.id, setActiveViewId)}
                  >
                    <span className="sidebar-main">
                      <strong>{section.title}</strong>
                      <small>{getSectionGroups(section).length} group{getSectionGroups(section).length === 1 ? "" : "s"}</small>
                    </span>
                    <span className="settings-sidebar-state">
                      {errorCount > 0 && <StatusChip tone="error">{errorCount} error{errorCount === 1 ? "" : "s"}</StatusChip>}
                      {dirty && <StatusChip tone="warning">Unsaved</StatusChip>}
                    </span>
                  </button>
                );
              })}
            </nav>
          </section>

          <section className="settings-sidebar-group settings-sidebar-utility">
            <p className="eyebrow">Utilities</p>
            <nav className="settings-nav">
              {UTILITY_VIEWS.map((view) => (
                <button
                  key={view.id}
                  type="button"
                  className={`sidebar-item ${activeViewId === view.id ? "active" : ""}`.trim()}
                  onClick={() => setActiveSettingsView(view.id, setActiveViewId)}
                >
                  <span className="sidebar-main">
                    <strong>{view.title}</strong>
                    <small>{view.description}</small>
                  </span>
                </button>
              ))}
            </nav>
          </section>
        </div>
      </aside>

      <div className="settings-main">
        {activeSection ? (
          <ActiveSettingsSection
            usingLegacySchema={usingLegacySchema}
            section={activeSection}
            dirty={isSectionDirty(activeSection)}
            errorCount={getSectionErrorCount(activeSection)}
            collapsedGroups={collapsedGroups}
            resolvedTheme={props.resolvedTheme}
            getFieldValue={getFieldValue}
            getFieldError={(field) => fieldErrors[`${field.source}.${field.key}`]}
            onFieldChange={setFieldValue}
            onReset={() => resetSection(activeSection)}
            onSave={() => void saveSection(activeSection)}
            onToggleGroup={(groupId) => toggleGroup(activeSection.id, groupId)}
          />
        ) : activeViewId === "unknown" ? (
          <UnknownSettingsView unknownSettings={unknownSettings} />
        ) : (
          <BackupRestoreView
            importText={importText}
            onImportTextChange={setImportText}
            onImportFile={handleImportFile}
            onExport={() => void props.onExport()}
            onImport={() => void handleImport()}
          />
        )}
      </div>
    </section>
  );
}

function ActiveSettingsSection(props: {
  usingLegacySchema: boolean;
  section: SettingsSection;
  dirty: boolean;
  errorCount: number;
  collapsedGroups: Record<string, boolean>;
  resolvedTheme: string;
  getFieldValue: (field: SettingsField) => unknown;
  getFieldError: (field: SettingsField) => string | undefined;
  onFieldChange: (field: SettingsField, value: unknown) => void;
  onReset: () => void;
  onSave: () => void;
  onToggleGroup: (groupId: string) => void;
}) {
  const sectionGroups = getSectionGroups(props.section);
  const sectionScopes = uniq(props.section.fields.map((field) => field.apply_scope).filter(Boolean));
  return (
    <DashboardPanel
      className="settings-workspace-panel"
      title={props.section.title}
      subtitle={props.section.description}
      actions={
        <div className="panel-actions">
          {props.dirty && <StatusChip tone="warning">Unsaved</StatusChip>}
          <button className="primary" type="button" disabled={!props.dirty} onClick={props.onSave}>
            Save Section
          </button>
          <button type="button" disabled={!props.dirty} onClick={props.onReset}>
            Reset
          </button>
        </div>
      }
    >
      <div className="settings-workspace-stack">
        <div className="badge-row">
          {sectionScopes.map((scope) => (
            <StatusChip key={scope} tone={scope === "Immediate" ? "success" : scope === "Daemon restart" ? "warning" : "neutral"}>
              {scope}
            </StatusChip>
          ))}
          {props.section.dangerous && <StatusChip tone="warning">Advanced / Sensitive</StatusChip>}
        </div>
        {props.usingLegacySchema && (
          <div className="settings-warning">
            Compatibility mode: this daemon is still serving the older flat settings schema. The page remains usable, but restart the daemon to get grouped sections and fully polished labels.
          </div>
        )}
        {props.errorCount > 0 && (
          <div className="settings-error-strip">
            {props.errorCount} field error{props.errorCount === 1 ? "" : "s"} need attention before this section can be saved cleanly.
          </div>
        )}
        <div className="settings-group-stack">
          {sectionGroups.map((group) => {
            const collapsed = props.collapsedGroups[makeGroupStateKey(props.section.id, group.id)] ?? Boolean(group.default_collapsed);
            return (
              <SettingsGroupBlock
                key={group.id}
                section={props.section}
                group={group}
                collapsed={collapsed}
                resolvedTheme={props.resolvedTheme}
                getFieldValue={props.getFieldValue}
                getFieldError={props.getFieldError}
                onFieldChange={props.onFieldChange}
                onToggle={() => props.onToggleGroup(group.id)}
              />
            );
          })}
        </div>
      </div>
    </DashboardPanel>
  );
}

function SettingsGroupBlock(props: {
  section: SettingsSection;
  group: SettingsGroup;
  collapsed: boolean;
  resolvedTheme: string;
  getFieldValue: (field: SettingsField) => unknown;
  getFieldError: (field: SettingsField) => string | undefined;
  onFieldChange: (field: SettingsField, value: unknown) => void;
  onToggle: () => void;
}) {
  const groupScope = props.group.apply_scope && props.group.apply_scope !== "Mixed" ? props.group.apply_scope : null;
  const simpleFields = props.group.fields.filter((field) => isSimpleField(field));
  const complexFields = props.group.fields.filter((field) => !isSimpleField(field));

  return (
    <section className={`panel settings-group-panel ${props.group.dangerous ? "danger-zone" : ""}`.trim()}>
      <div className="settings-group-header">
        <div className="settings-group-copy">
          <h4>{props.group.title}</h4>
          {props.group.description && <p className="muted">{props.group.description}</p>}
        </div>
        <div className="settings-group-actions">
          {groupScope && (
            <StatusChip tone={groupScope === "Immediate" ? "success" : groupScope === "Daemon restart" ? "warning" : "neutral"}>
              {groupScope}
            </StatusChip>
          )}
          {props.group.dangerous && <StatusChip tone="warning">Advanced</StatusChip>}
          <button type="button" className="ghost" onClick={props.onToggle}>
            {props.collapsed ? "Expand" : "Collapse"}
          </button>
        </div>
      </div>

      {!props.collapsed && (
        <div className="settings-group-content">
          {props.group.dangerous && (
            <div className="settings-warning">These controls are low-level and can break connectivity or point the runtime at the wrong Discord target.</div>
          )}
          {simpleFields.length > 0 && (
            <div className="settings-row-list">
              {simpleFields.map((field) => (
                <SettingFieldRow
                  key={`${field.source}-${field.key}`}
                  field={field}
                  value={props.getFieldValue(field)}
                  error={props.getFieldError(field)}
                  groupScope={groupScope}
                  resolvedTheme={props.resolvedTheme}
                  onChange={(value) => props.onFieldChange(field, value)}
                />
              ))}
            </div>
          )}
          {complexFields.length > 0 && (
            <div className="settings-complex-stack">
              {complexFields.map((field) => (
                <SettingFieldPanel
                  key={`${field.source}-${field.key}`}
                  field={field}
                  value={props.getFieldValue(field)}
                  error={props.getFieldError(field)}
                  groupScope={groupScope}
                  resolvedTheme={props.resolvedTheme}
                  onChange={(value) => props.onFieldChange(field, value)}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function SettingFieldRow(props: {
  field: SettingsField;
  value: unknown;
  error?: string;
  groupScope: string | null;
  resolvedTheme: string;
  onChange: (value: unknown) => void;
}) {
  const showScopeChip = props.field.show_apply_scope || !props.groupScope || props.groupScope !== props.field.apply_scope;
  return (
    <div className={`settings-row ${props.error ? "has-error" : ""} ${props.field.dangerous ? "danger-zone" : ""}`.trim()}>
      <div className="settings-row-copy">
        <div className="settings-row-heading">
          <strong>{props.field.label}</strong>
          <div className="badge-row">
            {showScopeChip && <StatusChip tone="neutral">{props.field.apply_scope}</StatusChip>}
            {props.field.key === "theme" && <StatusChip tone="neutral">Resolved: {props.resolvedTheme}</StatusChip>}
          </div>
        </div>
        {props.field.help_text ? <p className="muted">{props.field.help_text}</p> : props.field.description ? <p className="muted">{props.field.description}</p> : null}
        {props.error && <p className="field-error">{props.error}</p>}
      </div>
      <div className={`settings-row-control control-width-${getControlWidth(props.field)}`}>
        <SettingEditor field={props.field} value={props.value} onChange={props.onChange} />
      </div>
    </div>
  );
}

function SettingFieldPanel(props: {
  field: SettingsField;
  value: unknown;
  error?: string;
  groupScope: string | null;
  resolvedTheme: string;
  onChange: (value: unknown) => void;
}) {
  const showScopeChip = props.field.show_apply_scope || !props.groupScope || props.groupScope !== props.field.apply_scope;
  return (
    <section className={`panel inset settings-complex-field ${props.field.dangerous ? "danger-zone" : ""}`.trim()}>
      <div className="settings-field-header">
        <div>
          <h4>{props.field.label}</h4>
          {props.field.help_text ? <p className="muted">{props.field.help_text}</p> : props.field.description ? <p className="muted">{props.field.description}</p> : null}
        </div>
        <div className="badge-row">
          {showScopeChip && <StatusChip tone="neutral">{props.field.apply_scope}</StatusChip>}
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
      <button
        type="button"
        role="switch"
        aria-checked={Boolean(value)}
        className={`settings-toggle ${Boolean(value) ? "on" : ""}`.trim()}
        onClick={() => onChange(!Boolean(value))}
      >
        <span className="settings-toggle-track" />
        <span>{Boolean(value) ? "Enabled" : "Disabled"}</span>
      </button>
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
      <div className={`settings-control-shell ${field.unit ? "with-unit" : ""}`.trim()}>
        <input
          type="number"
          min={typeof field.validation.min === "number" ? field.validation.min : undefined}
          max={typeof field.validation.max === "number" ? field.validation.max : undefined}
          step={typeof field.validation.step === "number" ? field.validation.step : field.value_type === "float" ? 0.1 : 1}
          value={typeof value === "number" || typeof value === "string" ? value : ""}
          placeholder={field.placeholder}
          onChange={(event) => onChange(event.target.value)}
        />
        {field.unit && <span className="settings-control-unit">{field.unit}</span>}
      </div>
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
  return <input value={typeof value === "string" || typeof value === "number" ? String(value) : ""} placeholder={field.placeholder} onChange={(event) => onChange(event.target.value)} />;
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
        <button type="button" onClick={addItem}>
          Add
        </button>
      </div>
      <div className="settings-chip-list">
        {props.items.map((item, index) => (
          <div key={`${item}-${index}`} className="settings-chip-row">
            <span className="settings-chip">{item}</span>
            {props.ordered && (
              <>
                <button type="button" onClick={() => props.onChange(moveArrayItem(props.items, index, index - 1))} disabled={index === 0}>
                  Up
                </button>
                <button type="button" onClick={() => props.onChange(moveArrayItem(props.items, index, index + 1))} disabled={index === props.items.length - 1}>
                  Down
                </button>
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
      <button type="button" onClick={() => props.onChange([...props.items, ["", ""]])}>
        Add Row
      </button>
    </div>
  );
}

function UnknownSettingsView(props: { unknownSettings: UnknownSetting[] }) {
  return (
    <DashboardPanel
      className="settings-workspace-panel"
      title="Unknown / Unsupported"
      subtitle="These app-setting keys are preserved exactly as stored, but they do not yet have first-class controls."
    >
      <div className="settings-workspace-stack">
        {props.unknownSettings.length === 0 ? (
          <p className="muted">No unsupported app settings are currently stored.</p>
        ) : (
          <div className="unknown-settings-list">
            {props.unknownSettings.map((item) => (
              <UnknownSettingCard key={item.key} item={item} />
            ))}
          </div>
        )}
      </div>
    </DashboardPanel>
  );
}

function BackupRestoreView(props: {
  importText: string;
  onImportTextChange: (value: string) => void;
  onImportFile: (event: ChangeEvent<HTMLInputElement>) => void;
  onExport: () => void;
  onImport: () => void;
}) {
  return (
    <DashboardPanel
      className="settings-workspace-panel"
      title="Backup / Restore"
      subtitle="Export a full bundle or import one after stopping live sessions. Imports are full-bundle replacements."
    >
      <div className="settings-workspace-stack">
        <div className="settings-warning">Importing a bundle replaces the stored settings snapshot. Use it for portability or restore flows, not everyday editing.</div>
        <div className="button-row">
          <button className="primary" type="button" onClick={props.onExport}>
            Export Backup
          </button>
        </div>
        <input type="file" accept="application/json" onChange={props.onImportFile} />
        <textarea className="code-block" value={props.importText} onChange={(event) => props.onImportTextChange(event.target.value)} placeholder="Paste exported JSON here" />
        <div className="button-row">
          <button className="primary" type="button" disabled={!props.importText.trim()} onClick={props.onImport}>
            Import Backup
          </button>
        </div>
      </div>
    </DashboardPanel>
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

function getSectionGroups(section: SettingsSection): SettingsGroup[] {
  if (Array.isArray(section.groups) && section.groups.length > 0) return section.groups;
  return [
    {
      id: `${section.id}.legacy`,
      title: "Section Settings",
      description: "This section is being rendered from the older flat schema payload.",
      fields: section.fields ?? [],
      layout_hint: "rows",
      default_collapsed: false,
      dangerous: Boolean(section.dangerous)
    }
  ];
}

function readFieldErrors(error: unknown): FieldError[] {
  if (!error || typeof error !== "object" || !("fieldErrors" in error)) return [];
  const value = (error as { fieldErrors?: unknown }).fieldErrors;
  return Array.isArray(value) ? (value as FieldError[]) : [];
}

function isSimpleField(field: SettingsField): boolean {
  return SIMPLE_FIELD_EDITORS.has(field.editor) && field.layout_hint !== "panel";
}

function getControlWidth(field: SettingsField): string {
  if (field.control_width) return field.control_width;
  if (field.editor === "toggle") return "sm";
  if (field.editor === "number") return "xs";
  if (field.editor === "select") return "md";
  return "lg";
}

function makeGroupStateKey(sectionId: string, groupId: string): string {
  return `${sectionId}.${groupId}`;
}

function setActiveSettingsView(viewId: SettingsViewId, setActiveViewId: (value: SettingsViewId) => void) {
  setActiveViewId(viewId);
}

function isValidSettingsViewId(viewId: string, sections: SettingsSection[]): boolean {
  return sections.some((section) => section.id === viewId) || UTILITY_VIEWS.some((view) => view.id === viewId);
}

function getPreferredSettingsView(sections: SettingsSection[]): SettingsViewId {
  const hashView = readHashViewId();
  if (hashView && isValidSettingsViewId(hashView, sections)) return hashView;
  if (typeof window !== "undefined") {
    const stored = window.localStorage.getItem(SETTINGS_VIEW_STORAGE_KEY);
    if (stored && isValidSettingsViewId(stored, sections)) return stored;
  }
  return sections.find((section) => section.id === "appearance")?.id ?? sections[0]?.id ?? "appearance";
}

function persistSettingsView(viewId: SettingsViewId) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(SETTINGS_VIEW_STORAGE_KEY, viewId);
  const nextHash = `${SETTINGS_HASH_PREFIX}${viewId}`;
  if (window.location.hash !== nextHash) {
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}${nextHash}`);
  }
}

function readHashViewId(): string | null {
  if (typeof window === "undefined") return null;
  const { hash } = window.location;
  if (!hash.startsWith(SETTINGS_HASH_PREFIX)) return null;
  return hash.slice(SETTINGS_HASH_PREFIX.length) || null;
}

function uniq(values: string[]): string[] {
  return Array.from(new Set(values.filter(Boolean)));
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
