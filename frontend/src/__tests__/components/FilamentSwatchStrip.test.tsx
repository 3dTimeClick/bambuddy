import { describe, it, expect } from 'vitest';
import { render, screen } from '../utils';
import FilamentSwatchStrip from '../../components/FilamentSwatchStrip';
import type { AMSUnit, AMSTray } from '../../api/client';

const tray = (id: number, color: string | null, type: string | null, extra: Partial<AMSTray> = {}): AMSTray => ({
  id,
  tray_color: color,
  tray_type: type,
  tray_sub_brands: type,
  tray_id_name: null,
  tray_info_idx: null,
  remain: 50,
  k: null,
  cali_idx: null,
  tag_uid: null,
  tray_uuid: null,
  nozzle_temp_min: null,
  nozzle_temp_max: null,
  drying_temp: null,
  drying_time: null,
  state: 11,
  exists: true,
  ...extra,
});

const unit = (id: number, trays: AMSTray[], extra: Partial<AMSUnit> = {}): AMSUnit =>
  ({ id, humidity: 0, temp: 0, is_ams_ht: false, tray: trays, ...extra }) as unknown as AMSUnit;

const dots = () => screen.getByTestId('filament-swatch-strip').querySelectorAll('[data-active]');

describe('FilamentSwatchStrip', () => {
  it('lists loaded slots and skips empty ones', () => {
    const ams = [unit(0, [
      tray(0, '1C1C1CFF', 'PLA'),
      tray(1, null, null, { state: 9, exists: false }),
      tray(2, 'D62828FF', 'PETG'),
    ])];
    render(<FilamentSwatchStrip ams={ams} trayNow={255} />);
    expect(dots()).toHaveLength(2);
  });

  it('marks only the tray in use', () => {
    const ams = [unit(0, [tray(0, '1C1C1CFF', 'PLA'), tray(1, 'F4F4F4FF', 'PLA')])];
    render(<FilamentSwatchStrip ams={ams} trayNow={1} />);
    const active = Array.from(dots()).map((d) => d.getAttribute('data-active'));
    expect(active).toEqual(['false', 'true']);
  });

  it('marks nothing when no tray is loaded', () => {
    const ams = [unit(0, [tray(0, '1C1C1CFF', 'PLA')])];
    render(<FilamentSwatchStrip ams={ams} trayNow={undefined} />);
    expect(dots()[0].getAttribute('data-active')).toBe('false');
  });

  it('uses the AMS-HT unit id as the global tray id', () => {
    const ams = [unit(128, [tray(0, 'F2C200FF', 'PLA')], { is_ams_ht: true })];
    render(<FilamentSwatchStrip ams={ams} trayNow={128} />);
    expect(dots()[0].getAttribute('data-active')).toBe('true');
  });

  it('shows the external spool and marks it when tray_now is 254', () => {
    const ams = [unit(0, [tray(0, '1C1C1CFF', 'PLA')])];
    render(<FilamentSwatchStrip ams={ams} vtTray={[tray(254, 'F2C200FF', 'TPU')]} trayNow={254} />);
    const active = Array.from(dots()).map((d) => d.getAttribute('data-active'));
    expect(active).toEqual(['false', 'true']);
  });

  it('picks the external spool by active extruder on dual nozzle', () => {
    const vt = [tray(254, 'F2C200FF', 'TPU'), tray(255, '2A6FDBFF', 'PLA')];
    render(<FilamentSwatchStrip ams={[]} vtTray={vt} trayNow={254} isDualNozzle activeExtruder={0} />);
    const active = Array.from(dots()).map((d) => d.getAttribute('data-active'));
    expect(active).toEqual(['false', 'true']);
  });

  it('includes material and remaining percent in the tooltip', () => {
    const ams = [unit(0, [tray(0, 'D62828FF', 'PETG', { remain: 45 })])];
    render(<FilamentSwatchStrip ams={ams} />);
    const title = dots()[0].getAttribute('title') ?? '';
    expect(title).toContain('PETG');
    expect(title).toContain('45');
  });

  it('renders nothing when there is no loaded filament', () => {
    render(<FilamentSwatchStrip ams={[unit(0, [tray(0, null, null, { state: 9, exists: false })])]} />);
    expect(screen.queryByTestId('filament-swatch-strip')).toBeNull();
  });
});
