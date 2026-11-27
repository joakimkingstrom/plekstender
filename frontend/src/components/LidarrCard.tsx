import { useEffect, useState } from 'react'
import { errorMessage } from '../api/client'
import { checkLidarr, useDisconnectLidarr, useLidarrAdmin, useSaveLidarr, type LidarrOptions } from '../api/lidarr'
import { formatBytes } from '../format'
import { Segmented } from './Segmented'
import { Select } from './Select'
import { Button, Card, Notice, Spinner } from './ui'

const field = 'w-full rounded-lg border border-border bg-surface-2 px-3 py-2 font-mono text-sm'

type Access = 'admins' | 'everyone'

/**
 * Settings → Services → Lidarr (admins): its address and API key, checked with Lidarr, then
 * where new artists go (root folder, quality and metadata profiles) and who may add to it.
 */
export function LidarrCard() {
  const saved = useLidarrAdmin()
  const save = useSaveLidarr()
  const disconnect = useDisconnectLidarr()
  const [url, setUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [options, setOptions] = useState<LidarrOptions | null>(null)
  const [checking, setChecking] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [folder, setFolder] = useState('')
  const [quality, setQuality] = useState<number | null>(null)
  const [metadata, setMetadata] = useState<number | null>(null)
  const [access, setAccess] = useState<Access>('admins')
  const [done, setDone] = useState(false)

  const check = async (address: string, key: string | null) => {
    setChecking(true)
    setProblem(null)
    try {
      const found = await checkLidarr(address.trim(), key)
      setOptions(found)
      // Keep what's chosen when it's still there; else Lidarr's first.
      setFolder((f) => (found.root_folders.some((r) => r.path === f) ? f : (found.root_folders[0]?.path ?? '')))
      setQuality((q) => (found.quality_profiles.some((p) => p.id === q) ? q : (found.quality_profiles[0]?.id ?? null)))
      setMetadata((m) => (found.metadata_profiles.some((p) => p.id === m) ? m : (found.metadata_profiles[0]?.id ?? null)))
    } catch (e) {
      setOptions(null)
      setProblem(errorMessage(e))
    } finally {
      setChecking(false)
    }
  }

  // Connected: start from what's saved, and ask Lidarr what it offers (with the saved key).
  useEffect(() => {
    const s = saved.data
    if (!s?.configured || !s.url) return
    setUrl(s.url)
    setFolder(s.root_folder ?? '')
    setQuality(s.quality_profile_id ?? null)
    setMetadata(s.metadata_profile_id ?? null)
    setAccess(s.access)
    void check(s.url, null)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once the saved settings are in
  }, [saved.data?.configured, saved.data?.url])

  const configured = saved.data?.configured ?? false
  const ready = options && folder && quality !== null && metadata !== null
  return (
    <Card title="Lidarr">
      <div className="space-y-3 text-sm">
        <p className="text-muted">
          Add albums to your Lidarr from Library → Releases (download what's out, monitor what's coming) and artists from
          Discover. Its address as Plekstender's server reaches it (e.g. <span className="font-mono">http://lidarr:8686</span>
          ), and its API key from Lidarr's Settings → General.
        </p>
        {saved.isPending ? (
          <Spinner small />
        ) : saved.isError ? (
          <Notice tone="error">{errorMessage(saved.error)}</Notice>
        ) : (
          <p>{configured ? `✓ Connected to ${saved.data.url}${options ? ` (Lidarr ${options.version})` : ''}.` : 'Not connected.'}</p>
        )}
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault()
            setDone(false)
            if (url.trim()) void check(url, apiKey.trim() || null)
          }}
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1 block font-medium">Address</span>
              <input
                value={url}
                onChange={(e) => (setOptions(null), setUrl(e.target.value))}
                placeholder="http://lidarr:8686"
                autoComplete="off"
                spellCheck={false}
                className={field}
              />
            </label>
            <label className="block">
              <span className="mb-1 block font-medium">API key</span>
              <input
                type="password"
                value={apiKey}
                onChange={(e) => (setOptions(null), setApiKey(e.target.value))}
                placeholder={configured ? 'Saved (type a new one to change it)' : ''}
                autoComplete="off"
                spellCheck={false}
                className={field}
              />
            </label>
          </div>
          <Button type="submit" variant={options ? 'secondary' : 'primary'} busy={checking} disabled={!url.trim() || (!configured && !apiKey.trim())}>
            Check
          </Button>
        </form>
        {problem && <Notice tone="error">{problem}</Notice>}
        {options && (
          <div className="space-y-3 border-t border-border pt-3">
            <p className="text-muted">New artists go to:</p>
            <div className="flex flex-wrap gap-3">
              <div className="space-y-1">
                <span id="lidarr-folder" className="block text-xs font-medium text-muted">
                  Root folder
                </span>
                <Select
                  labelledBy="lidarr-folder"
                  value={folder}
                  onChange={setFolder}
                  options={options.root_folders.map((f) => ({
                    value: f.path,
                    label: f.free_space ? `${f.path} (${formatBytes(f.free_space)} free)` : f.path,
                  }))}
                />
              </div>
              <div className="space-y-1">
                <span id="lidarr-quality" className="block text-xs font-medium text-muted">
                  Quality profile
                </span>
                <Select
                  labelledBy="lidarr-quality"
                  value={quality ?? 0}
                  onChange={setQuality}
                  options={options.quality_profiles.map((p) => ({ value: p.id, label: p.name }))}
                />
              </div>
              <div className="space-y-1">
                <span id="lidarr-metadata" className="block text-xs font-medium text-muted">
                  Metadata profile
                </span>
                <Select
                  labelledBy="lidarr-metadata"
                  value={metadata ?? 0}
                  onChange={setMetadata}
                  options={options.metadata_profiles.map((p) => ({ value: p.id, label: p.name }))}
                />
              </div>
            </div>
            <div className="space-y-1">
              <span className="block text-xs font-medium text-muted">Who can add to Lidarr</span>
              <Segmented
                label="Who can add to Lidarr"
                value={access}
                onChange={setAccess}
                options={[
                  ['admins', 'Admins only'],
                  ['everyone', 'Everyone'],
                ]}
              />
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="primary"
                disabled={!ready}
                busy={save.isPending}
                onClick={() =>
                  save.mutate(
                    {
                      url: url.trim(),
                      api_key: apiKey.trim() || null,
                      root_folder: folder,
                      quality_profile_id: quality!,
                      metadata_profile_id: metadata!,
                      access,
                    },
                    { onSuccess: () => (setApiKey(''), setDone(true)) },
                  )
                }
              >
                Save
              </Button>
              {configured && (
                <Button variant="ghost" busy={disconnect.isPending} onClick={() => disconnect.mutate(undefined, { onSuccess: () => setOptions(null) })}>
                  Disconnect
                </Button>
              )}
            </div>
          </div>
        )}
        {save.isError && <Notice tone="error">{errorMessage(save.error)}</Notice>}
        {done && <Notice>Saved.</Notice>}
      </div>
    </Card>
  )
}
