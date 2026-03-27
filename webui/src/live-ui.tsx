import { ReactNode } from "react";

import { AnimatePresence, motion, useReducedMotion } from "framer-motion";

import {
  buildBadgePulse,
  buildFadeIn,
  buildFadeUp,
  buildScaleIn,
  buildStaggerContainer,
  emphasisAnimation,
  isCountdownThresholdPulse,
  usePulseControls
} from "./motion";
import { AccountSnapshot, EventItem, OtherRollEntry, QueueItem, RollEntry, SessionStatus } from "./types";

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function asStringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && item.trim().length > 0) : [];
}

export function fmtTime(value?: number | null): string {
  return value ? new Date(value * 1000).toLocaleString() : "n/a";
}

export function fmtCountdown(seconds?: number | null): string {
  if (seconds == null) return "n/a";
  const safe = Math.max(0, Math.floor(seconds));
  const hrs = Math.floor(safe / 3600);
  const mins = Math.floor((safe % 3600) / 60);
  const secs = safe % 60;
  return `${hrs.toString().padStart(2, "0")}:${mins.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
}

function fmtMinutes(minutes?: number | null): string {
  if (minutes == null) return "n/a";
  return fmtCountdown(minutes * 60);
}

function fmtRuntime(startedAt?: number | null): string {
  if (!startedAt) return "n/a";
  const elapsed = Math.max(0, Math.floor(Date.now() / 1000 - startedAt));
  return fmtCountdown(elapsed);
}

function renderFlags(flags: Array<string | null | undefined>): string {
  return flags.filter(Boolean).join(" ");
}

function getClaimLabel(status: SessionStatus | undefined): string {
  if (!status) return "n/a";
  if (status.can_claim_now) return "Available";
  const claimMin = asNumber(status.claim_reset_min);
  return claimMin != null && claimMin > 0 ? `Cooldown (${fmtMinutes(claimMin)})` : "Cooldown";
}

function getPowerLabel(status: SessionStatus | undefined): string {
  if (!status) return "n/a";
  const current = asNumber(status.current_power);
  const maxPower = asNumber(status.max_power);
  const cost = asNumber(status.kakera_cost);
  if (current == null) return "n/a";
  const reacts = cost && cost > 0 ? Math.floor(current / cost) : 0;
  return maxPower != null ? `${current}/${maxPower}%${reacts > 0 ? ` · ${reacts}x react` : ""}` : `${current}%`;
}

function getOuroTotal(snapshot: AccountSnapshot): number | null {
  const values = [snapshot.oh_left, snapshot.oc_left, snapshot.oq_left].map((value) => (typeof value === "number" ? value : 0));
  return values.some((value) => value > 0) ? values.reduce((sum, value) => sum + value, 0) : null;
}

function getWishlistCount(snapshot: AccountSnapshot): number | null {
  const status = snapshot.wishlist_state ?? snapshot.session_status;
  if (!status) return null;
  const starWishes = asStringList((status as Record<string, unknown>).star_wishes);
  const regularWishes = asStringList((status as Record<string, unknown>).regular_wishes);
  if (starWishes.length || regularWishes.length) return starWishes.length + regularWishes.length;
  const wlUsed = asNumber((status as Record<string, unknown>).wl_used);
  const swUsed = asNumber((status as Record<string, unknown>).sw_used);
  if (wlUsed != null || swUsed != null) return (wlUsed ?? 0) + (swUsed ?? 0);
  return null;
}

function CountdownValue(props: { seconds?: number | null }) {
  const reduced = useReducedMotion();
  const controls = usePulseControls(props.seconds ?? null, reduced, isCountdownThresholdPulse, (isReduced) => emphasisAnimation(isReduced, 1.045));
  return <motion.span animate={controls}>{fmtCountdown(props.seconds)}</motion.span>;
}

function AnimatedValue(props: { value: ReactNode; pulseKey?: string | number | null }) {
  const reduced = useReducedMotion();
  const controls = usePulseControls(props.pulseKey ?? null, reduced);
  return <motion.strong animate={controls}>{props.value}</motion.strong>;
}

function AnimatedTableBody(props: { children: ReactNode }) {
  const reduced = useReducedMotion();
  return (
    <motion.tbody variants={buildStaggerContainer(reduced, 0.035)} initial="hidden" animate="visible">
      <AnimatePresence initial={false}>{props.children}</AnimatePresence>
    </motion.tbody>
  );
}

export function StatusChip(props: { tone?: string; children: ReactNode; pulseKey?: string | number | null; className?: string }) {
  const reduced = useReducedMotion();
  const contentKey = `${props.tone ?? "neutral"}-${String(props.pulseKey ?? props.children)}`;
  return (
    <motion.span className={`status-chip tone-${props.tone ?? "neutral"} ${props.className ?? ""}`.trim()}>
      <AnimatePresence initial={false} mode="wait">
        <motion.span key={contentKey} variants={buildBadgePulse(reduced)} initial="hidden" animate="visible" exit="exit">
          {props.children}
        </motion.span>
      </AnimatePresence>
    </motion.span>
  );
}

export function DashboardPanel(props: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  className?: string;
  children: ReactNode;
  order?: number;
}) {
  const reduced = useReducedMotion();
  return (
    <motion.section
      className={`panel dashboard-panel ${props.className ?? ""}`.trim()}
      variants={buildFadeUp(reduced, 10)}
      initial="hidden"
      animate="visible"
      exit="exit"
      transition={{ delay: reduced ? 0 : (props.order ?? 0) * 0.035 }}
    >
      <div className="panel-header">
        <div>
          <h3>{props.title}</h3>
          {props.subtitle && <p className="muted">{props.subtitle}</p>}
        </div>
        {props.actions}
      </div>
      {props.children}
    </motion.section>
  );
}

export function MetricRow(props: { label: string; value: ReactNode; accent?: boolean; pulseKey?: string | number | null }) {
  const reduced = useReducedMotion();
  return (
    <motion.div className={`metric-row ${props.accent ? "accent" : ""}`} variants={buildFadeUp(reduced, 8)}>
      <span>{props.label}</span>
      <AnimatedValue value={props.value} pulseKey={props.pulseKey} />
    </motion.div>
  );
}

function RollRowView(props: { roll: RollEntry; index: number }) {
  const reduced = useReducedMotion();
  const flags = renderFlags([props.roll.kakera_react ? "💎" : null, props.roll.wishlist ? "⭐" : null, props.roll.candidate ? "❤️" : null, props.roll.claimed ? "C" : null]);
  const flagControls = usePulseControls(flags, reduced);
  return (
    <motion.tr layout={false} variants={buildFadeUp(reduced, 10)} initial="hidden" animate="visible" exit="exit">
      <td>{props.index + 1}</td>
      <td>
        {props.roll.name ?? "Unknown"}{" "}
        {flags && (
          <motion.span className="muted roll-flags" animate={flagControls}>
            {flags}
          </motion.span>
        )}
      </td>
      <td>{props.roll.series ?? "n/a"}</td>
      <td>{props.roll.kakera ?? "?"}</td>
      <td>{Array.isArray(props.roll.keys) && props.roll.keys.length > 0 ? props.roll.keys.join(" ") : "—"}</td>
    </motion.tr>
  );
}

function OtherRollRowView(props: { roll: OtherRollEntry; index: number }) {
  const reduced = useReducedMotion();
  const flags = renderFlags([props.roll.wishlist ? "⭐" : null, props.roll.claimed ? "C" : null, props.roll.kakera_button ? "💎" : null]);
  const flagControls = usePulseControls(flags, reduced);
  return (
    <motion.tr layout={false} variants={buildFadeUp(reduced, 10)} initial="hidden" animate="visible" exit="exit">
      <td>{props.index + 1}</td>
      <td>{props.roll.roller ?? "unknown"}</td>
      <td>
        {props.roll.name ?? "Unknown"}{" "}
        {flags && (
          <motion.span className="muted roll-flags" animate={flagControls}>
            {flags}
          </motion.span>
        )}
      </td>
      <td>{props.roll.series ?? "n/a"}</td>
      <td>{props.roll.kakera ?? "?"}</td>
    </motion.tr>
  );
}

function getLogPayloadLines(item: EventItem): string[] {
  if (item.kind === "state") {
    const stateType = asString(item.payload.state_type);
    if (stateType) return [`State update: ${stateType}`];
  }
  if (item.kind === "runner_event") {
    return Object.entries(item.payload)
      .filter(([, value]) => value != null && value !== "")
      .slice(0, 5)
      .map(([key, value]) => `${key}: ${String(value)}`);
  }
  if (item.kind === "worker_status") {
    return Object.entries(item.payload).map(([key, value]) => `${key}: ${String(value)}`);
  }
  return [];
}

export function LogPayloadView(props: { item: EventItem }) {
  const reduced = useReducedMotion();
  const lines = getLogPayloadLines(props.item);
  if (lines.length > 0) {
    return (
      <motion.ul className="inline-list" variants={buildFadeIn(reduced)} initial="hidden" animate="visible">
        {lines.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </motion.ul>
    );
  }
  return <pre>{JSON.stringify(props.item.payload, null, 2)}</pre>;
}

export function OverviewCard(props: {
  snapshot: AccountSnapshot;
  onOpen: () => void;
  onAction: (mode: string, action: string) => void;
  onForceStop: () => void;
  onClearQueue: () => void;
  index?: number;
}) {
  const reduced = useReducedMotion();
  const { snapshot, onOpen, onAction } = props;
  const claimLabel = getClaimLabel(snapshot.session_status);
  const powerLabel = getPowerLabel(snapshot.session_status);
  const wishlistCount = getWishlistCount(snapshot);
  const ouroTotal = getOuroTotal(snapshot);

  return (
    <motion.article
      className="panel card overview-card"
      variants={buildFadeUp(reduced, 12)}
      initial="hidden"
      animate="visible"
      exit="exit"
      transition={{ delay: reduced ? 0 : (props.index ?? 0) * 0.04 }}
    >
      <div className="card-header">
        <div>
          <h3>{snapshot.account.name}</h3>
          <p>{snapshot.account.discordusername || "No username"}</p>
        </div>
        <StatusChip tone={snapshot.status} pulseKey={`${snapshot.status}-${snapshot.active_mode ?? "idle"}`}>{snapshot.status}</StatusChip>
      </div>
      <dl className="meta-list">
        <div><dt>Mode</dt><dd>{snapshot.active_mode || snapshot.paused_mode || "idle"}</dd></div>
        <div><dt>Connection</dt><dd><StatusChip tone={snapshot.connection_status === "Connected" ? "success" : "warning"} pulseKey={snapshot.connection_status || "n/a"}>{snapshot.connection_status || "n/a"}</StatusChip></dd></div>
        <div><dt>Next</dt><dd>{snapshot.next_action || "n/a"}</dd></div>
        <div><dt>Countdown</dt><dd><CountdownValue seconds={snapshot.countdown_remaining} /></dd></div>
      </dl>
      <div className="badge-row">
        <StatusChip tone={snapshot.session_status?.can_claim_now ? "success" : "warning"} pulseKey={claimLabel}>Claim: {claimLabel}</StatusChip>
        <StatusChip tone="neutral" pulseKey={powerLabel}>Power: {powerLabel}</StatusChip>
        {wishlistCount != null && <StatusChip tone="neutral" pulseKey={wishlistCount}>Wishlist: {wishlistCount}</StatusChip>}
        {ouroTotal != null && <StatusChip tone="neutral" pulseKey={ouroTotal}>Ouro: {ouroTotal}</StatusChip>}
      </div>
      <p className="muted">{snapshot.last_message || snapshot.last_action || "No live events yet."}</p>
      <div className="button-row">
        <button className="primary" onClick={onOpen}>Open Live</button>
        <button onClick={() => onAction("main", "start")}>Start Main</button>
        {snapshot.status === "paused" ? (
          <button onClick={() => onAction(snapshot.active_mode || snapshot.paused_mode || "main", "resume")}>Resume</button>
        ) : (
          <button onClick={() => onAction(snapshot.active_mode || "main", "pause")}>Pause</button>
        )}
        <button className="danger ghost" onClick={props.onForceStop}>Force Stop</button>
      </div>
      <div className="button-row">
        <button onClick={() => onAction("oh", "start")}>OH</button>
        <button onClick={() => onAction("oc", "start")}>OC</button>
        <button onClick={() => onAction("oq", "start")}>OQ</button>
        <button onClick={props.onClearQueue}>Clear Queue</button>
      </div>
      <p className="muted">Use Accounts &gt; Live for queue details, schedules, and the full dashboard.</p>
    </motion.article>
  );
}

export function LiveDashboard(props: {
  snapshot: AccountSnapshot;
  queueMode: string;
  setQueueMode: (mode: string) => void;
  scheduleMode: string;
  setScheduleMode: (mode: string) => void;
  scheduleAt: string;
  setScheduleAt: (value: string) => void;
  onAction: (mode: string, action: string) => void;
  onForceStop: () => void;
  onClearQueue: () => void;
  onQueue: () => void;
  onSchedule: () => void;
}) {
  const reduced = useReducedMotion();
  const { snapshot } = props;
  const sessionStatus = snapshot.session_status;
  const wishlistSource = snapshot.wishlist_state ?? sessionStatus ?? {};
  const starWishes = asStringList((wishlistSource as Record<string, unknown>).star_wishes);
  const regularWishes = asStringList((wishlistSource as Record<string, unknown>).regular_wishes);
  const predictedStatus = asString(snapshot.predicted?.status);
  const rolls = snapshot.rolls ?? [];
  const others = snapshot.others_rolls ?? [];
  const candidateKey = `${asString(snapshot.best_candidate?.name) ?? "none"}-${asString(snapshot.best_candidate?.series) ?? "none"}-${String(snapshot.best_candidate?.kakera ?? "none")}`;
  const summaryKey = JSON.stringify(snapshot.summary ?? {});
  const ouroKey = `${snapshot.oh_left ?? "n"}-${snapshot.oc_left ?? "n"}-${snapshot.oq_left ?? "n"}-${snapshot.sphere_balance ?? "n"}-${snapshot.ouro_refill_min ?? "n"}`;
  const liveGridVariants = buildStaggerContainer(reduced, 0.04);
  const summaryControls = usePulseControls(summaryKey, reduced);
  const ouroControls = usePulseControls(ouroKey, reduced);

  return (
    <motion.section className="grid live-grid" initial="hidden" animate="visible" variants={liveGridVariants}>
      <DashboardPanel
        title="Session Header"
        subtitle={snapshot.account.discordusername || "No username"}
        actions={
          <div className="badge-row">
            <StatusChip tone={snapshot.status} pulseKey={snapshot.status}>{snapshot.status}</StatusChip>
            <StatusChip tone={snapshot.connection_status === "Connected" ? "success" : "warning"} pulseKey={snapshot.connection_status || "n/a"}>
              {snapshot.connection_status || "n/a"}
            </StatusChip>
            {snapshot.connection_retry_active && (
              <StatusChip tone="warning" pulseKey={snapshot.connection_retry_sec ?? 0}>Reconnect {fmtCountdown(snapshot.connection_retry_sec)}</StatusChip>
            )}
          </div>
        }
        className="span-2"
        order={0}
      >
        <div className="metrics-grid">
          <MetricRow label="Runtime" value={fmtRuntime(snapshot.session_started_at)} accent />
          <MetricRow label="Last roll start" value={snapshot.session_start || "n/a"} />
          <MetricRow label="State" value={snapshot.dashboard_state || "n/a"} pulseKey={snapshot.dashboard_state || "n/a"} />
          <MetricRow label="Reconnect" value={snapshot.connection_retry_active ? fmtCountdown(snapshot.connection_retry_sec) : "idle"} pulseKey={snapshot.connection_retry_active ? snapshot.connection_retry_sec : "idle"} />
        </div>
      </DashboardPanel>

      <DashboardPanel title="Session Status" className="span-2" order={1}>
        <div className="metrics-grid">
          <MetricRow label="Last action" value={snapshot.last_action || "n/a"} pulseKey={snapshot.last_action || "n/a"} />
          <MetricRow label="Next action" value={snapshot.next_action || "n/a"} pulseKey={snapshot.next_action || "n/a"} />
          <MetricRow label="Countdown" value={<CountdownValue seconds={snapshot.countdown_remaining} />} accent pulseKey={snapshot.countdown_remaining} />
          <MetricRow label="Rolls" value={snapshot.rolls_remaining ?? asNumber(sessionStatus?.rolls) ?? "n/a"} pulseKey={snapshot.rolls_remaining ?? asNumber(sessionStatus?.rolls) ?? "n/a"} />
          <MetricRow label="Claim" value={getClaimLabel(sessionStatus)} pulseKey={getClaimLabel(sessionStatus)} />
          <MetricRow label="Power" value={getPowerLabel(sessionStatus)} pulseKey={getPowerLabel(sessionStatus)} />
          <MetricRow label="$daily" value={sessionStatus?.daily_available ? "Available" : fmtMinutes(asNumber(sessionStatus?.daily_reset_min))} pulseKey={sessionStatus?.daily_available ? "available" : sessionStatus?.daily_reset_min ?? "n/a"} />
          <MetricRow label="$rt" value={sessionStatus?.rt_available ? "Available" : fmtMinutes(asNumber(sessionStatus?.rt_reset_min))} pulseKey={sessionStatus?.rt_available ? "available" : sessionStatus?.rt_reset_min ?? "n/a"} />
          <MetricRow label="$dk" value={sessionStatus?.dk_ready ? "Available" : fmtMinutes(asNumber(sessionStatus?.dk_reset_min))} pulseKey={sessionStatus?.dk_ready ? "available" : sessionStatus?.dk_reset_min ?? "n/a"} />
        </div>
      </DashboardPanel>

      <DashboardPanel title="Wishlist Status" order={2}>
        <div className="metrics-grid">
          <MetricRow label="$wl" value={`${asNumber((wishlistSource as Record<string, unknown>).wl_used) ?? regularWishes.length}/${asNumber((wishlistSource as Record<string, unknown>).wl_total) ?? regularWishes.length}`} pulseKey={`${(wishlistSource as Record<string, unknown>).wl_used ?? regularWishes.length}-${(wishlistSource as Record<string, unknown>).wl_total ?? regularWishes.length}`} />
          <MetricRow label="$sw" value={`${asNumber((wishlistSource as Record<string, unknown>).sw_used) ?? starWishes.length}/${asNumber((wishlistSource as Record<string, unknown>).sw_total) ?? starWishes.length}`} pulseKey={`${(wishlistSource as Record<string, unknown>).sw_used ?? starWishes.length}-${(wishlistSource as Record<string, unknown>).sw_total ?? starWishes.length}`} />
        </div>
        <div className="stack-block">
          <strong>Star wishes</strong>
          <p>{starWishes.length > 0 ? starWishes.join(", ") : "None"}</p>
        </div>
        <div className="stack-block">
          <strong>Regular wishes</strong>
          <ol className="data-list compact-list">
            {regularWishes.length > 0 ? regularWishes.map((item) => <li key={item}>{item}</li>) : <li>None</li>}
          </ol>
        </div>
      </DashboardPanel>

      <DashboardPanel title="Best Candidate" order={3}>
        <AnimatePresence mode="wait" initial={false}>
          {snapshot.best_candidate ? (
            <motion.div key={candidateKey} variants={buildScaleIn(reduced)} initial="hidden" animate="visible" exit="exit">
              <div className="metrics-grid">
                <MetricRow label="Character" value={asString(snapshot.best_candidate.name) || "Unknown"} pulseKey={asString(snapshot.best_candidate.name) || "Unknown"} />
                <MetricRow label="Series" value={asString(snapshot.best_candidate.series) || "n/a"} pulseKey={asString(snapshot.best_candidate.series) || "n/a"} />
                <MetricRow label="Kakera" value={String(snapshot.best_candidate.kakera ?? "?")} pulseKey={String(snapshot.best_candidate.kakera ?? "?")} />
                <MetricRow label="Status" value={asString(snapshot.best_candidate.status) || "n/a"} pulseKey={asString(snapshot.best_candidate.status) || "n/a"} />
                <MetricRow label="Image" value={asString(snapshot.best_candidate.image_url) || "n/a"} pulseKey={asString(snapshot.best_candidate.image_url) || "n/a"} />
              </div>
            </motion.div>
          ) : (
            <motion.p key="none" variants={buildFadeIn(reduced)} initial="hidden" animate="visible" exit="exit">No candidate yet.</motion.p>
          )}
        </AnimatePresence>
      </DashboardPanel>

      <DashboardPanel title="Latest Rolls" className="span-2" order={4}>
        {rolls.length > 0 ? (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Character</th>
                  <th>Series</th>
                  <th>Kakera</th>
                  <th>Keys</th>
                </tr>
              </thead>
              <AnimatedTableBody>
                {rolls.map((roll, index) => <RollRowView key={`${roll.name ?? "roll"}-${roll.series ?? "series"}-${index}`} roll={roll} index={index} />)}
              </AnimatedTableBody>
            </table>
          </div>
        ) : (
          <motion.p variants={buildFadeIn(reduced)} initial="hidden" animate="visible">No rolls yet.</motion.p>
        )}
      </DashboardPanel>

      <DashboardPanel title="Others' Rolls" className="span-2" order={5}>
        {others.length > 0 ? (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Roller</th>
                  <th>Character</th>
                  <th>Series</th>
                  <th>Kakera</th>
                </tr>
              </thead>
              <AnimatedTableBody>
                {others.map((roll, index) => <OtherRollRowView key={`${roll.roller ?? "other"}-${roll.name ?? "roll"}-${index}`} roll={roll} index={index} />)}
              </AnimatedTableBody>
            </table>
          </div>
        ) : (
          <motion.p variants={buildFadeIn(reduced)} initial="hidden" animate="visible">No other rolls yet.</motion.p>
        )}
      </DashboardPanel>

      <DashboardPanel title="Summary" order={6}>
        <motion.div animate={summaryControls}>
          <div className="metrics-grid">
            <MetricRow label="Rolls total" value={String(snapshot.summary?.rolls_total ?? "n/a")} pulseKey={String(snapshot.summary?.rolls_total ?? "n/a")} />
            <MetricRow label="Claims total" value={String(snapshot.summary?.claims_total ?? "n/a")} pulseKey={String(snapshot.summary?.claims_total ?? "n/a")} />
            <MetricRow label="Latest claim" value={String(snapshot.summary?.claims_latest ?? "n/a")} pulseKey={String(snapshot.summary?.claims_latest ?? "n/a")} />
            <MetricRow label="Kakera total" value={String(snapshot.summary?.kakera_total ?? "n/a")} pulseKey={String(snapshot.summary?.kakera_total ?? "n/a")} />
            <MetricRow label="React bonus" value={String(snapshot.summary?.reaction_kakera_total ?? "n/a")} pulseKey={String(snapshot.summary?.reaction_kakera_total ?? "n/a")} />
            <MetricRow label="Balance" value={String(snapshot.summary?.total_balance ?? sessionStatus?.total_balance ?? "n/a")} pulseKey={String(snapshot.summary?.total_balance ?? sessionStatus?.total_balance ?? "n/a")} />
          </div>
          <div className="stack-block">
            <strong>Predicted next session</strong>
            <AnimatePresence mode="wait" initial={false}>
              <motion.div key={`${snapshot.predicted?.predicted_at ?? "n/a"}-${predictedStatus ?? "none"}`} variants={buildFadeIn(reduced)} initial="hidden" animate="visible" exit="exit">
                <p>{snapshot.predicted?.predicted_at || "n/a"}</p>
                <p>{predictedStatus || "No prediction yet."}</p>
              </motion.div>
            </AnimatePresence>
          </div>
        </motion.div>
      </DashboardPanel>

      <DashboardPanel title="Ouro Status" order={7}>
        <motion.div animate={ouroControls}>
          <div className="metrics-grid">
            <MetricRow label="$oh" value={snapshot.oh_left != null ? `${snapshot.oh_left}${snapshot.oh_stored ? ` (+${snapshot.oh_stored})` : ""}` : "n/a"} pulseKey={`${snapshot.oh_left ?? "n"}-${snapshot.oh_stored ?? "n"}`} />
            <MetricRow label="$oc" value={snapshot.oc_left != null ? `${snapshot.oc_left}${snapshot.oc_stored ? ` (+${snapshot.oc_stored})` : ""}` : "n/a"} pulseKey={`${snapshot.oc_left ?? "n"}-${snapshot.oc_stored ?? "n"}`} />
            <MetricRow label="$oq" value={snapshot.oq_left != null ? `${snapshot.oq_left}${snapshot.oq_stored ? ` (+${snapshot.oq_stored})` : ""}` : "n/a"} pulseKey={`${snapshot.oq_left ?? "n"}-${snapshot.oq_stored ?? "n"}`} />
            <MetricRow label="Refill" value={snapshot.ouro_refill_min != null ? fmtMinutes(snapshot.ouro_refill_min) : "n/a"} pulseKey={snapshot.ouro_refill_min ?? "n/a"} />
            <MetricRow label="Ourospheres" value={snapshot.sphere_balance ?? "n/a"} pulseKey={snapshot.sphere_balance ?? "n/a"} />
          </div>
        </motion.div>
      </DashboardPanel>

      <DashboardPanel title="Operations" className="span-2" order={8}>
        <div className="button-row">
          <button className="primary" onClick={() => props.onAction("main", "start")}>Start Main</button>
          <button onClick={() => props.onAction(snapshot.active_mode || "main", "pause")}>Pause</button>
          <button onClick={() => props.onAction(snapshot.active_mode || "main", "resume")}>Resume</button>
          <button onClick={() => props.onAction(snapshot.active_mode || "main", "restart")}>Restart</button>
          <button className="danger" onClick={() => props.onAction(snapshot.active_mode || "main", "stop")}>Stop</button>
          <button className="danger ghost" onClick={props.onForceStop}>Force Stop</button>
          <button onClick={props.onClearQueue}>Clear Queue</button>
        </div>
        <div className="grid two-col">
          <section className="panel inset">
            <div className="panel-header"><h4>Queue</h4></div>
            <div className="inline-form">
              <select value={props.queueMode} onChange={(event) => props.setQueueMode(event.target.value)}>
                {["main", "oh", "oc", "oq"].map((mode) => (
                  <option key={mode} value={mode}>{mode.toUpperCase()}</option>
                ))}
              </select>
              <button onClick={props.onQueue}>Queue Run</button>
            </div>
            <ul className="data-list compact-list">
              {(snapshot.queue ?? []).map((item: QueueItem) => (
                <li key={item.id}>#{item.id} {item.mode.toUpperCase()} {item.action} [{item.status}]</li>
              ))}
              {(snapshot.queue ?? []).length === 0 && <li>No pending queue items.</li>}
            </ul>
          </section>
          <section className="panel inset">
            <div className="panel-header"><h4>One-Time Schedule</h4></div>
            <div className="inline-form">
              <select value={props.scheduleMode} onChange={(event) => props.setScheduleMode(event.target.value)}>
                {["main", "oh", "oc", "oq"].map((mode) => (
                  <option key={mode} value={mode}>{mode.toUpperCase()}</option>
                ))}
              </select>
              <input type="datetime-local" value={props.scheduleAt} onChange={(event) => props.setScheduleAt(event.target.value)} />
              <button onClick={props.onSchedule}>Schedule</button>
            </div>
          </section>
        </div>
      </DashboardPanel>
    </motion.section>
  );
}
