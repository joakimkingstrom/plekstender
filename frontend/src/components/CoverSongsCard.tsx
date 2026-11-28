import { useState } from "react";
import { errorMessage } from "../api/client";
import { useCoverSongsSettings, useSetCoverSongs } from "../api/coverSongs";
import { formatCount } from "../format";
import { Tooltip } from "./Tooltip";
import { Button, Card, Notice, Spinner } from "./ui";

/** About how long SecondHandSongs takes for the rest: its requests (about two an artist and
 * one a cover) at the hour's pace, within the day's. */
function timeLeft(c: {
  requests_left: number;
  per_hour: number;
  per_day: number;
}): string | null {
  if (c.requests_left <= 0) return null;
  const hours = Math.ceil(
    c.requests_left / Math.max(1, Math.min(c.per_hour, c.per_day / 24)),
  );
  if (hours < 48)
    return `About ${formatCount(hours)} more ${hours === 1 ? "hour" : "hours"} on SecondHandSongs`;
  return `About ${formatCount(Math.ceil(hours / 24))} more days on SecondHandSongs, at up to ${formatCount(c.per_day)} requests a day`;
}

const SHS = "https://secondhandsongs.com";

/** Cover songs (admins): turn the lookups on, choose SecondHandSongs and/or MusicBrainz, and
 * optionally give SecondHandSongs' API key. */
export function CoverSongsCard() {
  const current = useCoverSongsSettings();
  const save = useSetCoverSongs();
  const [key, setKey] = useState("");
  const [saved, setSaved] = useState(false);
  const validKey = /^[A-Za-z0-9-]{20,64}$/.test(key.trim());
  if (current.isPending) return <Spinner />;
  if (current.isError)
    return <Notice tone="error">{errorMessage(current.error)}</Notice>;
  const c = current.data;
  const settings = {
    enabled: c.enabled,
    secondhandsongs: c.secondhandsongs,
    musicbrainz: c.musicbrainz,
    song_versions: c.song_versions,
    remove_key: false,
  };
  const left = c.enabled && c.secondhandsongs ? timeLeft(c) : null;
  return (
    <Card title="Cover songs">
      <div className="space-y-3 text-sm">
        <p className="text-muted">
          Finds out which songs in the libraries are covers, and whose song they
          are. Song rows then say “Cover of …”, and server owners can write it
          into the files (Settings → Music files). The worker looks songs up a
          few at a time: the songs people play and rate first.
        </p>
        <label className="flex items-start gap-2">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={c.enabled}
            onChange={(e) =>
              save.mutate({ ...settings, enabled: e.target.checked })
            }
          />
          <span className="font-medium">Look up cover songs</span>
        </label>
        <fieldset className="space-y-2 pl-6" disabled={!c.enabled}>
          <legend className="sr-only">Where</legend>
          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={c.secondhandsongs}
              onChange={(e) =>
                save.mutate({ ...settings, secondhandsongs: e.target.checked })
              }
            />
            <span>
              <a
                href={SHS}
                target="_blank"
                rel="noreferrer"
                className="font-medium text-accent underline"
              >
                SecondHandSongs
              </a>
              <span className="block text-xs text-muted">
                Knows the most covers. Song titles and artist names are sent to
                it, within its rate limits. Its terms ask for permission before
                using its data beyond private use.
              </span>
            </span>
          </label>
          <label className="flex items-start gap-2 pl-6">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={c.song_versions}
              disabled={!c.secondhandsongs}
              onChange={(e) =>
                save.mutate({ ...settings, song_versions: e.target.checked })
              }
            />
            <span>
              <span className="flex items-center gap-1">
                <span className="font-medium">Use songs’ other versions</span>
                <Tooltip
                  content={
                    <>
                      For example, with three versions of “Hurt” in the library
                      (Johnny Cash’s, Nine Inch Nails’ original, and
                      Sevendust’s), looking them all up takes 4 requests
                      instead of 8: Johnny Cash’s list and his cover’s page,
                      then the song’s page settles the other two, so Nine Inch
                      Nails and Sevendust aren’t asked about at all.
                    </>
                  }
                >
                  {/* A button, so clicking it doesn't tick the box. */}
                  <button
                    type="button"
                    aria-label="An example of the requests it saves"
                    className="flex size-4 items-center justify-center rounded-full border border-current text-[10px] font-semibold leading-none text-muted hover:text-text"
                  >
                    i
                  </button>
                </Tooltip>
              </span>
              <span className="block text-xs text-muted">
                A cover’s song on SecondHandSongs lists every version of it, so
                one more request settles the library’s other versions of it
                too. Only asked for when two or more of them are waiting, so it
                saves requests in libraries with several versions of songs.
              </span>
            </span>
          </label>
          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={c.musicbrainz}
              onChange={(e) =>
                save.mutate({ ...settings, musicbrainz: e.target.checked })
              }
            />
            <span>
              <span className="font-medium">MusicBrainz</span>
              <span className="block text-xs text-muted">
                For songs SecondHandSongs doesn’t know (or all of them, without
                it): needs the recording’s MusicBrainz ID in the files (as
                Picard tags them) or in Plex.
              </span>
            </span>
          </label>
        </fieldset>
        {c.songs > 0 && (
          <p className="text-xs text-muted">
            {formatCount(c.checked)} of {formatCount(c.songs)} songs looked up
            {(c.by_versions ?? 0) > 0 &&
              ` (${formatCount(c.by_versions ?? 0)} through another version of the same song)`}
            , {formatCount(c.covers)} covers found
            {left && `. ${left}`}
            .
          </p>
        )}
        {c.secondhandsongs && (
          <>
            <form
              className="flex flex-wrap items-end gap-2 border-t border-border pt-3"
              onSubmit={(e) => {
                e.preventDefault();
                if (validKey)
                  save.mutate(
                    { ...settings, api_key: key.trim() },
                    {
                      onSuccess: () => {
                        setKey("");
                        setSaved(true);
                      },
                    },
                  );
              }}
            >
              <label className="min-w-0 flex-1">
                <span className="mb-1 block font-medium">
                  SecondHandSongs API key{" "}
                  <span className="font-normal text-muted">
                    (optional: higher limits)
                  </span>
                </span>
                <input
                  type="password"
                  value={key}
                  autoComplete="off"
                  spellCheck={false}
                  placeholder={c.api_key ? "A key is saved" : ""}
                  onChange={(e) => {
                    setSaved(false);
                    setKey(e.target.value);
                  }}
                  className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 font-mono text-sm"
                />
              </label>
              <Button
                type="submit"
                variant="primary"
                disabled={!validKey}
                busy={save.isPending && Boolean(save.variables?.api_key)}
              >
                Check and save
              </Button>
              {c.api_key && (
                <Button
                  variant="ghost"
                  busy={save.isPending && Boolean(save.variables?.remove_key)}
                  onClick={() => save.mutate({ ...settings, remove_key: true })}
                >
                  Remove
                </Button>
              )}
            </form>
            <p className="text-xs text-muted">
              Request a key from SecondHandSongs (see{" "}
              <a
                href={`${SHS}/page/API/Access`}
                target="_blank"
                rel="noreferrer"
                className="text-accent underline"
              >
                API access
              </a>
              ).
            </p>
          </>
        )}
        {saved && <Notice>Saved. SecondHandSongs accepted the key.</Notice>}
        {save.isError && (
          <Notice tone="error">{errorMessage(save.error)}</Notice>
        )}
      </div>
    </Card>
  );
}
