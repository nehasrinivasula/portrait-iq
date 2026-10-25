import { describe, expect, it } from 'vitest';
import { activeFilters, EMPTY_LOG_FILTERS, toLogApiFilters } from './logFilters';

describe('log filter contract', () => {
  it('lists exactly the UI filters selected by the field engineer', () => {
    expect(activeFilters({ ...EMPTY_LOG_FILTERS, deviceId: 'hub-4b', severity: 'ERROR' })).toMatchObject([
      { key: 'deviceId', label: 'Device ID', value: 'hub-4b' },
      { key: 'severity', label: 'Severity', value: 'ERROR' },
    ]);
  });

  it('uses SCRUM-53 parameter names and omits empty fields', () => {
    const query = toLogApiFilters({
      ...EMPTY_LOG_FILTERS,
      deviceId: 'hub-4b',
      severity: 'WARN',
      start: '2026-10-25T12:00:00.000Z',
    });
    expect(query.toString()).toBe('device_id=hub-4b&severity=WARN&start=2026-10-25T12%3A00%3A00.000Z');
  });

  it('shows no active filters after Clear all', () => {
    expect(activeFilters(EMPTY_LOG_FILTERS)).toEqual([]);
  });
});
