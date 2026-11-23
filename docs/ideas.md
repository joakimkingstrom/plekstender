# Ideas

Things that might come next, and questions still open. Nothing here is promised.

## Features

- **Shareable Rewind cards:** each story card as an image, in the style of the period's covers,
  to save or share. Drawing each card on a canvas (covers fetched through the image proxy) is its
  own piece of work.
- **Email or push notifications** (the app is installable) for the serious problems, chosen per person.
- **Achievement badges** drawn for each achievement. Today they share a record-label badge in the
  accent colour, with a symbol per kind.
- **Album covers:** Tidal's animated covers (its artworks include video ones), and saving
  `cover.jpg` into album folders.
- **More gig sources:** JamBase's per-artist IDs (`/v3/artists/id/musicbrainz:{mbid}`, free on
  every plan but counted) if name matching turns out to miss performers; Bandsintown, if they
  approve an app ID.
- **Sharing playlists** between people on the same server (e.g. "copy definition").

## Writing imported plays back to Plex

Plekstender's own history makes this optional: the rating queues and stats already count
imported plays. If Plex's own play counts should include them too:
- **API mode:** `/:/scrobble` per play. Raises play counts, but every play gets the current
  timestamp, which distorts "recently played".
- **Database mode:** write `metadata_item_views` / `metadata_item_settings` in Plex's SQLite
  database with the real timestamps. Requires Plex to be stopped, a backup first and the database
  file mounted. Unsupported by Plex, so opt-in with explicit warnings.
- Either way, skip plays Plex already has (matched listens with `source = plex` are the overlap).

## Open questions

- **Logo packs:** fanart.tv images come with their own terms. Exports keep each image's
  `source`, so people can decide what they share.
- **Plex history timestamps:** is `viewedAt` really the end of the track, and does the account
  mapping hold for shared users? (`backend/scripts/spikes/plex_probe.py --history`)
