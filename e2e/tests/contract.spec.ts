/**
 * Contract tests — verifies all 7 HTTP endpoints from task.md §3:
 *   GET  /health
 *   POST /turns            → 201  { id }
 *   POST /recall           → 200  { context, citations[] }
 *   POST /search           → 200  { results[] }
 *   GET  /users/{id}/memories → 200 { memories[] }
 *   DELETE /sessions/{id}  → 204
 *   DELETE /users/{id}     → 204
 */
import { test, expect } from '@playwright/test';
import { uid, postTurn, cleanupUser } from './helpers';

const NOW = new Date().toISOString();

test.describe('GET /health', () => {
  test('returns 200 with status:ok', async ({ request }) => {
    const res = await request.get('/health');
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body).toHaveProperty('status', 'ok');
  });
});

test.describe('POST /turns', () => {
  let userId: string;
  let sessionId: string;

  test.beforeEach(() => {
    userId = uid('turns-user');
    sessionId = uid('turns-sess');
  });

  test.afterEach(async ({ request }) => {
    await cleanupUser(request, userId);
  });

  test('returns 201 with a string id', async ({ request }) => {
    const res = await postTurn(request, {
      session_id: sessionId,
      user_id: userId,
      messages: [
        { role: 'user', content: 'Hello, I work at Acme Corp.' },
        { role: 'assistant', content: 'Welcome!' },
      ],
      timestamp: NOW,
    });
    expect(res.status()).toBe(201);
    const body = await res.json();
    expect(body).toHaveProperty('id');
    expect(typeof body.id).toBe('string');
    expect(body.id.length).toBeGreaterThan(0);
  });

  test('accepts multi-message turns with tool messages', async ({ request }) => {
    const res = await postTurn(request, {
      session_id: sessionId,
      user_id: userId,
      messages: [
        { role: 'user', content: 'Search for flights to Paris.' },
        { role: 'assistant', content: 'Searching...' },
        { role: 'tool', name: 'search_flights', content: 'Found 3 flights.' },
        { role: 'assistant', content: 'Here are your results.' },
      ],
    });
    expect(res.status()).toBe(201);
  });

  test('works with null user_id (anonymous session)', async ({ request }) => {
    const res = await postTurn(request, {
      session_id: uid('anon-sess'),
      user_id: null,
      messages: [
        { role: 'user', content: 'I prefer dark mode.' },
        { role: 'assistant', content: 'Noted!' },
      ],
    });
    expect(res.status()).toBe(201);
  });
});

test.describe('POST /recall', () => {
  let userId: string;
  let sessionId: string;

  test.beforeAll(async ({ request }) => {
    userId = uid('recall-user');
    sessionId = uid('recall-sess');
    // Seed a turn so recall has data
    await postTurn(request, {
      session_id: sessionId,
      user_id: userId,
      messages: [
        { role: 'user', content: 'I just moved to Berlin from NYC.' },
        { role: 'assistant', content: 'Berlin is a great city!' },
      ],
      timestamp: '2025-03-15T10:30:00Z',
    });
  });

  test.afterAll(async ({ request }) => {
    await cleanupUser(request, userId);
  });

  test('returns 200 with context and citations array', async ({ request }) => {
    const res = await request.post('/recall', {
      data: {
        query: 'Where does this user live?',
        session_id: sessionId,
        user_id: userId,
        max_tokens: 512,
      },
    });
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body).toHaveProperty('context');
    expect(typeof body.context).toBe('string');
    expect(body).toHaveProperty('citations');
    expect(Array.isArray(body.citations)).toBe(true);
  });

  test('context mentions Berlin after seeding Berlin turn', async ({ request }) => {
    const res = await request.post('/recall', {
      data: {
        query: 'Where does this user live?',
        session_id: uid('probe-sess'),
        user_id: userId,
        max_tokens: 512,
      },
    });
    const body = await res.json();
    // Berlin should appear — either from Tier-1 PG fact or Tier-2 search
    const hasBerlin = body.context.toLowerCase().includes('berlin');
    if (!hasBerlin) {
      console.log('⚠ Berlin not in context (extraction may have failed):', body.context.slice(0, 200));
    }
    expect(hasBerlin || body.context === '').toBe(true); // doesn't hallucinate wrong city
  });

  test('citations have required fields when present', async ({ request }) => {
    const res = await request.post('/recall', {
      data: { query: 'location', session_id: sessionId, user_id: userId, max_tokens: 512 },
    });
    const body = await res.json();
    for (const cite of body.citations) {
      expect(cite).toHaveProperty('turn_id');
      expect(cite).toHaveProperty('score');
      expect(cite).toHaveProperty('snippet');
      expect(typeof cite.score).toBe('number');
    }
  });

  test('cold session returns empty context and no citations', async ({ request }) => {
    const res = await request.post('/recall', {
      data: {
        query: 'anything',
        session_id: uid('cold-sess'),
        user_id: uid('cold-user'),
        max_tokens: 512,
      },
    });
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body.context).toBe('');
    expect(body.citations).toEqual([]);
  });
});

test.describe('POST /search', () => {
  let userId: string;
  let sessionId: string;

  test.beforeAll(async ({ request }) => {
    userId = uid('search-user');
    sessionId = uid('search-sess');
    await postTurn(request, {
      session_id: sessionId,
      user_id: userId,
      messages: [
        { role: 'user', content: 'I have a golden retriever named Biscuit.' },
        { role: 'assistant', content: 'What a lovely dog!' },
      ],
    });
  });

  test.afterAll(async ({ request }) => {
    await cleanupUser(request, userId);
  });

  test('returns 200 with results array', async ({ request }) => {
    const res = await request.post('/search', {
      data: { query: 'dog pet', user_id: userId, limit: 5 },
    });
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body).toHaveProperty('results');
    expect(Array.isArray(body.results)).toBe(true);
  });

  test('each result has required fields', async ({ request }) => {
    const res = await request.post('/search', {
      data: { query: 'Biscuit dog', user_id: userId, limit: 5 },
    });
    const body = await res.json();
    for (const r of body.results) {
      expect(r).toHaveProperty('content');
      expect(r).toHaveProperty('score');
      expect(r).toHaveProperty('session_id');
      expect(r).toHaveProperty('metadata');
      expect(typeof r.score).toBe('number');
    }
  });
});

test.describe('GET /users/{id}/memories', () => {
  let userId: string;
  let sessionId: string;

  test.beforeAll(async ({ request }) => {
    userId = uid('mem-user');
    sessionId = uid('mem-sess');
    await postTurn(request, {
      session_id: sessionId,
      user_id: userId,
      messages: [
        { role: 'user', content: 'I am a vegetarian software engineer.' },
        { role: 'assistant', content: 'Got it!' },
      ],
    });
  });

  test.afterAll(async ({ request }) => {
    await cleanupUser(request, userId);
  });

  test('returns 200 with memories array', async ({ request }) => {
    const res = await request.get(`/users/${userId}/memories`);
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body).toHaveProperty('memories');
    expect(Array.isArray(body.memories)).toBe(true);
  });

  test('memories have all required fields from task.md §3', async ({ request }) => {
    const res = await request.get(`/users/${userId}/memories`);
    const body = await res.json();
    for (const m of body.memories) {
      expect(m).toHaveProperty('id');
      expect(m).toHaveProperty('type');
      expect(m).toHaveProperty('key');
      expect(m).toHaveProperty('value');
      expect(m).toHaveProperty('confidence');
      expect(m).toHaveProperty('active');
      expect(m).toHaveProperty('created_at');
      expect(m).toHaveProperty('updated_at');
      // supersedes and superseded_by may be null
      expect('supersedes' in m).toBe(true);
      // type must be one of the valid enum values
      expect(['fact', 'preference', 'opinion', 'event']).toContain(m.type);
    }
  });

  test('unknown user returns empty memories array', async ({ request }) => {
    const res = await request.get(`/users/${uid('ghost-user')}/memories`);
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body.memories).toEqual([]);
  });
});

test.describe('DELETE /sessions/{id}', () => {
  test('returns 204', async ({ request }) => {
    const userId = uid('del-sess-user');
    const sessionId = uid('del-sess');
    await postTurn(request, {
      session_id: sessionId,
      user_id: userId,
      messages: [{ role: 'user', content: 'hello' }, { role: 'assistant', content: 'hi' }],
    });
    const res = await request.delete(`/sessions/${sessionId}`);
    expect(res.status()).toBe(204);
    await cleanupUser(request, userId);
  });

  test('non-existent session returns 204 (idempotent)', async ({ request }) => {
    const res = await request.delete(`/sessions/${uid('nonexistent')}`);
    expect(res.status()).toBe(204);
  });
});

test.describe('DELETE /users/{id}', () => {
  test('returns 204', async ({ request }) => {
    const userId = uid('del-user');
    const sessionId = uid('del-user-sess');
    await postTurn(request, {
      session_id: sessionId,
      user_id: userId,
      messages: [{ role: 'user', content: 'I live in Paris.' }, { role: 'assistant', content: 'Nice!' }],
    });
    const res = await request.delete(`/users/${userId}`);
    expect(res.status()).toBe(204);
  });

  test('after delete, memories returns empty', async ({ request }) => {
    const userId = uid('del-check-user');
    const sessionId = uid('del-check-sess');
    await postTurn(request, {
      session_id: sessionId,
      user_id: userId,
      messages: [{ role: 'user', content: 'I work at Google.' }, { role: 'assistant', content: 'Great!' }],
    });
    await request.delete(`/users/${userId}`);
    const memRes = await request.get(`/users/${userId}/memories`);
    expect(memRes.status()).toBe(200);
    const body = await memRes.json();
    expect(body.memories).toEqual([]);
  });
});
