import { useState } from 'react'
import { errorMessage } from '../api/client'
import { useRate } from '../api/ratings'
import { StarRating } from './StarRating'

/**
 * Stars that save the rating to Plex when clicked (anywhere in the library). The change
 * shows straight away and is undone if Plex refuses it.
 */
export function RateStars({
  serverId,
  ratingKey,
  value,
  label,
  size,
}: {
  serverId: number
  ratingKey: number
  value: number | null | undefined
  label: string
  size?: string
}) {
  const rate = useRate(serverId)
  const [local, setLocal] = useState<{ value: number | null } | null>(null)
  const shown = local ? local.value : value
  return (
    <span className="inline-flex flex-col items-start">
      <StarRating
        label={label}
        value={shown}
        size={size}
        onChange={(rating) => {
          setLocal({ value: rating })
          rate.mutate(
            { rating_key: ratingKey, rating },
            { onSuccess: () => setLocal(null), onError: () => setLocal(null) },
          )
        }}
      />
      {rate.isError && (
        <span role="alert" className="text-[11px] text-danger">
          {errorMessage(rate.error)}
        </span>
      )}
    </span>
  )
}
