/**
 * The one live reading on this site: what the engine's posture ACTUALLY is.
 *
 * The homepage promises "paper is the default and live trading stays off until
 * you switch it on." That is a claim about a running system, printed into
 * static HTML at build time — the same shape as `$72,669`, which sat on the old
 * site as a current price for three months. This reads the running system and
 * says what it finds.
 *
 * SAME-ORIGIN, AND THAT IS NOT AN IMPLEMENTATION DETAIL.
 *
 * `api_bridge.py` serves this site (`StaticFiles` mounted at `/`) and also
 * serves `/health` on that same origin, with no token. The trading platform is
 * a DIFFERENT origin — `app/` behind WEBSITE_URL — and it sets no CORS headers
 * at all, while api_bridge's own CORS defaults to an empty allow-list. So a
 * widget reading the platform would work in a dev tab and be blocked by the
 * browser in production, which is the worst possible place to discover it.
 * `/health` is the reading that genuinely works from here.
 *
 * EVERY FAILURE PATH RENDERS "COULD NOT READ", NEVER A VALUE.
 *
 * The tempting default — assume simulation when the fetch fails — is the exact
 * defect this repository is organised around: it would print the reassuring
 * answer precisely when nothing was measured, on the claim that matters most.
 * There are three outcomes here and they are all distinct: simulation on, live
 * armed, and nobody could tell. `/health` itself already works this way, which
 * is why it is worth reading: it OMITS engine-derived fields rather than
 * defaulting them, and reports `trading_gate_unknown` rather than an empty
 * reason that would read as "trading is fine".
 *
 * IT ALSO RENDERS NOTHING AT ALL WHEN THERE IS NO PLACEHOLDER. The block is
 * opt-in per page: no element, no fetch, no script cost.
 */

type Health = {
  status?: unknown
  engine?: unknown
  simulation_mode?: unknown
  circuit_breaker_active?: unknown
  trading_blocked_by?: unknown
  trading_gate_unknown?: unknown
}

/** What we can say about the engine's posture, or that we cannot say it. */
export type Posture =
  | { readonly kind: 'paper'; readonly gate: string | null }
  | { readonly kind: 'live'; readonly gate: string | null }
  | { readonly kind: 'unknown'; readonly why: string }

/**
 * Read a posture out of a /health body.
 *
 * Pure, and exported so the tests can drive every branch without a server.
 * `simulation_mode` must be a real boolean: absent, null, or a string is
 * `unknown`, because a missing field is not a false one.
 */
export function postureOf(d: Health | null | undefined): Posture {
  if (!d || typeof d !== 'object') return { kind: 'unknown', why: 'no answer' }
  if (d.engine !== 'ready') return { kind: 'unknown', why: 'engine absent' }
  if (typeof d.simulation_mode !== 'boolean') {
    return { kind: 'unknown', why: 'not reported' }
  }
  // `trading_gate_unknown` means the gate could not be read. Passing the empty
  // string through as "nothing is blocking" would turn an unread gate into an
  // all-clear, which is the same defect one field over.
  const gate =
    d.trading_gate_unknown === true
      ? null
      : typeof d.trading_blocked_by === 'string' && d.trading_blocked_by.length > 0
        ? d.trading_blocked_by
        : ''
  return { kind: d.simulation_mode ? 'paper' : 'live', gate }
}

/** The sentence a reader sees, and the accent it earns. */
export function renderPosture(p: Posture): { text: string; tone: 'up' | 'warn' | 'muted' } {
  if (p.kind === 'unknown') {
    // MUTED, NOT GREEN. Colour is a claim: a green chip over "could not read"
    // says "fine" louder than the words say "unknown".
    return { text: `Engine posture unavailable right now (${p.why}).`, tone: 'muted' }
  }
  const gate =
    p.gate === null
      ? ' The trade gate could not be read.'
      : p.gate === ''
        ? ''
        : ` Trading is currently held: ${p.gate}.`
  if (p.kind === 'paper') {
    return { text: `Simulation mode is ON — the engine is paper-trading.${gate}`, tone: 'up' }
  }
  return { text: `Simulation mode is OFF — live trading is armed.${gate}`, tone: 'warn' }
}

const TONE: Record<'up' | 'warn' | 'muted', string> = {
  up: 'text-up',
  warn: 'text-warn',
  muted: 'text-ink-3',
}

/**
 * Fill `#live-posture`, if the page has one.
 *
 * Deliberately not a framework component: this site prerenders to real HTML
 * and ships ~1KB of script on purpose (see platform-links.ts). One fetch and
 * one textContent write is the whole budget this deserves.
 */
export async function wireLivePosture(): Promise<void> {
  const host = document.getElementById('live-posture')
  if (!host) return
  let posture: Posture = { kind: 'unknown', why: 'no answer' }
  try {
    const r = await fetch('/health', {
      headers: { Accept: 'application/json' },
      signal: AbortSignal.timeout(6000),
    })
    // A non-200 is not an absent engine and not a paper one. It is a failed
    // read, and it says so.
    posture = r.ok ? postureOf((await r.json()) as Health) : { kind: 'unknown', why: `HTTP ${r.status}` }
  } catch {
    posture = { kind: 'unknown', why: 'unreachable' }
  }
  const { text, tone } = renderPosture(posture)
  const dot = host.querySelector<HTMLElement>('[data-live-dot]')
  const label = host.querySelector<HTMLElement>('[data-live-text]')
  if (label) label.textContent = text
  if (dot) {
    dot.className = dot.className.replace(/\bbg-\S+/g, '').trim()
      + (posture.kind === 'paper' ? ' bg-up' : posture.kind === 'live' ? ' bg-warn' : ' bg-ink-3')
  }
  host.classList.remove('opacity-0')
  if (label) label.className = label.className.replace(/\btext-\S+/g, '').trim() + ' ' + TONE[tone]
}
