/** The kinds of outside services, each its own page under Settings → Services. */
export const SERVICE_KINDS = [
  { slug: 'gigs', title: 'Gigs', about: 'Upcoming gigs near people (Gigs → Upcoming), and importing the ones they went to.' },
  { slug: 'history', title: 'History', about: 'Importing listening history.' },
  { slug: 'playlists', title: 'Playlists', about: 'Importing playlists from Spotify links.' },
  { slug: 'artwork', title: 'Artwork', about: 'Artist logos for the Artwork tab.' },
  { slug: 'files', title: 'Identifying files', about: 'Recognising music files by how they sound (Settings → Music files → Unknown files).' },
] as const

export type ServiceKind = (typeof SERVICE_KINDS)[number]['slug']
