import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { HOLD_MS, moved } from '../reorder'
import { ScrollTabs } from './ScrollTabs'

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('moved', () => {
  it('moves a key to where it is dragged', () => {
    expect(moved(['a', 'b', 'c', 'd'], 'a', 2)).toEqual(['b', 'c', 'a', 'd'])
    expect(moved(['a', 'b', 'c', 'd'], 'd', 0)).toEqual(['d', 'a', 'b', 'c'])
    expect(moved(['a', 'b', 'c'], 'b', 9)).toEqual(['a', 'c', 'b'])
    expect(moved(['a', 'b'], 'x', 0)).toEqual(['a', 'b'])
  })
})

function setup() {
  const onReorder = vi.fn()
  const onPick = vi.fn()
  render(
    <ScrollTabs
      label="Where from"
      order={['deezer', 'itunes', 'plex']}
      onReorder={onReorder}
      renderTab={(key) => (
        <button type="button" role="tab" onClick={() => onPick(key)}>
          {key}
        </button>
      )}
    />,
  )
  return { onReorder, onPick }
}

describe('ScrollTabs', () => {
  it('moves a tab held for a second, and the drag is not a click', () => {
    vi.useFakeTimers()
    const { onReorder, onPick } = setup()
    const deezer = screen.getByRole('tab', { name: 'deezer' })
    fireEvent.pointerDown(deezer, { button: 0, pointerId: 1, clientX: 10, pointerType: 'mouse' })
    act(() => {
      vi.advanceTimersByTime(HOLD_MS)
    })
    // jsdom has no layout: every tab is at 0, so anywhere to the right is the end.
    fireEvent.pointerMove(deezer, { pointerId: 1, clientX: 300, pointerType: 'mouse' })
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual(['itunes', 'plex', 'deezer'])
    fireEvent.pointerUp(deezer, { pointerId: 1 })
    expect(onReorder).toHaveBeenCalledWith(['itunes', 'plex', 'deezer'])
    fireEvent.click(deezer)
    expect(onPick).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('tab', { name: 'plex' }))
    expect(onPick).toHaveBeenCalledWith('plex')
  })

  it("doesn't move a tab let go of before a second: that's a click", () => {
    vi.useFakeTimers()
    const { onReorder, onPick } = setup()
    const deezer = screen.getByRole('tab', { name: 'deezer' })
    fireEvent.pointerDown(deezer, { button: 0, pointerId: 1, clientX: 10, pointerType: 'mouse' })
    act(() => {
      vi.advanceTimersByTime(HOLD_MS / 2)
    })
    fireEvent.pointerUp(deezer, { pointerId: 1 })
    fireEvent.click(deezer)
    expect(onPick).toHaveBeenCalledWith('deezer')
    expect(onReorder).not.toHaveBeenCalled()
  })

  it('scrolls when dragged with the mouse', () => {
    const { onPick } = setup()
    const list = screen.getByRole('tablist', { name: 'Where from' })
    list.scrollLeft = 100
    const itunes = screen.getByRole('tab', { name: 'itunes' })
    fireEvent.pointerDown(itunes, { button: 0, pointerId: 2, clientX: 200, pointerType: 'mouse' })
    fireEvent.pointerMove(itunes, { pointerId: 2, clientX: 150, pointerType: 'mouse' })
    expect(list.scrollLeft).toBe(150)
    fireEvent.pointerUp(itunes, { pointerId: 2 })
    fireEvent.click(itunes)
    expect(onPick).not.toHaveBeenCalled() // the end of a drag isn't a click
  })

  it('moves a focused tab with Alt and the arrow keys', () => {
    const { onReorder } = setup()
    fireEvent.keyDown(screen.getByRole('tab', { name: 'itunes' }), { key: 'ArrowRight', altKey: true })
    expect(onReorder).toHaveBeenCalledWith(['deezer', 'plex', 'itunes'])
  })
})
