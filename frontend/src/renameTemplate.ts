import { DEFAULT_TEMPLATE } from './api/files'

/** The rename template last used (per browser), shared by the album dialog and bulk renames. */
const STORED = 'plekstender.renameTemplate'

export function storedTemplate(): string {
  try {
    return localStorage.getItem(STORED) || DEFAULT_TEMPLATE
  } catch {
    return DEFAULT_TEMPLATE
  }
}

export function storeTemplate(template: string) {
  try {
    localStorage.setItem(STORED, template)
  } catch {
    // private mode: the default next time
  }
}
