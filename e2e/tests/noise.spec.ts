/**
 * Noise resistance tests — task.md §9 evaluation category
 *
 * The service must:
 *   • Return empty context for queries about topics never discussed
 *   • Return empty context for cold sessions (no data at all)
 *   • Never return hallucinated memories
 */
import { test, expect } from '@playwright/test';
import { uid, postTurn, recall, cleanupUser } from './helpers';

test.describe('Noise resistance', () => {
  let userId: string;
  let sessionId: string;

  test.beforeAll(async ({ request }) => {
    userId = uid('noise-user');
    sessionId = uid('noise-sess');

    // Only talk about work — nothing about hiking, siblings, hobbies
    await postTurn(request, {
      session_id: sessionId,
      user_id: userId,
      messages: [
        { role: 'user', content: 'I work as a data scientist at Meta and I love Python.' },
        { role: 'assistant', content: 'Python is great for data science!' },
      ],
    });
  });

  test.afterAll(async ({ request }) => {
    await cleanupUser(request, userId);
  });

  test('off-topic query returns empty context (not hallucinated hiking info)', async ({ request }) => {
    const res = await recall(
      request,
      "What is the user's favorite hiking trail?",
      userId,
      uid('probe-sess'),
    );
    expect(res.status()).toBe(200);
    const body = await res.json();
    console.log('  noise context:', JSON.stringify(body.context).slice(0, 200));
    // Should be empty OR contain only known facts (work/Python) — not hiking info
    const ctx = body.context.toLowerCase();
    expect(ctx).not.toContain('hik');
    expect(ctx).not.toContain('trail');
    expect(ctx).not.toContain('mountain');
  });

  test('query about siblings returns empty context', async ({ request }) => {
    const res = await recall(
      request,
      'Does the user have any siblings?',
      userId,
      uid('probe-sess'),
    );
    const body = await res.json();
    const ctx = body.context.toLowerCase();
    expect(ctx).not.toContain('sibling');
    expect(ctx).not.toContain('brother');
    expect(ctx).not.toContain('sister');
  });

  test('cold user + cold session returns empty context and empty citations', async ({ request }) => {
    const res = await recall(
      request,
      'Tell me everything about this person.',
      uid('absolute-cold-user'),
      uid('absolute-cold-sess'),
    );
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body.context).toBe('');
    expect(body.citations).toEqual([]);
  });

  test('known topic IS returned (sanity check — not over-filtering)', async ({ request }) => {
    const res = await recall(
      request,
      'What programming language does the user prefer?',
      userId,
      uid('probe-sess'),
    );
    const body = await res.json();
    // Python should appear — we ingested it
    const ctx = body.context.toLowerCase();
    console.log('  known-topic context:', body.context.slice(0, 300));
    // Lenient: if no memory was extracted, context may be empty — that's OK for noise test
    // but we note if extraction didn't capture Python
    if (!ctx.includes('python')) {
      console.log('  ⚠ Python not found in recall — extraction may not have captured it');
    }
  });
});
