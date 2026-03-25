export type QueueItem = {
  id: number;
  account_id: number;
  mode: string;
  action: string;
  status: string;
  source: string;
  scheduled_for?: number | null;
  session_id?: string | null;
};

export type SessionItem = {
  id: string;
  mode: string;
  status: string;
  started_at: number;
  ended_at?: number | null;
  error?: string | null;
  summary?: Record<string, unknown>;
};

export type EventItem = {
  id: number;
  created_at: number;
  account_id?: number | null;
  mode?: string | null;
  kind: string;
  level?: string | null;
  message?: string | null;
  payload: Record<string, unknown>;
};

export type Account = {
  id: number;
  name: string;
  discord_user_id?: string | null;
  discordusername?: string | null;
  token: string;
  max_power: number;
};

export type WishlistItem = {
  id?: number;
  account_id?: number | null;
  name: string;
  priority: number;
  is_star: boolean;
};

export type RollEntry = {
  name?: string;
  series?: string;
  kakera?: number | string;
  keys?: string[];
  wishlist?: boolean;
  candidate?: boolean;
  kakera_react?: boolean;
  claimed?: boolean;
  image_url?: string;
  status?: string;
};

export type OtherRollEntry = {
  roller?: string;
  name?: string;
  series?: string;
  kakera?: number | string;
  wishlist?: boolean;
  claimed?: boolean;
  kakera_button?: boolean;
};

export type SessionStatus = Record<string, unknown> & {
  rolls?: number;
  next_reset_min?: number;
  claim_reset_min?: number;
  daily_reset_min?: number;
  rt_reset_min?: number;
  dk_reset_min?: number;
  can_claim_now?: boolean;
  daily_available?: boolean;
  rt_available?: boolean;
  dk_ready?: boolean;
  current_power?: number;
  max_power?: number;
  kakera_cost?: number;
  wl_used?: number;
  wl_total?: number;
  sw_used?: number;
  sw_total?: number;
  star_wishes?: string[];
  regular_wishes?: string[];
  oh_left?: number | null;
  oc_left?: number | null;
  oq_left?: number | null;
  oh_stored?: number | null;
  oc_stored?: number | null;
  oq_stored?: number | null;
  sphere_balance?: number | null;
  ouro_refill_min?: number | null;
  total_balance?: number | null;
};

export type PredictedState = {
  status?: string;
  minutes_to_wait?: number;
  predicted_at?: string;
};

export type AccountSnapshot = {
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
  session_status?: SessionStatus;
  wishlist_state?: Record<string, unknown>;
  predicted?: PredictedState;
  connection_retry_active?: boolean;
  connection_retry_sec?: number | null;
  session_start?: string | null;
  session_start_ts?: number | null;
  session_started_at?: number | null;
  rolls?: RollEntry[];
  rolls_total?: number;
  rolls_target?: number | null;
  rolls_remaining?: number | null;
  others_rolls?: OtherRollEntry[];
  oh_left?: number | null;
  oc_left?: number | null;
  oq_left?: number | null;
  oh_stored?: number | null;
  oc_stored?: number | null;
  oq_stored?: number | null;
  sphere_balance?: number | null;
  ouro_refill_min?: number | null;
};

export type OverviewPayload = {
  accounts: AccountSnapshot[];
  queue: QueueItem[];
  recent_sessions: SessionItem[];
  running_count: number;
};

export type SettingsPayload = {
  app_settings: Record<string, unknown>;
  ui_settings: Record<string, unknown>;
};

export type WishlistPayload = {
  global: WishlistItem[];
  accounts: Record<string, WishlistItem[]>;
};

export type AccountHistory = {
  sessions: SessionItem[];
  events: EventItem[];
};

export type LiveEventMessage =
  | { kind: "bootstrap"; overview: OverviewPayload }
  | { kind: "account_state"; account_id: number; state: AccountSnapshot }
  | { kind: "event"; event: EventItem }
  | { kind: "account_exit"; account_id: number; status: string }
  | { kind: string; [key: string]: unknown };
