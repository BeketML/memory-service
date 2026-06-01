/**
 * Multi-hop recall tests — task.md §9 evaluation category
 *
 * Scenario: user establishes two separate facts (pet name + city) in
 * separate turns. A query that asks about *both* should surface them.
 *
 * e.g. "What city does the user with the dog named Biscuit live in?"
 * requires connecting pet.name=Biscuit with location.city=Berlin.
 */
import { test, expect } from '@playwright/test';
import { uid, postTurn, recall, cleanupUser } from './helpers';

test.describe('Multi-hop recall', () => {
  let userId: string;
  let sess1: string;
  let sess2: string;

  test.beforeAll(async ({ request }) => {
    userId = uid('mhop-user');
    sess1 = uid('mhop-s1');
    sess2 = uid('mhop-s2');

    // Fact 1: dog named Biscuit
    await postTurn(request, {
      session_id: sess1,
      user_id: userId,
      messages: [
        { role: 'user', content: 'My dog Biscuit is a golden retriever. She loves the park!' },
        { role: 'assistant', content: 'What a lovely name!' },
      ],
      timestamp: '2025-03-01T08:00:00Z',
    });

    // Fact 2: moved to Berlin
    await postTurn(request, {
      session_id: sess2,
      user_id: userId,
      messages: [
        { role: 'user', content: 'I just moved from NYC to Berlin last month.' },
        { role: 'assistant', content: 'Berlin is a fantastic city!' },
      ],
      timestamp: '2025-03-15T09:00:00Z',
    });
  });

  test.afterAll(async ({ request }) => {
    await cleanupUser(request, userId);
  });

  test('both facts appear in /memories', async ({ request }) => {
    const res = await request.get(`/users/${userId}/memories`);
    const { memories } = await res.json();
    const values = memories.map((m: any) => m.value.toLowerCase());
    console.log('  extracted values:', values);

    const hasBiscuit = values.some((v: string) => v.includes('biscuit'));
    const hasBerlin = values.some((v: string) => v.includes('berlin'));
    expect(hasBiscuit).toBe(true);
    expect(hasBerlin).toBe(true);
  });

  test('recall query connecting dog + city surfaces Berlin', async ({ request }) => {
    const res = await recall(
      request,
      "What city does the user with the dog named Biscuit live in?",
      userId,
      uid('probe-sess'),
    );
    const body = await res.json();
    const ctx = body.context.toLowerCase();
    console.log('  multi-hop context:', body.context.slice(0, 400));

    // At minimum, Berlin should appear (Tier-1 PG fact)
    expect(ctx).toContain('berlin');
  });

  test('recall surfaces Biscuit in context', async ({ request }) => {
    const res = await recall(
      request,
      "Tell me about the user's pet",
      userId,
      uid('probe-sess'),
    );
    const body = await res.json();
    const ctx = body.context.toLowerCase();
    expect(ctx).toContain('biscuit');
  });
});
