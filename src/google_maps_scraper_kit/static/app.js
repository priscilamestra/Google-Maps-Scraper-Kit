"use strict";
const $ = id => document.getElementById(id);
const states = {pending_approval:"Pedido salvo",queued:"Na fila",running:"Executando",waiting:"Em espera",completed:"Concluída",partial:"Parcial",failed:"Falhou",blocked:"Bloqueada",cancelled:"Cancelada",pending:"Pendente",approved:"Aprovado",rejected:"Rejeitado"};
let map, circle, pin, markers, center = null, centerLocation = "", active = null, results = [], visible = [], selected = new Set(), busy = false, refreshing = false, mutating = false;
let currentPanel = true, focusedId = null, loadVersion = 0, readOnly = false;
let copyTimer = null;
const markerIndex = new Map();
const notice = (text, error=false) => {const node=$("notice");node.textContent=text;node.hidden=!text;node.classList.toggle("error",error);};
const feedback = (text, error=false, loading=false) => {notice(text,error);const node=$("result-feedback");node.textContent=text;node.hidden=!text;node.classList.toggle("error",error);node.classList.toggle("loading",loading);};
const el = (tag,text) => {const node=document.createElement(tag); if(text!==undefined)node.textContent=text; return node;};
const split = value => value.split(",").map(x=>x.trim()).filter(Boolean);
const omitted = item => Boolean(item.exclusion_reason||item.selection_reason||item.selected===0||item.review_status==="rejected");
const ratingValue = item => {if(!["string","number"].includes(typeof item.review_rating)||String(item.review_rating).trim()==="")return null;const value=Number(item.review_rating);return Number.isFinite(value)&&value>0&&value<=5?value:null;};
const reviewCountValue = item => {if(!["string","number"].includes(typeof item.review_count)||String(item.review_count).trim()==="")return null;const value=Number(item.review_count);return Number.isInteger(value)&&value>=0?value:null;};
function displayPhones(value){return String(value||"").split(",").map(raw=>{const text=raw.trim(),digits=text.replace(/\D/g,"");const national=text.startsWith("+55")?digits.slice(2):digits;return /^\+?[\d\s().-]+$/.test(text)&&[10,11].includes(national.length)?`${text.startsWith("+55")?"+55 ":""}(${national.slice(0,2)}) ${national.slice(2,-4)}-${national.slice(-4)}`:text;}).filter(Boolean).join(" · ");}
const positionOf = item => Number.isFinite(item.latitude)&&item.latitude>=-90&&item.latitude<=90&&Number.isFinite(item.longitude)&&item.longitude>=-180&&item.longitude<=180?[item.latitude,item.longitude]:null;
const byName = (a,b) => String(a.name||"").localeCompare(String(b.name||""),"pt-BR");
const relativeDate = value => {const date=new Date(value||"");if(!Number.isFinite(date.getTime()))return "";const minutes=Math.max(0,Math.round((Date.now()-date.getTime())/60000));if(minutes<60)return `há ${Math.max(1,minutes)} min`;if(minutes<1440)return `há ${Math.round(minutes/60)} h`;return date.toLocaleDateString("pt-BR");};
const duplicateKey = item => [item.name,String(item.phone||"").replace(/\D/g,""),String(item.website||"").replace(/^https?:\/\/(www\.)?/i,"").replace(/\/$/,""),item.address].join("|").toLocaleLowerCase("pt-BR");
const markerIcon = (focused=false,excluded=false) => L.divIcon({className:"company-marker",html:`<span class="map-pin${focused?" focused":""}${excluded?" excluded-pin":""}"></span>`,iconSize:[22,30],iconAnchor:[11,30],popupAnchor:[0,-28]});
function uniqueLinks(item,pairs,seen=new Set()){const values=[];for(const [field,label] of pairs)if(item[field])for(const raw of String(item[field]).split(",")){try{const parsed=new URL(raw.trim());if(!["http:","https:"].includes(parsed.protocol)||seen.has(parsed.href))continue;seen.add(parsed.href);values.push({field,label,url:parsed.href});}catch{}}return values;}
async function api(path,method="GET",body) {
  const response=await fetch("/api/v1"+path,{method,headers:body?{"Content-Type":"application/json"}:{},body:body?JSON.stringify(body):undefined});
  if(response.status===204)return null;
  const data=await response.json(); if(!response.ok){const error=Error(data.error||`HTTP ${response.status}`);error.status=response.status;throw error;} return data;
}
function link(parent,label,url,className="") {
  try {const parsed=new URL(url); if(!["http:","https:"].includes(parsed.protocol))return;
    const a=el("a",label);a.href=parsed.href;a.target="_blank";a.rel="noopener noreferrer";a.className=className;parent.append(a);
  } catch { /* Missing or unsafe external URL: display no link. */ }
}
const linkClass = field => `link-chip ${field==="website"?"site-link":field==="instagram"?"instagram-link":field==="whatsapp"?"whatsapp-link":field==="maps_url"?"maps-link":field==="youtube"?"youtube-link":field==="linkedin"?"linkedin-link":["x","twitter"].includes(field)?"x-link":"social-link"}`;
function panel(open, showList=false) {
  const wasPanel=currentPanel, wasList=!$("map-leads").hidden;
  const previousCenter=map?map.getCenter():null;
  currentPanel=open;
  document.body.classList.toggle("panel-mode",open);
  document.body.classList.toggle("map-mode",!open);
  $("panel-tab").setAttribute("aria-pressed",String(open));
  $("map-tab").setAttribute("aria-pressed",String(!open));
  if(open){
    document.querySelector(".top-grid").append($("new-search"),$("history-card"));
    $("map-dock").append($("map-surface"));
  } else {
    $("map-controls-slot").append($("new-search"),$("history-card"));
    document.body.prepend($("map-surface"));
  }
  const list=!open&&showList&&results.length>0;
  $("map-leads").hidden=!list;
  $("map-controls-slot").hidden=list;
  if(open&&!wasPanel){const section=wasList?"results-panel":"new-search";setActiveSection(section);$(section).scrollIntoView({behavior:"instant",block:"start"});}
  if(map){map.invalidateSize({pan:false});map.setView(previousCenter,map.getZoom(),{animate:false});if(focusedId&&markerIndex.has(focusedId))markerIndex.get(focusedId).openPopup();}
}
function mapPadding(){return !currentPanel&&innerWidth>650?{paddingTopLeft:[340,32],paddingBottomRight:[32,32]}:{padding:[28,28]};}
function fitVisibleResults(fallbackArea=null){if(!map||!window.L)return false;const points=visible.map(positionOf).filter(Boolean);$("fit-results").disabled=points.length===0;if(points.length){map.fitBounds(L.latLngBounds(points),{...mapPadding(),maxZoom:15,animate:false});return true;}if(fallbackArea&&circle){map.fitBounds(circle.getBounds(),{padding:[36,36],maxZoom:15,animate:false});return true;}return false;}
function setActiveSection(id){for(const item of document.querySelectorAll("[data-section]")){if(item.dataset.section===id)item.setAttribute("aria-current","location");else item.removeAttribute("aria-current");}}
function goToSection(id){panel(true);setActiveSection(id);$(id).scrollIntoView({behavior:"smooth",block:"start"});}
window.addEventListener("scrollend",()=>{
  if(!currentPanel)return;
  const buttons=[...document.querySelectorAll("[data-section]")];
  const atTop=button=>{const bounds=$(button.dataset.section).getBoundingClientRect();return bounds.top<=120&&bounds.bottom>120;};
  const current=buttons.find(button=>button.hasAttribute("aria-current"));
  if(current&&atTop(current))return;
  const section=buttons.find(atTop);if(section)setActiveSection(section.dataset.section);
});
function setMutationLock(locked){
  mutating=locked;$("results-panel").setAttribute("aria-busy",String(locked));
  for(const id of ["approve","cancel","approve-selected","reject-selected","pending-selected","refresh"])$(id).disabled=locked||$(id).dataset.stateDisabled==="true";
  for(const item of document.querySelectorAll(".history-open,.history-delete,#search-form input,#search-form select,#search-form button"))item.disabled=locked;
  for(const item of document.querySelectorAll('#results input[type="checkbox"]'))item.disabled=locked||readOnly;
  $("select-all").disabled=locked||readOnly||visible.length===0;
}
async function mutate(message,operation){if(mutating||busy||!active||readOnly)return false;const campaignId=active;loadVersion++;setMutationLock(true);feedback(message,false,true);try{await operation(campaignId);feedback("Estado atualizado.");return true;}catch(error){feedback(error.message,true);return false;}finally{setMutationLock(false);}}
function routesUrl(item){
  if(!positionOf(item))return null;
  const query=new URLSearchParams({api:"1",destination:`${item.latitude},${item.longitude}`});
  if(item.place_id)query.set("destination_place_id",item.place_id);
  return "https://www.google.com/maps/dir/?"+query;
}
function selectCompany(id, expand=false){
  focusedId=id;
  if(expand)panel(false,true);
  const marker=markerIndex.get(id);
  if(marker){const point=marker.getLatLng();map.setView(point,expand?Math.max(map.getZoom(),15):map.getZoom(),{animate:false});if(map.panInside)map.panInside(point,mapPadding());marker.openPopup();}
  for(const node of document.querySelectorAll("[data-company-id]")){const focused=node.dataset.companyId===id;node.classList.toggle("focused",focused);if(focused)node.setAttribute("aria-current","true");else node.removeAttribute("aria-current");if(node.matches(".lead-card"))node.querySelector("button").setAttribute("aria-pressed",String(focused));}
  for(const [markerId,itemMarker] of markerIndex){if(itemMarker.setIcon)itemMarker.setIcon(markerIcon(markerId===id,itemMarker.excluded));else itemMarker.setStyle?.({radius:markerId===id?10:7,fillColor:markerId===id?"#0b57d0":itemMarker.baseFill});}
  const card=document.querySelector(`.lead-card[data-company-id="${CSS.escape(String(id))}"]`);if(card&&!currentPanel)card.scrollIntoView({behavior:"smooth",block:"nearest"});
}
function updateStats(){
  const eligible=results.filter(item=>!omitted(item));
  for(const [id,value] of [["stat-companies",eligible.length],["stat-phones",eligible.filter(x=>x.phone).length],["stat-emails",eligible.filter(x=>x.emails).length],["stat-socials",eligible.filter(x=>x.instagram||x.facebook||x.linkedin||x.linkedin_url).length]])$(id).textContent=active?String(value):"—";
  const ratings=eligible.map(ratingValue).filter(value=>value!==null);
  for(const [id,value] of [["result-companies",eligible.length],["result-phones",eligible.filter(item=>item.phone).length],["result-whatsapp",eligible.filter(item=>item.whatsapp).length],["result-websites",eligible.filter(item=>item.website).length],["result-emails",eligible.filter(item=>item.emails).length]])$(id).textContent=active?String(value):"—";
  $("result-rating").textContent=active&&ratings.length?`${(ratings.reduce((sum,value)=>sum+value,0)/ratings.length).toLocaleString("pt-BR",{maximumFractionDigits:1,minimumFractionDigits:1})} ★`:"—";
  $("summary-context").textContent=active?$("campaign-title").textContent:"Selecione uma busca para ver seus indicadores.";
}
function updateCategories(){
  const old=$("category").value;
  for(const id of ["category","map-category"]){
    const menu=$(id);menu.replaceChildren();const all=el("option","Todas as categorias");all.value="";menu.append(all);
    for(const value of [...new Set(results.map(x=>x.category).filter(Boolean))].sort()){const option=el("option",value);option.value=value;menu.append(option);}
    menu.value=[...menu.options].some(x=>x.value===old)?old:"";
  }
}
function drawArea(area) {
  if(!map)return;
  const position=[area.latitude,area.longitude];
  if(!circle){circle=L.circle(position,{radius:area.radius_m,color:"#1769e8",weight:2,fillOpacity:.08}).addTo(map);pin=L.marker(position,{draggable:true}).addTo(map);pin.on("dragend",()=>chooseCenter(pin.getLatLng()));}
  circle.setLatLng(position).setRadius(area.radius_m);pin.setLatLng(position);
}
function chooseCenter(position) {
  if(mutating||busy)return;
  loadVersion++;
  if(active){active=null;results=[];focusedId=null;selected.clear();renderResults();$("campaign-title").textContent="Nova busca";$("campaign-status").textContent="Centro alterado; campanhas anteriores foram preservadas.";}
  center={latitude:position.lat,longitude:position.lng};centerLocation=$("location").value.trim();
  $("center-label").textContent=`Centro selecionado: ${position.lat.toFixed(5)}, ${position.lng.toFixed(5)}`;
  drawArea({...center,radius_m:Number($("radius").value)});
}
if(window.L){
  map=L.map("map",{zoomControl:false}).setView([-22.2887,-42.5341],13);
  L.control.zoom({position:"bottomright"}).addTo(map);
  const tiles=L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",{maxZoom:19,attribution:'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>'}).addTo(map);
  tiles.on("tileerror",()=>{$("map-error").hidden=false;});tiles.on("load",()=>{$("map-error").hidden=true;});
  markers=L.layerGroup().addTo(map);map.on("click",event=>chooseCenter(event.latlng));
}else $("map-error").hidden=false;
$("radius").oninput=()=>{
  $("radius-value").textContent=`${Number($("radius").value)/1000} km`;
  if(center)chooseCenter({lat:center.latitude,lng:center.longitude});
};
$("locate").onclick=async()=>{
  $("locate").disabled=true;
  try {const location=await api("/geocode?q="+encodeURIComponent($("location").value));chooseCenter({lat:location.latitude,lng:location.longitude});$("center-label").textContent=location.label;if(map)map.fitBounds(circle.getBounds(),{padding:[35,35]});notice("Local confirmado. Ajuste o centro e o raio no mapa, se necessário.");}
  catch(error){notice(error.message,true);}finally{$("locate").disabled=false;}
};
async function runtime() {
  const status=await api("/runtime");
  const online=status.maps_available;
  $("runtime").textContent=online?"● Scraper online":"● Scraper offline";$("runtime").classList.toggle("online",online);$("runtime").title=`Maps: ${online?"disponível":"indisponível"} · Worker: ${status.worker_enabled?"ativo":"desligado (prévia)"}`;
  return status;
}
async function canRun() {
  const status=await runtime();
  if(!status.worker_enabled)throw Error("Worker desligado. Ative o serviço local antes de iniciar a busca.");
  if(!status.maps_available)throw Error("Scraper Maps indisponível. Verifique o Docker antes de iniciar a busca.");
}
async function create() {
  if(busy||mutating||!$("search-form").reportValidity())return;
  busy=true;$("search").disabled=true;$("search-form").setAttribute("aria-busy","true");
  const label=$("search").textContent;$("search").textContent="Iniciando busca…";
  try {
    const mode="companies";
    if(!center||centerLocation!==$("location").value.trim())throw Error("Confirme o centro com Localizar ou clicando no mapa para esta localidade.");
    await canRun();
    const config={name:$("terms").value.trim()+" · "+$("location").value.trim(),mode,target_count:Number($("count").value),business_terms:split($("terms").value),location:{city:$("location").value.trim()},collect_emails:$("emails").checked,collect_socials:$("socials").checked,negative_terms:split($("negative").value),required_fields:$("required").value?[$("required").value]:[],manual_approval_required:true};
    config.neighborhoods=split($("neighborhoods").value);
    config.collection_limit=Number($("collection-limit").value||config.target_count);
    config.language=$("language").value;
    config.search_area={...center,radius_m:Number($("radius").value)};
    config.depth=Number($("depth").value);config.grid_size=Number($("grid-size").value);config.deep_scan=$("deep-scan").checked;config.max_queries=Number($("max-queries").value);
    config.min_rating=$("min-rating").value===""?null:Number($("min-rating").value);config.min_reviews=$("min-reviews").value===""?null:Number($("min-reviews").value);
    config.no_website=$("no-website").checked;config.verify_emails=$("verify-emails").checked;config.exclude_campaign_ids=[...$("exclude-lists").selectedOptions].map(option=>option.value);
    config.match_business_terms=$("match-topic").checked;config.category_terms=split($("category-terms").value);
    const created=await api("/campaigns","POST",config);
    active=created.campaign_id;
    notice(`Pedido salvo: ${active}`);
    await api(`/campaigns/${active}/approve`,"POST",{});
    await history();await load(active,true);notice("Busca iniciada. Os resultados aparecerão conforme forem importados.");
  }catch(error){notice(error.message,true);await history().catch(()=>{});}
  finally{busy=false;$("search").disabled=false;$("search-form").setAttribute("aria-busy","false");$("search").textContent=label;}
}
$("search-form").onsubmit=event=>{event.preventDefault();create();};
async function history(){
  const {campaigns}=await api("/campaigns");$("history").replaceChildren();
  const previousExclusions=new Set([...$("exclude-lists").selectedOptions].map(option=>option.value));$("exclude-lists").replaceChildren();
  for(const campaign of campaigns){const option=el("option",campaign.name);option.value=campaign.campaign_id;option.selected=previousExclusions.has(campaign.campaign_id);$("exclude-lists").append(option);}
  $("stat-searches").textContent=campaigns.length;
  $("stat-completed").textContent=`${campaigns.filter(campaign=>campaign.status==="completed").length} concluídas · ${campaigns.filter(campaign=>["queued","running","waiting"].includes(campaign.status)).length} em andamento`;
  if(!campaigns.length)$("history").append(el("small","Nenhuma busca salva ainda."));
  for(const campaign of campaigns){
    const item=el("article");item.className="history-item";item.dataset.campaignId=campaign.campaign_id;item.classList.toggle("active",campaign.campaign_id===active);
    const button=el("button");button.className="history-open";button.disabled=mutating;button.setAttribute("aria-label",`Abrir ${campaign.name}`);
    const copy=el("span");copy.className="history-copy";copy.append(el("strong",campaign.name));
    const meta=el("span");meta.className=`history-meta status-${campaign.status}`;meta.append(el("span",states[campaign.status]||campaign.status));
    const age=relativeDate(campaign.created_at||campaign.results_frozen_at);if(age)meta.append(el("span",age));if(campaign.config?.collect_emails)meta.append(el("span","com e-mails"));if(campaign.read_only)meta.append(el("span","somente leitura"));copy.append(meta);
    const count=el("span");count.className="history-count";count.append(el("strong",String(campaign.result_count??0)),el("small","empresas"));button.append(copy,count);
    button.onclick=()=>{if(mutating){feedback("Aguarde a operação atual terminar antes de trocar de busca.");return;}load(campaign.campaign_id,true).catch(error=>feedback(error.message,true));};
    const remove=el("button","🗑");remove.className="history-delete";remove.type="button";remove.disabled=mutating;remove.title=`Excluir ${campaign.name}`;remove.setAttribute("aria-label",`Excluir ${campaign.name}`);remove.onclick=()=>deleteCampaign(campaign);
    const arrow=el("span","›");arrow.className="history-arrow";item.append(button,remove,arrow);$("history").append(item);
  }
}
function clearActiveCampaign(){
  loadVersion++;active=null;results=[];visible=[];selected.clear();focusedId=null;readOnly=false;if(markers)markers.clearLayers();markerIndex.clear();
  for(const item of document.querySelectorAll(".history-item.active"))item.classList.remove("active");
  $("manual-review").open=false;
  $("campaign-title").textContent="Resultados";$("campaign-status").textContent="Selecione uma busca no histórico.";$("map-campaign-title").textContent="Resultados no mapa";
  $("approve").hidden=true;$("cancel").hidden=true;for(const id of ["export","export-xlsx","export-json","copy","map-export","map-export-csv","map-export-json","map-copy"])$(id).disabled=true;
  updateCategories();renderResults();updateStats();if(!currentPanel)panel(false,false);
}
function clearAnalysis(){
  clearActiveCampaign();
  $("search-form").reset();center=null;centerLocation="";
  if(circle){map.removeLayer(circle);map.removeLayer(pin);circle=null;pin=null;}
  $("radius-value").textContent=`${Number($("radius").value)/1000} km`;
  for(const button of document.querySelectorAll("[data-count]"))button.classList.toggle("chosen",Number(button.dataset.count)===Number($("count").value));
  updateEmailNote();feedback("");
  $("center-label").textContent="Localize a cidade ou escolha o centro no mapa.";
}
async function deleteCampaign(campaign){
  if(mutating||busy||!confirm(`Excluir definitivamente a busca "${campaign.name}"? Esta ação não pode ser desfeita.`))return;
  const deletingActive=campaign.campaign_id===active;loadVersion++;setMutationLock(true);feedback(`Excluindo ${campaign.name}…`,false,true);
  try{await api(`/campaigns/${campaign.campaign_id}`,"DELETE");if(deletingActive)clearActiveCampaign();await history();feedback(`Busca "${campaign.name}" excluída.`);}
  catch(error){const message=error.status===404?"A busca não foi encontrada. Atualize o histórico e tente novamente.":error.status===409?"Esta busca possui uma execução ativa. Cancele a campanha e aguarde o encerramento antes de excluir.":error.message;feedback(message,true);}
  finally{setMutationLock(false);}
}
async function load(id,focus=false){
  if(mutating&&id!==active)return;
  if(focus)feedback("Carregando busca…",false,true);
  const version=++loadVersion;
  const campaign=await api(`/campaigns/${id}`);
  const data=await api(`/campaigns/${id}/results`);
  if(version!==loadVersion||(mutating&&id!==active))return;
  if(active!==id)selected.clear();active=id;results=data.results;readOnly=campaign.read_only;
  for(const item of document.querySelectorAll(".history-item"))item.classList.toggle("active",item.dataset.campaignId===id);
  $("campaign-title").textContent=campaign.name;
  const campaignState=el("span",states[campaign.status]||campaign.status);campaignState.className=`campaign-state status-${campaign.status}`;
  $("campaign-status").replaceChildren(campaignState,el("span",`${campaign.result_count} ${campaign.result_count===1?"empresa":"empresas"}`));
  $("approve").hidden=campaign.read_only||campaign.status!=="pending_approval";
  const cancelable=!campaign.read_only&&["pending_approval","queued","running","waiting"].includes(campaign.status);
  $("cancel").hidden=!cancelable;$("cancel").dataset.stateDisabled=String(!cancelable);$("cancel").disabled=!cancelable||mutating;
  $("approve").dataset.stateDisabled=String(campaign.read_only||campaign.status!=="pending_approval");
  $("approve").disabled=mutating||$("approve").dataset.stateDisabled==="true";
  for(const id of ["export","export-xlsx","export-json","copy","map-export","map-export-csv","map-export-json","map-copy"])$(id).disabled=false;
  $("map-campaign-title").textContent=campaign.name;
  const area=campaign.config.search_area;
  if(focus){
    feedback(campaign.read_only?"Histórico carregado: somente leitura.":"Busca carregada. Marque empresas para revisar os dados.");
    $("terms").value=campaign.config.business_terms.join(", ");$("location").value=Object.values(campaign.config.location).join(", ");
    $("count").value=campaign.target_count;for(const button of document.querySelectorAll("[data-count]"))button.classList.toggle("chosen",Number(button.dataset.count)===campaign.target_count);$("emails").checked=campaign.config.collect_emails;$("socials").checked=campaign.config.collect_socials;updateEmailNote();
    $("negative").value=(campaign.config.negative_terms||[]).join(", ");$("required").value=(campaign.config.required_fields||[])[0]||"";
    $("collection-limit").value=campaign.config.collection_limit||campaign.target_count;$("neighborhoods").value=(campaign.config.neighborhoods||[]).join(", ");
    $("language").value=campaign.config.language||"pt";
    $("depth").value=campaign.config.depth||1;$("grid-size").value=campaign.config.grid_size||0;$("deep-scan").checked=Boolean(campaign.config.deep_scan);$("max-queries").value=campaign.config.max_queries||100;
    $("min-rating").value=campaign.config.min_rating??"";$("min-reviews").value=campaign.config.min_reviews??"";$("no-website").checked=Boolean(campaign.config.no_website);$("verify-emails").checked=Boolean(campaign.config.verify_emails);
    $("match-topic").checked=campaign.config.match_business_terms!==false;$("category-terms").value=(campaign.config.category_terms||[]).join(", ");
    for(const option of $("exclude-lists").options)option.selected=(campaign.config.exclude_campaign_ids||[]).includes(option.value);
    center=area?{latitude:area.latitude,longitude:area.longitude}:null;centerLocation=$("location").value;
    if(area){$("radius").value=area.radius_m;$("radius-value").textContent=`${area.radius_m/1000} km`;$("center-label").textContent=`Centro salvo: ${area.latitude.toFixed(5)}, ${area.longitude.toFixed(5)}`;drawArea(area);}
    else {if(circle){map.removeLayer(circle);map.removeLayer(pin);circle=null;pin=null;}$("center-label").textContent="Campanha sem raio salvo.";}
  }
  updateCategories();renderResults();
  if(focus){
    panel(currentPanel,!currentPanel);
    fitVisibleResults(area);
    if(currentPanel){setActiveSection("results-panel");$("results-panel").scrollIntoView({behavior:"smooth",block:"start"});}
  }
}
function renderResults(){
  const listScroll=$("lead-list").scrollTop,tableScroll=document.querySelector(".table-wrap").scrollTop;
  $("results").replaceChildren();$("lead-list").replaceChildren();if(markers)markers.clearLayers();markerIndex.clear();const term=$("filter").value.trim().toLocaleLowerCase("pt-BR");
  visible=results.filter(item=>($("show-excluded").checked||!omitted(item))&&(!$("category").value||item.category===$("category").value)&&(!$("phone-only").checked||item.phone)&&(!$("email-only").checked||item.emails)&&[item.name,item.category,item.phone,item.emails,item.address,item.title].some(value=>String(value||"").toLocaleLowerCase("pt-BR").includes(term)));
  if($("dedupe-only").checked){const seen=new Set();visible=visible.filter(item=>{const key=duplicateKey(item);if(seen.has(key))return false;seen.add(key);return true;});}
  const order=$("sort").value;
  const descending=(getter,a,b)=>{const left=getter(a),right=getter(b);if(left===null&&right===null)return byName(a,b);if(left===null)return 1;if(right===null)return -1;return right-left||byName(a,b);};
  visible.sort((a,b)=>order==="name"?byName(a,b):order==="distance"?((Number.isFinite(a.distance_m)?a.distance_m:Infinity)-(Number.isFinite(b.distance_m)?b.distance_m:Infinity)||byName(a,b)):order==="rating"?descending(ratingValue,a,b):order==="reviews"?descending(reviewCountValue,a,b):byName(a,b));
  if(focusedId&&!visible.some(item=>item.company_id===focusedId))focusedId=null;
  updateStats();$("empty-results").hidden=visible.length>0;$("empty-results").textContent=!active?"Selecione uma busca para ver os resultados.":!results.length?"Esta busca ainda não possui resultados.":"Nenhuma empresa corresponde aos filtros atuais.";
  const reviewDisabled=!active||readOnly||mutating;for(const id of ["approve-selected","reject-selected","pending-selected"]){$(id).dataset.stateDisabled=String(!active||readOnly);$(id).disabled=reviewDisabled;}
  let located=0;
  for(const item of visible){
    const id=item.company_id;const row=el("tr");row.dataset.companyId=id;row.classList.toggle("focused",focusedId===id);row.classList.toggle("manual-selected",selected.has(id));if(omitted(item))row.classList.add("excluded");
    const check=el("input");check.type="checkbox";check.checked=selected.has(id);check.disabled=readOnly||mutating;check.setAttribute("aria-label",`Selecionar manualmente ${item.name} para revisão`);check.onchange=()=>{if(check.checked)selected.add(id);else selected.delete(id);updateManualSelection();};check.className="review-select";
    const identity=el("td",item.name);identity.append(el("small",item.category||item.title||""));
    if(item.selected===1&&!omitted(item)){const automatic=el("small","Seleção automática");automatic.className="automatic-selection";identity.append(automatic);}
    const reviewState=el("small",states[item.review_status]||item.review_status||"—");reviewState.className="review-state";identity.append(reviewState,check);
    const contact=el("td",displayPhones(item.phones||item.phone)||"—"),contactUrls=new Set();for(const entry of uniqueLinks(item,[["whatsapp","WhatsApp ↗"]],contactUrls))link(contact,entry.label,entry.url,"link-chip whatsapp-link");if(item.emails)contact.append(el("small",item.emails));
    const links=el("div");links.className="contact-links";
    for(const entry of uniqueLinks(item,[["website","Site"],["instagram","Instagram"],["facebook","Facebook"],["linkedin","LinkedIn"],["tiktok","TikTok"],["youtube","YouTube"],["twitter","Twitter"],["x","X"]],contactUrls))link(links,entry.field==="website"?entry.url.replace(/^https?:\/\//,"").replace(/\/$/,""):entry.label,entry.url,linkClass(entry.field));
    contact.append(links);
    const rating=el("td"),ratingNumber=ratingValue(item),reviewCount=reviewCountValue(item);rating.className="rating-cell";rating.append(el("span",ratingNumber!==null?`★ ${ratingNumber.toLocaleString("pt-BR",{minimumFractionDigits:1,maximumFractionDigits:1})}`:"—"));if(reviewCount!==null)rating.append(el("small",`(${reviewCount})`));
    const mapsLink=el("td");mapsLink.className="maps-cell";if(item.maps_url)link(mapsLink,"Maps ↗",item.maps_url,"link-chip maps-link");
    const quality=el("td",item.address||item.location||"—");if(item.distance_m!=null)quality.append(el("small",`${(item.distance_m/1000).toFixed(2)} km do centro`));if(item.exclusion_reason||item.selection_reason)quality.append(el("small",item.exclusion_reason||item.selection_reason));
    const position=positionOf(item);if(position&&map){
      located++;const popup=el("div");popup.append(el("small","EMPRESA NO MAPA"),el("h3",item.name),el("p",`${ratingNumber!==null?ratingNumber.toLocaleString("pt-BR",{minimumFractionDigits:1,maximumFractionDigits:1})+" ★ · ":""}${item.category||""}`),el("p",item.address||"Endereço não informado"));
      const details=el("button","Ver detalhes");details.onclick=()=>{panel(true);setActiveSection("results-panel");row.scrollIntoView({behavior:"smooth",block:"center"});};popup.append(details);
      link(popup,"Rotas",routesUrl(item));if(item.maps_url)link(popup,"Google Maps ↗",item.maps_url);
      const excluded=Boolean(item.exclusion_reason);let marker;if(L.divIcon){marker=L.marker(position,{icon:markerIcon(focusedId===id,excluded)}).bindPopup(popup,{autoPan:false}).addTo(markers);marker.excluded=excluded;}else{const baseFill=excluded?"#929ca8":"#ef4354";marker=L.circleMarker(position,{radius:focusedId===id?10:7,color:"white",weight:2,fillColor:focusedId===id?"#0b57d0":baseFill,fillOpacity:1}).bindPopup(popup,{autoPan:false}).addTo(markers);marker.baseFill=baseFill;}
      markerIndex.set(id,marker);marker.on("click",()=>selectCompany(id,true));
      const button=el("button",item.name);button.className="company-focus";button.title="Ver no mapa";button.setAttribute("aria-label",`Ver ${item.name} no mapa`);button.onclick=()=>selectCompany(id,true);identity.replaceChild(button,identity.firstChild);
      row.addEventListener("dblclick",()=>selectCompany(id,true));
    }
    row.append(identity,contact,rating,quality,mapsLink);$("results").append(row);
    const card=el("article");card.className="lead-card";card.dataset.companyId=id;card.setAttribute("role","listitem");if(focusedId===id)card.setAttribute("aria-current","true");card.classList.toggle("focused",focusedId===id);
    card.onclick=event=>{if(!event.target.closest("a,button"))selectCompany(id);};
    const heading=el("button",item.name);heading.className="subtle";heading.setAttribute("aria-pressed",String(focusedId===id));heading.onclick=()=>selectCompany(id);card.append(heading,el("p",item.category||item.title||""));
    if(ratingNumber!==null){const cardRating=el("p");cardRating.className="rating";cardRating.append(el("span",`★ ${ratingNumber.toLocaleString("pt-BR",{minimumFractionDigits:1,maximumFractionDigits:1})}`));if(reviewCount!==null)cardRating.append(el("small",`(${reviewCount})`));card.append(cardRating);}
    card.append(el("p",item.address||item.location||"Localização não informada"),el("p",[displayPhones(item.phones||item.phone),item.emails].filter(Boolean).join(" · ")||"Contato não encontrado"));
    for(const entry of uniqueLinks(item,[["website","Site"],["instagram","Instagram"],["facebook","Facebook"],["linkedin","LinkedIn"],["tiktok","TikTok"],["youtube","YouTube"],["twitter","Twitter"],["x","X"],["whatsapp","WhatsApp"],["maps_url","Google Maps"]]))link(card,entry.label,entry.url,linkClass(entry.field));
    if(!markerIndex.has(id))card.append(el("small","Sem coordenadas verificáveis"));
    $("lead-list").append(card);
  }
  updateManualSelection();
  $("result-count").textContent=`${visible.length} de ${results.length} empresas`;
  $("map-count").textContent=`${located} ${located===1?"empresa localizada":"empresas localizadas"} no mapa`;
  $("map-visible-count").textContent=`${visible.length} de ${results.length}`;
  $("fit-results").disabled=located===0;
  if(focusedId&&markerIndex.has(focusedId))markerIndex.get(focusedId).openPopup();
  $("lead-list").scrollTop=listScroll;document.querySelector(".table-wrap").scrollTop=tableScroll;
}
function updateManualSelection(){
  const count=visible.filter(item=>selected.has(item.company_id)).length;
  $("select-all").checked=visible.length>0&&count===visible.length;
  $("select-all").indeterminate=count>0&&count<visible.length;
  $("select-all").disabled=readOnly||mutating||visible.length===0;
  $("manual-count").textContent=`${selected.size} ${selected.size===1?"empresa selecionada":"empresas selecionadas"} manualmente`;
  $("review-tools").hidden=selected.size===0||readOnly;
  for(const row of $("results").rows){const checked=selected.has(row.dataset.companyId);row.classList.toggle("manual-selected",checked);row.querySelector('input[type="checkbox"]').checked=checked;}
  updateDataActions();
}
async function action(path,message="Atualizando estado…"){return mutate(message,async campaignId=>{await api(path.replace("{campaign}",campaignId),"POST",{});if(active===campaignId)await load(campaignId);await history();});}
async function review(status){if(!active||readOnly||!selected.size)return;const ids=[...selected];const saved=await mutate("Salvando revisão manual…",async campaignId=>{for(const item of results.filter(x=>ids.includes(x.company_id||x.person_id)))await api(`/campaigns/${campaignId}/results/${item.company_id}`,"PATCH",{review_status:status});if(active===campaignId)await load(campaignId);await history();});if(saved){for(const id of ids)selected.delete(id);renderResults();feedback("Revisão salva.");}}
$("approve").onclick=()=>mutate("Iniciando pedido salvo…",async campaignId=>{await api(`/campaigns/${campaignId}`);await canRun();await api(`/campaigns/${campaignId}/approve`,"POST",{});if(active===campaignId)await load(campaignId);await history();});
$("cancel").onclick=()=>{if(active&&!mutating&&confirm("Cancelar as próximas etapas desta campanha? Os dados já obtidos serão preservados."))action("/campaigns/{campaign}/cancel","Cancelando próximas etapas…");};
function exportRows(){return selected.size?results.filter(item=>selected.has(item.company_id)):visible;}
function updateDataActions(){const disabled=!active||exportRows().length===0;for(const id of ["export","export-xlsx","export-json","copy","map-export","map-export-csv","map-export-json","map-copy"])$(id).disabled=disabled;}
function exportUrl(format){const query=new URLSearchParams({format});for(const item of exportRows())query.append("company_id",item.company_id);return `/api/v1/campaigns/${active}/export?${query}`;}
$("export").onclick=()=>{if(active&&exportRows().length)window.location.assign(exportUrl("csv"));};
$("export-xlsx").onclick=()=>{if(active&&exportRows().length)window.location.assign(exportUrl("xlsx"));};
$("export-json").onclick=()=>{if(active&&exportRows().length)window.location.assign(exportUrl("json"));};
$("approve-selected").onclick=()=>review("approved");$("reject-selected").onclick=()=>review("rejected");$("pending-selected").onclick=()=>review("pending");
$("select-all").onchange=()=>{if(readOnly||mutating)return;for(const item of visible){const id=item.company_id;if($("select-all").checked)selected.add(id);else selected.delete(id);}updateManualSelection();};
$("filter").oninput=()=>{$("map-filter").value=$("filter").value;renderResults();};$("show-excluded").onchange=renderResults;
$("map-filter").oninput=()=>{$("filter").value=$("map-filter").value;renderResults();};
$("category").onchange=()=>{$("map-category").value=$("category").value;renderResults();};
$("map-category").onchange=()=>{$("category").value=$("map-category").value;renderResults();};
for(const id of ["dedupe-only","phone-only","email-only","sort"])$(id).onchange=renderResults;
$("map-tab").onclick=()=>panel(false,false);$("panel-tab").onclick=()=>panel(true);
$("fit-results").onclick=()=>fitVisibleResults();
$("manual-review").ontoggle=()=>document.body.classList.toggle("review-mode",$("manual-review").open);
$("show-map").onclick=()=>panel(false,true);
for(const button of document.querySelectorAll("[data-section]"))button.onclick=()=>goToSection(button.dataset.section);
for(const button of document.querySelectorAll("[data-count]"))button.onclick=()=>{$("count").value=button.dataset.count;$("collection-limit").value=Math.max(Number($("collection-limit").value),Number(button.dataset.count));for(const other of document.querySelectorAll("[data-count]"))other.classList.toggle("chosen",other===button);};
function showCopied(){clearTimeout(copyTimer);for(const id of ["copy","map-copy"])$(id).textContent="Copiado ✓";copyTimer=setTimeout(()=>{for(const id of ["copy","map-copy"])$(id).textContent="Copiar";},1500);}
$("copy").onclick=async()=>{const rows=exportRows();if(!active||!rows.length)return;try{await navigator.clipboard.writeText(rows.map(x=>[x.name,x.phone||"",x.emails||"",x.website||"",x.address||""].join("\t")).join("\n"));showCopied();feedback(selected.size?"Empresas selecionadas copiadas.":"Resultados visíveis copiados.");}catch{feedback("O navegador não permitiu copiar. Use o download CSV.",true);}};
$("map-export").onclick=()=>$("export-xlsx").click();$("map-export-csv").onclick=()=>$("export").click();$("map-export-json").onclick=()=>$("export-json").click();$("map-copy").onclick=()=>$("copy").click();
function updateEmailNote(){$("email-note").hidden=!$("emails").checked;}
$("emails").onchange=updateEmailNote;updateEmailNote();
async function refresh(){if(refreshing||busy||mutating)return false;refreshing=true;try{await history();await runtime();if(active)await load(active);return true;}catch(error){notice(error.message,true);return false;}finally{refreshing=false;}}
$("refresh").onclick=refresh;
$("clear-overview").onclick=clearAnalysis;
$("clear-map").onclick=clearAnalysis;
panel(true);
refresh();
setInterval(()=>{if(!document.hidden)refresh();},5000);
