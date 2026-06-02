/**
 * Fact evolution tests — task.md §4 "The Hard Problems"
 *
 * Covers:
 *   - Employment supersession: Stripe → Notion (separate sessions)
 *   - Location supersession:   NYC → Berlin
 *   - /memories must expose the full supersession chain
 *
 * Key correctness: reconcile.py deactivates the old row (flush) BEFORE
 * inserting the new one, so the partial unique index never fires.
 * Each candidate is wrapped in a SAVEPOINT so one failure doesn't abort
 * the whole transaction.
 *
 * Fixture: fixtures/custom_scenarios.json → fact_evolution_employer,
 *                                           fact_evolution_location
 */
import { test, expect } from '@playwright/test';
import { uid, postTurn, recall, cleanupUser } from './helpers';

// ── Employment: Stripe → Notion ───────────────────────────────────────────────

test.describe('Fact evolution — employment supersession (Stripe → Notion)', () => {
  let userId: string;
  let sess1: string;
  let sess2: string;

  test.beforeAll(async ({ request }) => {
    userId = uid('evo-emp');
    sess1  = uid('evo-emp-s1');
    sess2  = uid('evo-emp-s2');

    await postTurn(request, {
      session_id: sess1, user_id: userId,
      messages: [
        { role: 'user',      content: "I'm a backend engineer at Stripe." },
        { role: 'assistant', content: 'Nice, Stripe is a great company!' },
      ],
      timestamp: '2025-03-01T10:00:00Z',
    });

    await postTurn(request, {
      session_id: sess2, user_id: userId,
      messages: [
        { role: 'user',      content: 'Big news — I just started at Notion as a PM!' },
        { role: 'assistant', content: 'Congratulations on the new role at Notion!' },
      ],
      timestamp: '2025-03-15T10:00:00Z',
    });
  });

  test.afterAll(async ({ request }) => { await cleanupUser(request, userId); });

  test('/recall returns current employer (Notion)', async ({ request }) => {
    const res  = await recall(request, 'Where does this user work now?', userId, uid('probe'));
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body.context.toLowerCase()).toContain('notion');
    console.log('  context (200):', body.context.slice(0, 300));
  });

  test('/memories shows supersession chain — Stripe superseded, Notion active', async ({ request }) => {
    const res = await request.get(`/users/${userId}/memories`);
    expect(res.status()).toBe(200);
    const { memories } = await res.json();

    const empMems = memories.filter((m: any) =>
      m.key?.toLowerCase().includes('employer') ||
      m.value?.toLowerCase() === 'stripe' ||
      m.value?.toLowerCase() === 'notion'
    );

    console.log('  employer memories:', JSON.stringify(empMems.map((m: any) =>
      ({ value: m.value, active: m.active, supersedes: m.supersedes, superseded_by: m.superseded_by })
    )));

    // Both rows must exist
    expect(empMems.length).toBeGreaterThanOrEqual(2);

    const notionMem = empMems.find((m: any) => m.value?.toLowerCase().includes('notion'));
    const stripeMem = empMems.find((m: any) => m.value?.toLowerCase() === 'stripe');

    // Current fact is Notion
    expect(notionMem).toBeTruthy();
    expect(notionMem.active).toBe(true);
    expect(notionMem.supersedes).not.toBeNull(); // points back to Stripe

    // Stripe is deactivated and has superseded_by set
    expect(stripeMem).toBeTruthy();
    expect(stripeMem.active).toBe(false);
    expect(stripeMem.superseded_by).not.toBeNull();
  });

  test('/memories — Stripe id === Notion.supersedes (chain integrity)', async ({ request }) => {
    const { memories } = await (await request.get(`/users/${userId}/memories`)).json();
    const empMems = memories.filter((m: any) =>
      m.key?.toLowerCase().includes('employer') ||
      m.value?.toLowerCase() === 'stripe' ||
      m.value?.toLowerCase() === 'notion'
    );
    const notionMem = empMems.find((m: any) => m.value?.toLowerCase().includes('notion'));
    const stripeMem = empMems.find((m: any) => m.value?.toLowerCase() === 'stripe');
    if (!notionMem || !stripeMem) { test.skip(); return; }

    expect(notionMem.supersedes).toBe(stripeMem.id);
    expect(stripeMem.superseded_by).toBe(notionMem.id);
  });
});

// ── Location: NYC → Berlin ─────────────────────────────────────────────────────

test.describe('Fact evolution — location supersession (NYC → Berlin)', () => {
  let userId: string;
  let sess1: string;
  let sess2: string;

  test.beforeAll(async ({ request }) => {
    userId = uid('evo-loc');
    sess1  = uid('evo-loc-s1');
    sess2  = uid('evo-loc-s2');

    await postTurn(request, {
      session_id: sess1, user_id: userId,
      messages: [
        { role: 'user',      content: 'I live in New York City.' },
        { role: 'assistant', content: 'NYC is a vibrant city!' },
      ],
      timestamp: '2025-03-01T10:00:00Z',
    });

    await postTurn(request, {
      session_id: sess2, user_id: userId,
      messages: [
        { role: 'user',      content: 'I just moved to Berlin last month — loving it so far!' },
        { role: 'assistant', content: 'Berlin is a great city! How are you settling in?' },
      ],
      timestamp: '2025-03-15T10:00:00Z',
    });
  });

  test.afterAll(async ({ request }) => { await cleanupUser(request, userId); });

  test('/recall returns current city (Berlin)', async ({ request }) => {
    const res  = await recall(request, 'Where does this user live?', userId, uid('probe'));
    const body = await res.json();
    expect(body.context.toLowerCase()).toContain('berlin');
  });

  test('/memories shows city supersession chain', async ({ request }) => {
    const res = await request.get(`/users/${userId}/memories`);
    const { memories } = await res.json();

    const cityMems = memories.filter((m: any) =>
      m.key?.toLowerCase().includes('city') ||
      m.value?.toLowerCase() === 'nyc' || m.value?.toLowerCase() === 'new york city' ||
      m.value?.toLowerCase() === 'berlin'
    );

    const berlinMem = cityMems.find((m: any) => m.value?.toLowerCase().includes('berlin'));
    const nycMem    = cityMems.find((m: any) =>
      m.value?.toLowerCase().includes('nyc') || m.value?.toLowerCase().includes('new york'));

    expect(berlinMem?.active).toBe(true);
    if (nycMem) {
      expect(nycMem.active).toBe(false);
      expect(nycMem.superseded_by).not.toBeNull();
    }
  });
});
