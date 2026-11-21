import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import { DatePicker } from './DatePicker'

let last = ''
function Harness({ initial = '2026-11-21', min, max }: { initial?: string; min?: string; max?: string }) {
  const [value, setValue] = useState(initial)
  return (
    <DatePicker
      value={value}
      min={min}
      max={max}
      label="Date"
      onChange={(v) => {
        last = v
        setValue(v)
      }}
    />
  )
}

afterEach(() => {
  cleanup()
  last = ''
})

describe('DatePicker', () => {
  it('moves with the keyboard and picks with Enter', () => {
    render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: 'Date' }))
    const grid = screen.getByRole('grid')
    expect(grid).toHaveAccessibleName(/November 2026/)
    fireEvent.keyDown(grid, { key: 'ArrowRight' }) // 22nd
    fireEvent.keyDown(grid, { key: 'ArrowDown' }) // 29th
    fireEvent.keyDown(grid, { key: 'PageDown' }) // 29 December
    fireEvent.keyDown(grid, { key: 'Enter' })
    expect(last).toBe('2026-12-29')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it("won't go past min and max", () => {
    render(<Harness initial="2026-11-10" min="2026-11-05" max="2026-11-12" />)
    fireEvent.click(screen.getByRole('button', { name: 'Date' }))
    expect(screen.getByRole('button', { name: /November 4, 2026|4 November 2026/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /November 13, 2026|13 November 2026/ })).toBeDisabled()
    const grid = screen.getByRole('grid')
    fireEvent.keyDown(grid, { key: 'PageDown' }) // a month on: held at the 12th
    fireEvent.keyDown(grid, { key: 'Enter' })
    expect(last).toBe('2026-11-12')
  })
})
