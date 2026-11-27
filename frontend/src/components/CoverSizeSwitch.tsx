import { COVER_SIZES, type CoverSize } from '../coverSize'
import { Segmented } from './Segmented'

/** S / M / L for a grid of covers. */
export function CoverSizeSwitch({ value, onChange }: { value: CoverSize; onChange: (size: CoverSize) => void }) {
  return <Segmented label="Cover size" value={value} onChange={onChange} options={COVER_SIZES} />
}
