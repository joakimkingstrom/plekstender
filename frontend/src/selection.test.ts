import { act, renderHook } from '@testing-library/react'
import { expect, it } from 'vitest'
import { useRangeSelection } from './selection'

it('toggles one item, or a range with shift', () => {
  const order = [1, 2, 3, 4, 5]
  const { result } = renderHook(() => useRangeSelection<number>())
  act(() => result.current.toggle(2, order, false))
  act(() => result.current.toggle(4, order, true)) // 2..4 on
  expect([...result.current.selected].sort()).toEqual([2, 3, 4])
  act(() => result.current.toggle(3, order, false)) // 3 off
  act(() => result.current.toggle(5, order, true)) // 3..5 like 5: on
  expect([...result.current.selected].sort()).toEqual([2, 3, 4, 5])
  act(() => result.current.toggle(1, order, true)) // 5..1 like 1: on
  expect(result.current.selected.size).toBe(5)
  act(() => result.current.clear())
  expect(result.current.selected.size).toBe(0)
})
