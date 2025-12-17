import { describe, expect, it } from 'vitest'
import { fmtMs, fmtNumber, fmtUsd, humanize, shortId, statusTone, evidenceTone, actionIcon, pct, timeAgo } from '../lib/format'

describe('format helpers', () => {
  it('formats numbers defensively', () => {
    expect(fmtNumber(1234)).toBe('1,234')
    expect(fmtNumber(null)).toBe('—')
    expect(fmtNumber(undefined)).toBe('—')
    expect(fmtNumber(12.345, 1)).toBe('12.3')
  })

  it('formats durations', () => {
    expect(fmtMs(250)).toBe('250 ms')
    expect(fmtMs(1500)).toBe('1.50 s')
    expect(fmtMs(null)).toBe('—')
  })

  it('formats costs with a small-value precision', () => {
    expect(fmtUsd(0)).toBe('$0.00')
    expect(fmtUsd(1.5)).toBe('$1.50')
    expect(fmtUsd(0.000123)).toBe('$0.000123')
    expect(fmtUsd(null)).toBe('—')
  })

  it('humanizes identifiers', () => {
    expect(humanize('invalid_tool_arguments')).toBe('Invalid Tool Arguments')
    expect(humanize(null)).toBe('')
  })

  it('shortens long identifiers without losing the tail', () => {
    expect(shortId('short')).toBe('short')
    expect(shortId('a'.repeat(30))).toContain('…')
    expect(shortId(null)).toBe('—')
  })

  it('maps status to a tone', () => {
    expect(statusTone('success')).toBe('ok')
    expect(statusTone('failure')).toBe('danger')
    expect(statusTone('partial')).toBe('warn')
    expect(statusTone(undefined)).toBe('muted')
  })

  it('maps evidence labels to tones', () => {
    expect(evidenceTone('Observed')).toBe('info')
    expect(evidenceTone('Inferred Hypothesis')).toBe('warn')
    expect(evidenceTone('Experimentally Supported')).toBe('ok')
    expect(evidenceTone('Experimentally Refuted')).toBe('danger')
    expect(evidenceTone('Insufficient Evidence')).toBe('muted')
  })

  it('provides action icons and percentages', () => {
    expect(actionIcon('tool')).toBe('⚒')
    expect(actionIcon('unknown')).toBe('·')
    expect(pct(0.5)).toBe('50%')
    expect(pct(null)).toBe('—')
  })

  it('reports relative times', () => {
    expect(timeAgo(null)).toBe('—')
    expect(timeAgo(new Date().toISOString())).toBe('0s ago')
  })
})
