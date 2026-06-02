/**
 * Noise resistance tests — task.md §9
 *
 * Queries about topics the user NEVER discussed must not produce fabricated
 * memories.  The service will still surface real Tier-1 facts for known
 * users (this is by design — the agent benefits from known facts even for
 * tangential queries), but it must NOT invent hiking trails, siblings, etc.
 *
 * For a brand-new unknown user the context must be empty ("").
 *
 * Fixture: fixtures/custom_scenarios.json → noise_resistance
 */
import { test, expect } from '@playwright/test';
import { uid, postTurn, recall, cleanupUser } from './helpers';

test.describe('Noise resistance — no hallucinated memories', () => {
  let userId: string;

  test.beforeAll(async ({ request }) => {
    userId = uid('noise-user');
    await postTurn(request, {
      session_id: uid('noise-sess'),
      user_id: userId,
      messages: [
        { role: 'user',      content: 'I work as a data scientist and I love Python.' },
        { role: 'assistant', content: 'Python is excellent for data science!' },
      ],
      timestamp: '2025-03-01T10:00:00Z',
    });
  });

  test.afterAll(async ({ request }) => { await cleanupUser(request, userId); });

  test('hiking trail query — no fabricated hiking facts', async ({ request }) => {
    const res  = await recall(request, "What is the user's favorite hiking trail?", userId, uid('probe'));
    const body = await res.json();
    expect(res.status()).toBe(200);
    const ctx = body.context.toLowerCase();
    // Must NOT hallucinate a hiking trail
    expect(ctx).not.toContain('trail');
    expect(ctx).not.toContain('hiking');
    expect(ctx).not.toContain('mountain');
    console.log('  noise/hiking ctx:', body.context.slice(0, 200));
  });

  test('siblings query — no fabricated family info', async ({ request }) => {
    const res  = await recall(request, 'Does the user have any siblings?', userId, uid('probe'));
    const body = await res.json();
    const ctx  = body.context.toLowerCase();
    expect(ctx).not.toContain('sibling');
    expect(ctx).not.toContain('brother');
    expect(ctx).not.toContain('sister');
  });

  test('sensitive / never-discussed topic — no fabricated info', async ({ request }) => {
    const res  = await recall(request, "What is the user's credit card number?", userId, uid('probe'));
    const body = await res.json();
    const ctx  = body.context.toLowerCase();
    expect(ctx).not.toContain('credit');
    expect(ctx).not.toContain('card');
    expect(ctx).not.toMatch(/\d{4}[\s-]?\d{4}/); // no fake card numbers
  });

  test('cold (unknown) user returns empty context', async ({ request }) => {
    const res  = await recall(request, 'Tell me about this user.', null, uid('cold-sess'),
    );
    const body = await res.json();
    expect(res.status()).toBe(200);
    expect(body.context).toBe('');
    expect(body.citations).toEqual([]);
  });
});
