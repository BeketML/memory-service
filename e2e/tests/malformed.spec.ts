/**
 * Malformed input tests — task.md §5 "Hard Constraints" (resilience)
 *
 * Service must:
 *   • Return 4xx (not 5xx) for bad input
 *   • Never crash on malformed JSON, unicode, oversized payloads
 *   • Stay up and serve the next request after any bad input
 */
import { test, expect } from '@playwright/test';
import { uid } from './helpers';

test.describe('Input validation — 4xx, never crash', () => {

  test('POST /turns missing session_id → 4xx', async ({ request }) => {
    const res = await request.post('/turns', {
      data: {
        user_id: 'user-1',
        messages: [{ role: 'user', content: 'hello' }],
        timestamp: new Date().toISOString(),
      },
    });
    expect(res.status()).toBeGreaterThanOrEqual(400);
    expect(res.status()).toBeLessThan(500);
  });

  test('POST /turns empty messages array → 4xx', async ({ request }) => {
    const res = await request.post('/turns', {
      data: {
        session_id: uid('s'),
        user_id: uid('u'),
        messages: [],
        timestamp: new Date().toISOString(),
      },
    });
    expect(res.status()).toBeGreaterThanOrEqual(400);
    expect(res.status()).toBeLessThan(500);
  });

  test('POST /turns bad JSON body → 4xx', async ({ request }) => {
    const res = await request.post('/turns', {
      headers: { 'Content-Type': 'application/json' },
      data: 'this is not json',
    });
    expect(res.status()).toBeGreaterThanOrEqual(400);
    expect(res.status()).toBeLessThan(500);
  });

  test('POST /turns invalid timestamp → 4xx', async ({ request }) => {
    const res = await request.post('/turns', {
      data: {
        session_id: uid('s'),
        user_id: uid('u'),
        messages: [{ role: 'user', content: 'hi' }],
        timestamp: 'definitely-not-a-date',
      },
    });
    expect(res.status()).toBeGreaterThanOrEqual(400);
    expect(res.status()).toBeLessThan(500);
  });

  test('POST /recall missing query → 4xx', async ({ request }) => {
    const res = await request.post('/recall', {
      data: { session_id: uid('s'), max_tokens: 512 },
    });
    expect(res.status()).toBeGreaterThanOrEqual(400);
    expect(res.status()).toBeLessThan(500);
  });

  test('POST /recall empty query string → 400', async ({ request }) => {
    const res = await request.post('/recall', {
      data: { query: '', session_id: uid('s'), max_tokens: 512 },
    });
    expect(res.status()).toBe(400);
  });

  test('POST /search missing query → 4xx', async ({ request }) => {
    const res = await request.post('/search', {
      data: { limit: 5 },
    });
    expect(res.status()).toBeGreaterThanOrEqual(400);
    expect(res.status()).toBeLessThan(500);
  });

  test('POST /search empty query string → 400', async ({ request }) => {
    const res = await request.post('/search', {
      data: { query: '', limit: 5 },
    });
    expect(res.status()).toBe(400);
  });

  test('POST /turns with unicode content — 201 or 503, never 500-crash', async ({ request }) => {
    const res = await request.post('/turns', {
      data: {
        session_id: uid('unicode-sess'),
        user_id: uid('unicode-user'),
        messages: [
          { role: 'user', content: 'こんにちは！ベルリンに住んでいます 🐕 emoji 😀 مرحبا عالم' },
          { role: 'assistant', content: '素晴らしい！' },
        ],
        timestamp: new Date().toISOString(),
      },
    });
    // Should not be a 500-level crash — 201 (success) or 503 (LLM issue) both OK
    expect(res.status()).not.toBe(500);
    expect(res.status()).not.toBe(502);
  });

  test('POST /turns oversized message — handled, not crash', async ({ request }) => {
    const bigContent = 'x'.repeat(20_000);
    const res = await request.post('/turns', {
      data: {
        session_id: uid('big-sess'),
        user_id: uid('big-user'),
        messages: [
          { role: 'user', content: bigContent },
          { role: 'assistant', content: 'ok' },
        ],
        timestamp: new Date().toISOString(),
      },
    });
    // 201 (truncated and processed), 413 (too large), or 503 (LLM issue) — never 500 crash
    expect(res.status()).not.toBe(500);
  });

  test('service is still up after all bad inputs', async ({ request }) => {
    const res = await request.get('/health');
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body.status).toBe('ok');
  });
});
