/**
 * Fact evolution tests — task.md §4 "The Hard Problems"
 *
 * Scenario: user says "I work at Stripe" in session 1, then
 * "I just started at Notion as a PM" in session 2.
 *
 * Assertions:
 *   • /recall returns current employer (Notion), not stale (Stripe)
 *   • /users/{id}/memories shows supersession chain:
 *       Stripe  active=false, superseded_by=<notion-id>
 *       Notion  active=true,  supersedes=<stripe-id>
 *   • History is preserved (both rows present)
 */
import { test, expect } from '@playwright/test';
import { uid, postTurn, recall, cleanupUser } from './helpers';

test.describe('Fact evolution — employment supersession', () => {
  let userId: string;
  let sess1: string;
  let sess2: string;

  test.beforeAll(async ({ request }) => {
    userId = uid('evo-user');
    sess1 = uid('evo-s1');
    sess2 = uid('evo-s2');

    // Session 1: at Stripe
    await postTurn(request, {
      session_id: sess1,
      user_id: userId,
      messages: [
        { role: 'user', content: 'Hey, I work at Stripe as a backend engineer.' },
        { role: 'assistant', content: 'Nice, Stripe is a great place!' },
      ],
      timestamp: '2025-03-01T10:00:00Z',
    });

    // Session 2: moved to Notion
    await postTurn(request, {
      session_id: sess2,
      user_id: userId,
      messages: [
        { role: 'user', content: 'Big news — I just started at Notion as a PM!' },
        { role: 'assistant', content: 'Congratulations on the new role!' },
      ],
      timestamp: '2025-03-15T10:00:00Z',
    });
  });

  test.afterAll(async ({ request }) => {
    await cleanupUser(request, userId);
  });

  test('/recall returns current employer (Notion), not stale (Stripe)', async ({ request }) => {
    const res = await recall(request, 'Where does this user work?', userId, uid('probe-sess'));
    expect(res.status()).toBe(200);
    const body = await res.json();
    const ctx = body.context.toLowerCase();
    console.log('  context:', body.context.slice(0, 300));
    expect(ctx).toContain('notion');
    // Stripe may appear as history note ("previously at Stripe") but should not be the current fact
  });

  test('/memories shows supersession chain with active flags', async ({ request }) => {
    const res = await request.get(`/users/${userId}/memories`);
    expect(res.status()).toBe(200);
    const { memories } = await res.json();

    // Find employer-related memories
    const employerMems = memories.filter((m: any) =>
      m.key?.toLowerCase().includes('employer') ||
      m.value?.toLowerCase().includes('stripe') ||
      m.value?.toLowerCase().includes('notion')
    );

    console.log('  employer memories:', employerMems.map((m: any) =>
      `${m.value} active=${m.active} supersedes=${m.supersedes}`
    ));

    expect(employerMems.length).toBeGreaterThanOrEqual(2); // both rows exist

    const active = employerMems.filter((m: any) => m.active);
    const inactive = employerMems.filter((m: any) => !m.active);

    expect(active.length).toBe(1);
    const activeEmp = active[0];
    expect(activeEmp.value.toLowerCase()).toContain('notion');

    // History preserved
    expect(inactive.length).toBeGreaterThanOrEqual(1);
    const stripeRow = inactive.find((m: any) => m.value.toLowerCase().includes('stripe'));
    expect(stripeRow).toBeDefined();

    // Chain linkage
    expect(activeEmp.supersedes).toBe(stripeRow.id);
    expect(stripeRow.superseded_by).toBe(activeEmp.id);
  });

  test('history row for Stripe is preserved (not deleted)', async ({ request }) => {
    const res = await request.get(`/users/${userId}/memories`);
    const { memories } = await res.json();
    const stripeRow = memories.find((m: any) => m.value.toLowerCase().includes('stripe'));
    expect(stripeRow).toBeDefined();
    expect(stripeRow.active).toBe(false);
  });
});
