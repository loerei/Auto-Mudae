export type CompatFieldMeta = {
  label: string;
  helpText?: string;
  unit?: string;
  controlWidth?: string;
  layoutHint?: string;
  pairLabels?: string[];
};

export type LegacySectionGroup = {
  id: string;
  title: string;
  description: string;
  layoutHint?: string;
  defaultCollapsed?: boolean;
  dangerous?: boolean;
  fieldKeys: string[];
};

export const SETTINGS_COMPAT_FIELD_META: Record<string, CompatFieldMeta> = {
  theme: { label: "Theme Mode", helpText: "Controls whether the WebUI follows the OS theme or forces a local override.", controlWidth: "md" },
  bind_host: { label: "Bind Host", controlWidth: "md" },
  bind_port: { label: "Bind Port", controlWidth: "xs" },
  retention_days: { label: "Retention Days", unit: "days", controlWidth: "xs" },
  auto_open_browser: { label: "Auto Open Browser" },
  rollCommand: { label: "Roll Command", controlWidth: "sm" },
  pokeRoll: { label: "Poke Roll" },
  ENABLE_INTERACTION_ID_CORRELATION: { label: "Interaction ID Correlation" },
  SLEEP_SHORT_SEC: { label: "Short Delay", unit: "sec", controlWidth: "xs" },
  SLEEP_MED_SEC: { label: "Medium Delay", unit: "sec", controlWidth: "xs" },
  SLEEP_LONG_SEC: { label: "Long Delay", unit: "sec", controlWidth: "xs" },
  ROLL_TRIGGER_DELAY_SEC: { label: "Roll Trigger Delay", unit: "sec", controlWidth: "xs" },
  KAKERA_REACT_DELAY_SEC: { label: "Kakera React Delay", unit: "sec", controlWidth: "xs" },
  STEAL_REACT_DELAY_SEC: { label: "Steal React Delay", unit: "sec", controlWidth: "xs" },
  REACT_CLICK_WAIT_SEC: { label: "React Click Wait", unit: "sec", controlWidth: "xs" },
  STEAL_REACT_CLICK_WAIT_SEC: { label: "Steal React Click Wait", unit: "sec", controlWidth: "xs" },
  WISH_CLAIM_RETRY_COUNT: { label: "Wish Claim Retry Count", unit: "tries", controlWidth: "xs" },
  WISH_CLAIM_RETRY_DELAY_SEC: { label: "Wish Claim Retry Delay", unit: "sec", controlWidth: "xs" },
  STEAL_ALLOW_TU_REFRESH: { label: "Allow /tu Refresh During Steal" },
  STEAL_TU_MAX_AGE_SEC: { label: "Steal /tu Max Age", unit: "sec", controlWidth: "xs" },
  TU_INFO_REUSE_MAX_AGE_SEC: { label: "/tu Cache Reuse Window", unit: "sec", controlWidth: "xs" },
  WISHLIST_CACHE_TTL_SEC: { label: "Wishlist Cache TTL", unit: "sec", controlWidth: "xs" },
  NO_RESET_RETRY_JITTER_PCT: { label: "No-Reset Retry Jitter", unit: "%", controlWidth: "xs" },
  LATENCY_PROFILE_DEFAULT: { label: "Latency Profile", controlWidth: "md" },
  LATENCY_FORCE_PROFILE: { label: "Forced Latency Profile", controlWidth: "md" },
  LATENCY_AUTO_DEGRADE: { label: "Latency Auto-Degrade" },
  LATENCY_METRICS_ENABLED: { label: "Latency Metrics" },
  ROLLS_PER_RESET: { label: "Rolls Per Reset", unit: "rolls", controlWidth: "xs" },
  minKakeratoclaim: { label: "Minimum Kakera to Claim", controlWidth: "sm" },
  EMOJI_CLAIM_REACT: { label: "Claim Reaction Emoji", controlWidth: "sm" },
  EMOJI_STATUS_CLAIMED: { label: "Claimed Status Emoji", controlWidth: "sm" },
  EMOJI_STATUS_UNCLAIMED: { label: "Unclaimed Status Emoji", controlWidth: "sm" },
  EMOJI_KAKERA: { label: "Kakera Emoji", controlWidth: "sm" },
  KAKERA_REACTION_PRIORITY: { label: "Kakera Reaction Priority", layoutHint: "panel" },
  Kakera_Give: { label: "Kakera Give Rules", layoutHint: "panel", pairLabels: ["From Account ID", "To Account ID"] },
  Sphere_Give: { label: "Sphere Give Rules", layoutHint: "panel", pairLabels: ["From Account ID", "To Account ID"] },
  steal_claim_whitelist: { label: "Steal Claim Whitelist", layoutHint: "panel" },
  steal_react_whitelist: { label: "Steal React Whitelist", layoutHint: "panel" },
  WISHLIST_NORMALIZE_TEXT: { label: "Normalize Wishlist Text" },
  ROLL_COORDINATION_ENABLED: { label: "Roll Coordination" },
  ROLL_LEASE_TTL_SEC: { label: "Roll Lease TTL", unit: "sec", controlWidth: "xs" },
  ROLL_LEASE_HEARTBEAT_SEC: { label: "Roll Lease Heartbeat", unit: "sec", controlWidth: "xs" },
  ROLL_LEASE_WAIT_SEC: { label: "Roll Lease Wait Timeout", unit: "sec", controlWidth: "xs" },
  ROLL_STALL_REFRESH_THRESHOLD: { label: "Roll Stall Refresh Threshold", controlWidth: "xs" },
  ROLL_STALL_ABORT_THRESHOLD: { label: "Roll Stall Abort Threshold", controlWidth: "xs" },
  AUTO_OURO_AFTER_ROLL: { label: "Auto-Ouro After Roll" },
  AUTO_OH: { label: "Auto $oh" },
  AUTO_OC: { label: "Auto $oc" },
  AUTO_OQ: { label: "Auto $oq" },
  OQ_RAM_CACHE_MB: { label: "OQ RAM Cache", unit: "MB", controlWidth: "sm" },
  OQ_BEAM_K: { label: "OQ Beam Width", controlWidth: "xs" },
  OQ_CACHE_MAX_GB: { label: "OQ Cache Limit", unit: "GB", controlWidth: "sm" },
  OQ_AUTO_LEARN_HIGHER_EMOJIS: { label: "Auto-Learn Higher Emojis" },
  OQ_HIGHER_THAN_RED_EMOJIS: { label: "Higher-Than-Red Emojis", layoutHint: "panel" },
  OQ_RED_EMOJI_ALIASES: { label: "Red Emoji Aliases", layoutHint: "panel" },
  DASHBOARD_LIVE_REDRAW: { label: "Live Redraw" },
  DASHBOARD_STATUS_LOG_SEC: { label: "Status Log Interval", unit: "sec", controlWidth: "xs" },
  DASHBOARD_FORCE_CLEAR: { label: "Force Screen Clear" },
  DASHBOARD_RENDERER_MODE: { label: "Renderer Mode", controlWidth: "md" },
  DASHBOARD_NO_SCROLL: { label: "No-Scroll Guard" },
  DASHBOARD_WIDECHAR_AWARE: { label: "Wide-Character Aware" },
  DASHBOARD_RENDER_SAFETY_COLS: { label: "Render Safety Columns", controlWidth: "xs" },
  DASHBOARD_RENDER_SAFETY_ROWS: { label: "Render Safety Rows", controlWidth: "xs" },
  DASHBOARD_AUTO_FIT: { label: "Auto-Fit Dashboard" },
  DASHBOARD_MIN_WIDTH: { label: "Minimum Width", controlWidth: "xs" },
  DASHBOARD_MAX_WIDTH: { label: "Maximum Width", controlWidth: "xs" },
  LOG_LEVEL_DEFAULT: { label: "Default Log Level", controlWidth: "sm" },
  LOG_USE_EMOJI: { label: "Emoji Log Prefixes" },
  LOG_EMOJI: { label: "Log Emoji Map", layoutHint: "panel" },
  ALT_C_DEBOUNCE_MS: { label: "Alt+C Debounce", unit: "ms", controlWidth: "xs" },
  ALT_C_INPUT_GUARD_MS: { label: "Alt+C Input Guard", unit: "ms", controlWidth: "xs" },
  channelId: { label: "Channel ID", controlWidth: "md" },
  serverId: { label: "Server ID", controlWidth: "md" },
  DISCORD_API_BASE: { label: "Discord API Base", controlWidth: "full" },
  DISCORD_API_VERSION_MESSAGES: { label: "Message API Version", controlWidth: "xs" },
  DISCORD_API_VERSION_USERS: { label: "User API Version", controlWidth: "xs" },
  MUDAE_BOT_ID: { label: "Mudae Bot ID", controlWidth: "md" },
  LAST_SEEN_PATH: { label: "Last-Seen Cache Path", controlWidth: "full" },
  LAST_SEEN_FLUSH_SEC: { label: "Last-Seen Flush Interval", unit: "sec", controlWidth: "xs" },
  LATENCY_METRICS_PATH: { label: "Latency Metrics Path", controlWidth: "full" }
};

export const LEGACY_SECTION_GROUPS: Record<string, LegacySectionGroup[]> = {
  appearance: [{ id: "appearance_theme", title: "Theme", description: "Choose how the WebUI resolves light and dark mode.", layoutHint: "rows", fieldKeys: ["theme"] }],
  webui_runtime: [
    {
      id: "webui_network",
      title: "Networking & Startup",
      description: "Controls how the local daemon binds, retains history, and opens the browser.",
      layoutHint: "rows",
      fieldKeys: ["bind_host", "bind_port", "retention_days", "auto_open_browser"]
    }
  ],
  core_runtime_timing: [
    {
      id: "command_pacing",
      title: "Command Pacing",
      description: "Low-level send, click, and reaction timings used during the roll loop.",
      layoutHint: "rows",
      fieldKeys: ["rollCommand", "pokeRoll", "ENABLE_INTERACTION_ID_CORRELATION", "SLEEP_SHORT_SEC", "SLEEP_MED_SEC", "SLEEP_LONG_SEC", "ROLL_TRIGGER_DELAY_SEC", "KAKERA_REACT_DELAY_SEC", "STEAL_REACT_DELAY_SEC", "REACT_CLICK_WAIT_SEC", "STEAL_REACT_CLICK_WAIT_SEC"]
    },
    {
      id: "cache_refresh",
      title: "Cache & Refresh",
      description: "Retry windows and cache freshness before the runtime re-syncs with Discord.",
      layoutHint: "rows",
      fieldKeys: ["WISH_CLAIM_RETRY_COUNT", "WISH_CLAIM_RETRY_DELAY_SEC", "STEAL_ALLOW_TU_REFRESH", "STEAL_TU_MAX_AGE_SEC", "TU_INFO_REUSE_MAX_AGE_SEC", "WISHLIST_CACHE_TTL_SEC"]
    },
    {
      id: "latency_retry",
      title: "Latency & Retry",
      description: "Retry jitter, latency profile selection, and reset pacing.",
      layoutHint: "rows",
      fieldKeys: ["NO_RESET_RETRY_JITTER_PCT", "LATENCY_PROFILE_DEFAULT", "LATENCY_FORCE_PROFILE", "LATENCY_AUTO_DEGRADE", "LATENCY_METRICS_ENABLED", "ROLLS_PER_RESET"]
    }
  ],
  roll_claim_react: [
    { id: "claim_policy", title: "Claim Policy", description: "Thresholds and emojis used during roll evaluation.", layoutHint: "rows", fieldKeys: ["minKakeratoclaim", "EMOJI_CLAIM_REACT", "EMOJI_STATUS_CLAIMED", "EMOJI_STATUS_UNCLAIMED", "EMOJI_KAKERA", "WISHLIST_NORMALIZE_TEXT"] },
    { id: "reaction_transfer_rules", title: "Reaction & Transfer Rules", description: "Kakera reaction order and give mappings.", layoutHint: "cards", fieldKeys: ["KAKERA_REACTION_PRIORITY", "Kakera_Give", "Sphere_Give"] },
    { id: "steal_behavior", title: "Steal Behavior", description: "Whitelists for shared-channel steal behavior.", layoutHint: "cards", fieldKeys: ["steal_claim_whitelist", "steal_react_whitelist"] },
    { id: "coordination", title: "Coordination", description: "Same-account lease timings and stall thresholds.", layoutHint: "rows", fieldKeys: ["ROLL_COORDINATION_ENABLED", "ROLL_LEASE_TTL_SEC", "ROLL_LEASE_HEARTBEAT_SEC", "ROLL_LEASE_WAIT_SEC", "ROLL_STALL_REFRESH_THRESHOLD", "ROLL_STALL_ABORT_THRESHOLD"] }
  ],
  ouro: [
    { id: "ouro_automation", title: "Automation", description: "Choose which Ouro commands may run automatically.", layoutHint: "rows", fieldKeys: ["AUTO_OURO_AFTER_ROLL", "AUTO_OH", "AUTO_OC", "AUTO_OQ", "OQ_AUTO_LEARN_HIGHER_EMOJIS"] },
    { id: "oq_tuning", title: "OQ Tuning", description: "Memory, beam search, and emoji alias tuning for OQ.", layoutHint: "cards", fieldKeys: ["OQ_RAM_CACHE_MB", "OQ_BEAM_K", "OQ_CACHE_MAX_GB", "OQ_HIGHER_THAN_RED_EMOJIS", "OQ_RED_EMOJI_ALIASES"] }
  ],
  dashboard_logging: [
    { id: "dashboard_rendering", title: "Dashboard Rendering", description: "Terminal redraw strategy, sizing guards, and wide-character handling.", layoutHint: "rows", fieldKeys: ["DASHBOARD_LIVE_REDRAW", "DASHBOARD_STATUS_LOG_SEC", "DASHBOARD_FORCE_CLEAR", "DASHBOARD_RENDERER_MODE", "DASHBOARD_NO_SCROLL", "DASHBOARD_WIDECHAR_AWARE", "DASHBOARD_RENDER_SAFETY_COLS", "DASHBOARD_RENDER_SAFETY_ROWS", "DASHBOARD_AUTO_FIT", "DASHBOARD_MIN_WIDTH", "DASHBOARD_MAX_WIDTH"] },
    { id: "logging_behavior", title: "Logging Behavior", description: "Verbosity, emoji prefixes, and Alt+C terminal safety timings.", layoutHint: "cards", fieldKeys: ["LOG_LEVEL_DEFAULT", "LOG_USE_EMOJI", "LOG_EMOJI", "ALT_C_DEBOUNCE_MS", "ALT_C_INPUT_GUARD_MS"] }
  ],
  advanced_integration: [
    { id: "targeting", title: "Targeting", description: "Server, channel, and Mudae bot identity.", layoutHint: "rows", defaultCollapsed: true, dangerous: true, fieldKeys: ["channelId", "serverId", "MUDAE_BOT_ID"] },
    { id: "discord_api", title: "Discord API", description: "Low-level API base and version settings.", layoutHint: "rows", defaultCollapsed: true, dangerous: true, fieldKeys: ["DISCORD_API_BASE", "DISCORD_API_VERSION_MESSAGES", "DISCORD_API_VERSION_USERS"] },
    { id: "runtime_paths", title: "Runtime Paths", description: "Disk locations and flush intervals for caches and metrics files.", layoutHint: "rows", defaultCollapsed: true, dangerous: true, fieldKeys: ["LAST_SEEN_PATH", "LAST_SEEN_FLUSH_SEC", "LATENCY_METRICS_PATH"] }
  ]
};
