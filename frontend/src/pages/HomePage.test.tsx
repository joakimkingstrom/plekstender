import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { HomePage } from "./HomePage";

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request | string) => {
      const url = new URL(
        typeof input === "string" ? input : input.url,
        "http://localhost",
      );
      const json = (body: unknown, status = 200) =>
        new Response(JSON.stringify(body), {
          status,
          headers: { "content-type": "application/json" },
        });
      switch (url.pathname) {
        case "/api/auth/me":
          return json({
            id: 1,
            username: "me",
            email: null,
            thumb_url: null,
            is_admin: true,
          });
        case "/api/servers":
          return json([
            {
              id: 1,
              machine_identifier: "m",
              name: "Home",
              is_owner: true,
              approved: true,
              connection_kind: "local",
              music_section_keys: ["3"],
            },
          ]);
        case "/api/servers/1/sync":
          return json({
            active: null,
            last: null,
            last_sync_at: null,
            counts: {
              artists: 1,
              albums: 1,
              tracks: 2,
              rated_tracks: 0,
              plays: 0,
            },
            incremental_minutes: 30,
            full_days: 7,
          });
        case "/api/health":
          return json({
            status: "ok",
            database: true,
            worker: true,
            version: "1.2.3",
          });
        case "/api/status/connections":
          return json([
            {
              name: "Plex: Home",
              group: "yours",
              state: "ok",
              detail: null,
              link: "/settings",
            },
            {
              name: "Koito",
              group: "yours",
              state: "error",
              detail: "Koito rejected the token.",
              link: "/settings/scrobbling",
            },
            {
              name: "JamBase",
              group: "instance",
              state: "warning",
              detail: "765 of 900 requests this month",
              link: "/settings/services/gigs",
            },
            {
              name: "SearXNG (image search)",
              group: "instance",
              state: "ok",
              detail: null,
              link: null,
            },
            {
              name: "Ticketmaster",
              group: "instance",
              state: "off",
              detail: "Not set up",
              link: "/settings/services/gigs",
            },
            {
              name: "Spotify",
              group: "instance",
              state: "off",
              detail: "Not set up",
              link: "/settings/services/playlists",
            },
          ]);
        case "/api/stats/overview": {
          const week = url.searchParams.get("range") === "7d";
          return json({
            period: {},
            totals: { listens: week ? 120 : 480, listening_ms: 30 * 3_600_000, artists: 64, tracks: 300 },
            previous: { listens: 100, listening_ms: 0, artists: 0, tracks: 0 },
            new_artists: 3,
            top_artists: [
              { name: "Radiohead", artist: null, listens: 40, listening_ms: 0, previous_rank: 2, rating_key: 100, thumb: null },
              { name: "Portishead", artist: null, listens: 20, listening_ms: 0, previous_rank: null, rating_key: null, thumb: null },
            ],
            top_albums: [
              { name: "OK Computer", artist: "Radiohead", listens: 25, listening_ms: 0, previous_rank: 1, rating_key: 200, thumb: null },
            ],
            top_tracks: [],
            current_streak: 6,
            longest_streak: 41,
            first_listen_at: "2020-01-01T00:00:00Z",
          });
        }
        case "/api/stats/activity":
          return json({ bucket: "day", points: [{ start: "2026-11-19", listens: 12 }, { start: "2026-11-20", listens: 30 }], heatmap: [] });
        case "/api/servers/1/library/recent":
          return json([{ rating_key: 202, title: "Dummy", subtitle: "Portishead", thumb: null, added_at: "2026-11-20T10:00:00Z" }]);
        case "/api/servers/1/ratings":
          return json({
            items: [{ rating_key: 300, kind: "track", title: "Paranoid Android", subtitle: "Radiohead · OK Computer", thumb: null, user_rating: 9, rated_at: null, link_key: 200 }],
            total: 1,
          });
        case "/api/live/settings":
          return json({ place_name: null, latitude: null, longitude: null });
        default:
          return json({});
      }
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("lists every connection on the Status card", async () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(await screen.findByText("Koito")).toBeTruthy();
  expect(screen.getByText("Koito rejected the token.")).toBeTruthy();
  expect(screen.getByText("765 of 900 requests this month")).toBeTruthy();
  expect(screen.getByText("SearXNG (image search)")).toBeTruthy();
  expect(screen.getByText("Not set up: Ticketmaster, Spotify")).toBeTruthy();
  const fixes = screen
    .getAllByRole("link", { name: "Fix" })
    .map((a) => a.getAttribute("href"));
  expect(fixes).toEqual(["/settings/scrobbling", "/settings/services/gigs"]);
});

it("shows your listening, top artists and albums, recent albums, gigs and ratings", async () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(await screen.findByText("120")).toBeTruthy();
  expect(screen.getByText("+20% on the week before")).toBeTruthy();
  expect(screen.getByText("6 days")).toBeTruthy();
  expect(screen.getByText("Longest 41")).toBeTruthy();
  expect(screen.getByRole("link", { name: "Radiohead: 40 listens" }).getAttribute("href")).toBe("/library/artists/100");
  expect(screen.getByRole("link", { name: "OK Computer by Radiohead: 25 listens" }).getAttribute("href")).toBe("/library/albums/200");
  expect((await screen.findByRole("link", { name: "Dummy" })).getAttribute("href")).toBe("/library/albums/202");
  expect(await screen.findByText(/Set your place on/)).toBeTruthy();
  expect(await screen.findByRole("link", { name: "Paranoid Android" })).toBeTruthy();
  expect(screen.getByLabelText("4.5 stars").textContent).toBe("★★★★½");
  expect(screen.queryByText("Coming up")).toBeNull();
});
