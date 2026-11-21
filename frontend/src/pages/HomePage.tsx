import { Link } from "react-router";
import { errorMessage } from "../api/client";
import { useMe, useServers } from "../api/hooks";
import { useActiveServer } from "../api/library";
import {
  LatestRatings,
  RecentlyAdded,
  TopThisMonth,
  UpcomingGigs,
  YourListening,
} from "../components/HomeSections";
import { SyncPanel } from "../components/library";
import { StatusCard } from "../components/StatusCard";
import {
  Badge,
  Card,
  Notice,
  PageTitle,
  Spinner,
} from "../components/ui";
import { ServersPage } from "./ServersPage";

export function HomePage() {
  const { data: me } = useMe();
  const servers = useServers();
  const { active } = useActiveServer();
  const libraryServer =
    active && active.music_section_keys.length > 0 ? active : null;

  if (servers.isPending) return <Spinner />;
  if (servers.isError)
    return <Notice tone="error">{errorMessage(servers.error)}</Notice>;
  if (servers.data.length === 0) return <ServersPage firstRun />;

  const needsLibrary = servers.data.some(
    (s) => s.music_section_keys.length === 0,
  );

  return (
    <>
      <PageTitle subtitle="Your music at a glance.">
        Hi {me?.username}
      </PageTitle>

      {needsLibrary && (
        <div className="mb-4">
          <Notice tone="warn">
            Pick a music library for each server in{" "}
            <Link className="underline" to="/settings">
              Settings
            </Link>
            .
          </Notice>
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <div className="md:col-span-2">
          <YourListening serverId={libraryServer?.id ?? null} />
        </div>
        <TopThisMonth serverId={libraryServer?.id ?? null} />
        {libraryServer && <RecentlyAdded serverId={libraryServer.id} />}
        <UpcomingGigs serverId={libraryServer?.id ?? null} />
        {libraryServer && <LatestRatings serverId={libraryServer.id} />}
        {servers.data
          .filter((s) => s.music_section_keys.length > 0)
          .map((s) => (
            <Card
              key={s.id}
              title={
                servers.data.length > 1 ? `Library · ${s.name}` : "Library"
              }
              action={
                <Link to="/library" className="text-sm text-accent">
                  Browse
                </Link>
              }
              className="md:col-span-2"
            >
              <SyncPanel serverId={s.id} />
            </Card>
          ))}
        <Card
          title="Servers"
          action={
            <Link to="/settings" className="text-sm text-accent">
              Manage
            </Link>
          }
        >
          <ul className="space-y-2">
            {servers.data.map((s) => (
              <li
                key={s.id}
                className="flex items-center justify-between gap-3 text-sm"
              >
                <span className="font-medium">{s.name}</span>
                <Badge>
                  {s.music_section_keys.length} music{" "}
                  {s.music_section_keys.length === 1 ? "library" : "libraries"}
                </Badge>
              </li>
            ))}
          </ul>
        </Card>
        <StatusCard />
      </div>

    </>
  );
}

export function ComingSoonPage({
  title,
  text,
}: {
  title: string;
  text: string;
}) {
  return (
    <>
      <PageTitle>{title}</PageTitle>
      <Card>
        <p className="text-sm text-muted">{text}</p>
      </Card>
    </>
  );
}

export function NotFoundPage() {
  return (
    <>
      <PageTitle>Not found</PageTitle>
      <Link to="/" className="text-accent">
        Back home
      </Link>
    </>
  );
}
