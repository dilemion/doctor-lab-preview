const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {selectionState, selectionNote} = require('./catalog.js');
const services = JSON.parse(fs.readFileSync(require('node:path').join(__dirname, '../data/analyses.json'), 'utf8')).services;
test('selected sampling is included once and described as included', () => {
  const state = selectionState(services, ['14.149','50.101','50.101']);
  assert.equal(state.total, 1245);
  assert.equal(state.rows.length, 2);
  assert.match(selectionNote(state.rows), /уже включено/);
  assert.doesNotMatch(selectionNote(state.rows), /оплачивается отдельно/);
});
test('sampling is separate when it has not been selected', () => {
  const state = selectionState(services, ['14.149']);
  assert.equal(state.total, 915);
  assert.match(selectionNote(state.rows), /не включено/);
});
test('unavailable saved positions are preserved and total is explicitly incomplete', () => {
  const reduced = services.filter(s => s.code !== '14.149');
  const state = selectionState(reduced, ['14.149','62.056']);
  assert.deepEqual(state.codes, ['14.149','62.056']);
  assert.deepEqual(state.missing, ['14.149']);
  assert.equal(state.total, 5760);
});
test('corrupt storage is bounded, deduplicated, and cannot introduce arbitrary values', () => {
  assert.deepEqual(selectionState(services, null).codes, []);
  const state = selectionState(services, ['14.149',null,{},5,'14.149','00.000']);
  assert.deepEqual(state.codes, ['14.149','00.000']);
  assert.deepEqual(state.missing, ['00.000']);
  assert.equal(selectionState(services, Array.from({length:101}, (_,i)=>String(i))).codes.length, 100);
});
test('empty selection has no hidden fees and current price changes affect the total', () => {
  assert.equal(selectionState(services, []).total, 0);
  const changed = services.map(s=>s.code==='14.149'?{...s,price_rub:1015}:s);
  assert.equal(selectionState(changed,['14.149','62.056']).total,6775);
});
