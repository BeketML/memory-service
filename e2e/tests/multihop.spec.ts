/**
 * Multi-hop recall tests — task.md §9 probe queries
 *
 * "What city does the user with the dog named Biscuit live in?" requires
 * connecting two separate facts (pet.name + location.city) for the same user.
 * The recall pipeline achieves this by retrieving ALL active facts for the
 * user (Tier-1 PG) plus hybrid Qdrant search — no explicit graph traversal
 * needed because both facts share the same user_id.
 *
 * Fixture: fixtures/custom_scenarios.json → multihop_pet_city
 */
import { test, expect } from '@playwright/test';
import { uid, postTurn, recall, cleanupUser } from './helpers';

test.describe('Multi-hop recall — pet name + city', () => {
  let userId: string;
  let sess1: string;
  let sess2: string;

  test.beforeAll(async ({ request }) => {
    userId = uid('hop-user');
    sess1  = uid('hop-s1');
    sess2  = uid('hop-s2');

    // Session 1: establish pet fact
    await postTurn(request, {
      session_id: sess1, user_id: userId,
      messages: [
        { role: 'user',      content: 'I have a golden retriever named Biscuit.' },
        { role: 'assistant', content: 'Biscuit sounds adorable!' },
      ],
      timestamp: '2025-03-01T10:00:00Z',
    });

    // Session 2: establish location fact (separate session — tests cross-session recall)
    await postTurn(request, {
      session_id: sess2, user_id: userId,
      messages: [
        { role: 'user',      content: 'I just moved to Berlin from NYC last month.' },
        { role: 'assistant', content: 'Berlin is a great city! How are you settling in?' },
      ],
      timestamp: '2025-03-15T10:00:00Z',
    });
  });

  test.afterAll(async ({ request }) => { await cleanupUser(request, userId); });

  test('single-hop: pet name', async ({ request }) => {
    const res  = await recall(request, "What is the user's dog's name?", userId, uid('probe'));
    const body = await res.json();
    expect(res.status()).toBe(200);
    expect(body.context.toLowerCase()).toContain('biscuit');
    console.log('  pet context:', body.context.slice(0, 200));
  });

  test('single-hop: city', async ({ request }) => {
    const res  = await recall(request, 'Where does the user live?', userId, uid('probe'));
    const body = await res.json();
    expect(body.context.toLowerCase()).toContain('berlin');
  });

  test('multi-hop: city of user with dog named Biscuit', async ({ request }) => {
    const res  = await recall(
      request,
      'What city does the user with the dog named Biscuit live in?',
      userId,
      uid('probe'),
    );
    const body = await res.json();
    expect(res.status()).toBe(200);
    // Both facts should surface — either direct match or both in Tier-1
    const ctx = body.context.toLowerCase();
    const hasBerlin  = ctx.includes('berlin');
    const hasBiscuit = ctx.includes('biscuit');
    console.log(`  multi-hop ctx (berlin=${hasBerlin}, biscuit=${hasBiscuit}):`, body.context.slice(0, 300));
    // At minimum the answer (Berlin) must be in context
    expect(hasBerlin).toBe(true);
  });

  test('/memories has both active facts (pet + city)', async ({ request }) => {
    const res = await request.get(`/users/${userId}/memories`);
    const { memories } = await res.json();
    const active = memories.filter((m: any) => m.active);
    const hasPet  = active.some((m: any) => m.value?.toLowerCase().includes('biscuit'));
    const hasCity = active.some((m: any) => m.value?.toLowerCase().includes('berlin'));
    expect(hasPet).toBe(true);
    expect(hasCity).toBe(true);
  });
});
