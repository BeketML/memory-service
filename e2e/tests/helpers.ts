import { APIRequestContext } from '@playwright/test';

let _counter = 0;

export function uid(prefix = 'u'): string {
  _counter++;
  return `${prefix}-${Date.now()}-${_counter}`;
}

export interface TurnPayload {
  session_id: string;
  user_id?: string | null;
  messages: { role: string; content: string; name?: string }[];
  timestamp?: string;
  metadata?: Record<string, unknown>;
}

export async function postTurn(request: APIRequestContext, payload: TurnPayload) {
  const res = await request.post('/turns', {
    data: {
      timestamp: new Date().toISOString(),
      metadata: {},
      ...payload,
    },
  });
  return res;
}

export async function recall(
  request: APIRequestContext,
  query: string,
  user_id: string | null,
  session_id: string,
  max_tokens = 1024,
) {
  const res = await request.post('/recall', {
    data: { query, user_id, session_id, max_tokens },
  });
  return res;
}

export async function cleanupUser(request: APIRequestContext, user_id: string) {
  try {
    await request.delete(`/users/${user_id}`);
  } catch {
    // best-effort cleanup
  }
}
