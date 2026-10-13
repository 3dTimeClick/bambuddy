import { useState } from 'react';

/**
 * A random id for one camera viewer, stable while it is mounted. It goes on
 * the viewer's stream URL and on its /camera/stop, so the stop ends that
 * viewer's own external camera stream and leaves other viewers' running
 * (external cameras run one stream per viewer, unseen by the fan-out guard).
 *
 * Math.random, not crypto.randomUUID: that needs a secure context, and
 * Bambuddy is often opened over plain http on the LAN.
 */
export function useCameraViewerId(): string {
  const [id] = useState(() => `v${Math.random().toString(36).slice(2, 12)}`);
  return id;
}
