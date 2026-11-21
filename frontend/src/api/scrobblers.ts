import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap, type Schemas } from './client'

export type ScrobbleTarget = Schemas['ScrobbleTargetOut']
export type ScrobbleTargetIn = Schemas['ScrobbleTargetIn']
export type ScrobbleService = ScrobbleTargetIn['service']

const key = ['scrobblers'] as const

export function useScrobblers() {
  return useQuery({ queryKey: key, queryFn: () => unwrap(api.GET('/api/scrobblers')) })
}

export function useAddScrobbler() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: ScrobbleTargetIn) => unwrap(api.POST('/api/scrobblers', { body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: key }),
  })
}

export function useRemoveScrobbler() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => unwrap(api.DELETE('/api/scrobblers/{target_id}', { params: { path: { target_id: id } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: key }),
  })
}

/** Whether people can add Last.fm (an admin has set the API key and its shared secret). */
export function useLastFmScrobbling() {
  return useQuery({ queryKey: [...key, 'lastfm'], queryFn: () => unwrap(api.GET('/api/scrobblers/lastfm')) })
}

/** Off to last.fm to approve Plekstender; it comes back to Settings → Scrobbling. */
export const LASTFM_CONNECT = '/api/scrobblers/lastfm/connect'
