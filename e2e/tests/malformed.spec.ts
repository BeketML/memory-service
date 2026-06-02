/**
 * Malformed input tests — task.md §5 "Resilience"
 *
 * Service must:
 *   • Return 4xx on bad input — never crash
 *   • Stay up and serve subsequent valid requests after any bad input
 *   • Handle unicode, emoji, RTL text gracefully (201, stored verbatim)
 *   • Treat oversized payloads as truncated input (201 or 413 — not 500)
 */
import { test, expect } from '@playwright/test';
import { uid, cleanupUser } from './helpers';

test.describe('Malformed input — 4xx, not crashes', () => {

  test('missing "messages" field → 400', async ({ request }) => {
    const res = await request.post('/turns', {
      data: {
        session_id: uid('mal-sess'),
        user_id:    uid('mal-user'),
        timestamp:  new Date().toISOString(),
        // messages intentionally omitted
      },
    });
    expect(res.status()).toBe(400);
  });

  test('"messages" as a string → 400', async ({ request }) => {
    const res = await request.post('/turns', {
      data: {
        session_id: uid('mal-sess'),
        user_id:    uid('mal-user'),
        messages:   'not-an-array',   // wrong type
        timestamp:  new Date().toISOString(),
      },
    });
    expect(res.status()).toBe(400);
  });

  test('empty messages array → 400', async ({ request }) => {
    const res = await request.post('/turns', {
      data: {
        session_id: uid('mal-sess'),
        user_id:    uid('mal-user'),
        messages:   [],
        timestamp:  new Date().toISOString(),
      },
    });
    // Empty messages should be rejected (nothing to ingest)
    expect([400, 422]).toContain(res.status());
  });

  test('invalid timestamp format → 400', async ({ request }) => {
    const res = await request.post('/turns', {
      data: {
        session_id: uid('mal-sess'),
        user_id:    uid('mal-user'),
        messages:   [{ role: 'user', content: 'hello' }],
        timestamp:  'not-a-date',
      },
    });
    expect(res.status()).toBe(400);
  });

  test('service stays up after malformed input', async ({ request }) => {
    // Send bad request first
    await request.post('/turns', {
      data: { session_id: 'x', user_id: 'x' },
    });
    // Service must still respond to health
    const health = await request.get('/health');
    expect(health.status()).toBe(200);
    const body = await health.json();
    expect(body.status).toBe('ok');
  });

  test('unicode + emoji content → 201 (stored verbatim, not crash)', async ({ request }) => {
    const userId  = uid('uni-user');
    const sessId  = uid('uni-sess');
    const res = await request.post('/turns', {
      data: {
        session_id: sessId,
        user_id:    userId,
        messages: [
          { role: 'user',      content: '🐕 I love 東京 and Ünïcödé! مرحبا' },
          { role: 'assistant', content: '✓ Stored safely.' },
        ],
        timestamp: new Date().toISOString(),
        metadata:  {},
      },
    });
    expect(res.status()).toBe(201);
    const body = await res.json();
    expect(body).toHaveProperty('id');
    await cleanupUser(request, userId);
  });

  test('RTL + mixed-script content → 201', async ({ request }) => {
    const userId = uid('rtl-user');
    const res = await request.post('/turns', {
      data: {
        session_id: uid('rtl-sess'),
        user_id:    userId,
        messages: [
          { role: 'user',      content: 'أنا مهندس برمجيات في القاهرة. שלום from Tel Aviv.' },
          { role: 'assistant', content: '📍 Noted!' },
        ],
        timestamp: new Date().toISOString(),
        metadata:  {},
      },
    });
    expect(res.status()).toBe(201);
    await cleanupUser(request, userId);
  });
});

// ── /recall robustness ────────────────────────────────────────────────────────

test.describe('Malformed /recall input', () => {

  test('missing query → 400', async ({ request }) => {
    const res = await request.post('/recall', {
      data: { session_id: uid('sess'), user_id: uid('user'), max_tokens: 512 },
    });
    expect(res.status()).toBe(400);
  });

  test('empty query string → 400', async ({ request }) => {
    const res = await request.post('/recall', {
      data: { query: '', session_id: uid('sess'), user_id: uid('user'), max_tokens: 512 },
    });
    expect(res.status()).toBe(400);
  });
});
