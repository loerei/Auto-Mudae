import { useEffect, useState } from "react";

import { ResolvedTheme, ThemeMode, UISettings } from "./types";

export const DEFAULT_UI_SETTINGS: UISettings = {
  bind_host: "127.0.0.1",
  bind_port: 8765,
  retention_days: 30,
  auto_open_browser: true,
  theme: "system"
};

export function normalizeThemeMode(value: unknown): ThemeMode {
  const theme = typeof value === "string" ? value.trim().toLowerCase() : "";
  if (theme === "light" || theme === "dark") return theme;
  return "system";
}

export function normalizeUiSettings(value?: Record<string, unknown> | null): UISettings {
  return {
    ...DEFAULT_UI_SETTINGS,
    ...(value ?? {}),
    bind_host: String(value?.bind_host ?? DEFAULT_UI_SETTINGS.bind_host),
    bind_port: normalizeNumber(value?.bind_port, DEFAULT_UI_SETTINGS.bind_port, 1),
    retention_days: normalizeNumber(value?.retention_days, DEFAULT_UI_SETTINGS.retention_days, 1),
    auto_open_browser: normalizeBoolean(value?.auto_open_browser, DEFAULT_UI_SETTINGS.auto_open_browser),
    theme: normalizeThemeMode(value?.theme)
  };
}

export function mergeUiSettings(current?: Record<string, unknown> | null, patch?: Partial<UISettings>): UISettings {
  return normalizeUiSettings({ ...(current ?? {}), ...(patch ?? {}) });
}

export function resolveThemeMode(mode: ThemeMode, systemTheme: ResolvedTheme): ResolvedTheme {
  return mode === "system" ? systemTheme : mode;
}

export function nextThemeMode(current: ThemeMode): ThemeMode {
  const order: ThemeMode[] = ["system", "light", "dark"];
  const index = order.indexOf(current);
  return order[(index + 1 + order.length) % order.length];
}

export function useResolvedTheme(mode: ThemeMode): ResolvedTheme {
  const [systemTheme, setSystemTheme] = useState<ResolvedTheme>(() => getSystemTheme());

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return undefined;
    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    const handleChange = () => setSystemTheme(mediaQuery.matches ? "dark" : "light");
    handleChange();
    if (typeof mediaQuery.addEventListener === "function") {
      mediaQuery.addEventListener("change", handleChange);
      return () => mediaQuery.removeEventListener("change", handleChange);
    }
    mediaQuery.addListener(handleChange);
    return () => mediaQuery.removeListener(handleChange);
  }, []);

  return resolveThemeMode(mode, systemTheme);
}

function getSystemTheme(): ResolvedTheme {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return "light";
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function normalizeBoolean(value: unknown, fallback: boolean): boolean {
  if (typeof value === "boolean") return value;
  if (typeof value === "string") {
    const lowered = value.trim().toLowerCase();
    if (["1", "true", "yes", "on"].includes(lowered)) return true;
    if (["0", "false", "no", "off"].includes(lowered)) return false;
  }
  if (value == null) return fallback;
  return Boolean(value);
}

function normalizeNumber(value: unknown, fallback: number, minValue: number): number {
  const parsed = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(minValue, Math.floor(parsed));
}
