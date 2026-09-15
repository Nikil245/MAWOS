import { afterEach, describe, expect, it, vi } from 'vitest';
import viteConfig from '../../vite.config.js';
import { api, buildChatPayload, request } from '../services/api';

describe('separate Vite/frontend runtime', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('runs on port 5173 and proxies relative API paths to FastAPI', () => {
    expect(viteConfig.server).toMatchObject({
      host: '127.0.0.1', port: 5173, strictPort: true,
      proxy: { '/api': 'http://127.0.0.1:8000' },
    });
  });

  it('uses a relative API URL so Vite can proxy authenticated requests', async () => {
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: () => Promise.resolve({ ok: true }),
    });
    vi.stubGlobal('fetch', fetch);

    await expect(request('/student/dashboard', { token: 'token' }))
      .resolves.toEqual({ ok: true });
    expect(fetch).toHaveBeenCalledWith('/api/student/dashboard', expect.objectContaining({
      headers: { Authorization: 'Bearer token' },
    }));
  });

  it('always posts the current chat message when optional context cannot serialize', async () => {
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: () => Promise.resolve({ text: 'Overall attendance: 82%' }),
    });
    vi.stubGlobal('fetch', fetch);
    const circular = {};
    circular.self = circular;

    await api.chat('student-token', 'show me my attendance status', undefined, 'invalid_topic', circular);

    expect(fetch).toHaveBeenCalledTimes(1);
    const [url, options] = fetch.mock.calls[0];
    expect(url).toBe('/api/chat');
    expect(options.method).toBe('POST');
    expect(JSON.parse(options.body)).toEqual({ message: 'show me my attendance status' });
  });

  it('drops malformed optional pairs without changing the required payload', () => {
    expect(buildChatPayload('show me my attendance status', 'attendance', [
      { role: 'user', category: 'general_ai', content: 'prior question' },
      { role: 'assistant', category: 'general_ai', content: 'tampered answer', proof: 'bad' },
    ])).toEqual({ message: 'show me my attendance status', context_topic: 'attendance' });
  });
});
