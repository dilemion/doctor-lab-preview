const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const search = require('./search.js');
const actual = require('../data/analyses.json').services;
const fixtures = [
  {code:'26.138',name:'Родительское исследование',price_rub:500,is_profile:false,is_sampling_service:false},
  {code:'26.138.01',name:'Расширенное исследование',price_rub:400,old_price_rub:500,is_profile:true,is_sampling_service:false},
  {code:'00.028',name:'Анализ с ведущими нулями',price_rub:300,is_profile:false,is_sampling_service:false},
  {code:'00.028.01',name:'Анализ с ведущими нулями дополнительный',price_rub:250,old_price_rub:300,is_profile:false,is_sampling_service:false},
  {code:'004.343',name:'Дополнительный реальный формат кода',price_rub:100,is_profile:false,is_sampling_service:false},
  {code:'50.101',name:'Взятие крови',price_rub:330,is_profile:false,is_sampling_service:true}
];
const codes = result => result.rows.map(service => service.code);

test('normalization keeps letters and digits, handles ё, punctuation and all whitespace', () => {
  assert.equal(search.normalizeQuery('  ЩЁЛОЧНАЯ,\t фосфатаза!  Vitamin-D 25-ОН '), 'щелочная фосфатаза vitamin d 25 он');
  assert.equal(search.normalizeQuery('00.028.01'), '00 028 01');
  assert.equal(search.normalizeQuery(null), '');
  assert.equal(search.normalizeQuery({code:'00.028'}), '');
  assert.equal(search.normalizeQuery(25), '25');
});

test('exact codes isolate parents and children, including leading zeros and three-digit prefixes', () => {
  for (const code of ['26.138','26.138.01','00.028','00.028.01','004.343']) {
    const result = search.searchServices(fixtures, '  ' + code + '  ');
    assert.deepEqual(codes(result), [code]);
    assert.equal(result.matchType, 'exact-code');
    assert.equal(result.originalQuery, code);
    assert.equal(result.suggestedQuery, null);
  }
  assert.deepEqual(codes(search.searchServices(fixtures,'26.138',{discountedOnly:true})), []);
  assert.deepEqual(codes(search.searchServices(fixtures,'26.138',{kind:'profiles'})), []);
});

test('partial codes retain source strings, while unknown full codes cannot select children or guesses', () => {
  assert.deepEqual(codes(search.searchServices(fixtures,'26.13')), ['26.138','26.138.01']);
  assert.deepEqual(codes(search.searchServices(fixtures,'00.02')), ['00.028','00.028.01']);
  assert.deepEqual(codes(search.searchServices(fixtures,'004.34')), ['004.343']);
  for (const code of ['26.137','00.029','00.028.02','26.138.011','260138','0.028']) {
    const result = search.searchServices(fixtures, code);
    assert.deepEqual(codes(result), [], code);
    assert.equal(result.matchType, 'empty', code);
    assert.equal(result.suggestedQuery, null, code);
  }
  // A full code that exists only as a parent prefix is still not available.
  assert.deepEqual(codes(search.searchServices(fixtures.filter(row=>row.code!=='26.138'), '26.138')), []);
});

test('words use AND regardless of order and punctuation without searching medical descriptions', () => {
  const services = [
    {code:'01.001',name:'Щёлочная фосфатаза крови',price_rub:10,description:{summary:'ферритин'}},
    {code:'01.002',name:'Фосфатаза мочи',price_rub:20},
    {code:'01.003',name:'Кровь для исследования глюкозы',price_rub:30}
  ];
  for (const query of ['фосфатаза крови','КРОВИ, фосфатаза','  крови \tфосфатаза ']) {
    assert.deepEqual(codes(search.searchServices(services,query)), ['01.001']);
  }
  assert.deepEqual(codes(search.searchServices(services,'щелочная')), ['01.001']);
  assert.deepEqual(codes(search.searchServices(services,'ферритин')), []);
});

test('actual names and source abbreviations remain searchable outside the explicit alias list', () => {
  for (const [query, code] of [
    ['ТТГ','15.104'], ['CRP','14.147'], ['CBC','18.118'], ['СОЭ','18.102'],
    ['фосфатаза щелочная','14.125'], ['глюкоза моча','23.108']
  ]) {
    const result = search.searchServices(actual, query);
    assert.ok(codes(result).includes(code), query + ': ' + codes(result).join(','));
    assert.equal(result.matchType, 'text');
    assert.equal(result.suggestedQuery, null);
  }
});

test('ОАК matches only real names for general or clinical blood analyses', () => {
  const result = search.searchServices(actual,'ОАК');
  assert.deepEqual(new Set(codes(result)), new Set(['18.100','18.104','18.106','18.118']));
  assert.ok(result.rows.every(row=>/общий анализ крови|клинический анализ крови/i.test(row.name)));
  assert.equal(result.matchType, 'text');
  assert.equal(result.suggestedQuery, null);
  for (const query of ['ОАК СОЭ','СОЭ ОАК']) {
    assert.deepEqual(new Set(codes(search.searchServices(actual,query))), new Set(['18.104','18.118']));
  }
  assert.deepEqual(codes(search.searchServices([{code:'01.001',name:'ОАК как в источнике',price_rub:1}],'ОАК')), ['01.001']);
  // Alias expansions are evaluated against actual names, never remembered codes.
  assert.deepEqual(codes(search.searchServices([{code:'18.100',name:'Совсем иное исследование',price_rub:1}],'ОАК')), []);
});

test('vitamin D and Russian variants include existing 25-OH analyses', () => {
  for (const query of ['vitamin D','витамин D','витамин Д','vitamind']) {
    const result = search.searchServices(actual,query);
    for (const code of ['35.102','35.146','35.151']) assert.ok(codes(result).includes(code), query + ': ' + code);
    assert.ok(result.rows.every(row=>/витамин/i.test(row.name)));
    assert.equal(result.matchType,'text');
    assert.equal(result.suggestedQuery,null);
  }
});

test('empty query browses the catalog; unrelated and malformed full queries never receive guessed codes', () => {
  assert.equal(search.searchServices(fixtures,'').rows.length, fixtures.length);
  assert.equal(search.searchServices(fixtures,'').matchType, 'text');
  assert.deepEqual(codes(search.searchServices(actual,'несуществующая абракадабра')), []);
  assert.deepEqual(codes(search.searchServices(actual,'constructor')), []);
  assert.deepEqual(codes(search.searchServices([], 'ферритин')), []);
  assert.deepEqual(codes(search.searchServices(null, 'ферритин')), []);
});

test('one conservative typo is labelled as a suggestion and can be disabled', () => {
  for (const query of ['феритин','ферритни']) {
    const result = search.searchServices(actual,query);
    assert.ok(codes(result).includes('14.149'));
    assert.equal(result.matchType,'suggested');
    assert.equal(result.suggestedQuery,'ферритин');
    assert.equal(result.originalQuery,query);
    assert.deepEqual(codes(search.searchServices(actual,query,{allowSuggestions:false})), []);
  }
  const ambiguous = [
    {code:'01.001',name:'Ферритин',price_rub:10},
    {code:'01.002',name:'Фарритин',price_rub:20}
  ];
  assert.equal(search.searchServices(ambiguous,'фирритин').matchType,'empty');
  assert.equal(search.searchServices(ambiguous,'фирритин').suggestedQuery,null);
  // At most one word is corrected, with no correction for short tokens or codes.
  assert.equal(search.searchServices(actual,'феритин щелочнаа').suggestedQuery,null);
  assert.equal(search.searchServices(actual,'ттд').suggestedQuery,null);
});

test('filtering keeps prices and type flags authoritative and never relaxes filters', () => {
  assert.deepEqual(codes(search.searchServices(fixtures,'',{discountedOnly:true})), ['26.138.01','00.028.01']);
  assert.deepEqual(codes(search.searchServices(fixtures,'',{kind:'profiles'})), ['26.138.01']);
  assert.deepEqual(codes(search.searchServices(fixtures,'',{kind:'tests'})), ['26.138','00.028','00.028.01','004.343']);
  assert.deepEqual(codes(search.searchServices(fixtures,'',{kind:'tests',discountedOnly:true})), ['00.028.01']);
  assert.equal(search.searchServices(fixtures,'родительское',{discountedOnly:true}).suggestedQuery,null);
  const invalidSales = [{code:'01.001',name:'Анализ',price_rub:500,old_price_rub:500,discount_percent:5},
    {code:'01.002',name:'Анализ',price_rub:500,old_price_rub:'600',discount_percent:5}];
  assert.deepEqual(codes(search.searchServices(invalidSales,'',{discountedOnly:true})), []);
});

test('sorting ranks precise titles, sorts actual prices, and does not mutate source order', () => {
  const services = [
    {code:'01.001',name:'Ферритин расширенный',price_rub:200},
    {code:'01.002',name:'Ферритин',price_rub:300},
    {code:'01.003',name:'Анализ ферритина',price_rub:100,old_price_rub:400}
  ];
  const original = services.slice();
  assert.equal(search.searchServices(services,'ферритин').rows[0].code,'01.002');
  assert.deepEqual(codes(search.searchServices(services,'ферритин',{sort:'price-asc'})), ['01.003','01.001','01.002']);
  assert.deepEqual(codes(search.searchServices(services,'ферритин',{sort:'name'})), ['01.003','01.002','01.001']);
  assert.deepEqual(services, original);
});

test('selection accepts only unique exact existing codes, retaining order and current records', () => {
  const selected = ['00.028','26.138','00.028','missing',null,26.138,{code:'26.138.01',price_rub:1},' 00.028 ','004.343'];
  assert.deepEqual(search.selectedServices(fixtures,selected).map(row=>row.code), ['00.028','26.138','004.343']);
  assert.equal(search.selectedServices(fixtures,selected)[0], fixtures[2]);
  assert.deepEqual(search.selectedServices(fixtures,'00.028'), []);
  assert.deepEqual(search.selectedServices(null,selected), []);
  assert.equal(search.selectionTotal(fixtures,selected),900);
  assert.equal(search.selectionTotal(fixtures,['26.138']),500);
  assert.equal(search.selectionTotal(fixtures,[]),0);
});

test('selection reprices against current catalog and discards removed records', () => {
  const codesToKeep = ['26.138','00.028'];
  assert.equal(search.selectionTotal(fixtures,codesToKeep),800);
  const repriced = fixtures.filter(row=>row.code!=='00.028').map(row=>row.code==='26.138'?{...row,price_rub:700}:row);
  assert.deepEqual(search.selectedServices(repriced,codesToKeep).map(row=>row.code),['26.138']);
  assert.equal(search.selectionTotal(repriced,codesToKeep),700);
});

test('totals use final source prices once, without percentage discounts or an added sampling fee', () => {
  // A fixed, previously verified price fixture permits legitimate future repricing.
  const sales = [
    {code:'62.056',name:'Анализ A',price_rub:5760,old_price_rub:6195,discount_percent:7},
    {code:'10.308',name:'Анализ B',price_rub:6680,old_price_rub:7185,discount_percent:7},
    {code:'99.022',name:'Анализ C',price_rub:4790,old_price_rub:5045,discount_percent:5},
    {code:'50.101',name:'Взятие крови',price_rub:330,is_sampling_service:true}
  ];
  assert.equal(search.selectionTotal(sales,['62.056','10.308','99.022']),17230);
  assert.equal(search.selectionTotal(sales,['62.056','62.056']),5760);
  assert.equal(search.selectedServices(sales,['62.056']).length,1);
  assert.equal(search.selectionTotal(sales,['62.056','50.101']),6090);
  const decimals = [{code:'01.001',name:'A',price_rub:0.1},{code:'01.002',name:'B',price_rub:0.2}];
  assert.equal(search.selectionTotal(decimals,['01.001','01.002']),0.3);
  for (const price_rub of [-1,NaN,Infinity,'1',null,true]) {
    assert.throws(()=>search.selectionTotal([{code:'01.001',name:'A',price_rub}],['01.001']), TypeError);
  }
  assert.throws(()=>search.selectionTotal([{code:'01.001',name:'A',price_rub:Number.MAX_SAFE_INTEGER}],['01.001']), RangeError);
});

test('UMD browser build exposes the same rules without DOM or storage access', () => {
  const context = {window:{}};
  vm.runInNewContext(fs.readFileSync(require.resolve('./search.js'),'utf8'), context);
  assert.equal(typeof context.window.DocnlabSearch.searchServices,'function');
  assert.equal(context.window.DocnlabSearch.selectionTotal(fixtures,['00.028']),300);
});

test('exact source abbreviation outranks an unrelated name prefix on the real snapshot', () => {
  for (const query of ['АСТ','аст']) {
    const result = search.searchServices(actual,query);
    assert.equal(result.rows[0].code,'14.121');
    assert.ok(codes(result).includes('10.526'));
    assert.equal(result.rows[0].price_rub, actual.find(row=>row.code==='14.121').price_rub);
    assert.equal(result.matchType,'text');
  }
  // This rule follows source abbreviations, rather than hard-coded service codes.
  const services = [
    {code:'01.001',name:'Астровирус',price_rub:10},
    {code:'01.002',name:'Любое исходное название (АСТ)',price_rub:20}
  ];
  assert.deepEqual(codes(search.searchServices(services,'АСТ')),['01.002','01.001']);
});

test('vitamin designations fold scripts only in context and match whole source tokens', () => {
  for (const query of ['vitamin C','витамин С','витамин C','витамин c','С витамин']) {
    const result = search.searchServices(actual,query);
    assert.equal(result.rows[0].code,'35.108',query);
    assert.equal(result.suggestedQuery,null);
    assert.equal(result.matchType,'text');
  }
  const russianB = search.searchServices(actual,'витамин В12');
  const latinB = search.searchServices(actual,'витамин B12');
  assert.ok(codes(russianB).includes('14.190'));
  assert.deepEqual(codes(latinB),codes(russianB));
  const services = [
    {code:'01.001',name:'Витамин D суммарно',price_rub:1},
    {code:'01.002',name:'Витамин D с уточнением',price_rub:2},
    {code:'01.003',name:'Витамин C',price_rub:3},
    {code:'01.004',name:'Витамин В12',price_rub:4},
    {code:'01.005',name:'Витамин B123',price_rub:5},
    {code:'01.006',name:'Витамин Е',price_rub:6}
  ];
  assert.deepEqual(codes(search.searchServices(services,'витамин С')),['01.003']);
  assert.deepEqual(codes(search.searchServices(services,'витамин В12')),['01.004']);
  assert.deepEqual(codes(search.searchServices(services,'витамин B12')),['01.004']);
  assert.deepEqual(codes(search.searchServices(services,'vitamin E')),['01.006']);
  const lowercase = [
    {code:'01.001',name:'Витамин c (описание)',price_rub:1},
    {code:'01.002',name:'Витамин в12 (описание)',price_rub:2},
    {code:'01.003',name:'Витамин с ретинолом',price_rub:3}
  ];
  assert.deepEqual(codes(search.searchServices(lowercase,'витамин С')),['01.001']);
  assert.deepEqual(codes(search.searchServices(lowercase,'С витамин')),['01.001']);
  assert.deepEqual(codes(search.searchServices(lowercase,'vitamin B12')),['01.002']);
  assert.deepEqual(codes(search.searchServices(lowercase,'Витамин с ретинолом')),['01.003']);
  assert.deepEqual(codes(search.searchServices([{code:'01.001',name:'Компонент C',price_rub:1}],'С')),[]);
});

test('vitamin D keeps the primary real name first and retains 25-OH alternatives', () => {
  for (const query of ['витамин D','vitamin D','витамин Д','vitamind']) {
    const result = search.searchServices(actual,query);
    assert.equal(result.rows[0].code,'35.102',query);
    for (const code of ['35.102','35.107','35.146','35.151']) {
      assert.ok(codes(result).includes(code),query + ': ' + code);
    }
    assert.equal(result.matchType,'text');
  }
});

test('long vitamin queries retain ordinary prepositions and word permutations on the snapshot', () => {
  for (const query of ['витамин С в крови','витамин C в крови','крови в С витамин','С крови витамин в']) {
    assert.deepEqual(new Set(codes(search.searchServices(actual,query))),new Set(['35.116','35.118','42.139']),query);
  }
  for (const query of ['витамин B9 в эритроцитах','эритроцитах в B9 витамин']) {
    assert.deepEqual(codes(search.searchServices(actual,query)),['14.192'],query);
  }
  // Generic "в" is a preposition, rather than a forced vitamin B designation.
  assert.ok(search.searchServices(actual,'витамин в крови').rows.length > 0);
});

test('a conjunction stays in ordinary AND instead of becoming a vitamin label', () => {
  for (const query of ['витамин D и омега','омега и D витамин']) {
    assert.deepEqual(codes(search.searchServices(actual,query)),['62.054','99.094'],query);
  }
});

test('source names and meaningful parts outside the alias list keep ordinary word search', () => {
  const services = [
    {code:'01.001',name:'Витамин D с кальцием',price_rub:1},
    {code:'01.002',name:'Витамин D к исследованию',price_rub:2},
    {code:'01.003',name:'Витамин D и фосфор',price_rub:3},
    {code:'01.004',name:'Витамин Q новый исходный термин',price_rub:4}
  ];
  for (const [query,expected] of [
    ['витамин D с кальцием','01.001'],['кальцием с D витамин','01.001'],
    ['витамин D к исследованию','01.002'],['исследованию к D витамин','01.002'],
    ['витамин D и фосфор','01.003'],['фосфор и D витамин','01.003'],
    ['витамин Q новый','01.004'],['новый Q витамин','01.004']
  ]) assert.deepEqual(codes(search.searchServices(services,query)),[expected],query);
});

test('Cyrillic and Latin K match exact vitamin labels in longer source queries', () => {
  for (const query of ['витамин К ВЭЖХ','витамин K ВЭЖХ','ВЭЖХ К витамин','витамин к вэжх']) {
    assert.deepEqual(codes(search.searchServices(actual,query)),['35.104','35.116','35.120'],query);
  }
  for (const query of ['витамин К крови','витамин K крови','крови витамин К','крови К витамин']) {
    assert.deepEqual(codes(search.searchServices(actual,query)),['35.116','35.120'],query);
  }
});

test('multiple explicit vitamin letters keep identical results across scripts and order', () => {
  const reference = codes(search.searchServices(actual,'витамин D C'));
  assert.ok(reference.includes('35.116'));
  for (const query of ['витамин D С','С витамин D','витамин С D','D С витамин','витамин Д C']) {
    assert.deepEqual(codes(search.searchServices(actual,query)),reference,query);
  }
  for (const query of ['крови витамин D С','витамин С D крови','крови С D витамин','D крови С витамин']) {
    assert.deepEqual(codes(search.searchServices(actual,query)),['35.116'],query);
  }
  for (const query of ['витамин C D E','Е витамин С D','D Е C витамин','витамин Д Е С крови']) {
    assert.deepEqual(codes(search.searchServices(actual,query)),['35.116'],query);
  }
});

test('explicit letter evidence does not change lowercase repeated prepositions into extra vitamins', () => {
  const services = [
    {code:'01.001',name:'Витамин C с кальцием',price_rub:1},
    {code:'01.002',name:'Витамин D к исследованию',price_rub:2},
    {code:'01.003',name:'Витамин C D E в крови',price_rub:3}
  ];
  assert.deepEqual(codes(search.searchServices(services,'витамин С с кальцием')),['01.001']);
  assert.deepEqual(codes(search.searchServices(services,'с кальцием С витамин')),['01.001']);
  assert.deepEqual(codes(search.searchServices(services,'витамин D к исследованию')),['01.002']);
  assert.deepEqual(codes(search.searchServices(services,'к исследованию D витамин')),['01.002']);
  assert.deepEqual(codes(search.searchServices(services,'крови в Е С D витамин')),['01.003']);
});


test('case folding applies to complete queries including vitamin prepositions', () => {
  for(const [query,expected] of [
    ['витамин B9 в эритроцитах',['14.192']],
    ['витамин С в крови',['42.139','35.116','35.118']],
    ['к витамин крови',['35.116','35.120']],
    ['витамин d с е',['35.116']]
  ]) for(const phrase of [query.toUpperCase(),query.toLowerCase()]) assert.deepEqual(codes(search.searchServices(actual,phrase)),expected,phrase);
  const services=[{code:'01.001',name:'Витамин D суммарно в крови',price_rub:1},{code:'01.002',name:'Витамин C D в крови',price_rub:1}];
  assert.deepEqual(codes(search.searchServices(services,'витамин D С крови')),['01.002']);
});

test('all word permutations preserve literal function words beside vitamin letters', () => {
  function permutations(values){return values.length ? values.flatMap((value,i)=>permutations(values.filter((_,j)=>j!==i)).map(rest=>[value,...rest])):[[]];}
  const services=[{code:'01.001',name:'Витамин D с кальцием дополнительный маркер',price_rub:1},{code:'01.002',name:'Витамин D к исследованию дополнительный маркер',price_rub:1}];
  for(const [words,expected] of [[['витамин','D','с','кальцием'],'01.001'],[['витамин','D','к','исследованию'],'01.002']]) {
    for(const phrase of permutations(words).map(p=>p.join(' '))) for(const query of [phrase.toLowerCase(),phrase.toUpperCase()]) assert.deepEqual(codes(search.searchServices(services,query)),[expected],query);
  }
  for(const phrase of permutations(['крови','К','витамин']).map(p=>p.join(' '))) for(const query of [phrase.toLowerCase(),phrase.toUpperCase()]) assert.deepEqual(codes(search.searchServices(actual,query)),['35.116','35.120'],query);
});

test('literal parts of an unmapped source designation remain available', () => {
  const services=[{code:'01.001',name:'Витамин Q к исследованию новый термин',price_rub:1}];
  for(const query of ['витамин Q к исследованию','исследованию к Q витамин','ВИТАМИН Q К ИССЛЕДОВАНИЮ']) assert.deepEqual(codes(search.searchServices(services,query)),['01.001']);
});


test('source compound fragments keep a literal path without weakening a vitamin designation', () => {
  for(const query of ['витамин фолиевая к та','витамин пантотеновая к та','к та фолиевая витамин']) assert.deepEqual(codes(search.searchServices(actual,query)),['35.113','35.115'],query);
  assert.deepEqual(codes(search.searchServices(actual,'витамин К ВЭЖХ')),['35.104','35.116','35.120']);
});
