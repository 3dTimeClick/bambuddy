/**
 * Two settings the page showed and let the user change but never saved:
 * "Max copies per run" (a 100-copy pipeline run stayed refused at 50) and the
 * Docker compose directory used in the update instructions.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { SettingsPage } from '../../pages/SettingsPage';
import { setAuthToken } from '../../api/client';

const settings = {
  auto_archive: true,
  save_thumbnails: true,
  capture_finish_photo: true,
  default_filament_cost: 25.0,
  currency: 'USD',
  time_format: 'system',
  date_format: 'system',
  check_updates: true,
  check_printer_firmware: false,
  pipeline_max_copies: 50,
  docker_compose_dir: '',
};

describe('SettingsPage fields that were never saved', () => {
  let saves: Record<string, unknown>[];

  beforeEach(() => {
    window.history.replaceState({}, '', '/');
    vi.mocked(localStorage.getItem).mockReset();
    localStorage.clear();
    setAuthToken(null);
    saves = [];
    server.use(
      http.get('/api/v1/settings/', () => HttpResponse.json(settings)),
      http.put('/api/v1/settings/', async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        saves.push(body);
        return HttpResponse.json({ ...settings, ...body });
      }),
      http.get('/api/v1/updates/check', () =>
        HttpResponse.json({
          update_available: true,
          current_version: '1.0.0',
          latest_version: '9.9.9',
          is_docker: true,
          compose_dir_detected: '',
        }),
      ),
      http.get('/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: false, requires_setup: false })),
    );
  });

  async function open(tab?: string) {
    const user = userEvent.setup();
    render(<SettingsPage />);
    await screen.findByText('Date Format');
    if (tab) await user.click(screen.getByText(tab));
    // The page suppresses auto-save right after the settings load.
    await new Promise((resolve) => setTimeout(resolve, 200));
    return user;
  }

  it('saves the maximum copies per pipeline run', async () => {
    await open('Workflow');

    const input = await screen.findByLabelText('Max copies per run');
    fireEvent.change(input, { target: { value: '200' } });

    await waitFor(() => expect(saves.at(-1)).toMatchObject({ pipeline_max_copies: 200 }), { timeout: 3000 });
  });

  it('saves the Docker compose directory', async () => {
    const user = await open();

    const field = await screen.findByPlaceholderText('/opt/bambuddy');
    await user.type(field, '/srv/bambuddy');

    await waitFor(() => expect(saves.at(-1)).toMatchObject({ docker_compose_dir: '/srv/bambuddy' }), {
      timeout: 3000,
    });
  });
});
