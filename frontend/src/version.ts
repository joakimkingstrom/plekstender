/** The version this page was built as (set when the image is built; empty in development). */
export const BUILD_VERSION: string | null = import.meta.env.VITE_APP_VERSION || null

/** Where the code lives, for linking a build to its commit. */
export const SOURCE_URL = 'https://github.com/joakimkingstrom/plekstender'

/** The server runs a different build from the one this page came with: it was updated. */
export function wasUpdated(pageVersion: string | null, serverVersion: string | undefined): boolean {
  return Boolean(pageVersion && serverVersion && pageVersion !== serverVersion && !serverVersion.endsWith('-dev'))
}

/** A build from the staging branch (published for trying changes before they go into main). */
export function isStaging(version: string | null | undefined): boolean {
  return Boolean(version?.endsWith('-staging'))
}
