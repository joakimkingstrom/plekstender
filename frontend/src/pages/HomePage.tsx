import { Link } from "react-router";
import { errorMessage } from "../api/client";
import {
  useConnections,
  useHealth,
  useMe,
  useServers,
  type ConnectionStatus,
} from "../api/hooks";
import { useActiveServer } from "../api/library";
import {
  LatestRatings,
  RecentlyAdded,
  TopThisMonth,
  UpcomingGigs,
  YourListening,
} from "../components/HomeSections";
import { SyncPanel } from "../components/library";
import {
  Badge,
  Card,
  Notice,
  PageTitle,
  Spinner,
  StatusDot,
} from "../components/ui";
import { ServersPage } from "./ServersPage";

export function HomePage() {
  const { data: me } = useMe();
  const servers = useServers();
  const health = useHealth();
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
        <Card title="Status">
          {health.data ? (
            <div className="flex flex-col gap-2">
              <StatusDot ok={health.data.database} label="Database" />
              <StatusDot
                ok={health.data.worker.healthy}
                label={
                  health.data.worker.healthy
                    ? "Background worker"
                    : "Background worker not running"
                }
              />
              <Connections />
              <span className="text-xs text-muted">
                Plekstender {health.data.version}
              </span>
            </div>
          ) : (
            <Spinner small />
          )}
        </Card>
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

const STATE_WORD = {
  ok: "working",
  warning: "needs a look",
  error: "not working",
  off: "not set up",
} as const;

/** Every connection: the user's own, then this Plekstender's (services not set up on one line). */
function Connections() {
  const connections = useConnections();
  if (!connections.data) return null;
  const groups: [string, ConnectionStatus[]][] = [
    ["Yours", connections.data.filter((c) => c.group === "yours")],
    [
      "This Plekstender",
      connections.data.filter(
        (c) => c.group === "instance" && c.state !== "off",
      ),
    ],
  ];
  const off = connections.data.filter(
    (c) => c.group === "instance" && c.state === "off",
  );
  return (
    <div className="mt-1 space-y-3">
      {groups.map(
        ([title, items]) =>
          items.length > 0 && (
            <div key={title}>
              <p className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-muted">
                {title}
              </p>
              <ul className="space-y-1">
                {items.map((c) => (
                  <li key={c.name} className="text-sm">
                    <div className="flex items-center justify-between gap-2">
                      <span title={`${c.name}: ${STATE_WORD[c.state]}`}>
                        <StatusDot tone={c.state} label={c.name} />
                      </span>
                      {c.link && c.state !== "ok" && (
                        <Link
                          to={c.link}
                          className="text-xs text-accent hover:underline"
                        >
                          Fix
                        </Link>
                      )}
                    </div>
                    {c.detail && (
                      <p className="ml-4 text-xs text-muted">{c.detail}</p>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ),
      )}
      {off.length > 0 && (
        <p className="text-xs text-muted">
          Not set up: {off.map((c) => c.name).join(", ")}
        </p>
      )}
    </div>
  );
}
