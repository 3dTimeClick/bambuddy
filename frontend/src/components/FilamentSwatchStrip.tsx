import { useTranslation } from 'react-i18next';
import type { AMSUnit, AMSTray } from '../api/client';
import { getEmptySlotKind, getGlobalTrayId } from '../utils/amsHelpers';
import { getColorName, getSwatchStyle } from '../utils/colors';

interface FilamentSwatchStripProps {
  ams: AMSUnit[];
  vtTray?: AMSTray[];
  /** Global tray id of the loaded tray (254 = external, undefined/255 = none). */
  trayNow?: number;
  isDualNozzle?: boolean;
  /** Dual-nozzle only: 0 = right, 1 = left. */
  activeExtruder?: number;
  /** AMS unit id -> extruder id (dual-nozzle), used to group by hotend. */
  amsExtruderMap?: Record<string, number>;
  className?: string;
}

interface Swatch {
  key: string;
  color: string | null;
  tip: string;
  active: boolean;
}

interface Group {
  key: string;
  tag?: string;
  swatches: Swatch[];
}

function describe(tray: AMSTray, where: string, t: (k: string, o?: Record<string, unknown>) => string): string {
  const material = tray.tray_sub_brands || tray.tray_type || '';
  const name = tray.tray_color ? getColorName(tray.tray_color, tray.tray_sub_brands) : '';
  const parts = [material, name].filter(Boolean);
  let text = parts.join(' · ');
  if (tray.remain >= 0) text += ` · ${t('printers.filamentStrip.remaining', { percent: tray.remain })}`;
  return `${text} (${where})`;
}

/**
 * Compact row of colour dots: every loaded AMS / AMS-HT slot and external spool
 * of a printer, with a ring on the one currently in use. Empty slots are skipped.
 */
export default function FilamentSwatchStrip({
  ams,
  vtTray = [],
  trayNow,
  isDualNozzle = false,
  activeExtruder,
  amsExtruderMap = {},
  className = '',
}: FilamentSwatchStripProps) {
  const { t } = useTranslation();

  const groups: Group[] = [];
  const sideTag = (extruder: number | undefined) =>
    isDualNozzle && extruder !== undefined
      ? t(extruder === 1 ? 'printers.filamentStrip.left' : 'printers.filamentStrip.right')
      : undefined;

  for (const unit of ams) {
    const swatches: Swatch[] = [];
    for (const tray of unit.tray || []) {
      if (getEmptySlotKind(tray) !== null) continue;
      const slot = tray.id ?? 0;
      const globalId = getGlobalTrayId(unit.id, slot, false);
      const letter = String.fromCharCode(65 + (unit.id >= 128 ? unit.id - 128 : unit.id));
      swatches.push({
        key: `ams-${unit.id}-${slot}`,
        color: tray.tray_color,
        tip: describe(tray, unit.is_ams_ht ? `HT-${letter}` : `${letter}${slot + 1}`, t),
        active: trayNow === globalId,
      });
    }
    if (swatches.length > 0) {
      groups.push({ key: `ams-${unit.id}`, tag: sideTag(amsExtruderMap[String(unit.id)]), swatches });
    }
  }

  const external: Swatch[] = [];
  for (const tray of [...vtTray].sort((a, b) => (a.id ?? 254) - (b.id ?? 254))) {
    if (getEmptySlotKind(tray) !== null) continue;
    const id = tray.id ?? 254;
    const active = isDualNozzle && trayNow === 254
      ? (id === 254 && activeExtruder === 1) || (id === 255 && activeExtruder === 0)
      : trayNow === id;
    const where = isDualNozzle
      ? t(id === 254 ? 'printers.extL' : 'printers.extR')
      : t('printers.filamentStrip.external');
    external.push({ key: `ext-${id}`, color: tray.tray_color, tip: describe(tray, where, t), active });
  }
  if (external.length > 0) groups.push({ key: 'ext', swatches: external });

  if (groups.length === 0) return null;

  return (
    <div
      className={`flex flex-wrap items-center gap-x-3 gap-y-1 p-1 ${className}`}
      data-testid="filament-swatch-strip"
    >
      {groups.map((group) => (
        <div key={group.key} className="flex items-center gap-1.5">
          {group.tag && (
            <span className="text-[length:var(--pc-t10,10px)] font-bold text-bambu-gray">{group.tag}</span>
          )}
          {group.swatches.map((s) => (
            <span
              key={s.key}
              title={s.tip}
              aria-label={s.tip}
              data-active={s.active ? 'true' : 'false'}
              className={`block h-[var(--pc-i4,1rem)] w-[var(--pc-i4,1rem)] shrink-0 rounded-full border border-white/20 ${
                s.active ? 'ring-2 ring-bambu-green ring-offset-2 ring-offset-bambu-dark-secondary' : ''
              }`}
              style={getSwatchStyle(s.color)}
            />
          ))}
        </div>
      ))}
    </div>
  );
}
