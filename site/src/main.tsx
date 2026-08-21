/**
 * Client entry — styles, plus the one behaviour this site actually needs.
 *
 * NOT `hydrateRoot`. Every page is prerendered to real HTML and none of it is
 * interactive, so hydration would download React and TanStack Router to
 * re-render markup already on screen. See src/platform-links.ts for the
 * measurement that settled it.
 */
import './styles.css'
import { wirePlatformLinks } from './platform-links'

void wirePlatformLinks()
