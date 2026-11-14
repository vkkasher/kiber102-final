import test from 'node:test';
import { isAllowedOrigin } from '../src/lib/origin.ts';
import assert from 'node:assert/strict';
import { localDay, periodRange } from '../src/lib/calendar.ts';
import { api, price } from '../src/lib/api.ts';
import { pendingAction, subscriptionCapability } from '../src/lib/agent-reply.ts';

test('untyped agent actions need a valid display contract and subscription capability',()=>{
  assert.equal(pendingAction({nonce:'short',tool:'subscribe',arguments:{},expires_at:'2027-01-01'}),null);
  assert.equal(pendingAction({nonce:'valid_nonce_123456789',tool:'publish_event',arguments:{},expires_at:'2027-01-01'}),null);
  assert.equal(pendingAction({nonce:'valid_nonce_123456789',tool:'subscribe',arguments:[],expires_at:'2027-01-01'}),null);
  assert.equal(pendingAction({nonce:'valid_nonce_123456789',tool:'subscribe',arguments:{},expires_at:'invalid'}),null);
  assert.equal(pendingAction({nonce:'valid_nonce_123456789',tool:'subscribe',arguments:{city:'Уфа'},expires_at:'2027-01-01T00:00:00Z'}).tool,'subscribe');
  assert.equal(subscriptionCapability({id:'1'}),null);
  assert.deepEqual(subscriptionCapability({id:'1',delete_token:'capability'}),{id:'1',delete_token:'capability'});
});

test('unknown, free, starting and fixed prices remain distinct', () => {
  assert.equal(price({ price_min: null }), 'Цена уточняется');
  assert.equal(price({ price_min: 0, price_max: 0, is_free: true }), 'Бесплатно');
  assert.equal(price({ price_min: 300, price_max: null }), 'От 300 ₽');
  assert.equal(price({ price_min: 300, price_max: 300 }), '300 ₽');
});


test('weekend includes the current Sunday, with region day at UTC boundary', () => {
  assert.deepEqual(periodRange('weekend', new Date('2026-11-15T08:00:00Z')), {date_from:'2026-11-15',date_to:'2026-11-15'});
  assert.deepEqual(periodRange('weekend', new Date('2026-11-14T18:00:00Z')), {date_from:'2026-11-14',date_to:'2026-11-15'});
  assert.deepEqual(periodRange('weekend', new Date('2026-11-13T20:00:00Z')), {date_from:'2026-11-14',date_to:'2026-11-15'});
  assert.equal(localDay(0,new Date('2026-11-14T20:00:00Z')),'2026-11-15');
  assert.deepEqual(periodRange('week', new Date('2026-12-29T20:00:00Z')), {date_from:'2026-12-30',date_to:'2027-01-05'});
});
test('browser network errors are translated without hiding server validation', async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async () => { throw new TypeError('Failed to fetch'); };
    await assert.rejects(api('/events'), /Нет связи с сервисом афиши/);
    globalThis.fetch = async () => new Response(JSON.stringify({message:'Укажите город'}), {status:422});
    await assert.rejects(api('/events'), /Укажите город/);
  } finally { globalThis.fetch = original; }
});


test('standalone loopback aliases allow local browser POST without trusting forwarded host', () => {
  assert.equal(isAllowedOrigin('http://127.0.0.1:3000','http://localhost:3000'),true);
  assert.equal(isAllowedOrigin('http://localhost:3000','http://127.0.0.1:3000'),true);
  assert.equal(isAllowedOrigin('http://127.0.0.1:3000','http://0.0.0.0:3000'),true);
  assert.equal(isAllowedOrigin('http://127.0.0.1:3001','http://localhost:3000'),false);
  assert.equal(isAllowedOrigin('https://127.0.0.1:3000','http://localhost:3000'),false);
  assert.equal(isAllowedOrigin('https://evil.example','http://localhost:3000'),false);
  assert.equal(isAllowedOrigin('http://localhost.evil.example:3000','http://localhost:3000'),false);
  assert.equal(isAllowedOrigin('http://127.0.0.1:3000','https://catalog.example'),false);
  assert.equal(isAllowedOrigin('https://catalog.example','http://localhost:3000','https://catalog.example'),true);
  assert.equal(isAllowedOrigin('null','http://localhost:3000'),false);
  assert.equal(isAllowedOrigin('https://catalog.example@evil.example','http://localhost:3000','https://catalog.example'),false);
});
