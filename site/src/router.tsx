/**
 * Router factory.
 *
 * A FACTORY, NOT A SINGLETON, because the same tree is mounted two ways: the
 * browser hydrates it against real history, and `prerender.js` renders it once
 * per route against a memory history to emit static HTML. A module-level
 * singleton would carry one render's resolved state into the next and quietly
 * emit the homepage's markup under three different filenames.
 */
import { createRouter, createMemoryHistory } from '@tanstack/react-router'
import { routeTree } from './routeTree.gen'

export function makeRouter(pathname?: string) {
  return createRouter({
    routeTree,
    defaultPreload: 'intent',
    // Static hosting has no server to answer an unknown path with a rewrite;
    // api_bridge's StaticFiles returns its own 404. Scrolling to top on
    // navigation is the behaviour a multi-page marketing site is expected to
    // have and the one a SPA router removes by default.
    scrollRestoration: true,
    ...(pathname
      ? { history: createMemoryHistory({ initialEntries: [pathname] }) }
      : {}),
  })
}

declare module '@tanstack/react-router' {
  interface Register {
    router: ReturnType<typeof makeRouter>
  }
}
