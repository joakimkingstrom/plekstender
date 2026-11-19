"""Easy-interface keys mutagen lacks for MP4 files, registered the way Picard writes them
(iTunes freeform atoms), so every format reads and writes the same field names."""

from __future__ import annotations

from mutagen.easymp4 import EasyMP4Tags

FREEFORM = {
    "musicbrainz_releasegroupid": "MusicBrainz Release Group Id",
    "musicbrainz_releasetrackid": "MusicBrainz Release Track Id",
    "originaldate": "ORIGINALDATE",
}

for _key, _name in FREEFORM.items():
    if _key not in EasyMP4Tags.Get:
        EasyMP4Tags.RegisterFreeformKey(_key, _name)
