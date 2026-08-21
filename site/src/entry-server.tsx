/**
 * Server entry — renders one route to an HTML string at BUILD time.
 *
 * WHY A MARKETING SPA MUST PRERENDER. `api_bridge.py` serves this directory as
 * static files; there is no Node runtime in production and never will be. A
 * client-rendered React app would therefore ship `<div id="root"></div>` to
 * every crawler and every first paint — strictly WORSE for search than the
 * hand-written HTML it replaces, on a site whose stated goal includes being
 * crawlable. Prerendering is what makes "rebuild it in React" and "the homepage
 * is indexable" both true at once.
 *
 * Each route gets its own router instance against a memory history. Sharing one
 * would carry the first render's resolved state into the next and emit the same
 * markup under three filenames.
 */
import { StrictMode } from 'react'
import { renderToString } from 'react-dom/server'
import { RouterProvider } from '@tanstack/react-router'
import { makeRouter } from './router'

export async function render(pathname: string): Promise<string> {
  const router = makeRouter(pathname)
  await router.load()
  return renderToString(
    <StrictMode>
      <RouterProvider router={router} />
    </StrictMode>,
  )
}
