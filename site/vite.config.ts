/**
 * Build config for the RUNECLAW public site.
 *
 * OUTPUT GOES STRAIGHT INTO ../website, AND THAT IS DELIBERATE.
 *
 * `api_bridge.py:1066` mounts `StaticFiles(directory=<repo>/website, html=True)`
 * at `/`. There is no build step on the server and no nginx root to repoint —
 * whatever sits in `website/` in the checkout IS the live site. Emitting
 * anywhere else would mean the deploy needs a new coordinated step, and this
 * repo's most expensive outages have all been deploy-coordination failures
 * ("a dead bot cannot look like a live one" in CLAUDE.md is about exactly that).
 * Building into the served directory keeps the deploy story at `git pull`.
 *
 * `emptyOutDir: false` IS LOAD-BEARING. `website/` also holds things this build
 * does not produce and must never delete:
 *   - archive/hackathon/  — the frozen Bitget submission
 *   - google*.html        — Search Console verification; losing it silently
 *                           un-verifies the domain and nothing reports it
 *   - media the site references
 * Vite's default would wipe all of them on the first build.
 */
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { tanstackRouter } from '@tanstack/router-plugin/vite'
import { execFileSync } from 'node:child_process'
import { rmSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))

/**
 * Regenerate the design tokens from the platform stylesheet before every build.
 *
 * Not a manual step, because a manual step is how `website/index.html` came to
 * carry a comment claiming it mirrored `app/public/styles.css` while its brand
 * accent had drifted to a different colour. If the platform repaints, the site
 * repaints on the next build or the build fails — there is no third outcome
 * where it quietly ships the old palette.
 */
/**
 * Clear ONLY `website/assets/` before a client build.
 *
 * `emptyOutDir` is false because `website/` also holds the hackathon archive,
 * the Search Console verification file and the media — vite would delete all of
 * them. The cost of that is that hashed bundles ACCUMULATE: three builds left
 * three JS and three CSS files in there, including a dead 281KB bundle from
 * before runtime hydration was dropped. Nothing served references it, so
 * nothing breaks and nothing reports it — the directory just grows, and a
 * byte-size budget measured across it reads the corpses as current weight.
 *
 * `assets/` is exclusively build output, so it alone is safe to wipe.
 */
function cleanAssets() {
  return {
    name: 'runeclaw-clean-assets',
    enforce: 'pre' as const,
    apply: 'build' as const,
    buildStart() {
      if (process.env.VITE_SSR_BUILD) return
      rmSync(join(HERE, '..', 'website', 'assets'), { recursive: true, force: true })
    },
  }
}

function derivedTokens() {
  return {
    name: 'runeclaw-derived-tokens',
    enforce: 'pre' as const,
    buildStart() {
      // Inherit stdio so the failure — and the accent it derived — is visible in
      // build output rather than swallowed into a plugin error.
      execFileSync(process.execPath, [join(HERE, 'scripts', 'tokens.mjs')],
        { stdio: 'inherit' })
    },
  }
}

export default defineConfig(({ isSsrBuild }) => ({
  plugins: [
    ...(isSsrBuild ? [] : [cleanAssets()]),
    derivedTokens(),
    // Must precede the react plugin — it generates routeTree.gen.ts that the
    // app imports, so it has to run before the JSX transform sees the import.
    tanstackRouter({ target: 'react', autoCodeSplitting: false }),
    react(),
    tailwindcss(),
  ],
  build: isSsrBuild
    ? {
        // The SSR bundle is a BUILD ARTEFACT that prerender.js imports by path;
        // it is never served. So it gets a stable, unhashed name — the default
        // `assets/entry-server.<hash>.js` means prerender.js cannot import it
        // without globbing for a filename that changes on every build.
        outDir: join(HERE, '.ssr'),
        emptyOutDir: true,
        rollupOptions: {
          output: { entryFileNames: 'entry-server.js', format: 'es' },
        },
      }
    : {
        outDir: join(HERE, '..', 'website'),
        emptyOutDir: false,      // see above — this deletes the archive if true
        assetsDir: 'assets',
        chunkSizeWarningLimit: 200,
        rollupOptions: {
          output: {
            entryFileNames: 'assets/[name].[hash].js',
            chunkFileNames: 'assets/[name].[hash].js',
            assetFileNames: 'assets/[name].[hash][extname]',
          },
        },
      },
}))
