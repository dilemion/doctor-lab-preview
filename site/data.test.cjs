const test = require('node:test');
const assert = require('node:assert/strict');
const data = require('./data.js');
const services = [
  {code:'62.056',name:'После вакцинации',price_rub:5760,old_price_rub:6195,discount_percent:7},
  {code:'10.308',name:'Covid-пакет Базовый',price_rub:6680,old_price_rub:7185,discount_percent:7},
  {code:'99.022',name:'Витамины и минералы для красоты кожи, волос и ногтей',price_rub:4790,old_price_rub:5045,discount_percent:5},
  {code:'14.149',name:'Ферритин',price_rub:915,old_price_rub:null,discount_percent:null}
];
test('money uses existing final price and rejects invalid values', () => {
  assert.equal(data.money(5760).replace(/[\s\u00a0\u202f]/g,''),'5760₽');
  assert.equal(data.money(915.5).replace(/[\s\u00a0\u202f]/g,''),'915,5₽');
  for (const value of [-1,NaN,Infinity,true,'5760',null]) assert.throws(()=>data.money(value));
});
test('codes remain strings and required discounted positions match exactly', () => {
  for(const code of ['62.056','10.308','99.022']) {
    const rows=data.filterServices(services,code,false);
    assert.equal(rows.length,1); assert.equal(rows[0].code,code);
  }
  assert.equal(data.filterServices(services,'62056',false).length,0);
  assert.equal(data.filterServices(services,'ФЕРРИТИН',false)[0].code,'14.149');
  assert.equal(data.filterServices(services,'',true).length,3);
  assert.equal(data.filterServices(services,'ферритин',true).length,0);
});
test('strike-through requires an actual reduction, no discount reapplied', () => {
  assert.equal(data.hasDiscount(services[0]),true);
  assert.equal(services[0].price_rub,5760);
  for(const old of [null,0,5760,5000,NaN,'6195']) assert.equal(data.hasDiscount({...services[0],old_price_rub:old}),false);
});
test('links cannot execute scripts or include credentials', () => {
  for(const url of ['javascript:alert(1)','data:text/html,<script>alert(1)</script>','https://user:password@example.com','/relative',null]) assert.equal(data.safeHttpUrl(url),null);
  assert.equal(data.safeHttpUrl('https://dnkom.ru/analizy-i-tseny/'),'https://dnkom.ru/analizy-i-tseny/');
});
test('only published rows appear and promotion boundaries include end date', () => {
  const rows=data.published([{status:'draft',id:'hidden'},{status:'published',id:'later',sort_order:2},{status:'published',id:'first',sort_order:1}]);
  assert.deepEqual(rows.map(r=>r.id),['first','later']);
  assert.equal(data.promotionIsCurrent({starts_on:'2026-10-09',ends_on:'2026-10-09'},'2026-10-09'),true);
  assert.equal(data.promotionIsCurrent({ends_on:'2026-10-08'},'2026-10-09'),false);
  assert.equal(data.promotionIsCurrent({starts_on:'2026-10-10'},'2026-10-09'),false);
  assert.equal(data.moscowDate(new Date('2026-10-08T22:00:00Z')),'2026-10-09');
});
test('catalog refuses unconfirmed tariffs, duplicate codes and invalid price', () => {
  const catalog={pricing_status:'confirmed_docnlab_moscow',services};
  assert.equal(data.validateCatalog(catalog),catalog);
  assert.throws(()=>data.validateCatalog({...catalog,pricing_status:'research_only'}));
  assert.throws(()=>data.validateCatalog({...catalog,services:[services[0],services[0]]}));
  assert.throws(()=>data.validateCatalog({...catalog,services:[{...services[0],price_rub:NaN}]}));
  assert.throws(()=>data.validateCatalog({...catalog,services:[{...services[0],code:62.056}]}));
});

test('local photos accept only approved assets paths within the site root', () => {
  const base = new URL('https://example.com/docnlab/');
  assert.equal(data.safeAssetUrl('assets/photo.jpg', base), 'https://example.com/docnlab/assets/photo.jpg');
  assert.equal(data.safeAssetUrl('assets/doctors/photo.jpg', base), 'https://example.com/docnlab/assets/doctors/photo.jpg');
  assert.equal(data.safeAssetUrl('https://cdn.example.com/photo.jpg', base), 'https://cdn.example.com/photo.jpg');
  for (const path of ['../photo.jpg', '/assets/photo.jpg', 'assets/../private.jpg', 'assets/%2e%2e/private.jpg', 'assets/%252e%252e/private.jpg', 'assets/%5c..%5cprivate.jpg', 'assets//photo.jpg', 'assets/photo.jpg?token=123', 'assets/photo.jpg#fragment', 'javascript:alert(1)', 'https://user:pass@example.com/photo.jpg']) assert.equal(data.safeAssetUrl(path, base), null, path);
});

test('an existing exact code wins over parent/child substring matches and keeps leading zeros', () => {
  const rows = [
    {code:'26.138',name:'Parent analysis',price_rub:500,old_price_rub:null},
    {code:'26.138.01',name:'Extended analysis',price_rub:400,old_price_rub:500},
    {code:'00.028',name:'Leading-zero analysis',price_rub:300,old_price_rub:null},
    {code:'00.028.01',name:'Leading-zero child',price_rub:250,old_price_rub:300}
  ];
  assert.deepEqual(data.filterServices(rows,'26.138',false).map(s=>s.code),['26.138']);
  assert.deepEqual(data.filterServices(rows,'26.138',true),[]);
  assert.deepEqual(data.filterServices(rows,'26.138.01',true).map(s=>s.code),['26.138.01']);
  assert.deepEqual(data.filterServices(rows,' 00.028 ',false).map(s=>s.code),['00.028']);
  assert.deepEqual(data.filterServices(rows,'26.13',false).map(s=>s.code),['26.138','26.138.01']);
  assert.deepEqual(data.filterServices(rows,'Leading-zero',false).map(s=>s.code),['00.028','00.028.01']);
});
