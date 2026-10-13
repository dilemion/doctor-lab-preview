/* Search and selection rules shared by the Docnlab catalog. No DOM or storage. */
(function (scope, factory) {
  'use strict';
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else scope.DocnlabSearch = api;
})(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  function queryString(value) {
    return typeof value === 'string' || typeof value === 'number' ? String(value) : '';
  }

  function normalizeQuery(value) {
    return queryString(value).normalize('NFKC').toLocaleLowerCase('ru-RU')
      .replace(/ё/g, 'е').replace(/[^\p{L}\p{N}]+/gu, ' ').trim().replace(/\s+/g, ' ');
  }

  function servicesArray(services) {
    return Array.isArray(services) ? services.filter(function (service) {
      return service && typeof service.code === 'string' && service.code &&
        typeof service.name === 'string';
    }) : [];
  }

  function hasDiscount(service) {
    return typeof service.price_rub === 'number' && Number.isFinite(service.price_rub) &&
      typeof service.old_price_rub === 'number' && Number.isFinite(service.old_price_rub) &&
      service.old_price_rub > service.price_rub;
  }

  function passesFilters(service, options) {
    if (options.discountedOnly && !hasDiscount(service)) return false;
    if (options.kind === 'tests') return !service.is_profile && !service.is_sampling_service;
    if (options.kind === 'profiles') return service.is_profile === true;
    return true;
  }

  /*
   * These are search aliases, never a list of available tests. Every expansion
   * must still match the current service name. All other queries use name/code.
   */
  var phraseAliases = {
    'оак': ['общий анализ крови', 'клинический анализ крови'],
    'оам': ['общий анализ мочи'],
    'vitamin d': ['витамин d'],
    'vitamind': ['витамин d'],
    'витамин д': ['витамин d'],
    'витаминd': ['витамин d']
  };

  function queryVariants(query) {
    var variants = [query];
    if (Object.prototype.hasOwnProperty.call(phraseAliases, query)) {
      return variants.concat(phraseAliases[query]);
    }
    var tokenAliases = {
      'оак': phraseAliases['оак'],
      'оам': phraseAliases['оам'],
      'vitamin': ['витамин']
    };
    var expanded = [''];
    var tokens = query.split(' ');
    tokens.forEach(function (token) {
      var replacements = Object.prototype.hasOwnProperty.call(tokenAliases, token) ? tokenAliases[token] : [token];
      var next = [];
      expanded.forEach(function (prefix) {
        replacements.forEach(function (replacement) { next.push((prefix + ' ' + replacement).trim()); });
      });
      expanded = next.slice(0, 8);
    });
    variants = variants.concat(expanded);
    if (tokens.includes('витамин') || tokens.includes('vitamin')) {
      variants = variants.concat(variants.map(function (variant) {
        return variant.split(' ').map(function (token) { return token === 'д' ? 'd' : token; }).join(' ');
      }));
    }
    return Array.from(new Set(variants));
  }

  function vitaminContext(tokens) {
    return tokens.includes('витамин') || tokens.includes('vitamin');
  }

  function vitaminDesignation(token) {
    var match = /^([abcdekавсдек])(\d{0,2})$/.exec(token);
    if (!match) return null;
    // Letter equivalence applies to vitamin designations, not names or codes.
    var letters = { 'а': 'a', 'в': 'b', 'с': 'c', 'д': 'd', 'е': 'e', 'к': 'k' };
    return (letters[match[1]] || match[1]) + match[2];
  }

  function indexed(services) {
    return services.map(function (service, index) {
      var name = normalizeQuery(service.name);
      // Source abbreviations (ТТГ, CRP, CBC...) remain individual searchable tokens.
      var abbreviations = (service.name.match(/(?<![\p{L}\p{N}])[\p{Lu}][\p{Lu}\p{N}]{1,9}(?![\p{L}\p{N}])/gu) || [])
        .map(normalizeQuery);
      // Source capitals distinguish vitamin C/С from the ordinary preposition с.
      var designations = (service.name.match(/(?<![\p{L}\p{N}])[A-ZА-Я](?:\d{1,2})?(?![\p{L}\p{N}])/gu) || [])
        .map(function (token) { return vitaminDesignation(normalizeQuery(token)); }).filter(Boolean);
      return { service: service, name: name, words: name.split(' '),
        abbreviations: abbreviations, designations: designations,
        compounds: (service.name.match(/[\p{L}\p{N}]+(?:[-‐‑‒–—][\p{L}\p{N}]+)+/gu)||[]).map(function(value){return normalizeQuery(value).split(' ');}).filter(function(parts){return parts.some(vitaminDesignation);}),
        otherLabels: name.split(' ').filter(function(word,position,words){return position>0&&['витамин','vitamin'].includes(words[position-1])&&/^[a-zа-я]$/.test(word)&&!vitaminDesignation(word);}),
        code: service.code.toLocaleLowerCase('ru-RU'), index: index };
    });
  }

  function textMatch(item, tokens) {
    // Literal complete source names remain available independently of aliases.
    if (item.name === tokens.join(' ') || (tokens.length === item.words.length &&
      tokens.slice().sort().join(' ') === item.words.slice().sort().join(' '))) return true;
    var isVitamin = vitaminContext(tokens);
    var labelsOnly = isVitamin && tokens.every(function(token){
      return token === 'витамин' || token === 'vitamin' || vitaminDesignation(token);
    });
    function sourceLabel(label) {
      return item.designations.includes(label) || item.words.some(function(word){
        return vitaminDesignation(word) === label && (/^[a-z]/.test(word) || /\d/.test(word));
      });
    }
    var countV = tokens.filter(function(token){return token === 'в';}).length;
    var coreLabels = isVitamin ? tokens.filter(function(token){
      return token !== 'в' || labelsOnly || countV > 1;
    }).map(vitaminDesignation).filter(Boolean) : [];
    // A generic в remains a preposition in a longer name. Unmapped source
    // designations keep the literal path, so the alias map is never a catalog.
    var unmapped = item.otherLabels.some(function(label){return tokens.includes(label);});
    var literalCompound = item.compounds.some(function(parts){return parts.every(function(part){return tokens.includes(part);});});
    if (coreLabels.length && !coreLabels.some(sourceLabel) && !unmapped && !literalCompound) return false;
    return tokens.every(function(token){
      var label = isVitamin ? vitaminDesignation(token) : null;
      if (label) {
        if (labelsOnly) return sourceLabel(label);
        if (token === 'в') return sourceLabel(label) || item.words.some(function(word){return word.startsWith(token);});
        // Exact literal function words may occur anywhere; С/К never match
        // arbitrary prefixes such as суммарно or крови.
        return sourceLabel(label) || item.words.includes(token);
      }
      var nameMatch = token.length <= 3 && /^[a-zа-я]+$/.test(token) ?
        item.words.some(function(word){return word.startsWith(token);}) : item.name.includes(token);
      return nameMatch || item.code.includes(token) || item.abbreviations.includes(token);
    });
  }

  function textScore(item, tokens, phrase) {
    var isVitamin = vitaminContext(tokens);
    var name = item.name;
    var words = item.words;
    if (isVitamin) {
      tokens = tokens.map(function (token) { return vitaminDesignation(token) || token; });
      phrase = tokens.join(' ');
      words = words.map(function (word) { return vitaminDesignation(word) || word; });
      name = words.join(' ');
    }
    var score = name === phrase ? 1000 : 0;
    if (name.startsWith(phrase)) score += 100;
    else if (name.includes(phrase)) score += 50;
    tokens.forEach(function (token) {
      // A complete source abbreviation wins over an unrelated word prefix.
      if (item.abbreviations.some(function (abbr) {
        return (isVitamin ? vitaminDesignation(abbr) || abbr : abbr) === token;
      })) score += 220;
      if (words.includes(token)) score += 10;
      else if (words.some(function (word) { return word.startsWith(token); })) score += 5;
    });
    return score;
  }

  function textResults(items, variants) {
    var queries = variants.map(function(variant){return {phrase:variant,tokens:variant?variant.split(' '):[]};});
    var matches = [];
    items.forEach(function(item){
      var score = -1;
      queries.forEach(function(query){
        if(textMatch(item,query.tokens))score=Math.max(score,textScore(item,query.tokens,query.phrase));
      });
      if(score>=0)matches.push({item:item,score:score});
    });
    return matches;
  }

  // Exactly one insertion, deletion, replacement, or adjacent transposition.
  function oneEditApart(a, b) {
    if (a === b || Math.abs(a.length - b.length) > 1) return false;
    if (a.length === b.length) {
      var different = [];
      for (var i = 0; i < a.length; i += 1) if (a[i] !== b[i]) different.push(i);
      return different.length === 1 || (different.length === 2 &&
        different[1] === different[0] + 1 && a[different[0]] === b[different[1]] &&
        a[different[1]] === b[different[0]]);
    }
    var shorter = a.length < b.length ? a : b;
    var longer = a.length < b.length ? b : a;
    var offset = 0;
    for (var j = 0; j < shorter.length; j += 1) {
      if (shorter[j] !== longer[j + offset]) {
        if (offset) return false;
        offset = 1;
        if (shorter[j] !== longer[j + offset]) return false;
      }
    }
    return true;
  }

  function suggest(items, query) {
    var tokens = query.split(' ');
    if (tokens.length > 6 || query.length > 100) return null;
    var vocabulary = new Set();
    items.forEach(function (item) {
      item.words.forEach(function (word) { if (/^[a-zа-я]{7,40}$/.test(word)) vocabulary.add(word); });
    });
    var corrected = tokens.slice();
    var corrections = 0;
    for (var i = 0; i < tokens.length; i += 1) {
      var token = tokens[i];
      if (!/^[a-zа-я]{7,40}$/.test(token) || vocabulary.has(token)) continue;
      var candidates = Array.from(vocabulary).filter(function (word) { return oneEditApart(token, word); });
      // Ambiguous or multiple corrections do not produce an assumed match.
      if (candidates.length !== 1) return null;
      corrected[i] = candidates[0];
      corrections += 1;
      if (corrections > 1) return null;
    }
    return corrections === 1 ? corrected.join(' ') : null;
  }

  function sortedRows(matches, sort) {
    return matches.slice().sort(function (a, b) {
      if (sort === 'price-asc') {
        var aPrice = typeof a.item.service.price_rub === 'number' && Number.isFinite(a.item.service.price_rub) ? a.item.service.price_rub : Infinity;
        var bPrice = typeof b.item.service.price_rub === 'number' && Number.isFinite(b.item.service.price_rub) ? b.item.service.price_rub : Infinity;
        if (aPrice !== bPrice) return aPrice - bPrice;
      } else if (sort === 'relevance' && a.score !== b.score) return b.score - a.score;
      if (sort === 'name' || sort === 'price-asc') {
        var nameOrder = a.item.service.name.localeCompare(b.item.service.name, 'ru');
        if (nameOrder) return nameOrder;
      }
      return a.item.index - b.item.index;
    }).map(function (match) { return match.item.service; });
  }

  function searchServices(services, query, options) {
    options = options || {};
    var original = queryString(query).trim();
    var normalized = normalizeQuery(original);
    var sort = ['relevance', 'price-asc', 'name'].includes(options.sort) ? options.sort : 'relevance';
    var all = indexed(servicesArray(services));
    var items = all.filter(function (item) { return passesFilters(item.service, options); });
    var rawCode = original.toLocaleLowerCase('ru-RU').replace(/\s+/g, '');
    var exact = original && all.some(function (item) { return item.code === rawCode; });
    var matches = [];
    var type = 'text';
    var suggestion = null;
    if (exact) {
      matches = items.filter(function (item) { return item.code === rawCode; })
        .map(function (item) { return { item: item, score: 1000 }; });
      type = 'exact-code';
    } else if (/^\d+\.\d{3}(?:\.[a-z0-9]+)*$/i.test(rawCode)) {
      // Full unknown codes never return a parent, child, or guessed replacement.
      matches = [];
    } else if (/^[\d.\s]+$/.test(original) && /\d/.test(original)) {
      matches = items.filter(function (item) { return item.code.includes(rawCode); })
        .map(function (item) { return { item: item, score: item.code.startsWith(rawCode) ? 20 : 0 }; });
    } else {
      matches = textResults(items, queryVariants(normalized));
      if (!matches.length && options.allowSuggestions !== false && normalized) {
        // Do not correct a known query merely because its rows were filtered out.
        var unfiltered = textResults(all, queryVariants(normalized));
        if (!unfiltered.length) {
          suggestion = suggest(all, normalized);
          if (suggestion) {
            matches = textResults(items, queryVariants(suggestion));
            if (matches.length) type = 'suggested';
            else suggestion = null;
          }
        }
      }
    }
    return { rows: sortedRows(matches, sort), matchType: matches.length ? type : 'empty',
      suggestedQuery: suggestion, originalQuery: original };
  }

  function selectedServices(services, codes) {
    if (!Array.isArray(codes)) return [];
    var lookup = new Map();
    servicesArray(services).forEach(function (service) {
      if (!lookup.has(service.code)) lookup.set(service.code, service);
    });
    var seen = new Set();
    return codes.filter(function (code) {
      if (typeof code !== 'string' || !lookup.has(code) || seen.has(code)) return false;
      seen.add(code);
      return true;
    }).map(function (code) { return lookup.get(code); });
  }

  function selectionTotal(services, codes) {
    var cents = selectedServices(services, codes).reduce(function (sum, service) {
      var price = service.price_rub;
      if (typeof price !== 'number' || !Number.isFinite(price) || price < 0) throw new TypeError('Invalid service price');
      var value = price * 100;
      var rounded = Math.round(value + Number.EPSILON * Math.max(1, value));
      if (!Number.isSafeInteger(rounded) || !Number.isSafeInteger(sum + rounded)) throw new RangeError('Selection total exceeds safe currency range');
      return sum + rounded;
    }, 0);
    return cents / 100;
  }

  return { normalizeQuery: normalizeQuery, searchServices: searchServices,
    selectedServices: selectedServices, selectionTotal: selectionTotal };
});
