import { StrictMode } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { render } from '../utils';
import { EmbeddedCameraViewer } from '../../components/EmbeddedCameraViewer';

// A detached img keeps its MJPEG connection open (measured in Chromium): the
// backend stream, the chamber light it holds and the /camera/stop it blocks
// stay until the browser closes. Every img the viewer drops has its src removed.

async function settle() {
  await act(async () => {
    await Promise.resolve();
  });
}

function viewer() {
  return <EmbeddedCameraViewer printerId={7} printerName="P1S" onClose={() => {}} />;
}

describe('EmbeddedCameraViewer stream lifetime', () => {
  beforeEach(() => {
    localStorage.clear();
    server.use(
      http.post('/api/v1/printers/:id/camera/stop', () => HttpResponse.json({ success: true })),
      http.get('/api/v1/printers/:id/camera/status', () => HttpResponse.json({ active: true, stalled: false })),
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('closes the stream when the viewer is minimized', async () => {
    render(viewer());
    await settle();
    const img = screen.getByAltText('Camera stream');
    expect(img.getAttribute('src')).toContain('/api/v1/printers/7/camera/stream');

    fireEvent.click(screen.getByTitle('Minimize'));
    await settle();

    expect(img.getAttribute('src')).toBeNull();
  });

  it('closes the current stream on close after the stream was restarted', async () => {
    const { unmount } = render(viewer());
    await settle();
    const first = screen.getByAltText('Camera stream');
    fireEvent.load(first);
    await waitFor(() => expect(screen.getByTitle('Refresh stream')).not.toBeDisabled());

    fireEvent.click(screen.getByTitle('Refresh stream'));
    await waitFor(() => expect(screen.getByAltText('Camera stream')).not.toBe(first));
    const current = screen.getByAltText('Camera stream');
    expect(current.getAttribute('src')).toContain('/camera/stream');

    unmount();
    await settle();

    expect(current.getAttribute('src')).toBeNull();
  });

  it('takes the error of a closed img for no stream failure', async () => {
    render(viewer());
    await settle();
    const img = screen.getByAltText('Camera stream');

    await act(async () => {
      img.setAttribute('src', ''); // what the reconnect and restart code does
      img.dispatchEvent(new Event('error'));
    });

    expect(screen.queryByText(/Reconnecting in/)).toBeNull();
  });

  it('stops its own stream by the viewer id it opened it with', async () => {
    const fetchMock = vi.spyOn(global, 'fetch');
    const { unmount } = render(viewer());
    await settle();
    const id = new URL((screen.getByAltText('Camera stream') as HTMLImageElement).src).searchParams.get('viewer');
    expect(id).toMatch(/^[A-Za-z0-9_-]{1,64}$/);

    unmount();

    const stop = fetchMock.mock.calls.map(([url]) => String(url)).find((url) => url.includes('/camera/stop'));
    expect(new URL(stop!, 'http://x').searchParams.get('viewer')).toBe(id);
  });

  it('names itself on the stop its Refresh sends', async () => {
    const fetchMock = vi.spyOn(global, 'fetch');
    render(viewer());
    await settle();
    const img = screen.getByAltText('Camera stream');
    const id = new URL((img as HTMLImageElement).src).searchParams.get('viewer');
    fireEvent.load(img);
    await waitFor(() => expect(screen.getByTitle('Refresh stream')).not.toBeDisabled());

    fireEvent.click(screen.getByTitle('Refresh stream'));

    const stop = fetchMock.mock.calls.map(([url]) => String(url)).find((url) => url.includes('/camera/stop'));
    expect(new URL(stop!, 'http://x').searchParams.get('viewer')).toBe(id);
  });

  it('keeps its stream under StrictMode', async () => {
    render(<StrictMode>{viewer()}</StrictMode>);
    await settle();

    expect(screen.getByAltText('Camera stream').getAttribute('src')).toContain('/api/v1/printers/7/camera/stream');
  });
});
