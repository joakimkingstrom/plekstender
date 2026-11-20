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
