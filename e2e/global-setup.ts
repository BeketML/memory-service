import { request } from '@playwright/test';

const BASE_URL = 'http://localhost:8080';
const TIMEOUT_MS = 180_000; // 3 minutes — first startup loads ColBERT ONNX

export default async function globalSetup() {
  console.log(`\nWaiting for memory service at ${BASE_URL}/health ...`);
  const ctx = await request.newContext({ baseURL: BASE_URL });
  const start = Date.now();

  while (Date.now() - start < TIMEOUT_MS) {
    try {
      const res = await ctx.get('/health');
      if (res.ok()) {
        const body = await res.json();
        console.log('✓ Memory service healthy:', body);
        await ctx.dispose();
        return;
      }
      console.log(`  /health → ${res.status()} (waiting...)`);
    } catch (e: any) {
      console.log(`  /health error: ${e.message} (retrying...)`);
    }
    await new Promise(r => setTimeout(r, 3000));
  }

  await ctx.dispose();
  throw new Error(`Memory service failed to become healthy within ${TIMEOUT_MS / 1000}s`);
}
