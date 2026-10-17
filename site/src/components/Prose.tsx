/**
 * The prose blocks the policy pages are written in: /privacy and /terms.
 *
 * One copy, because the two documents are read side by side and a reader
 * should not meet two typographies for one kind of sentence. They render
 * classes only, never a style attribute: www serves these articles under a
 * policy that allows no inline styles (app/routes/legal.js).
 */
export function P({ children }: { children: React.ReactNode }) {
  return <p className="mt-4 leading-relaxed text-ink-2">{children}</p>
}

export function H({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="mt-12 font-[family-name:var(--font-brand)] text-2xl font-bold">
      {children}
    </h2>
  )
}

export function L({ items }: { items: React.ReactNode[] }) {
  return (
    <ul className="mt-4 space-y-2.5">
      {items.map((it, i) => (
        <li key={i} className="flex gap-3 leading-relaxed text-ink-2">
          <span aria-hidden="true" className="mt-2 size-1.5 shrink-0 rounded-full bg-accent" />
          <span>{it}</span>
        </li>
      ))}
    </ul>
  )
}

/** Sections where the honest answer is "this does not exist yet". */
export function Gap({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mt-5 rounded-lg border border-warn/40 bg-warn/5 p-4">
      <p className="text-sm font-semibold text-warn">{title}</p>
      <div className="mt-1.5 text-sm leading-relaxed text-ink-2">{children}</div>
    </div>
  )
}
