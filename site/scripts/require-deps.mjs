/**
 * Fail the build with a sentence instead of `vite: not found`.
 *
 * `scripts/preflight.py` runs CI's steps but SKIPS anything whose name matches
 * "install" — correct, because a preflight that installs a toolchain behind
 * your back is not a preflight. The consequence is that the site build is the
 * one local gate with an unstated prerequisite, and when it is unmet the
 * failure is a bare `sh: 1: vite: not found` after 0.1 seconds: no cause, no
 * fix, and easy to read as the build itself being broken.
 *
 * This is the repo's own rule about unreadable states, applied to a build log.
 */
import { existsSync } from 'node:fs'

if (!existsSync(new URL('../node_modules/vite/package.json', import.meta.url))) {
  console.error(
    '\nsite/: dependencies are not installed, so there is nothing to build.\n' +
    '  cd site && npm ci\n\n' +
    'CI installs them in its own step; preflight deliberately skips install\n' +
    'steps, so this is the one gate you have to satisfy by hand once.\n')
  process.exit(1)
}
