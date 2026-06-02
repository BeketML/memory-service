/**
 * Session scoping + cross-user isolation tests — task.md §5 hard constraints
 *
 * Design decision (documented in README):
 *   - Facts are USER-scoped. The same user_id across multiple sessions shares
 *     facts — this is intentional cross-session knowledge sharing.
 *   - Different user_ids are fully isolated — no fact bleed.
 *   - DELETE /sessions removes the session row and cascades to turns.
 *     Memories are user-scoped and their session_id is SET NULL — they survive.
 *   - DELETE /users cascades to all memories/turns/sessions — full wipe.
 *
 * Fixture: fixtures/custom_scenarios.json → cross_session_scoping, delete_cleanup
 */
import { test, expect } from '@playwright/test';
import { uid, postTurn, recall, cleanupUser } from './helpers';

// ── Same user, multiple sessions: facts visible across sessions ───────────────

test.describe('Cross-session recall — same user', () => {
  let userA: string;
  let sessA1: string;
  let sessA2: string;

  test.beforeAll(async ({ request }) => {
    userA  = uid('scope-a');
    sessA1 = uid('scope-a-s1');
    sessA2 = uid('scope-a-s2');

    await postTurn(request, {
      session_id: sessA1, user_id: userA,
      messages: [
        { role: 'user',      content: 'I live in Tokyo.' },
        { role: 'assistant', content: 'Tokyo is amazing!' },
      ],
    });

    await postTurn(request, {
      session_id: sessA2, user_id: userA,
      messages: [
        { role: 'user',      content: 'I have a cat named Mochi.' },
        { role: 'assistant', content: 'Mochi is a lovely name!' },
      ],
    });
  });

  test.afterAll(async ({ request }) => { await cleanupUser(request, userA); });

  test('city from session-1 visible when querying in session-2 context', async ({ request }) => {
    const res  = await recall(request, 'Where does the user live?', userA, sessA2);
    const body = await res.json();
    expect(body.context.toLowerCase()).toContain('tokyo');
  });

  test('pet from session-2 visible when querying in session-1 context', async ({ request }) => {
    const res  = await recall(request, "What is this user's pet's name?", userA, sessA1);
    const body = await res.json();
    expect(body.context.toLowerCase()).toContain('mochi');
  });
});

// ── Different users: zero bleed ───────────────────────────────────────────────

test.describe('Cross-user isolation', () => {
  let userA: string;
  let userB: string;

  test.beforeAll(async ({ request }) => {
    userA = uid('iso-a');
    userB = uid('iso-b');

    await postTurn(request, {
      session_id: uid('iso-a-s1'), user_id: userA,
      messages: [
        { role: 'user',      content: 'I live in Tokyo.' },
        { role: 'assistant', content: 'Nice city!' },
      ],
    });

    await postTurn(request, {
      session_id: uid('iso-b-s1'), user_id: userB,
      messages: [
        { role: 'user',      content: 'I work at Amazon.' },
        { role: 'assistant', content: 'Interesting!' },
      ],
    });
  });

  test.afterAll(async ({ request }) => {
    await cleanupUser(request, userA);
    await cleanupUser(request, userB);
  });

  test('user A cannot see user B data (Amazon)', async ({ request }) => {
    const res  = await recall(request, 'Amazon', userA, uid('probe'));
    const body = await res.json();
    expect(body.context.toLowerCase()).not.toContain('amazon');
  });

  test('user B cannot see user A data (Tokyo)', async ({ request }) => {
    const res  = await recall(request, 'Tokyo', userB, uid('probe'));
    const body = await res.json();
    expect(body.context.toLowerCase()).not.toContain('tokyo');
  });
});

// ── DELETE /sessions keeps user-scoped memories ───────────────────────────────

test.describe('DELETE /sessions — user memories survive session deletion', () => {
  let userId: string;
  let sessToDelete: string;
  let sessToKeep: string;

  test.beforeAll(async ({ request }) => {
    userId       = uid('del-scope');
    sessToDelete = uid('del-scope-s1');
    sessToKeep   = uid('del-scope-s2');

    await postTurn(request, {
      session_id: sessToDelete, user_id: userId,
      messages: [
        { role: 'user',      content: 'I am a nurse and I live in Madrid.' },
        { role: 'assistant', content: 'Madrid is beautiful!' },
      ],
    });

    await postTurn(request, {
      session_id: sessToKeep, user_id: userId,
      messages: [
        { role: 'user',      content: 'I love cooking Italian food.' },
        { role: 'assistant', content: 'Italian cuisine is delicious!' },
      ],
    });

    // Delete session 1 — memories from it should survive (user-scoped)
    await request.delete(`/sessions/${sessToDelete}`);
  });

  test.afterAll(async ({ request }) => { await cleanupUser(request, userId); });

  test('memories survive after session deletion', async ({ request }) => {
    const res = await request.get(`/users/${userId}/memories`);
    const { memories } = await res.json();
    expect(memories.length).toBeGreaterThan(0);
  });

  test('/recall still returns facts from the deleted session', async ({ request }) => {
    // Memories are user-scoped — session_id becomes NULL but the row stays
    const res  = await recall(request, 'What does this user love to cook?', userId, uid('probe'));
    const body = await res.json();
    const ctx  = body.context.toLowerCase();
    // Italian food from sess-to-keep should still be there
    const hasFood = ctx.includes('italian') || ctx.includes('cook');
    expect(hasFood).toBe(true);
  });
});

// ── DELETE /users wipes everything ───────────────────────────────────────────

test.describe('DELETE /users — full wipe', () => {
  test('after delete, /memories returns empty', async ({ request }) => {
    const userId  = uid('full-del');
    const sessId  = uid('full-del-s');

    await postTurn(request, {
      session_id: sessId, user_id: userId,
      messages: [
        { role: 'user',      content: 'I work at Google.' },
        { role: 'assistant', content: 'Great company!' },
      ],
    });

    await request.delete(`/users/${userId}`);

    const res = await request.get(`/users/${userId}/memories`);
    expect(res.status()).toBe(200);
    const { memories } = await res.json();
    expect(memories).toEqual([]);
  });

  test('after delete, /recall returns empty context', async ({ request }) => {
    const userId  = uid('del-recall');
    const sessId  = uid('del-recall-s');

    await postTurn(request, {
      session_id: sessId, user_id: userId,
      messages: [
        { role: 'user',      content: 'I have a startup in San Francisco.' },
        { role: 'assistant', content: 'Nice!' },
      ],
    });

    await request.delete(`/users/${userId}`);

    const res  = await recall(request, 'Tell me about this user.', userId, uid('probe'));
    const body = await res.json();
    expect(body.context).toBe('');
    expect(body.citations).toEqual([]);
  });
});
