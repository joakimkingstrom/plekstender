/** The kinds of outside services, each its own page under Settings → Services. */
export const SERVICE_KINDS = [
  { slug: 'gigs', title: 'Gigs', about: 'Upcoming gigs near people (Gigs → Upcoming), and importing the ones they went to.' },
  { slug: 'playlists', title: 'Playlists', about: 'Importing playlists from Spotify and Tidal links.' },
  { slug: 'artwork', title: 'Artwork', about: 'Artist logos and album covers for Library → Artwork.' },
  { slug: 'files', title: 'Identifying files', about: 'Recognising music files by how they sound (Settings → Music files → Unknown files).' },
] as const

export type ServiceKind = (typeof SERVICE_KINDS)[number]['slug']

/** The parts of Settings → Export & import, each its own page. Artist logos are for server owners. */
export const EXPORT_PARTS = [
  { slug: 'history', title: 'History', about: 'Bring your plays in from Last.fm or ListenBrainz, or take them all out in one file.', owners: false },
  { slug: 'library', title: 'Library', about: 'Your library with your ratings and play counts, to keep or to use elsewhere.', owners: false },
  { slug: 'gigs', title: 'Gigs', about: 'The gigs you’ve been to: in from setlist.fm, a spreadsheet or a calendar, and out again.', owners: false },
  { slug: 'logos', title: 'Artist logos', about: 'Logo packs: the logos you’ve picked, to move to another server or share.', owners: true },
] as const

export type ExportPart = (typeof EXPORT_PARTS)[number]['slug']
