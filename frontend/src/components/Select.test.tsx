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
})
