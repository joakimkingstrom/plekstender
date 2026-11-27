import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import { Select } from './Select'

afterEach(cleanup)

function Fonts() {
  const [value, setValue] = useState('Anton')
  return (
    <Select
      label="Font"
      value={value}
      onChange={setValue}
      options={[
        { value: 'Anton', label: 'Anton', group: 'Sans serif' },
        { value: 'Raleway', label: 'Raleway', group: 'Sans serif' },
        { value: 'Cinzel', label: 'Cinzel', group: 'Serif' },
        { value: 'Lobster', label: 'Lobster', group: 'Display' },
      ]}
    />
  )
}

describe('Select', () => {
  it('works with the keyboard and shows group headings', async () => {
    render(<Fonts />)
    const button = screen.getByRole('button', { name: 'Font' })
    expect(button).toHaveTextContent('Anton')
    fireEvent.keyDown(button, { key: 'ArrowDown' })
    const list = await screen.findByRole('listbox', { name: 'Font' })
    expect(screen.getByText('Serif')).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /Anton/ })).toHaveAttribute('aria-selected', 'true')

    fireEvent.keyDown(list, { key: 'ArrowDown' })
    fireEvent.keyDown(list, { key: 'ArrowDown' })
    fireEvent.keyDown(list, { key: 'Enter' })
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    expect(button).toHaveTextContent('Cinzel')

    fireEvent.click(button)
    fireEvent.keyDown(await screen.findByRole('listbox'), { key: 'l' })
    fireEvent.keyDown(screen.getByRole('listbox'), { key: 'Enter' })
    expect(button).toHaveTextContent('Lobster') // typed "l"

    fireEvent.click(button)
    fireEvent.keyDown(await screen.findByRole('listbox'), { key: 'Escape' })
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    expect(button).toHaveTextContent('Lobster')
  })

  it("opens upwards near the bottom of the window, so the page doesn't jump", async () => {
    render(<Fonts />)
    const button = screen.getByRole('button', { name: 'Font' })
    const at = (top: number) =>
      (button.getBoundingClientRect = () => ({ top, bottom: top + 36, left: 0, right: 100, width: 100, height: 36, x: 0, y: top, toJSON: () => ({}) }))
    at(window.innerHeight - 60) // the bottom of the window
    fireEvent.click(button)
    expect(await screen.findByRole('listbox')).toHaveClass('bottom-full')
    fireEvent.click(button)
    at(20) // the top
    fireEvent.click(button)
    expect(await screen.findByRole('listbox')).toHaveClass('top-full')
  })

  it('opens upwards near the bottom of a dialog that scrolls, not only of the window', async () => {
    render(
      <div data-testid="body" style={{ overflowY: 'auto' }}>
        <Fonts />
      </div>,
    )
    const body = screen.getByTestId('body')
    body.getBoundingClientRect = () => ({ top: 100, bottom: 400, left: 0, right: 500, width: 500, height: 300, x: 0, y: 100, toJSON: () => ({}) })
    const button = screen.getByRole('button', { name: 'Font' })
    // Plenty of window below, but the dialog's body ends 40 px under the button.
    button.getBoundingClientRect = () => ({ top: 324, bottom: 360, left: 0, right: 100, width: 100, height: 36, x: 0, y: 324, toJSON: () => ({}) })
    fireEvent.click(button)
    expect(await screen.findByRole('listbox')).toHaveClass('bottom-full')
  })
})
