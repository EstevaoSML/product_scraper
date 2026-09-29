const products = JSON.parse(document.getElementById('catalog-data').textContent);
const money = cents => cents === null || cents === undefined ? tr('Indisponível') : new Intl.NumberFormat(locale(), {style:'currency', currency:'BRL'}).format(cents / 100);
const normalize = value => value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
const search = document.getElementById('search');
const sort = document.getElementById('sort');
const grid = document.getElementById('product-grid');
const cards = new Map([...grid.children].map(card => [card.dataset.id, card]));
let category = '';
function filterProducts() {
  const query = normalize(search.value.trim());
  const visible = products.filter(p => (!category || p.category === category) && normalize(p.name + ' ' + p.category + ' ' + tr(p.category)).includes(query));
  if (sort.value === 'price') visible.sort((a,b) => (a.current_cents ?? Infinity) - (b.current_cents ?? Infinity));
  if (sort.value === 'drop') visible.sort((a,b) => (a.change ?? Infinity) - (b.change ?? Infinity));
  for (const card of cards.values()) card.hidden = true;
  for (const p of visible) {const card = cards.get(p.id); card.hidden = false; grid.append(card);}
  document.getElementById('result-count').textContent = tr(`${visible.length} ${visible.length === 1 ? 'produto' : 'produtos'}`);
  document.getElementById('empty').hidden = visible.length > 0;
}
search.addEventListener('input', filterProducts);
sort.addEventListener('change', filterProducts);
document.querySelectorAll('[data-category]').forEach(button => button.addEventListener('click', () => {
  category = button.dataset.category;
  document.querySelectorAll('[data-category]').forEach(b => {b.classList.toggle('active', b === button); b.setAttribute('aria-pressed', String(b === button));});
  filterProducts();
}));
document.getElementById('reset').addEventListener('click', () => {search.value=''; sort.value='featured'; document.querySelector('[data-category=""]').click(); search.focus();});
document.querySelectorAll('.product-photo img').forEach(img => img.addEventListener('error', () => {
  img.hidden = true;
  const message = document.createElement('p'); message.textContent = tr('Foto ilustrativa indisponível'); message.dataset.i18nFallback = 'true'; message.style.cssText='text-align:center;padding:75px 15px;color:#64745c'; img.parentElement.append(message);
}, {once:true}));
const dialog = document.getElementById('history-dialog');
let activeProduct = null;
const dateLabel = date => new Intl.DateTimeFormat(locale(),{timeZone:'UTC'}).format(new Date(date.slice(0,10)+'T12:00:00Z'));
document.getElementById('close-dialog').addEventListener('click', () => dialog.close());
dialog.addEventListener('click', event => {if (event.target === dialog) {const r = dialog.getBoundingClientRect(); if(event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.close();}});
const svgNS = 'http://www.w3.org/2000/svg';
function svgNode(tag, attrs, text) {const node = document.createElementNS(svgNS, tag); for(const [k,v] of Object.entries(attrs)) node.setAttribute(k,v); if(text !== undefined) node.textContent=tr(text); return node;}
function textNode(tag, text, className) {const node=document.createElement(tag);node.textContent=tr(text);if(className)node.className=className;return node;}
function openHistory(id) {
  const p = products.find(p => p.id === id);
  if(!p) return;
  activeProduct = id;
  document.getElementById('history-title').textContent = p.name;
  document.getElementById('history-variant').textContent = p.variant + ' · BRL';
  const summary = document.getElementById('history-summary'); summary.replaceChildren();
  const minimum=p.history.length ? Math.min(...p.history.map(h=>h.price_cents)) : null;
  for(const [label,value] of [['Preço médio atual',money(p.current_cents)],[p.first_observed_date ? `Primeiro registro · ${dateLabel(p.first_observed_date)}` : 'Aguardando primeira coleta',money(p.first_cents)],['Menor média observada',money(minimum)]]) {const box=textNode('div',label);box.append(textNode('strong',value));summary.append(box);}
  const offers=document.getElementById('retailer-offers');offers.replaceChildren(textNode('h3','Ofertas por varejista'));
  if(!p.offers.length) offers.append(textNode('p','Nenhuma oferta confirmada pelo agente para este produto.','offer-note'));
  for(const offer of p.offers) {
    const card=textNode('div','','retailer-offer');
    const header=textNode('div','','offer-header');header.append(textNode('strong',offer.retailer),textNode('strong',money(offer.price_cents)));
    card.append(textNode('p',offer.source_method === 'mcp_direct' ? 'Origem: verificação direta pelo scraper MCP.' : 'Origem: pesquisa pelo agente GPT-5 mini.','offer-note'));
    card.append(header,textNode('p',`Vendedor: ${offer.seller || 'Não informado na evidência'}`));
    const availability=offer.availability?.endsWith('InStock') ? 'Em estoque na coleta' : offer.availability?.endsWith('OutOfStock') ? 'Indisponível na coleta' : 'Disponibilidade não confirmada';
    card.append(textNode('p',`${tr(availability)} · ${new Date(offer.observed_at).toLocaleString(locale(),{timeZone:'UTC'}) + ' UTC'}`));
    card.append(textNode('p',offer.stale ? 'Última leitura deste varejista, há mais de 48 horas. Incluída na média; confirme o preço na loja.' : 'Preço da oferta estruturada. Descontos Pix, frete e condições de pagamento não foram confirmados.','offer-note'));
    const link=textNode('a','Ver oferta na loja ↗');link.href=offer.url;link.target='_blank';link.rel='noopener noreferrer';card.append(link);offers.append(card);
  }
  const chart=document.getElementById('history-chart');chart.replaceChildren();
  if(p.history.length<2) {
    chart.append(textNode('div',p.history.length ? 'Primeira observação registrada. O gráfico aparecerá após uma coleta em outra data.' : 'O histórico começará quando uma oferta com preço e disponibilidade for confirmada.','history-empty'));
  } else {
    const svg=svgNode('svg',{viewBox:'0 0 760 310',role:'img','aria-label':tr(`Histórico de ${p.name}. Valores completos na tabela.`),class:'history-chart-svg'});
    const values=p.history.map(h=>h.price_cents), minimum=Math.min(...values), maximum=Math.max(...values);
    const pad=Math.max(100, (maximum-minimum)*.2), low=Math.max(0,minimum-pad),high=maximum+pad;
    const dates=p.history.map(h=>Date.parse(h.date+'T12:00:00Z')),first=dates[0],last=dates.at(-1);
    const x=i=>85+(dates[i]-first)/(last-first)*636,y=v=>235-(v-low)/(high-low)*175;
    for(let i=0;i<=4;i++){const v=low+(high-low)*i/4;svg.append(svgNode('line',{x1:85,y1:y(v),x2:721,y2:y(v),stroke:'#dfe5d9','stroke-dasharray':'4 4'}));svg.append(svgNode('text',{x:75,y:y(v)+4,'text-anchor':'end',fill:'#788372','font-size':10},money(v)));}
    svg.append(svgNode('polyline',{points:values.map((v,i)=>`${x(i)},${y(v)}`).join(' '),fill:'none',stroke:'#267354','stroke-width':3}));
    p.history.forEach((h,i)=>{const dot=svgNode('circle',{cx:x(i),cy:y(h.price_cents),r:5,fill:'#267354'});dot.append(svgNode('title',{},`${h.date}: ${money(h.price_cents)}`));svg.append(dot);if(i===0||i===p.history.length-1||i%Math.ceil(p.history.length/5)===0)svg.append(svgNode('text',{x:x(i),y:263,'text-anchor':'middle','font-size':10,fill:'#788372'},dateLabel(h.date)));});
    chart.append(svg);
  }
  const table=document.getElementById('history-table');table.replaceChildren();
  p.history.forEach(h=>{const row=document.createElement('tr');[dateLabel(h.date),money(h.price_cents),String(h.retailer_count)].forEach(text=>row.append(textNode('td',text)));table.append(row);});
  if(!p.history.length){const row=document.createElement('tr');const cell=textNode('td','Ainda não há observações confirmadas.');cell.colSpan=3;row.append(cell);table.append(row);}
  if(!dialog.open) dialog.showModal();
}
document.querySelectorAll('[data-product]').forEach(button=>button.addEventListener('click',()=>openHistory(button.dataset.product)));
document.getElementById('suggestion-form').addEventListener('submit', event=>{
  event.preventDefault();const form=event.currentTarget;
  const product=document.getElementById('suggestion').value.trim();
  const notes=document.getElementById('suggestion-notes').value.trim();
  if(!product){document.getElementById('suggestion-status').textContent=tr('Informe o produto e o modelo.');return;}
  const body=`${tr('Gostaria de sugerir o acompanhamento deste produto:')}\n\n${product}\n\n${notes}`;
  if(form.dataset.email){window.location.href=`mailto:${encodeURIComponent(form.dataset.email)}?subject=${encodeURIComponent(tr('Sugestão de produto — Preço Claro'))}&body=${encodeURIComponent(body)}`;document.getElementById('suggestion-status').textContent=tr('Solicitamos a abertura do seu aplicativo de e-mail. Revise e confirme o envio por lá.');}
  else {document.getElementById('suggestion-status').textContent=`${tr('Prévia — nenhum e-mail foi enviado ou armazenado.')}\n\n${body}`;}
});

function applyLanguage() {
  translatePage();
  for(const p of products) {
    const card = cards.get(p.id);
    card.querySelector('.price-detail strong').textContent = money(p.current_cents);
    const oldPrice = card.querySelector('.first-price');
    if(oldPrice) oldPrice.textContent = money(p.first_cents);
    const firstLabel = card.querySelector('.old-price .price-label');
    if(firstLabel) firstLabel.textContent = tr(`Primeiro registro · ${dateLabel(p.first_observed_date)}`);
    card.querySelector('.observed-label').textContent = p.observed_at ? tr(`Observado em ${dateLabel(p.observed_at)} (UTC)`) : tr('Pesquisa pendente de confirmação');
  }
  document.querySelectorAll('[data-i18n-fallback]').forEach(node=>node.textContent=tr('Foto ilustrativa indisponível'));
  filterProducts();
  if(dialog.open && activeProduct) openHistory(activeProduct);
}
document.getElementById('language').addEventListener('change', event => {
  language = event.target.value;
  try { localStorage.setItem('preco-language',language); } catch {}
  document.getElementById('suggestion-status').textContent='';
  applyLanguage();
});
applyLanguage();
