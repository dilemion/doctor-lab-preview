/* Catalog interactions use only the public snapshot; the visit list stays in this tab. */
(function (scope) {
  'use strict';
  function node(tag, cls, text) { var n=document.createElement(tag); if(cls)n.className=cls; if(text!==undefined)n.textContent=text; return n; }
  var model=typeof module==='object'&&module.exports?require('./search.js'):scope.DocnlabSearch;
  function selectionState(services, value) {
    var codes=Array.isArray(value)?[...new Set(value.filter(function(c){return typeof c==='string';}))].slice(0,100):[];
    var rows=model.selectedServices(services,codes), known=new Set(rows.map(function(s){return s.code;}));
    return {codes:codes,rows:rows,missing:codes.filter(function(c){return !known.has(c);}),total:model.selectionTotal(services,codes)};
  }
  function selectionNote(rows) {
    return rows.some(function(s){return s.is_sampling_service;})
      ?'Выбранное взятие биоматериала уже включено в сумму. Другие услуги не добавляются автоматически.'
      :'Взятие биоматериала оплачивается отдельно и не включено в сумму.';
  }
  function mount(data, base) {
    var api=scope.DocnlabData;
    if(!model)throw new Error('Search model unavailable');
    var root=document.querySelector('[data-analyses-catalog]'), input=root.querySelector('[data-analysis-search]');
    var sale=root.querySelector('[data-discount-filter]'), kind=root.querySelector('[data-kind-filter]'), sort=root.querySelector('[data-sort]');
    var list=root.querySelector('[data-analysis-list]'), count=root.querySelector('[data-result-count]');
    var prev=root.querySelector('[data-page-prev]'), next=root.querySelector('[data-page-next]'), pagesLabel=root.querySelector('[data-page-label]');
    var reset=root.querySelector('[data-reset-filters]'), clear=root.querySelector('[data-clear-query]'), guess=root.querySelector('[data-query-suggestion]');
    var announcement=root.querySelector('[data-catalog-announcement]'), dialog=document.querySelector('[data-selection-dialog]');
    var codes=[], page=0, size=20, timer, selectedKey='docnlab-visit-list-v1', committed=window.location.href;
    function announce(text){announcement.textContent=text;}
    try{codes=selectionState(data.services,JSON.parse(sessionStorage.getItem(selectedKey)||'[]')).codes;}catch(_){codes=[];}
    function save(){try{sessionStorage.setItem(selectedKey,JSON.stringify(codes));}catch(_){}}
    function state(){return selectionState(data.services,codes);}
    function selected(){return state().rows;}
    function resultsFocus(){count.tabIndex=-1;count.focus({preventScroll:true});count.scrollIntoView({block:'start'});}
    function dateLabel(value){return new Intl.DateTimeFormat('ru-RU',{timeZone:'Europe/Moscow',dateStyle:'medium'}).format(new Date(value));}
    function applyURL(){
      var params=new URL(window.location.href).searchParams;
      input.value=(params.get('q')||'').slice(0,200);sale.checked=params.get('sale')==='1';
      kind.value=['tests','profiles'].includes(params.get('type'))?params.get('type'):'all';
      sort.value=['price-asc','name'].includes(params.get('sort'))?params.get('sort'):'relevance';page=0;
    }
    function writeURL(push){
      var url=new URL(window.location.href);url.search='';
      if(input.value.trim())url.searchParams.set('q',input.value.trim());
      if(sale.checked)url.searchParams.set('sale','1');
      if(kind.value!=='all')url.searchParams.set('type',kind.value);
      if(sort.value!=='relevance')url.searchParams.set('sort',sort.value);
      if(push&&url.href!==committed){history.replaceState(null,'',committed);history.pushState(null,'',url.href);committed=url.href;}
      else history.replaceState(null,'',url.href);
    }
    function filterActive(){return sale.checked||kind.value!=='all'||sort.value!=='relevance';}
    function resetAll(){input.value='';sale.checked=false;kind.value='all';sort.value='relevance';page=0;writeURL(true);render();input.focus();}
    function external(text,url){
      var safe=api.safeHttpUrl(url);if(!safe)return node('span','',text);
      var a=node('a','',text);a.href=safe;a.target='_blank';a.rel='noopener noreferrer';return a;
    }
    function detailContent(service){
      var content=node('div','dl-description-content');
      if(service.turnaround)content.append(node('p','','Срок выполнения: '+service.turnaround));
      var desc=service.description;
      if(desc&&(desc.summary||(Array.isArray(desc.sections)&&desc.sections.length))){
        if(desc.summary&&(!Array.isArray(desc.sections)||!desc.sections.length))content.append(node('p','',desc.summary));
        (desc.sections||[]).forEach(function(section){if(section.title)content.append(node('h3','',section.title));if(section.text)content.append(node('p','',section.text));});
        var provenance=node('p','dl-source');provenance.append(external('Источник описания: ДНКом ↗',desc.source_url||service.source_url));
        if(desc.updated_at)provenance.append(document.createTextNode(' · '+dateLabel(desc.updated_at)));
        content.append(provenance);
      }else{
        content.append(node('p','','Описание и правила подготовки доступны в карточке лаборатории. Ссылка откроется в новой вкладке.'));
        content.append(external('Открыть описание и подготовку в ДНКом ↗',service.source_url));
      }
      return content;
    }
    function serviceCard(service){
      var card=node('article','dl-test');card.dataset.code=service.code;
      var header=node('div','dl-test-header'), title=node('div','dl-test-title'), meta=node('div','dl-test-meta');
      meta.append(node('span','dl-code',service.code));
      if(service.is_sampling_service)meta.append(node('span','dl-tag','Взятие биоматериала'));
      else if(service.is_profile)meta.append(node('span','dl-tag','Комплекс'));
      title.append(node('h2','',service.name),meta);
      var price=node('div','dl-price');
      if(api.hasDiscount(service)){
        var old=node('del','dl-old-price',api.money(service.old_price_rub));old.setAttribute('aria-label','Старая цена '+api.money(service.old_price_rub));price.append(old);
        if(typeof service.discount_percent==='number'&&service.discount_percent>0&&service.discount_percent<100)price.append(node('span','dl-discount','−'+service.discount_percent+'%'));
      }
      price.append(node('strong','dl-current-price',api.money(service.price_rub)));header.append(title,price);card.append(header);
      var facts=node('div','dl-result-meta');
      if(service.turnaround)facts.append(node('p','dl-turnaround','Срок: '+service.turnaround));
      var materials=[...new Set((service.biomaterials||[]).map(function(m){return m.name;}).filter(Boolean))];
      if(materials.length)facts.append(node('p','dl-turnaround',materials.join('; ')));
      if(facts.children.length)card.append(facts);
      var sampling=new Map();
      (service.biomaterials||[]).forEach(function(m){(m.sampling_options||[]).forEach(function(option){
        if(option.code&&!sampling.has(option.code))sampling.set(option.code,option);
      });});
      if(sampling.size){
        var prices=Array.from(sampling.values()).map(function(option){
          var current=data.services.find(function(s){return s.code===option.code;});
          return (current?current.name:option.name)+' — '+api.money(current?current.price_rub:option.price_rub);
        });
        card.append(node('p','dl-sampling-price',(prices.length>1?'Варианты взятия: ':'')+prices.join('; ')+'. Оплачивается отдельно; в список автоматически не добавляется.'));
      }
      var actions=node('div','dl-result-actions'), details=node('details','dl-description'), summary=node('summary','','Описание и подготовка');
      details.append(summary);
      details.addEventListener('toggle',function(){if(details.open&&details.children.length===1)details.append(detailContent(service));});
      var button=node('button','dl-add-test');button.type='button';button.dataset.addCode=service.code;
      button.setAttribute('aria-label','Добавить '+service.name+' в список');
      function update(){var active=codes.includes(service.code);button.textContent=active?'✓ Добавлено':'+ Добавить';button.setAttribute('aria-pressed',String(active));button.setAttribute('aria-label',(active?'Убрать ':'Добавить ')+service.name+(active?' из списка':' в список'));}
      update();button.addEventListener('click',function(){
        var active=codes.includes(service.code);
        if(active)codes=codes.filter(function(c){return c!==service.code;});
        else if(codes.length<100)codes.push(service.code);
        else{announce('В списке уже 100 позиций. Уберите лишние, чтобы добавить ещё.');return;}
        save();renderSelection();update();announce((active?'Убрано: ':'Добавлено: ')+service.name+'. В списке '+codes.length+'.');
      });
      actions.append(details,button);card.append(actions);return card;
    }
    function render(){
      var result=model.searchServices(data.services,input.value,{discountedOnly:sale.checked,kind:kind.value,sort:sort.value});
      var rows=result.rows,totalPages=Math.max(1,Math.ceil(rows.length/size));page=Math.max(0,Math.min(page,totalPages-1));
      list.replaceChildren();list.setAttribute('aria-busy','false');
      guess.hidden=result.matchType!=='suggested';
      guess.textContent=result.matchType==='suggested'?'Точных совпадений нет. Возможно, вы искали «'+result.suggestedQuery+'». Проверьте название по назначению.':'';
      if(!rows.length){
        var box=node('div','dl-no-results');box.append(node('h3','','Ничего не найдено'));
        var unfiltered=model.searchServices(data.services,input.value,{allowSuggestions:false}).rows.length;
        var filtered=filterActive()&&unfiltered>0;
        box.append(node('p','',filtered?'По вашему запросу есть позиции, но они не подходят под выбранные фильтры.':'Проверьте название в назначении. Попробуйте часть названия: например, «ферритин» или «ТТГ».'));
        var action=node('button','',filtered?'Убрать фильтры':'Показать все позиции');action.type='button';
        action.addEventListener('click',function(){if(filtered){sale.checked=false;kind.value='all';sort.value='relevance';page=0;writeURL(true);render();}else resetAll();});box.append(action);list.append(box);
      }else rows.slice(page*size,(page+1)*size).forEach(function(s){list.append(serviceCard(s));});
      var plural=new Intl.PluralRules('ru-RU').select(rows.length);
      count.textContent=rows.length.toLocaleString('ru-RU')+' '+({one:'позиция',few:'позиции',many:'позиций',other:'позиции'}[plural])+(input.value.trim()?' по запросу':' в каталоге');
      pagesLabel.textContent=rows.length?'Показано '+(page*size+1)+'–'+Math.min((page+1)*size,rows.length)+' из '+rows.length:'';
      prev.disabled=page===0;next.disabled=page+1>=totalPages;
      root.querySelector('.dl-pagination').hidden=rows.length<=size;
      clear.hidden=!input.value;reset.hidden=!input.value&&!filterActive();
    }
    function updateCardButtons(){
      root.querySelectorAll('[data-add-code]').forEach(function(button){var service=data.services.find(function(s){return s.code===button.dataset.addCode;});var active=codes.includes(button.dataset.addCode);button.textContent=active?'✓ Добавлено':'+ Добавить';button.setAttribute('aria-pressed',String(active));if(service)button.setAttribute('aria-label',(active?'Убрать ':'Добавить ')+service.name+(active?' из списка':' в список'));});
    }
    function restoreListFocus(area,index){
      var buttons=area.querySelectorAll('[data-remove-code]');
      var target=buttons[Math.min(index,buttons.length-1)]||area.querySelector('[data-close-selection]')||area.querySelector('h2');
      if(target){if(target.tagName==='H2')target.tabIndex=-1;target.focus();}
    }
    function renderSelection(){
      var current=state(),rows=current.rows,total=current.total;
      document.querySelectorAll('[data-selected-count]').forEach(function(n){n.textContent=current.codes.length;});
      document.querySelectorAll('[data-selection-total]').forEach(function(n){n.textContent=api.money(total)+(current.missing.length?' + ?':'');});
      document.querySelectorAll('[data-total-label]').forEach(function(n){n.textContent=current.missing.length?'Известная стоимость':'Стоимость выбранного';});
      document.querySelectorAll('[data-selection-note]').forEach(function(n){n.textContent=selectionNote(rows)+' Это список для визита, а не оформленный заказ.';});
      document.querySelectorAll('[data-selection-list]').forEach(function(container){
        container.replaceChildren();
        if(current.missing.length){
          var warning=node('div','dl-selection-warning');warning.setAttribute('role','status');
          warning.append(node('strong','','В списке есть недоступные позиции'),node('p','','Коды: '+current.missing.join(', ')+'. Их цены неизвестны. Проверьте назначение перед копированием.'));
          var acknowledge=node('button','','Убрать недоступные позиции');acknowledge.type='button';
          acknowledge.addEventListener('click',function(){var area=container.closest('dialog,aside');codes=selected().map(function(s){return s.code;});save();renderSelection();restoreListFocus(area,0);announce('Недоступные позиции убраны из списка.');});
          warning.append(acknowledge);container.append(warning);
        }
        if(!rows.length&&!current.missing.length){var text=node('div','dl-selected-empty');text.append(node('strong','','Соберите назначение в одном месте'),document.createTextNode('Добавляйте анализы из результатов поиска. Здесь будут их названия и общая стоимость.'));container.append(text);}
        rows.forEach(function(service,index){
          var item=node('div','dl-selected-item'), body=node('div');body.append(node('p','',service.name),node('span','dl-code',service.code),node('strong','',api.money(service.price_rub)));
          var remove=node('button','dl-remove-test','×');remove.type='button';remove.dataset.removeCode=service.code;remove.setAttribute('aria-label','Убрать '+service.name+' из списка');
          remove.addEventListener('click',function(){var area=container.closest('dialog,aside');codes=codes.filter(function(c){return c!==service.code;});save();renderSelection();updateCardButtons();restoreListFocus(area,index);announce('Убрано: '+service.name+'. В списке '+codes.length+'.');});item.append(body,remove);container.append(item);
        });
      });
      document.querySelectorAll('[data-copy-list]').forEach(function(button){button.disabled=rows.length===0||current.missing.length>0;});
      document.querySelectorAll('[data-clear-selection]').forEach(function(button){button.disabled=codes.length===0;});
      document.querySelectorAll('[data-selection-feedback]').forEach(function(n){n.replaceChildren();});
    }
    function openSelection(){if(dialog&&!dialog.open)dialog.showModal();}
    document.querySelectorAll('[data-open-selection]').forEach(function(button){button.disabled=false;button.addEventListener('click',openSelection);});
    document.querySelector('[data-close-selection]').addEventListener('click',function(){dialog.close();});
    dialog.addEventListener('click',function(event){if(event.target===dialog){var rect=dialog.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)dialog.close();}});
    document.querySelectorAll('[data-clear-selection]').forEach(function(button){button.addEventListener('click',function(){var area=button.closest('dialog,aside');codes=[];save();renderSelection();updateCardButtons();restoreListFocus(area,0);announce('Список очищен.');});});
    function listText(){var rows=selected();return 'Доктор и Лаборатория — список для визита\n\n'+rows.map(function(s){return s.code+' — '+s.name+' — '+api.money(s.price_rub);}).join('\n')+'\n\nСтоимость выбранного: '+api.money(model.selectionTotal(data.services,codes))+'\n'+selectionNote(rows)+'\nЦены по прайсу от '+dateLabel(data.updated_at);}
    document.querySelectorAll('[data-copy-list]').forEach(function(button){button.addEventListener('click',async function(){
      if(!selected().length||state().missing.length)return;var text=listText();
      try{await navigator.clipboard.writeText(text);document.querySelectorAll('[data-selection-feedback]').forEach(function(n){n.textContent='Список скопирован. Можно отправить его администратору.';});announce('Список скопирован.');}
      catch(_){document.querySelectorAll('[data-selection-feedback]').forEach(function(n){n.replaceChildren(node('span','','Не удалось скопировать автоматически. Выделите и скопируйте текст:'));var area=node('textarea','dl-copy-fallback');area.readOnly=true;area.value=text;area.setAttribute('aria-label','Текст списка анализов для копирования');n.append(area);});var visible=Array.from(document.querySelectorAll('.dl-copy-fallback')).find(function(n){return n.getClientRects().length;});if(visible){visible.focus();visible.select();}}
    });});
    input.addEventListener('input',function(){clearTimeout(timer);timer=setTimeout(function(){page=0;writeURL(false);render();},180);});
    root.querySelector('form').addEventListener('submit',function(event){event.preventDefault();clearTimeout(timer);page=0;writeURL(true);render();resultsFocus();});
    [sale,kind,sort].forEach(function(control){control.addEventListener('change',function(){clearTimeout(timer);page=0;writeURL(true);render();});});
    clear.addEventListener('click',function(){input.value='';page=0;writeURL(true);render();input.focus();});
    reset.addEventListener('click',resetAll);
    root.querySelectorAll('[data-query]').forEach(function(button){button.addEventListener('click',function(){clearTimeout(timer);input.value=button.dataset.query;page=0;writeURL(true);render();resultsFocus();});});
    prev.addEventListener('click',function(){page-=1;render();list.scrollIntoView({block:'start'});count.tabIndex=-1;count.focus({preventScroll:true});});
    next.addEventListener('click',function(){page+=1;render();list.scrollIntoView({block:'start'});count.tabIndex=-1;count.focus({preventScroll:true});});
    window.addEventListener('popstate',function(){committed=window.location.href;applyURL();render();});
    root.querySelectorAll('[data-query]').forEach(function(button){button.disabled=false;});
    root.querySelector('[data-search-controls]').disabled=false;
    var status=root.querySelector('[data-analysis-status]');status.textContent='Прайс от '+dateLabel(data.updated_at);
    if((Date.now()-new Date(data.updated_at).getTime())>3*86400000){status.textContent+=' · уточните цену перед визитом';status.classList.add('dl-status--warning');}
    applyURL();renderSelection();render();
  }
  var exports={mount:mount,selectionState:selectionState,selectionNote:selectionNote};
  if(typeof module==='object'&&module.exports)module.exports=exports;else scope.DocnlabCatalog=exports;
})(typeof window==='undefined'?globalThis:window);
