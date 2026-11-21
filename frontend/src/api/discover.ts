import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap, type Schemas } from './client'

export type ArtistSuggestion = Schemas['ArtistSuggestionOut']

const keys = {
  all: (serverId: number) => ['servers', serverId, 'library', 'discover'] as const,
}

/** Artists to add: not in the library, similar to the most library artists. */
export function useArtistSuggestions(serverId: number, hidden: boolean, limit: number) {
  return useQuery({
    queryKey: [...keys.all(serverId), hidden, limit],
    placeholderData: keepPreviousData,
    staleTime: 5 * 60_000,
    queryFn: () =>
      unwrap(
        api.GET('/api/servers/{server_id}/library/discover', {
          params: { path: { server_id: serverId }, query: { hidden, limit } },
        }),
      ),
  })
}

/** Hide an artist from the suggestions, or bring one back. */
export function useHideSuggestion(serverId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ artist, hide }: { artist: ArtistSuggestion; hide: boolean }) =>
      hide
        ? unwrap(
            api.POST('/api/servers/{server_id}/library/discover/hidden', {
              params: { path: { server_id: serverId } },
              body: { key: artist.key, name: artist.name },
            }),
          )
        : unwrap(
            api.DELETE('/api/servers/{server_id}/library/discover/hidden/{key}', {
              params: { path: { server_id: serverId, key: artist.key } },
            }),
          ),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all(serverId) }),
  })
}
