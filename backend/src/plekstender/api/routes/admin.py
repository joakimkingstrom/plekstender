"""Instance administration: access mode, approved servers, users."""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from plekstender import acoustid, musicbrainz, tidal
from plekstender.api.deps import AdminDep, DbDep, HttpDep, SettingsDep
from plekstender.api.routes.servers import get_user_server
from plekstender.api.schemas import (
    AccessSettingsIn,
    AccessSettingsOut,
    AcoustIdKeyIn,
    AcoustIdKeyOut,
    AdminServerIn,
    AdminServerOut,
    AdminUserIn,
    AdminUserOut,
    CatalogueFont,
    CataloguePage,
    DataThistleOut,
    DataThistleTokensIn,
    FanartKeyIn,
    FanartKeyOut,
    FontGroup,
    FontGroupName,
    FontIn,
    JamBaseKeyIn,
    JamBaseKeyOut,
    LastFmKeyIn,
    LastFmKeyOut,
    MusicBrainzContactIn,
    MusicBrainzContactOut,
    PopularityPreviewItem,
    SetlistFmKeyIn,
    SetlistFmKeyOut,
    SpotifyAppIn,
    SpotifyAppOut,
    TicketmasterKeyIn,
    TicketmasterKeyOut,
    TidalIn,
    TidalOut,
)
from plekstender.artwork.catalogue import catalogue, find
from plekstender.artwork.sources import SourceError, fanart_logos
from plekstender.artwork.text import FontError, check_font
from plekstender.auth.sessions import end_sessions_without_access
from plekstender.db.instance import (
    DataThistleTokens,
    LibrarySettings,
    get_access_settings,
    get_acoustid_api_key,
    get_datathistle_tokens,
    get_datathistle_usage,
    get_fanart_api_key,
    get_fonts,
    get_jambase_api_key,
    get_jambase_usage,
    get_lastfm_api_key,
    get_lastfm_shared_secret,
    get_library_settings,
    get_musicbrainz_contact,
    get_setlistfm_api_key,
    get_spotify_app,
    get_ticketmaster_api_key,
    get_ticketmaster_usage,
    get_tidal_client,
    save_datathistle_quota,
    save_jambase_quota,
    set_access_settings,
    set_acoustid_api_key,
    set_datathistle_tokens,
    set_fanart_api_key,
    set_fonts,
    set_jambase_api_key,
    set_lastfm_api_key,
    set_library_settings,
    set_musicbrainz_contact,
    set_setlistfm_api_key,
    set_spotify_app,
    set_ticketmaster_api_key,
    set_tidal_client,
)
from plekstender.db.models import PlexServer, User, UserServer
from plekstender.history import scrobblers
from plekstender.history.lastfm import LastFmClient, LastFmError
from plekstender.history.listenbrainz import ScrobbleServiceError
from plekstender.live import datathistle, jambase, ticketmaster
from plekstender.sync.popularity import preview_artist_ranking
from plekstender.worker import enqueue_popularity_recompute

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/access", response_model=AccessSettingsOut)
async def get_access(db: DbDep, _: AdminDep, settings: SettingsDep) -> AccessSettingsOut:
    access = await get_access_settings(db)
    return AccessSettingsOut(
        mode=access.mode,
        allowlist=access.allowlist,
        initial_admin_configured=bool(settings.initial_admin),
    )


@router.put("/access", response_model=AccessSettingsOut)
async def put_access(
    body: AccessSettingsIn, db: DbDep, _: AdminDep, settings: SettingsDep
) -> AccessSettingsOut:
    await set_access_settings(db, body.mode, body.allowlist)
    # Tighter rules apply now, not when people's sessions run out.
    await end_sessions_without_access(db)
    await db.commit()
    return await get_access(db, _, settings)


@router.get("/servers", response_model=list[AdminServerOut])
async def list_all_servers(db: DbDep, _: AdminDep) -> list[AdminServerOut]:
    rows = await db.execute(
        select(PlexServer, func.count(UserServer.id))
        .outerjoin(UserServer)
        .group_by(PlexServer.id)
        .order_by(PlexServer.name)
    )
    return [
        AdminServerOut(
            id=s.id,
            machine_identifier=s.machine_identifier,
            name=s.name,
            approved=s.approved,
            user_count=count,
        )
        for s, count in rows.all()
    ]


@router.put("/servers/{server_id}", response_model=AdminServerOut)
async def update_server(
    server_id: int, body: AdminServerIn, db: DbDep, _: AdminDep
) -> AdminServerOut:
    server = await db.get(PlexServer, server_id)
    if server is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Server not found")
    server.approved = body.approved
    if not body.approved:
        await end_sessions_without_access(db)
    await db.commit()
    count = await db.scalar(
        select(func.count(UserServer.id)).where(UserServer.server_id == server.id)
    )
    return AdminServerOut(
        id=server.id,
        machine_identifier=server.machine_identifier,
        name=server.name,
        approved=server.approved,
        user_count=count or 0,
    )


def _admin_user_out(u: User, server_count: int) -> AdminUserOut:
    return AdminUserOut(
        id=u.id,
        username=u.username,
        email=u.email,
        thumb_url=u.thumb_url,
        is_admin=u.is_admin,
        created_at=u.created_at,
        last_login_at=u.last_login_at,
        server_count=server_count,
    )


@router.get("/users", response_model=list[AdminUserOut])
async def list_users(db: DbDep, _: AdminDep) -> list[AdminUserOut]:
    rows = await db.execute(
        select(User, func.count(UserServer.id))
        .outerjoin(UserServer)
        .group_by(User.id)
        .order_by(User.created_at)
    )
    return [_admin_user_out(u, count) for u, count in rows.all()]


@router.put("/users/{user_id}", response_model=AdminUserOut)
async def update_user(user_id: int, body: AdminUserIn, db: DbDep, admin: AdminDep) -> AdminUserOut:
    if user_id == admin.id and not body.is_admin:
        raise HTTPException(status.HTTP_409_CONFLICT, "You can't remove your own admin role")
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    user.is_admin = body.is_admin
    await db.commit()
    count = await db.scalar(select(func.count(UserServer.id)).where(UserServer.user_id == user.id))
    return _admin_user_out(user, count or 0)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: int, db: DbDep, admin: AdminDep) -> None:
    """Removes the user and all their data and sessions. They can sign in again if allowed."""
    if user_id == admin.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "You can't remove yourself")
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    await db.delete(user)
    await db.commit()


# --- library settings ---------------------------------------------------------------------


@router.get("/library", response_model=LibrarySettings)
async def get_library(db: DbDep, _: AdminDep) -> LibrarySettings:
    return await get_library_settings(db)


@router.put("/library", response_model=LibrarySettings)
async def put_library(body: LibrarySettings, db: DbDep, _: AdminDep) -> LibrarySettings:
    before = await get_library_settings(db)
    await set_library_settings(db, body)
    await db.commit()
    if (before.album_top_n, before.artist_top_n) != (body.album_top_n, body.artist_top_n):
        # Re-scoring every server can take a few seconds; do it in the background.
        await enqueue_popularity_recompute()
    return body


@router.get("/library/preview", response_model=list[PopularityPreviewItem])
async def preview_popularity(
    db: DbDep,
    admin: AdminDep,
    server_id: int,
    artist_top_n: int = Query(ge=1, le=50),
    limit: int = Query(default=20, ge=1, le=50),
) -> list[PopularityPreviewItem]:
    """Your top artists if artist popularity used `artist_top_n`, with their current rank."""
    us = await get_user_server(db, admin, server_id)
    rows = await preview_artist_ranking(
        db,
        user_id=admin.id,
        server_id=us.server_id,
        sections=us.music_section_keys,
        artist_top_n=artist_top_n,
        limit=limit,
    )
    return [PopularityPreviewItem(**row.__dict__) for row in rows]


@router.get("/lastfm", response_model=LastFmKeyOut)
async def get_lastfm(db: DbDep, _: AdminDep, settings: SettingsDep) -> LastFmKeyOut:
    key, source = await get_lastfm_api_key(db, settings)
    secret = await get_lastfm_shared_secret(db, settings)
    return LastFmKeyOut(configured=key is not None, source=source, scrobbling=bool(key and secret))


@router.put("/lastfm", response_model=LastFmKeyOut)
async def put_lastfm(
    body: LastFmKeyIn, db: DbDep, admin: AdminDep, settings: SettingsDep, http: HttpDep
) -> LastFmKeyOut:
    """Set (after checking them with Last.fm) or clear the instance's Last.fm API key and
    its shared secret. A secret on its own goes with the key that's set already."""
    if body.api_key:
        try:
            await LastFmClient(http, body.api_key, settings.lastfm_api_url).check_key()
        except LastFmError as exc:
            code = status.HTTP_502_BAD_GATEWAY if exc.transient else status.HTTP_400_BAD_REQUEST
            raise HTTPException(code, str(exc)) from exc
    key, source = await get_lastfm_api_key(db, settings)
    if body.shared_secret:
        api_key = body.api_key or key
        if not api_key:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Add the API key first.")
        client = scrobblers.AudioscrobblerClient(
            http,
            settings.lastfm_api_url,
            api_key=api_key,
            api_secret=body.shared_secret,
            name="Last.fm",
        )
        try:
            await client.check()
        except ScrobbleServiceError as exc:
            refused = exc.status in (400, 401, 403)
            code = status.HTTP_400_BAD_REQUEST if refused else status.HTTP_502_BAD_GATEWAY
            raise HTTPException(code, str(exc)) from exc
    # A new key without a secret drops the old secret (it belonged to the old key); a
    # secret on its own keeps the key saved here.
    keep_key = body.api_key or (key if body.shared_secret and source == "settings" else None)
    await set_lastfm_api_key(db, settings, keep_key, body.shared_secret)
    await db.commit()
    return await get_lastfm(db, admin, settings)


# Radiohead: a well-known artist to check a fanart.tv key against.
FANART_CHECK_MBID = "a74b1b7f-71a5-4011-9441-d0b5e4122711"


@router.get("/fanart", response_model=FanartKeyOut)
async def get_fanart(db: DbDep, _: AdminDep, settings: SettingsDep) -> FanartKeyOut:
    key, source = await get_fanart_api_key(db, settings)
    return FanartKeyOut(configured=key is not None, source=source)


@router.put("/fanart", response_model=FanartKeyOut)
async def put_fanart(
    body: FanartKeyIn, db: DbDep, admin: AdminDep, settings: SettingsDep, http: HttpDep
) -> FanartKeyOut:
    """Set (after checking it with fanart.tv) or clear the instance's fanart.tv API key."""
    if body.api_key:
        try:
            await fanart_logos(http, settings.fanart_api_url, body.api_key, FANART_CHECK_MBID)
        except SourceError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await set_fanart_api_key(db, settings, body.api_key)
    await db.commit()
    return await get_fanart(db, admin, settings)


@router.get("/tidal", response_model=TidalOut)
async def get_tidal(db: DbDep, _: AdminDep, settings: SettingsDep) -> TidalOut:
    client = await get_tidal_client(db, settings)
    return TidalOut(configured=client is not None, client_id=client[0] if client else None)


@router.put("/tidal", response_model=TidalOut)
async def put_tidal(
    body: TidalIn, db: DbDep, admin: AdminDep, settings: SettingsDep, http: HttpDep
) -> TidalOut:
    """Set (after Tidal accepts them) or remove the Tidal app's client ID and secret, used
    for album covers and importing Tidal playlists."""
    if bool(body.client_id) != bool(body.client_secret):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Enter both the client ID and the secret."
        )
    client = (body.client_id, body.client_secret) if body.client_id and body.client_secret else None
    if client:
        try:
            await tidal.check(http, settings, client)
        except tidal.TidalError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await set_tidal_client(db, settings, client)
    await db.commit()
    return await get_tidal(db, admin, settings)


@router.get("/musicbrainz", response_model=MusicBrainzContactOut)
async def get_musicbrainz(db: DbDep, _: AdminDep, settings: SettingsDep) -> MusicBrainzContactOut:
    contact, source = await get_musicbrainz_contact(db, settings)
    return MusicBrainzContactOut(contact=contact, source=source)


@router.put("/musicbrainz", response_model=MusicBrainzContactOut)
async def put_musicbrainz(
    body: MusicBrainzContactIn, db: DbDep, admin: AdminDep, settings: SettingsDep
) -> MusicBrainzContactOut:
    """Set or clear the contact sent to MusicBrainz in the User-Agent (an email address or a
    web page). The worker picks it up within a minute."""
    await set_musicbrainz_contact(db, body.contact)
    await db.commit()
    musicbrainz.forget_contact()
    return await get_musicbrainz(db, admin, settings)


@router.get("/acoustid", response_model=AcoustIdKeyOut)
async def get_acoustid(db: DbDep, _: AdminDep, settings: SettingsDep) -> AcoustIdKeyOut:
    key, source = await get_acoustid_api_key(db, settings)
    return AcoustIdKeyOut(configured=key is not None, source=source)


@router.put("/acoustid", response_model=AcoustIdKeyOut)
async def put_acoustid(
    body: AcoustIdKeyIn, db: DbDep, admin: AdminDep, settings: SettingsDep, http: HttpDep
) -> AcoustIdKeyOut:
    """Set (after checking it with AcoustID) or clear the instance's AcoustID application key."""
    if body.api_key:
        try:
            await acoustid.check_key(http, settings.acoustid_api_url, body.api_key)
        except acoustid.AcoustIdError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await set_acoustid_api_key(db, settings, body.api_key)
    await db.commit()
    return await get_acoustid(db, admin, settings)


@router.get("/jambase", response_model=JamBaseKeyOut)
async def get_jambase(
    db: DbDep, _: AdminDep, settings: SettingsDep, http: HttpDep, check: bool = False
) -> JamBaseKeyOut:
    """The JamBase key and this month's requests; `check` asks JamBase for its own count
    (a free request)."""
    key, source = await get_jambase_api_key(db, settings)
    if check and key:
        try:
            await save_jambase_quota(db, await jambase.quota(http, settings.jambase_api_url, key))
            await db.commit()
        except jambase.JamBaseError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    usage = await get_jambase_usage(db)
    return JamBaseKeyOut(
        configured=key is not None,
        source=source,
        month=usage.month,
        calls=usage.calls,
        cap=settings.jambase_monthly_cap,
        quota=usage.quota,
        quota_checked_at=usage.quota_checked_at,
    )


@router.put("/jambase", response_model=JamBaseKeyOut)
async def put_jambase(
    body: JamBaseKeyIn, db: DbDep, admin: AdminDep, settings: SettingsDep, http: HttpDep
) -> JamBaseKeyOut:
    """Set (after checking it with JamBase's free quota request) or clear the JamBase key."""
    if body.api_key:
        try:
            quota = await jambase.quota(http, settings.jambase_api_url, body.api_key)
        except jambase.JamBaseError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        await save_jambase_quota(db, quota)
    await set_jambase_api_key(db, settings, body.api_key)
    await db.commit()
    return await get_jambase(db, admin, settings, http)


@router.get("/ticketmaster", response_model=TicketmasterKeyOut)
async def get_ticketmaster(db: DbDep, _: AdminDep, settings: SettingsDep) -> TicketmasterKeyOut:
    """The Ticketmaster key and today's requests."""
    key, source = await get_ticketmaster_api_key(db, settings)
    usage = await get_ticketmaster_usage(db)
    return TicketmasterKeyOut(
        configured=key is not None,
        source=source,
        day=usage.day,
        calls=usage.calls,
        cap=settings.ticketmaster_daily_cap,
    )


@router.put("/ticketmaster", response_model=TicketmasterKeyOut)
async def put_ticketmaster(
    body: TicketmasterKeyIn, db: DbDep, admin: AdminDep, settings: SettingsDep, http: HttpDep
) -> TicketmasterKeyOut:
    """Set (after checking it with one small search) or clear the Ticketmaster key."""
    if body.api_key:
        try:
            await ticketmaster.check_key(http, settings.ticketmaster_api_url, body.api_key)
        except ticketmaster.TicketmasterError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await set_ticketmaster_api_key(db, settings, body.api_key)
    await db.commit()
    return await get_ticketmaster(db, admin, settings)


async def _datathistle_out(db: DbDep, settings: SettingsDep) -> DataThistleOut:
    tokens = await get_datathistle_tokens(db, settings)
    usage = await get_datathistle_usage(db)
    return DataThistleOut(
        configured=tokens is not None,
        access_expires=tokens.access_expires if tokens else None,
        refresh_expires=tokens.refresh_expires if tokens else None,
        renewed_at=tokens.renewed_at if tokens else None,
        error=tokens.error if tokens else None,
        day=usage.day,
        calls=usage.calls,
        cap=settings.datathistle_daily_cap,
        limit=usage.limit,
        remaining=usage.remaining,
        resets_at=usage.resets_at,
    )


@router.get("/datathistle", response_model=DataThistleOut)
async def get_datathistle(db: DbDep, _: AdminDep, settings: SettingsDep) -> DataThistleOut:
    """The Data Thistle tokens' expiry dates, renewals and the month's requests."""
    return await _datathistle_out(db, settings)


@router.put("/datathistle", response_model=DataThistleOut)
async def put_datathistle(
    body: DataThistleTokensIn, db: DbDep, _: AdminDep, settings: SettingsDep, http: HttpDep
) -> DataThistleOut:
    """Save the access and refresh tokens from the Data Thistle account page (the access
    token is checked with a ping). Plekstender renews them from then on."""
    access_expires = datathistle.token_expiry(body.access_token)
    refresh_expires = datathistle.token_expiry(body.refresh_token)
    if access_expires is None or refresh_expires is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Those don't look like Data Thistle tokens."
        )
    if body.access_token == body.refresh_token:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Paste the access token and the refresh token."
        )
    try:
        quota = await datathistle.ping(http, settings.datathistle_api_url, body.access_token)
    except datathistle.DataThistleError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await set_datathistle_tokens(
        db,
        settings,
        DataThistleTokens(
            access=body.access_token,
            refresh=body.refresh_token,
            access_expires=access_expires,
            refresh_expires=refresh_expires,
        ),
    )
    await save_datathistle_quota(db, quota.limit, quota.remaining, quota.resets_at)
    await db.commit()
    return await _datathistle_out(db, settings)


@router.delete("/datathistle", response_model=DataThistleOut)
async def delete_datathistle(db: DbDep, _: AdminDep, settings: SettingsDep) -> DataThistleOut:
    await set_datathistle_tokens(db, settings, None)
    await db.commit()
    return await _datathistle_out(db, settings)


@router.get("/setlistfm", response_model=SetlistFmKeyOut)
async def get_setlistfm(db: DbDep, _: AdminDep, settings: SettingsDep) -> SetlistFmKeyOut:
    key, source = await get_setlistfm_api_key(db, settings)
    return SetlistFmKeyOut(configured=key is not None, source=source)


@router.put("/setlistfm", response_model=SetlistFmKeyOut)
async def put_setlistfm(
    body: SetlistFmKeyIn, db: DbDep, admin: AdminDep, settings: SettingsDep, http: HttpDep
) -> SetlistFmKeyOut:
    """Set (after checking it with one small search) or clear the setlist.fm API key."""
    if body.api_key:
        try:
            resp = await http.get(
                f"{settings.setlistfm_api_url}/search/artists",
                params={"artistName": "Radiohead", "p": 1},
                headers={"x-api-key": body.api_key, "Accept": "application/json"},
                timeout=20,
            )
        except httpx.HTTPError as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, "setlist.fm couldn't be reached."
            ) from exc
        if resp.status_code in (401, 403):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "setlist.fm didn't accept the key.")
        if resp.status_code >= 400 and resp.status_code != 404:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, f"setlist.fm answered {resp.status_code}."
            )
    await set_setlistfm_api_key(db, settings, body.api_key)
    await db.commit()
    return await get_setlistfm(db, admin, settings)


@router.get("/spotify", response_model=SpotifyAppOut)
async def get_spotify(db: DbDep, _: AdminDep, settings: SettingsDep) -> SpotifyAppOut:
    app = await get_spotify_app(db, settings)
    return SpotifyAppOut(
        configured=app is not None,
        source=app[2] if app else None,
        client_id=app[0] if app else None,
    )


@router.put("/spotify", response_model=SpotifyAppOut)
async def put_spotify(
    body: SpotifyAppIn, db: DbDep, admin: AdminDep, settings: SettingsDep, http: HttpDep
) -> SpotifyAppOut:
    """Set (after signing in with it) or clear the Spotify app used to read playlist links."""
    from plekstender.playlists import spotify

    if body.client_id and body.client_secret:
        try:
            await spotify.access_token(
                http, body.client_id, body.client_secret, settings.spotify_accounts_url
            )
        except spotify.SpotifyError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    elif body.client_id or body.client_secret:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Enter both the client ID and secret."
        )
    await set_spotify_app(db, settings, body.client_id, body.client_secret)
    await db.commit()
    return await get_spotify(db, admin, settings)


# --- text logo fonts -----------------------------------------------------------------------


async def _font_groups(db: DbDep) -> list[FontGroup]:
    return [FontGroup(name=name, fonts=names) for name, names in (await get_fonts(db)).items()]


@router.get("/fonts/catalogue", response_model=CataloguePage)
async def font_catalogue(
    db: DbDep,
    _: AdminDep,
    http: HttpDep,
    settings: SettingsDep,
    q: Annotated[str, Query(max_length=64)] = "",
    group: FontGroupName | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 24,
) -> CataloguePage:
    """Browse Google Fonts (most used first), to add fonts for text logos."""
    families = await catalogue(http, settings.data_dir / "fonts")
    offered = {n.casefold() for names in (await get_fonts(db)).values() for n in names}
    wanted = " ".join(q.split()).casefold()
    matches = [
        f
        for f in families
        if (not wanted or wanted in f.name.casefold()) and (group is None or f.group == group)
    ]
    # An exact name first, then the most used.
    matches.sort(key=lambda f: (f.name.casefold() != wanted, f.popularity))
    return CataloguePage(
        items=[
            CatalogueFont(name=f.name, group=f.group, offered=f.name.casefold() in offered)
            for f in matches[offset : offset + limit]
        ],
        total=len(matches),
        available=bool(families),
    )


@router.post("/fonts", response_model=list[FontGroup])
async def add_font(
    body: FontIn, db: DbDep, _: AdminDep, http: HttpDep, settings: SettingsDep
) -> list[FontGroup]:
    """Offer another Google Fonts family for text logos. The name may use any capitals:
    the catalogue gives the family's exact name and its group."""
    name = " ".join(body.name.split())
    family = await find(http, settings.data_dir / "fonts", name)
    if family is not None:
        name, group = family.name, family.group
    elif await catalogue(http, settings.data_dir / "fonts"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Google Fonts has no font called {name!r}."
        )
    else:
        # No catalogue (Google unreachable): ask the CSS API (exact name needed then).
        try:
            await check_font(http, name)
        except FontError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        group = body.group or "Display"
    groups = await get_fonts(db)
    if any(name.casefold() == n.casefold() for names in groups.values() for n in names):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{name} is already in the list.")
    groups[group] = sorted([*groups[group], name])
    await set_fonts(db, groups)
    await db.commit()
    return await _font_groups(db)


@router.delete("/fonts/{name}", response_model=list[FontGroup])
async def remove_font(name: str, db: DbDep, _: AdminDep) -> list[FontGroup]:
    groups = await get_fonts(db)
    remaining = {g: [n for n in names if n != name] for g, names in groups.items()}
    if remaining == groups:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That font isn't in the list.")
    if not any(remaining.values()):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Keep at least one font.")
    await set_fonts(db, remaining)
    await db.commit()
    return await _font_groups(db)


@router.delete("/fonts", response_model=list[FontGroup])
async def reset_fonts(db: DbDep, _: AdminDep) -> list[FontGroup]:
    """Go back to the default list."""
    await set_fonts(db, None)
    await db.commit()
    return await _font_groups(db)
