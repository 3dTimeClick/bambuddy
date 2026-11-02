/**
 * Tests for the filament color strip on printer cards.
 *
 * Every card shows one dot per loaded AMS / external slot, with a ring on the
 * tray in use: below the metrics line on size S (compact), below the model
 * line on the larger (expanded) sizes.
 */

import { describe, it, expect, afterEach, vi } from 'vitest';
import { screen, within } from '@testing-library/react';
import { render } from '../utils';
import { PrintersPage } from '../../pages/PrintersPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const mockPrinters = [
  {
    id: 1,
    name: 'X1 Carbon',
    ip_address: '192.168.1.100',
    serial_number: '00M09A350100001',
    access_code: '12345678',
    model: 'X1C',
    enabled: true,
    nozzle_diameter: 0.4,
    nozzle_type: 'hardened_steel',
    location: 'Workshop',
    auto_archive: true,
    is_active: true,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
  },
];

const tray = (id: number, color: string | null, type: string | null, state = 11) => ({
  id,
  tray_color: color,
  tray_type: type,
  tray_sub_brands: type,
  tray_id_name: null,
  tray_info_idx: null,
  remain: 60,
  k: null,
  cali_idx: null,
  tag_uid: null,
  tray_uuid: null,
  nozzle_temp_min: null,
  nozzle_temp_max: null,
  drying_temp: null,
  drying_time: null,
  state,
  exists: type !== null,
});

const status = {
  connected: true,
  state: 'RUNNING',
  progress: 42,
  layer_num: 120,
  total_layers: 267,
  remaining_time: 83,
  temperatures: { nozzle: 220, bed: 60, chamber: 35 },
  filename: 'test_print.3mf',
  wifi_signal: -50,
  speed_level: 2,
  hms_errors: [],
  ams: [
    {
      id: 0,
      humidity: 3,
      temp: 25,
      is_ams_ht: false,
      tray: [
        tray(0, '1C1C1CFF', 'PLA'),
        tray(1, 'F4F4F4FF', 'PLA'),
        tray(2, null, null, 9),
        tray(3, 'D62828FF', 'PETG'),
      ],
    },
  ],
  vt_tray: [tray(254, 'F2C200FF', 'TPU')],
  tray_now: 1,
};

function useStatus(s: Record<string, unknown>) {
  server.use(
    http.get('/api/v1/printers/', () => HttpResponse.json(mockPrinters)),
    http.get('/api/v1/queue/', () => HttpResponse.json([])),
    http.get('/api/v1/printers/:id/status', () => HttpResponse.json(s)),
  );
}

// The test harness replaces localStorage with vi.fn() stubs (setup.ts), so
// the card size has to be taught to getItem.
function useCardSize(size: '1' | '2') {
  vi.mocked(localStorage.getItem).mockImplementation((key: string) =>
    key === 'printerCardSize' ? size : null,
  );
}

const activeStates = (strip: HTMLElement) =>
  Array.from(strip.querySelectorAll('[data-active]')).map((d) => d.getAttribute('data-active'));

describe('PrintersPage — filament color strip', () => {
  afterEach(() => {
    vi.mocked(localStorage.getItem).mockReset();
  });

  it('shows loaded slots and marks the tray in use on a size S card', async () => {
    useCardSize('1');
    useStatus(status);

    render(<PrintersPage />);

    const strip = await screen.findByTestId('filament-swatch-strip');
    // 3 loaded AMS slots (the empty one is skipped) + the external spool.
    expect(activeStates(strip)).toEqual(['false', 'true', 'false', 'false']);
  });

  it('shows the strip on an expanded card too', async () => {
    useCardSize('2');
    useStatus(status);

    render(<PrintersPage />);

    const strip = await screen.findByTestId('filament-swatch-strip');
    expect(activeStates(strip)).toEqual(['false', 'true', 'false', 'false']);
  });

  it('marks the external spool when it is in use', async () => {
    useCardSize('1');
    useStatus({ ...status, tray_now: 254 });

    render(<PrintersPage />);

    const strip = await screen.findByTestId('filament-swatch-strip');
    expect(activeStates(strip)).toEqual(['false', 'false', 'false', 'true']);
  });

  it('marks nothing when no tray is loaded', async () => {
    useCardSize('1');
    useStatus({ ...status, tray_now: 255 });

    render(<PrintersPage />);

    const strip = await screen.findByTestId('filament-swatch-strip');
    expect(activeStates(strip)).not.toContain('true');
  });

  it('puts material and color details in each dot tooltip', async () => {
    useCardSize('1');
    useStatus(status);

    render(<PrintersPage />);

    const strip = await screen.findByTestId('filament-swatch-strip');
    expect(within(strip).getByTitle(/PETG.*A4/)).toBeInTheDocument();
  });

  it('renders no strip when the printer has no filament loaded', async () => {
    useCardSize('1');
    useStatus({ ...status, ams: [], vt_tray: [], tray_now: 255 });

    render(<PrintersPage />);

    // The compact metrics confirm the card rendered.
    expect(await screen.findByText('120/267')).toBeInTheDocument();
    expect(screen.queryByTestId('filament-swatch-strip')).not.toBeInTheDocument();
  });
});
