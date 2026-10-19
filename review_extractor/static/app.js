import {DocumentViewer} from './viewer.js';

const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const state = {reviews:[],review:null,items:[],papers:[],paper:null,item:null,pages:[],suggestions:[],evidence:[],field:null,editorId:null,preview:[],dirty:false,loadSequence:0,section:'setup',extractionSection:'study',outcomes:[],outcome:null,outcomesPaper:null,outcomeEditorId:null,searchResults:[],learning:null,loadedSource:null,candidateContext:null,showAllCandidates:false};
const defaultCandidateLimit = 10;
const statuses = {unreviewed:'Not yet reviewed',approved:'Approved value',not_reported:'Not reported',not_applicable:'Not applicable',unclear:'Unclear',conflicting:'Conflicting evidence'};
let toastTimer;

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}
function button(text, action, className = '') {
  const node = el('button',text,className);
  node.type = 'button';
  node.addEventListener('click',event => run(()=>action(event), node));
  return node;
}
function toast(message, error = false) {
  $('#toast').textContent = message;
  $('#toast').className = error ? 'error' : '';
  $('#toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(()=>$('#toast').hidden = true,error ? 10000 : 4500);
}
function saved(message = 'Saved locally') { $('#save-status').textContent = message; $('#save-status').className = ''; }
async function api(path, options = {}) {
  const config = {...options};
  if (config.body && !(config.body instanceof FormData)) { config.body = JSON.stringify(config.body); config.headers = {'Content-Type':'application/json',...config.headers}; }
  const response = await fetch(path,config);
  if (!response.ok) {
    const body = await response.json().catch(()=>({detail:response.statusText}));
    const detail = body.detail ?? body;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  if (response.status === 204) return null;
  return response.json();
}
async function run(action, control) {
  if (control) control.disabled = true;
  try { return await action(); }
  catch(error) { toast(error.message || String(error),true); saved('Could not save / complete action'); console.error(error); }
  finally { if (control) control.disabled = false; }
}
function bind(selector, type, action) {
  $(selector).addEventListener(type,event=>{
    if (type === 'submit') event.preventDefault();
    const control = type === 'submit' ? event.submitter : (event.currentTarget.tagName === 'BUTTON' ? event.currentTarget : null);
    run(()=>action(event),control);
  });
}
function reviewPath(suffix = '') { if(!state.review) throw new Error('Create or select a review first.'); return `/api/reviews/${state.review.id}${suffix}`; }
function paperPath(suffix = '') { if(!state.paper) throw new Error('Select a paper first.'); return reviewPath(`/papers/${state.paper.id}${suffix}`); }
function scopedPath(path, outcomeId = state.extractionSection==='meta_analysis' ? state.outcome?.id : null) {
  if(state.extractionSection==='meta_analysis' && !outcomeId)throw new Error('Add or select an outcome row first.');
  return outcomeId ? `${path}${path.includes('?')?'&':'?'}outcome_id=${encodeURIComponent(outcomeId)}` : path;
}
function itemPath(suffix = '') { if(!state.item) throw new Error('Select an extraction item first.'); return scopedPath(paperPath(`/items/${state.item.id}${suffix}`)); }
function activeItems() { return state.items.filter(item=>!item.deleted).sort((a,b)=>a.position-b.position); }
function workspaceItems() { return activeItems().filter(item=>(item.section || 'study')===state.extractionSection); }
function hasExtractionContext() { return !!state.item && (state.extractionSection!=='meta_analysis' || !!state.outcome); }
function workspaceContext() { return JSON.stringify([state.review?.id,state.paper?.id,state.item?.id,state.extractionSection,state.extractionSection==='meta_analysis'?state.outcome?.id:null]); }
function workspaceReady() { return !state.workspaceLoading && state.loadedWorkspaceContext===workspaceContext() && hasExtractionContext(); }
function beginWorkspaceLoad() {
  state.workspaceLoading=true;
  $('.evidence-panel').inert=true;
  $('.evidence-panel').setAttribute('aria-busy','true');
  $('#capture-selection').disabled=true;$('#manual-evidence').disabled=true;
  if(state.loadedWorkspaceContext!==workspaceContext()) {
    state.field=null;state.evidence=[];state.suggestions=[];
    $('#suggestions').replaceChildren();$('#saved-evidence').replaceChildren();$('#evidence-links').replaceChildren();
    $('#field-form').reset();$('#field-form').hidden=true;
    $('#current-item-name').textContent='Loading extraction…';
    $('#item-description').textContent='Loading this selection…';$('#field-progress').textContent='';
    viewer.setHighlights([]);
  }
}
function finishWorkspaceLoad() {
  state.loadedWorkspaceContext=workspaceContext();state.workspaceLoading=false;
  $('.evidence-panel').inert=false;$('.evidence-panel').removeAttribute('aria-busy');
  renderOutcomeControls();
}
async function saveEvidenceChange(path, data, message) {
  if(!workspaceReady())return;
  const context=workspaceContext(),sequence=state.loadSequence;
  await api(path,{method:'PATCH',body:data});
  if(context!==workspaceContext() || sequence!==state.loadSequence)return;
  await loadFieldWorkspace(sequence,true);
  if(context===workspaceContext() && sequence===state.loadSequence)saved(message);
}
function sourceReady() { return !!state.loadedSource && state.loadedSource.reviewId===state.review?.id && state.loadedSource.paperId===state.paper?.id && state.loadedSource.documentVersion===state.paper?.document_version; }
function outcomeLabel(row) { return row.label || [row.outcome,row.group,row.time_point].filter(Boolean).join(' · ') || 'Outcome row'; }
function options(select, records, selected, label = record => record.name) {
  select.replaceChildren(...records.map(record=>{const node=el('option',label(record));node.value=record.id;return node;}));
  if (records.some(record=>record.id === selected)) select.value = selected;
}
function jsonInput(selector, fallback, kind) {
  const value = $(selector).value.trim();
  if(!value) return fallback;
  let parsed;
  try { parsed = JSON.parse(value); } catch { throw new Error(`${kind} must be valid JSON. Check quotes, commas, and brackets.`); }
  return parsed;
}
function lines(selector) { return $(selector).value.split(/\r?\n/).map(line=>line.trim()).filter(Boolean); }
function allowedValues() {
  const type=$('#edit-type').value;
  return lines('#edit-allowed').map(value=>{
    if(['number','integer'].includes(type)) {
      const numeric=Number(value);
      if(!Number.isFinite(numeric) || (type==='integer'&&!Number.isInteger(numeric)))throw new Error('Every allowed value must match the numeric data type.');
      return numeric;
    }
    if(type==='boolean') {
      if(/^(true|yes)$/i.test(value))return true;
      if(/^(false|no)$/i.test(value))return false;
      throw new Error('Allowed Yes / No values must be true, false, yes, or no.');
    }
    return value;
  });
}
function keepUnsaved() {
  if (!state.dirty) return true;
  if (!window.confirm('You have unsaved extraction edits. Discard those edits and continue?')) return false;
  state.dirty = false;
  return true;
}
function markDirty() {
  state.dirty = true;
  $('#field-save-status').textContent = 'Unsaved changes — save this extraction decision before switching papers or items.';
  $('#save-status').textContent = 'Unsaved extraction edits';
  $('#save-status').className = 'unsaved';
}
window.addEventListener('beforeunload',event=>{if(state.dirty){event.preventDefault();event.returnValue='';}});

const viewer = new DocumentViewer((number,total,page)=>{
  $('#page-number').value = number;
  $('#page-number').max = total;
  $('#page-total').textContent = `of ${total}`;
  $('#source-label').textContent = `Original PDF · physical page ${number}${page?.ocr ? ' · OCR-derived text' : ''}`;
  const warnings = [];
  if (page?.status && page.status !== 'ok') warnings.push(page.error || 'This page could not be searched. Review the original PDF; OCR or manual transcription may be needed.');
  if (page?.warning) warnings.push(page.warning);
  $('#page-warning').textContent = warnings.join(' ');
  $('#page-warning').hidden = warnings.length === 0;
},error=>toast(`PDF viewer: ${error.message}`,true));

function showSection(section) {
  state.section = section;
  $$('.main-section').forEach(node=>node.hidden = node.id !== `section-${section}`);
  $$('[data-section]').forEach(node=>node.classList.toggle('active',node.dataset.section === section));
  if (section === 'export') run(loadValidation);
}
$$('[data-section]').forEach(node=>node.addEventListener('click',()=>showSection(node.dataset.section)));
$$('[data-close]').forEach(node=>node.addEventListener('click',()=>document.getElementById(node.dataset.close).close()));

async function loadReviews(preferred) {
  state.reviews = await api('/api/reviews');
  options($('#review-select'),state.reviews,preferred);
  if(!state.reviews.length) { const option = el('option','Choose a review');option.value='';$('#review-select').append(option); }
  const remembered = preferred || localStorage.getItem('systematic.review_id');
  const selected = state.reviews.find(review=>review.id === remembered) || state.reviews[0];
  if(selected) await selectReview(selected.id);
}

async function selectReview(id) {
  if (!keepUnsaved()) { $('#review-select').value = state.review?.id || ''; return; }
  const sequence=++state.loadSequence;
  beginWorkspaceLoad();
  const [review,items,papers] = await Promise.all([api(`/api/reviews/${id}`),api(`/api/reviews/${id}/items?include_deleted=true`),api(`/api/reviews/${id}/papers`)]);
  if(sequence!==state.loadSequence)return;
  state.review = review;
  state.items = items;
  state.papers = papers;
  state.paper = null;
  state.item = null;
  state.outcomes=[];state.outcome=null;state.outcomesPaper=null;
  state.extractionSection=review.settings?.last_section || 'study';
  state.pages = [];
  state.preview = [];
  state.learning = null;
  state.loadedSource = null;
  $('#template-preview').hidden = true;
  $('#review-select').value = id;
  localStorage.setItem('systematic.review_id',id);
  $$('.requires-review').forEach(node=>node.hidden = false);
  $('#review-settings-form').hidden = false;
  $('#settings-name').value = review.name;
  $('#settings-reviewer').value = review.reviewer || '';
  renderReviewSummary();
  renderPapers();
  renderSelectors(review.settings?.last_paper_id,review.settings?.last_item_id);
  const workspaceLoad=loadWorkspace(true),workspaceSequence=state.loadSequence;
  await workspaceLoad;
  if(state.review?.id!==id || workspaceSequence!==state.loadSequence)return;
  saved();
  if(state.section === 'export') await loadValidation();
}

function renderReviewSummary() {
  $('#review-summary').textContent = `${state.review.name} · ${activeItems().length} active extraction items · ${state.papers.length} papers. Schema ${state.review.schema_version}, rules ${state.review.rule_version}.`;
  const metaCount=activeItems().filter(item=>item.section==='meta_analysis').length;
  $('#design-summary').textContent = `${activeItems().length-metaCount} study items and ${metaCount} meta-analysis items. Each item is an export column. Study sheets use one row per paper; meta-analysis sheets use one row per chosen outcome, arm and assessment time.`;
  $('#paper-count').textContent = `${state.papers.length} paper${state.papers.length === 1 ? '' : 's'}`;
}

async function rememberPosition() {
  if(!state.review) return;
  const id = state.review.id;
  const settings = {...state.review.settings,last_paper_id:state.paper?.id || null,last_item_id:state.item?.id || null,last_section:state.extractionSection,last_outcome_id:state.outcome?.id || null};
  const updated = await api(`/api/reviews/${id}`,{method:'PATCH',body:{settings}});
  if(state.review?.id === id) state.review = updated;
}

function renderSelectors(paperId = state.paper?.id, itemId = state.item?.id) {
  options($('#paper-select'),state.papers,paperId,p=>`${p.title}${p.processing_status !== 'ready' ? ` (${p.processing_status})` : ''}${p.included === false ? ' · excluded' : ''}`);
  const items=workspaceItems();
  const multipleSheets=new Set(items.map(i=>i.worksheet_name || 'Data')).size>1;
  options($('#item-select'),items,itemId,i=>multipleSheets?`${i.worksheet_name || 'Data'} · ${i.name}`:i.name);
  state.paper = state.papers.find(p=>p.id === $('#paper-select').value) || null;
  state.item = items.find(i=>i.id === $('#item-select').value) || null;
  $('#reading-workspace').hidden = !state.paper;
  $('#extract-empty').hidden = !!state.paper;
  renderOutcomeControls();
}

function renderOutcomeControls() {
  const meta=state.extractionSection==='meta_analysis';
  $('#study-extraction-tab').setAttribute('aria-selected',String(!meta));
  $('#meta-analysis-tab').setAttribute('aria-selected',String(meta));
  $('#outcome-section').hidden=!meta;
  const rows=state.outcomesPaper===state.paper?.id ? state.outcomes.filter(row=>!row.deleted) : [];
  options($('#outcome-select'),rows,state.outcome?.id,row=>`${outcomeLabel(row)}${row.included===false?' · excluded from export':''}`);
  $('#outcome-select').disabled=!rows.length;
  $('#edit-outcome').disabled=!state.outcome;
  $('#outcome-included').checked=!!state.outcome && state.outcome.included!==false;
  $('#outcome-included').disabled=!state.outcome;
  $('#outcome-summary').textContent=rows.length ? `${rows.length} outcome row${rows.length===1?'':'s'} in this paper. ${rows.filter(row=>row.included!==false).length} selected for meta-analysis export.` : 'This section is optional. Add a row for each usable outcome, study arm and assessment time you choose to extract.';
  const deleted=$('#deleted-outcomes');deleted.replaceChildren();
  state.outcomes.filter(row=>row.deleted).forEach(row=>deleted.append(button(`Restore ${outcomeLabel(row)}`,async()=>{
    if(!keepUnsaved())return;
    await api(paperPath(`/outcomes/${row.id}/restore`),{method:'POST'});
    await loadOutcomes(row.id);await loadWorkspace();await rememberPosition();saved('Outcome row restored');
  },'small-button')));
  $('#deleted-outcomes-details').hidden=!deleted.childNodes.length;
  const canCapture=workspaceReady() && sourceReady() && state.pages.length>0;
  $('#capture-selection').disabled=!canCapture;$('#manual-evidence').disabled=!canCapture;
}

async function loadOutcomes(preferred) {
  if(!state.paper)return;
  const paperId=state.paper.id,reviewId=state.review.id;
  const rows=await api(paperPath('/outcomes?include_deleted=true'));
  if(paperId!==state.paper?.id || reviewId!==state.review?.id)return;
  const wanted=preferred || (state.outcomesPaper===paperId ? state.outcome?.id : state.review.settings?.last_paper_id===paperId ? state.review.settings?.last_outcome_id : null);
  state.outcomes=rows;state.outcomesPaper=paperId;
  state.outcome=rows.find(row=>row.id===wanted && !row.deleted) || rows.find(row=>!row.deleted) || null;
  renderOutcomeControls();
}

async function switchExtractionSection(section) {
  if(section===state.extractionSection || !keepUnsaved())return;
  state.extractionSection=section;renderSelectors();await loadWorkspace();await loadReadingProgress();await rememberPosition();
}
bind('#study-extraction-tab','click',()=>switchExtractionSection('study'));
bind('#meta-analysis-tab','click',()=>switchExtractionSection('meta_analysis'));
bind('#outcome-select','change',async()=>{
  if(!keepUnsaved()){$('#outcome-select').value=state.outcome?.id || '';return;}
  state.outcome=state.outcomes.find(row=>row.id===$('#outcome-select').value && !row.deleted) || null;
  renderOutcomeControls();await loadWorkspace();await loadReadingProgress();await rememberPosition();
});
function editOutcome(id=null) {
  if(!keepUnsaved())return;
  const row=state.outcomes.find(row=>row.id===id) || {};
  state.outcomeEditorId=id;
  $('#outcome-dialog-title').textContent=id?'Edit outcome row':'Add an outcome row';
  for(const key of ['label','outcome','group','time_point','units'])$(`#outcome-${key.replace('_','-')}`).value=row[key] || '';
  $('#outcome-export').checked=row.included!==false;
  $('#delete-outcome').hidden=!id;$('#outcome-dialog').showModal();$('#outcome-outcome').focus();
}
bind('#add-outcome','click',()=>editOutcome());
bind('#edit-outcome','click',()=>editOutcome(state.outcome?.id));
bind('#outcome-form','submit',async()=>{
  const data={};
  for(const key of ['label','outcome','group','time_point','units'])data[key]=$(`#outcome-${key.replace('_','-')}`).value.trim();
  data.included=$('#outcome-export').checked;
  if(!data.outcome)throw new Error('Name the outcome measured in this row.');
  if(!data.label)data.label=[data.outcome,data.group,data.time_point].filter(Boolean).join(' · ');
  const row=await api(paperPath(`/outcomes${state.outcomeEditorId?`/${state.outcomeEditorId}`:''}`),{method:state.outcomeEditorId?'PATCH':'POST',body:data});
  $('#outcome-dialog').close();await loadOutcomes(row.id);await loadWorkspace();await loadReadingProgress();await rememberPosition();saved('Outcome row saved');
});
bind('#outcome-included','change',async()=>{
  if(!state.outcome)return;
  const context=workspaceContext(),paperId=state.paper.id;
  const row=await api(paperPath(`/outcomes/${state.outcome.id}`),{method:'PATCH',body:{included:$('#outcome-included').checked}});
  if(state.paper?.id!==paperId)return;
  state.outcomes=state.outcomes.map(current=>current.id===row.id?row:current);
  if(context!==workspaceContext())return;
  state.outcome=row;renderOutcomeControls();await loadReadingProgress();saved(row.included?'Outcome selected for export':'Outcome excluded from export');
});
bind('#delete-outcome','click',async()=>{
  if(!state.outcomeEditorId)return;
  if(!window.confirm('Remove this outcome row from active extraction and export? Its evidence, values and history will be kept for restoration.'))return;
  await api(paperPath(`/outcomes/${state.outcomeEditorId}`),{method:'DELETE'});
  $('#outcome-dialog').close();await loadOutcomes();await loadWorkspace();await loadReadingProgress();await rememberPosition();saved('Outcome row removed; history retained');
});

function searchStatusLabel(paper) {
  if(paper.search_status==='current')return 'Ready to review';
  if(paper.search_status==='partial')return 'Scan incomplete · partial results';
  if(paper.search_status==='stale')return 'Awaiting manual scan';
  if(paper.search_status==='error')return 'Scan failed';
  return `Search ${paper.search_status || 'pending'}${paper.search_status==='refreshing' ? ` · ${paper.search_progress || 0}%` : ''}`;
}

function renderPapers() {
  const list = $('#paper-list');list.replaceChildren();
  if(!state.papers.length) list.append(el('p','No PDFs yet. Upload your first paper above.','hint'));
  state.papers.forEach(paper=>{
    const row = el('div',null,'paper-card');
    row.append(el('div','PDF','paper-icon'));
    const details = el('div',null,'paper-details');
    details.append(el('strong',paper.title));
    details.append(el('p',`${paper.filename} · ${paper.page_count || 0} pages · ${paper.included === false ? 'Excluded' : 'Included'}`));
    if(paper.processing_error) details.append(el('p',paper.processing_error,'notice error'));
    row.append(details);
    const status = el('div');
    if(paper.processing_status==='pending') {
      status.append(el('span','Awaiting manual scan','pill warning'));
    } else if(['queued','processing'].includes(paper.processing_status)) {
      status.append(el('p',`${paper.processing_status} · ${paper.processing_progress || 0}%`,'hint'));
      const progress=el('progress');progress.max=100;progress.value=paper.processing_progress || 0;progress.setAttribute('aria-label',`${paper.title} processing progress`);status.append(progress);
    } else status.append(el('span',paper.processing_status === 'error' ? 'Processing error' : searchStatusLabel(paper),`pill${paper.processing_status === 'error' ? ' error' : (paper.search_status !== 'current' ? ' warning' : '')}`));
    row.append(status,button('Open paper',async()=>{if(!keepUnsaved())return;state.paper=paper;renderSelectors(paper.id);showSection('extract');await loadWorkspace(true);rememberPosition().catch(e=>toast(e.message,true));}));
    row.append(button('Remove',async()=>{
      if(!keepUnsaved())return;
      if(!window.confirm(`Remove “${paper.title}” from this review? It will leave the paper list and exports. Its stored PDF and history are kept; uploading the same PDF again restores it.`))return;
      const reviewId=state.review.id;
      await api(`/api/reviews/${reviewId}/papers/${paper.id}`,{method:'DELETE'});
      if(state.review?.id!==reviewId)return;
      state.papers=await api(`/api/reviews/${reviewId}/papers`);
      renderPapers();renderReviewSummary();renderSelectors();
      await loadWorkspace(true);await rememberPosition();
      saved('Paper removed');toast('Paper removed from this review. Upload it again to restore it.');
    },'danger'));
    list.append(row);
  });
}

function renderProcessingNotice() {
  if(!state.paper) return;
  const messages = [];
  if(state.paper.processing_status==='pending') messages.push('PDF saved. Click Scan this paper to extract text and find suggestions.');
  else if(state.paper.processing_status !== 'ready') messages.push(`PDF ${state.paper.processing_status}: ${state.paper.processing_progress || 0}%. ${state.paper.processing_error || ''}`);
  if(state.paper.search_status==='partial') {
    messages.push('Scan completed with some searches incomplete. Successful suggestions are available, but affected searches may have missed evidence. An incomplete search does not mean evidence is absent. Saved decisions are retained.');
  } else if(state.paper.search_status==='error') {
    messages.push('Scan failed. Saved suggestions and decisions are retained. Click Scan this paper to retry.');
  } else if(state.paper.search_status !== 'current') {
    messages.push(`${searchStatusLabel(state.paper)}. Click Scan this paper when you want to update suggestions. Saved suggestions and decisions are retained until the scan finishes.`);
  }
  for(const error of state.paper.search_errors || []) {
    if(typeof error==='string') {messages.push(error);continue;}
    const item=state.items.find(item=>item.id===error.item_id);
    messages.push(`${item?.name || 'Extraction item'}${error.page_number ? ` · page ${error.page_number}` : ''}${error.rule ? ` · “${error.rule}”` : ''}: ${error.error || 'Search incomplete.'}`);
  }
  if(state.paper.search_status==='partial')messages.push('Review the affected search rules in Edit extraction items, then click Scan this paper to retry.');
  for(const warning of state.paper.retrieval_warnings || [])messages.push(warning);
  const unsearchable = state.pages.filter(p=>p.status !== 'ok');
  const partial = state.pages.filter(p=>p.warning);
  if(unsearchable.length) messages.push(`Physical page${unsearchable.length === 1 ? '' : 's'} ${unsearchable.map(p=>p.page_number).join(', ')} could not be fully searched. Review the original PDF and record manual review in Paper details.`);
  if(partial.length) messages.push(`Image content on physical page${partial.length === 1 ? '' : 's'} ${partial.map(p=>p.page_number).join(', ')} may need visual review.`);
  $('#processing-notice').textContent = messages.join(' ');
  $('#processing-notice').hidden = !messages.length;
}

async function loadWorkspace(loadDocument = false, preserveForm = false) {
  const sequence = ++state.loadSequence;
  beginWorkspaceLoad();
  if(state.paper && state.outcomesPaper!==state.paper.id)await loadOutcomes();
  if(sequence!==state.loadSequence)return;
  const candidateContext = JSON.stringify([state.review?.id,state.paper?.id,state.item?.id,state.extractionSection,state.outcome?.id]);
  if(candidateContext !== state.candidateContext) {
    state.candidateContext = candidateContext;
    state.showAllCandidates = false;
  }
  if(!state.paper) {viewer.clear();state.pages=[];state.suggestions=[];state.evidence=[];state.field=null;state.searchResults=[];state.dirty=false;state.loadedSource=null;state.outcomes=[];state.outcome=null;state.outcomesPaper=null;finishWorkspaceLoad();return;}
  // A section or item can change while the initial PDF is still opening.
  // Ensure the newest workspace request finishes loading that same source.
  loadDocument=loadDocument || !sourceReady();
  if(loadDocument) {
    const source={reviewId:state.review.id,paperId:state.paper.id,documentVersion:state.paper.document_version};
    state.loadedSource=null;
    renderOutcomeControls();
    const pages = await api(paperPath('/pages'));
    if(sequence !== state.loadSequence)return;
    state.pages=pages;
    state.searchResults = [];
    $('#document-search-results').hidden = true;
    $('#clear-document-search').hidden = true;
    $('#document-query').value = '';
    // The original PDF is readable even if text processing is still in progress.
    await viewer.open(paperPath('/pdf'),state.pages);
    if(sequence !== state.loadSequence)return;
    state.loadedSource=source;
    renderOutcomeControls();
  }
  if(sequence !== state.loadSequence)return;
  renderProcessingNotice();
  if(!hasExtractionContext()) {
    $('#current-item-name').textContent = state.extractionSection==='meta_analysis' && !state.outcome ? 'Choose outcomes for this study' : 'Add an extraction item';
    $('#item-description').textContent = state.extractionSection==='meta_analysis' && !state.outcome ? 'Add an outcome row above when this study has an outcome you want to include in your meta-analysis. Study extraction remains available in the first section.' : `Use Edit extraction items to add items to the ${state.extractionSection==='meta_analysis'?'meta-analysis':'study'} section.`;
    $('#suggestions').replaceChildren();$('#saved-evidence').replaceChildren();$('#field-form').hidden=true;
    $('#field-progress').textContent='';state.suggestions=[];state.evidence=[];state.field=null;
    viewer.setHighlights([]);finishWorkspaceLoad();await loadReadingProgress();return;
  }
  await loadFieldWorkspace(sequence, preserveForm);
  if(loadDocument)await loadReadingProgress();
}

async function loadReadingProgress() {
  if(!state.paper || !state.review)return;
  if(state.extractionSection==='meta_analysis' && !state.outcome) {
    $('#reading-progress-summary').textContent='No outcome row selected';
    $('#unresolved-items').replaceChildren(el('p','Add an outcome row when you choose to include a result from this study.','hint'));
    return;
  }
  const paperId=state.paper.id,reviewId=state.review.id;
  const validation=await api(reviewPath('/validation'));
  if(paperId!==state.paper?.id || reviewId!==state.review?.id)return;
  const byItem=new Map();
  for(const issue of validation.issues || []) {
    const matchesScope=state.extractionSection==='meta_analysis' ? !!state.outcome?.id && issue.outcome_id===state.outcome.id : !issue.outcome_id;
    if(issue.paper_id===paperId && issue.item_id && matchesScope) {
      const entry=byItem.get(issue.item_id) || [];entry.push(issue);byItem.set(issue.item_id,entry);
    }
  }
  const container=$('#unresolved-items');container.replaceChildren();
  $('#reading-progress-summary').textContent=`${byItem.size} item${byItem.size===1?'':'s'} need attention in this ${state.extractionSection==='meta_analysis'?'outcome row':'paper'}`;
  workspaceItems().filter(item=>byItem.has(item.id)).forEach(item=>{
    const issues=byItem.get(item.id);
    const label=issues.some(issue=>issue.code==='reconsideration')?'Reconsideration needed':issues.some(issue=>issue.code==='conflicting')?'Conflicting evidence':issues.some(issue=>issue.code==='unclear')?'Unclear':issues.some(issue=>issue.code==='unreviewed')?'Not yet reviewed':'Needs attention';
    container.append(button(`${item.name} · ${label}`,async()=>{if(!keepUnsaved())return;state.item=item;$('#item-select').value=item.id;await loadWorkspace(false);showPanel(true);await rememberPosition();},'small-button'));
  });
  if(!byItem.size)container.append(el('p',state.paper.included===false?'This paper is excluded from export validation.':'No unresolved validation issues for this paper.','hint'));
}

async function loadFieldWorkspace(sequence = state.loadSequence, preserveForm = false) {
  if(!state.paper || !hasExtractionContext()) return;
  const [suggestions,evidence,field,learning] = await Promise.all([
    api(scopedPath(paperPath(`/suggestions?item_id=${encodeURIComponent(state.item.id)}`))),api(itemPath('/evidence')),api(itemPath('/field')),api(reviewPath('/learning'))]);
  if(sequence !== state.loadSequence)return;
  state.suggestions=suggestions;state.evidence=evidence;state.field=field;state.learning=learning;
  finishWorkspaceLoad();
  renderItemIntro();renderSuggestions();renderEvidence();
  if(!preserveForm && !state.dirty) renderField();
  else renderEvidenceLinks(true);
  showPanel($('#value-tab').getAttribute('aria-selected')==='true');
  updateHighlights();
  if($('#learning-dialog').open)renderLearning();
}

function renderItemIntro() {
  const item=state.item;
  $('#current-item-name').textContent=item.name;
  const description=$('#item-description');description.replaceChildren();
  if(item.definition)description.append(el('p',item.definition));
  if(state.extractionSection==='meta_analysis' && state.outcome)description.append(el('p',`Outcome row: ${outcomeLabel(state.outcome)}${state.outcome.units?` · ${state.outcome.units}`:''}${state.outcome.included===false?' · excluded from export':''}`,'notice'));
  if(item.instructions)description.append(el('p',`Instructions: ${item.instructions}`));
  if(Object.keys(item.context || {}).length)description.append(el('p',Object.entries(item.context).map(([key,value])=>`${key}: ${value}`).join(' · ')));
  description.append(el('p',`Keywords: ${(item.keywords || []).join(' · ') || 'None configured. Use manual evidence or add keywords.'}`));
  $('#field-progress').textContent=`${statuses[state.field?.status] || 'Not yet reviewed'} · ${state.evidence.filter(e=>e.decision==='accepted').length} accepted source passage(s)`;
}

function updateHighlights() {
  viewer.setHighlights([...state.suggestions.filter(s=>s.active !== false && s.decision !== 'rejected'),...state.evidence.filter(e=>e.decision==='accepted' && !e.source_stale && !state.suggestions.some(s=>s.id===e.suggestion_id && s.active !== false)),...state.searchResults]);
}

function sourceButton(record) {
  if(record.source_type==='pdf_metadata')return button('PDF metadata · inspect first page',()=>viewer.goTo(1),'source-button');
  return button(`PDF p. ${record.page_number} · show source`,()=>viewer.goTo(record.page_number,sourceHighlightId(record)),'source-button');
}
function sourceHighlightId(record) {
  // Linked evidence shares the suggestion's visible highlight; its stable
  // evidence ID still identifies the saved decision and final-value support.
  const suggestion=state.suggestions.find(row=>row.id===record.suggestion_id && row.active!==false && row.decision!=='rejected');
  return suggestion?.id || record.id;
}

const rejectionReasons = [['','No reason specified'],['irrelevant','Irrelevant'],['wrong_arm','Wrong arm'],['wrong_outcome','Wrong outcome'],['wrong_timepoint','Wrong timepoint'],['wrong_statistic','Wrong statistic'],['duplicate','Duplicate']];
function rejectionControl(record, saveReason) {
  const label=el('label','Optional rejection reason','rejection-reason');
  const select=el('select');select.setAttribute('aria-label','Optional rejection reason');
  options(select,rejectionReasons.map(([id,name])=>({id,name})),record.rejection_reason || '');
  select.addEventListener('change',()=>{if(record.decision==='rejected')run(()=>saveReason(select.value),select);});
  label.append(select);
  return {label,select};
}

function learningExplanation(suggestion) {
  const learning=suggestion.learning;if(!learning)return null;
  const details=el('details',null,'learning-explanation');
  details.append(el('summary','Reviewer feedback influence'));
  const baseline=learning.baseline_score==null?'not measured':Number(learning.baseline_score).toFixed(3);
  const delta=learning.feedback_score==null?null:Number(learning.feedback_score);
  const deltaLabel=delta===null?'not measured':`${delta>=0?'+':''}${delta.toFixed(3)}`;
  const status={disabled:'Learning is off.',insufficient_examples:'More accepted examples from other source documents are needed.',sparse:'More accepted examples from other source documents are needed.',sparse_feedback:'At least two accepted source documents outside this paper are needed.',fast_mode:'Fast search uses the baseline ranking. Choose Intelligent search to use feedback embeddings.',unavailable:'Feedback embeddings are unavailable; the baseline ranking is retained.',protected_profile:'This field uses a protected retrieval profile.',gated:'Evidence type or context checks block a positive adjustment.',applied:'Examples from other papers were compared for this score.'}[learning.status] || String(learning.status || 'No feedback adjustment.').replaceAll('_',' ');
  details.append(el('p',`Baseline ${baseline} · Feedback ${deltaLabel}. ${status}`,'hint'));
  details.append(el('p',`Configuration ${learning.config_version ?? '—'} · feedback revision ${learning.feedback_revision ?? '—'}. Feedback never approves a value or overrides context checks.`,'hint'));
  for(const influence of learning.influences || []) {
    const example=state.learning?.examples.find(record=>record.example_id===influence.example_id);
    const paper=state.papers.find(record=>record.id===influence.paper_id);
    const row=el('div',null,'learning-influence');
    row.append(el('p',`${influence.decision || 'Accepted'} example · Similarity ${Number(influence.similarity || 0).toFixed(3)}${influence.reason ? ` · ${String(influence.reason).replaceAll('_',' ')}` : ''}`,'hint'));
    if(example?.quote)row.append(el('blockquote',example.quote,'quote'));
    if(paper)row.append(button(`Open source: ${paper.title || paper.filename}`,()=>openLearningSource(example || {...influence,item_id:state.item.id}),'small-button'));
    details.append(row);
  }
  return details;
}

function renderSuggestions() {
  const container=$('#suggestions');container.replaceChildren();
  const all=state.suggestions.filter(s=>s.active !== false || s.decision !== 'pending' || s.origin==='user');
  const visible=all.filter((s,index)=>index<defaultCandidateLimit || s.decision!=='pending' || s.origin==='user' || state.showAllCandidates);
  visible.sort((a,b)=>Number(b.decision==='accepted')-Number(a.decision==='accepted'));
  // Manually added evidence can have no suggestion; keep it visible at the top too.
  const standalone=state.evidence.filter(e=>e.decision==='accepted' && !all.some(s=>s.id===e.suggestion_id));
  standalone.forEach(evidence=>{
    const card=el('article',null,'suggestion-card accepted');card.dataset.evidenceId=evidence.id;
    card.append(el('span','Accepted source passage','pill'),sourceButton(evidence),el('blockquote',evidence.quote,'quote'));
    if(evidence.verification_location)card.append(el('p',evidence.verification_location,'hint'));
    card.append(button('Reconsider evidence',()=>changeEvidence(evidence,'pending')));container.append(card);
  });
  if(all.length>defaultCandidateLimit)container.append(button(state.showAllCandidates ? `Show top ${defaultCandidateLimit}, user candidates and saved decisions` : `Show all ${all.length} candidates`,()=>{state.showAllCandidates=!state.showAllCandidates;renderSuggestions();}));
  if(!visible.length && !standalone.length)container.append(el('div','No candidate passages found for this item. This does not mean the information is not reported. Refine the item definition, search the full document, or add an exact quotation.','empty-state'));
  visible.forEach(suggestion=>{
    const card=el('article',null,`suggestion-card ${suggestion.decision}`);card.dataset.suggestionId=suggestion.id;
    const meta=el('div',null,'suggestion-meta');meta.append(el('span',`${state.item.name} · ${suggestion.decision}`),sourceButton(suggestion));card.append(meta);
    const metadata=suggestion.source_type==='pdf_metadata';
    if(suggestion.origin==='user')card.append(el('span','User-created candidate','pill'));
    card.append(el('div',metadata ? `Document metadata · ${suggestion.metadata_key || 'Author'} · no page location` : `Visible PDF text${suggestion.ocr ? ' · OCR-derived' : ''}`,'hint'));
    if(suggestion.rank_score!==undefined)card.append(el('p',`Rank ${suggestion.rank || '—'} · Relevance ${suggestion.rank_score.toFixed(2)}${suggestion.section ? ` · ${suggestion.section}` : ''}${suggestion.table_id ? ` · ${suggestion.table_id}, row ${suggestion.table_row+1}, column ${suggestion.table_column+1}` : ''}`,'hint'));
    if(suggestion.scores){const debug=el('details');debug.append(el('summary','Why this ranked here'),el('pre',JSON.stringify({scores:suggestion.scores,weights:suggestion.ranking_weights,missing_signals:suggestion.missing_signals,semantic_basis:suggestion.semantic_basis,overlapping_versions:suggestion.duplicate_spans,mode:suggestion.search_mode_used},null,2),'context-text'),el('p','Relevance is a heuristic ranking score, not a probability that a value is correct.','hint'));card.append(debug);}
    const learning=learningExplanation(suggestion);if(learning)card.append(learning);
    for(const reason of suggestion.reasons || [])card.append(el('p',reason,'hint'));
    if(suggestion.matched_rules?.length)card.append(el('div',`Supporting keyword/rule matches: ${suggestion.matched_rules.join(', ')}`,'hint'));
    if(metadata)card.append(el('p','Metadata may describe the file creator. Verify the names on the PDF and select the visible byline as evidence.','notice'));
    if(suggestion.active===false || state.paper.search_status!=='current')card.append(el('span',suggestion.active===false ? 'Historical passage · decision retained' : (state.paper.search_status==='partial' ? 'Partial scan · some searches incomplete' : 'Refresh pending · older rules may apply'),'pill warning'));
    card.append(el('blockquote',suggestion.quote,'quote'));
    const acceptedSource=state.evidence.find(e=>e.suggestion_id===suggestion.id && e.decision==='accepted');
    if(acceptedSource?.verification_location)card.append(el('p',acceptedSource.verification_location,'hint'));
    if(suggestion.context){const details=el('details');details.append(el('summary','Surrounding context'),el('p',suggestion.context,'context-text'));card.append(details);}
    if(suggestion.proposed_value!==null && suggestion.proposed_value!==undefined)card.append(el('p',`Candidate value: ${String(suggestion.proposed_value)}. Verify and enter the final value separately.`,'hint'));
    if(suggestion.multiple_proposed_values)card.append(el('p','Several values occur in this passage. Check groups and time points; no single value has been selected.','notice'));
    if(suggestion.value_validation?.ambiguous)card.append(el('p',`Ambiguous: ${suggestion.value_validation.issues.join('; ')}. Confirm context before entering a value.`,'notice'));
    if(suggestion.extracted_value?.statistic){const detail=el('details');detail.append(el('summary','Quantitative context'),el('pre',JSON.stringify(suggestion.extracted_value,null,2),'context-text'));card.append(detail);}
    const actions=el('div',null,'decision-actions');
    const reason=rejectionControl(suggestion,async rejection_reason=>{
      await saveEvidenceChange(scopedPath(paperPath(`/suggestions/${suggestion.id}`)),{decision:'rejected',rejection_reason},'Rejection reason saved');
    });
    card.append(reason.label);
    for(const [decision,label] of [['accepted','Accept passage'],['rejected','Reject passage'],['pending','Reconsider']]) {
      if(suggestion.decision===decision)continue;
      if(metadata && decision==='accepted')continue;
      actions.append(button(label,async()=>{
        await saveEvidenceChange(scopedPath(paperPath(`/suggestions/${suggestion.id}`)),{decision,rejection_reason:decision==='rejected'?reason.select.value:''},'Evidence decision saved');
      },decision==='accepted'?'primary':''));
    }
    card.append(actions);container.append(card);
  });
}

function renderEvidence() {
  const container=$('#saved-evidence');container.replaceChildren();
  if(!state.evidence.length)container.append(el('p','No saved evidence yet.','hint'));
  state.evidence.forEach(evidence=>{
    const card=el('article',null,`evidence-card ${evidence.decision}`);card.dataset.evidenceId=evidence.id;
    const meta=el('div',null,'suggestion-meta');meta.append(el('span',`${evidence.decision}${evidence.ocr ? ' · OCR-derived' : ''}${evidence.manual ? ' · manual transcription' : ''}`),sourceButton(evidence));card.append(meta,el('blockquote',evidence.quote,'quote'));
    if(evidence.source_stale)card.append(el('p','Source changed after PDF reprocessing. Select this evidence again from the current document before approving a value. The previous quotation remains in the history.','notice'));
    if(evidence.notes)card.append(el('p',evidence.notes,'hint'));
    if(evidence.verification_location)card.append(el('p',`Location: ${evidence.verification_location}`,'hint'));
    const reason=rejectionControl(evidence,rejection_reason=>changeEvidence(evidence,'rejected',rejection_reason));
    card.append(reason.label);
    const actions=el('div',null,'decision-actions');
    if(evidence.decision!=='accepted')actions.append(button('Accept evidence',()=>changeEvidence(evidence,'accepted')));
    if(evidence.decision!=='rejected')actions.append(button('Reject evidence',()=>changeEvidence(evidence,'rejected',reason.select.value)));
    if(evidence.decision!=='pending')actions.append(button('Reconsider evidence',()=>changeEvidence(evidence,'pending')));
    actions.append(button('Edit source note',async()=>{const notes=window.prompt('Source note (kept separately from the exact quotation)',evidence.notes || '');if(notes===null)return;await saveEvidenceChange(scopedPath(paperPath(`/evidence/${evidence.id}`)),{notes},'Source note saved');}));
    actions.append(button('Edit source location',async()=>{const source_location=window.prompt('Location on this page, for example: within Figure 1, panel B; Table 2, Treatment row, Week 6 column',evidence.source_location || '');if(source_location===null)return;await saveEvidenceChange(scopedPath(paperPath(`/evidence/${evidence.id}`)),{source_location},'Source location saved');}));
    card.append(actions);container.append(card);
  });
}
async function changeEvidence(evidence,decision,rejection_reason='') {
  await saveEvidenceChange(scopedPath(paperPath(`/evidence/${evidence.id}`)),{decision,rejection_reason},'Evidence decision saved');
}

function renderEvidenceLinks(preserve = false) {
  const container=$('#evidence-links');
  const prior = preserve ? $$('input[name="evidence-link"]:checked').map(node=>node.value) : state.field?.evidence_ids || [];
  const alreadyShown = $$('input[name="evidence-link"]').map(node=>node.value);
  container.replaceChildren();
  const accepted=state.evidence.filter(e=>e.decision==='accepted' && !e.source_stale);
  $('#observation-evidence-ids').textContent=accepted.map(e=>`PDF p. ${e.page_number}: ${e.quote.slice(0,90)}\n${JSON.stringify(e.id)}`).join('\n\n') || 'No accepted evidence yet.';
  if(!accepted.length)container.append(el('p','Accept evidence passages before approving a reported value.','hint'));
  accepted.forEach(evidence=>{
    const card=el('article',null,'accepted-value-source');
    const label=el('label',null,'check-label');
    const input=el('input');input.type='checkbox';input.name='evidence-link';input.value=evidence.id;
    input.checked=prior.includes(evidence.id) || (preserve && !alreadyShown.includes(evidence.id)) || (!preserve && !prior.length && state.field?.status==='unreviewed');
    input.setAttribute('aria-label',`Use accepted passage on page ${evidence.page_number}`);
    label.append(input,el('span','Use this accepted passage'));
    card.append(label,el('blockquote',evidence.quote,'quote'),el('p',evidence.verification_location || `Page ${evidence.page_number}`,'hint'),sourceButton(evidence));container.append(card);
  });
}

function renderField() {
  const field=state.field || {};
  $('#field-status').value=field.status==='unreviewed' && state.evidence.some(e=>e.decision==='accepted' && !e.source_stale) ? 'approved' : field.status || 'unreviewed';
  $('#field-value').value=field.value===null || field.value===undefined ? '' : (typeof field.value==='object' ? JSON.stringify(field.value) : String(field.value));
  $('#value-kind').value=field.value_kind || 'reported';
  $('#field-rationale').value=field.rationale || '';
  $('#field-notes').value=field.notes || '';
  $('#calculation-inputs').value=field.calculation_inputs && Object.keys(field.calculation_inputs).length ? JSON.stringify(field.calculation_inputs,null,2) : '';
  $('#field-observations').value=field.observations?.length ? JSON.stringify(field.observations,null,2) : '';
  $('#value-hint').textContent=`Type: ${state.item.data_type}${state.item.units ? ` · Units: ${state.item.units}` : ''}${state.item.allowed_values?.length ? ` · Allowed values: ${state.item.allowed_values.join(', ')}` : ''}. Source wording stays in evidence; this is your final value. Not reported / Not applicable require a rationale or note.`;
  $('#reconsideration-notice').hidden=!field.needs_reconsideration;
  $('#reconsideration-notice').textContent='The item or its supporting evidence changed after your previous decision. Reconsider the definition, current source evidence and value, then save your decision again.';
  $('#field-save-status').textContent=field.updated_at ? `Saved by ${field.reviewer || 'reviewer'} · ${new Date(field.updated_at).toLocaleString()}` : 'No extraction decision saved yet.';
  renderEvidenceLinks();state.dirty=false;
}

function parseFinalValue() {
  const raw=$('#field-value').value;
  if(raw.trim()==='')return null;
  if(['number','integer'].includes(state.item.data_type)) {
    const value=Number(raw);
    if(!Number.isFinite(value) || (state.item.data_type==='integer'&&!Number.isInteger(value)))throw new Error(`Enter a valid ${state.item.data_type}. Keep units in the item settings.`);
    return value;
  }
  if(state.item.data_type==='boolean') {
    if(/^(true|yes)$/i.test(raw.trim()))return true;
    if(/^(false|no)$/i.test(raw.trim()))return false;
    throw new Error('For Yes / No data, enter true, false, yes, or no.');
  }
  return raw;
}

function showPanel(value) {
  $('#evidence-content').hidden=value;
  $('#field-form').hidden=!value || !workspaceReady();
  $('#evidence-tab').setAttribute('aria-selected',String(!value));
  $('#value-tab').setAttribute('aria-selected',String(value));
  if(value && !state.dirty && state.field?.status==='unreviewed' && state.evidence.some(e=>e.decision==='accepted' && !e.source_stale))$('#field-status').value='approved';
}
bind('#evidence-tab','click',()=>showPanel(false));bind('#value-tab','click',()=>showPanel(true));
$('#field-form').addEventListener('input',markDirty);$('#field-form').addEventListener('change',markDirty);
bind('#field-form','submit',async()=>{
  if(!workspaceReady())return;
  const context=workspaceContext(),sequence=state.loadSequence;
  const value=parseFinalValue();
  const observations=jsonInput('#field-observations',[],'Repeated observations');
  if(!Array.isArray(observations))throw new Error('Repeated observations must be a JSON array.');
  const data={status:$('#field-status').value,value,value_kind:$('#value-kind').value,rationale:$('#field-rationale').value,notes:$('#field-notes').value,
    evidence_ids:$$('input[name="evidence-link"]:checked').map(node=>node.value),calculation_inputs:jsonInput('#calculation-inputs',[],'Calculation inputs'),observations,needs_reconsideration:false};
  saved('Saving extraction decision…');
  const field=await api(itemPath('/field'),{method:'PUT',body:data});
  if(context!==workspaceContext() || sequence!==state.loadSequence)return;
  state.field=field;state.dirty=false;renderField();renderItemIntro();saved('Extraction decision saved');toast('Extraction decision saved locally.');await loadReadingProgress();
});

bind('#create-review-form','submit',async()=>{
  if(!keepUnsaved())return;
  const review=await api('/api/reviews',{method:'POST',body:{name:$('#review-name').value.trim(),reviewer:$('#reviewer-name').value.trim()}});
  $('#create-review-form').reset();await loadReviews(review.id);toast('Review created. Preview the reference headings or import your own design.');
});
bind('#review-select','change',()=>selectReview($('#review-select').value));
bind('#review-settings-form','submit',async()=>{
  state.review=await api(reviewPath(),{method:'PATCH',body:{name:$('#settings-name').value.trim(),reviewer:$('#settings-reviewer').value.trim()}});
  const option=$('#review-select').selectedOptions[0];if(option)option.textContent=state.review.name;renderReviewSummary();saved('Review details saved');toast('Review details saved.');
});

function renderTemplatePreview(data) {
  state.preview=data.items.map(item=>({...item}));
  $('#template-preview').hidden=false;
  $('#template-warnings').textContent=[...(data.warnings || []),data.source ? `Reference: ${data.source}.` : '',`${state.preview.length} extraction item(s) detected. Example rows are excluded.`].filter(Boolean).join(' ');
  const container=$('#template-items');container.replaceChildren();
  state.preview.forEach((item,index)=>{
    const row=el('tr');row.dataset.previewIndex=index;
    const included=el('input');included.type='checkbox';included.checked=true;included.setAttribute('aria-label',`Import ${item.name}`);included.dataset.property='included';
    const name=el('input');name.value=item.name || '';name.setAttribute('aria-label',`Item name ${index+1}`);name.dataset.property='name';
    const heading=el('input');heading.value=item.export_heading || item.name || '';heading.setAttribute('aria-label',`Export heading ${index+1}`);heading.dataset.property='export_heading';
    [included,el('span',item.worksheet_name || 'Data'),name,heading].forEach(node=>{const cell=el('td');cell.append(node);row.append(cell);});row.append(el('td',item.data_type || 'text'));
    const section=el('select');section.dataset.property='section';section.setAttribute('aria-label',`Extraction section ${index+1}`);
    options(section,[{id:'study',name:'Study extraction'},{id:'meta_analysis',name:'Meta-analysis outcomes'}],item.section || (/meta[ _-]*analysis/i.test(item.worksheet_name || '')?'meta_analysis':'study'));
    const sectionCell=el('td');sectionCell.append(section);row.append(sectionCell);container.append(row);
  });
}
bind('#reference-template','click',async()=>renderTemplatePreview(await api('/api/reference-template')));
bind('#template-form','submit',async()=>{
  const file=$('#template-file').files[0];if(!file)throw new Error('Choose an Excel template workbook first.');
  const form=new FormData();form.append('file',file);form.append('header_rows',$('#template-rows').value);form.append('mode',$('#template-mode').value);
  if($('#template-sheet').value)form.append('sheet',$('#template-sheet').value);
  form.append('all_sheets',String(!$('#template-sheet').value));
  if($('#template-mapping').value.trim())form.append('mapping',JSON.stringify(jsonInput('#template-mapping',{},'Dictionary mapping')));
  const result=await api('/api/templates/preview',{method:'POST',body:form});
  options($('#template-sheet'),[{id:'',name:'All worksheets (kept separate)'},...(result.sheets || []).map(sheet=>({id:sheet,name:sheet}))],result.all_sheets?'':result.sheet);
  renderTemplatePreview(result);
});
bind('#template-file','change',()=>{const option=el('option','All worksheets (kept separate)');option.value='';$('#template-sheet').replaceChildren(option);});
bind('#import-items','click',async()=>{
  const items=[];
  $$('#template-items tr').forEach(row=>{
    if(!row.querySelector('[data-property="included"]').checked)return;
    items.push({...state.preview[Number(row.dataset.previewIndex)],name:row.querySelector('[data-property="name"]').value.trim(),export_heading:row.querySelector('[data-property="export_heading"]').value.trim(),section:row.querySelector('[data-property="section"]').value});
  });
  if(!items.length)throw new Error('Select at least one extraction item.');
  await api(reviewPath('/items/import'),{method:'POST',body:{items}});
  await reloadDesign();$('#template-preview').hidden=true;saved('Extraction design saved');toast(`${items.length} extraction items imported. No example study data was imported.`);
});
bind('#upload-form','submit',async()=>{
  const files=$('#pdf-files').files;if(!files.length)throw new Error('Choose one or more PDF files.');
  const form=new FormData();[...files].forEach(file=>form.append('files',file));
  $('#upload-status').textContent=`Uploading ${files.length} PDF file(s)…`;
  try {
    const result=await api(reviewPath('/papers'),{method:'POST',body:form});
    const restored=result.papers.filter(p=>p.restored).length;
    const duplicates=result.papers.filter(p=>p.duplicate && !p.restored).length;
    $('#upload-status').textContent=`${result.papers.length-duplicates-restored} PDF(s) saved. Click Scan all papers to begin processing.${restored ? ` ${restored} removed paper(s) restored with their saved work.` : ''}${duplicates ? ` ${duplicates} duplicate(s) detected; existing records retained.` : ''}`;
    $('#upload-form').reset();state.papers=await api(reviewPath('/papers'));renderPapers();renderReviewSummary();const previous=state.paper?.id;renderSelectors();if(!previous)await loadWorkspace(true);
  } catch(error){
    $('#upload-status').textContent=`Upload failed: ${error.message}. Earlier valid files in this batch may already have been added.`;
    const papers=await api(reviewPath('/papers')).catch(()=>null);
    if(papers){state.papers=papers;renderPapers();renderReviewSummary();renderSelectors();}
    throw error;
  }
});

async function reloadDesign() {
  const [items,review,papers]=await Promise.all([api(reviewPath('/items?include_deleted=true')),api(reviewPath()),api(reviewPath('/papers'))]);
  state.items=items;state.review=review;state.papers=papers;renderSelectors();renderReviewSummary();renderPapers();renderEditorList();
  await loadWorkspace(false);
  await loadReadingProgress();
}
function openItemEditor() {
  reviewPath();if(!keepUnsaved())return;
  renderEditorList();editItem(state.item?.id || activeItems()[0]?.id || null);$('#item-dialog').showModal();
}
bind('#edit-items','click',openItemEditor);bind('#setup-edit-items','click',openItemEditor);
bind('#new-item','click',()=>editItem(null));

let keywordTimer, keywordRequest=0, keywordsEdited=false;
function editItem(id) {
  clearTimeout(keywordTimer);keywordRequest++;keywordsEdited=false;
  $('#keyword-status').textContent='';
  state.editorId=id;
  delete $('#edit-heading').dataset.edited;
  const item=state.items.find(item=>item.id===id) || {};
  $('#edit-section').value=item.section || (id?'study':state.extractionSection) || 'study';
  $('#edit-worksheet').value=item.worksheet_name || ($('#edit-section').value==='meta_analysis'?'Meta Analysis':'Data');
  $('#worksheet-names').replaceChildren(...[...new Set(activeItems().map(i=>i.worksheet_name || 'Data'))].map(name=>{const option=el('option');option.value=name;return option;}));
  $('#edit-name').value=item.name || '';$('#edit-heading').value=item.export_heading || '';$('#edit-type').value=item.data_type || 'text';$('#edit-units').value=item.units || '';
  $('#edit-required').checked=!!item.required;$('#edit-definition').value=item.definition || '';$('#edit-instructions').value=item.instructions || '';
  $('#edit-keywords').value=(item.keywords || []).join('\n');$('#edit-allowed').value=(item.allowed_values || []).join('\n');
  $('#edit-context').value=Object.keys(item.context || {}).length ? JSON.stringify(item.context,null,2) : '';
  $('#edit-rules').value=item.rules?.length ? JSON.stringify(item.rules,null,2) : '';
  const retrievalKeys=['aliases','positive_examples','negative_examples','expected_type','expected_units','preferred_sections','negative_penalty'];
  $('#edit-retrieval').value=JSON.stringify(Object.fromEntries(retrievalKeys.filter(key=>key in item).map(key=>[key,item[key]])),null,2);
  $('#edit-search-mode').value=item.search_mode || 'intelligent';
  $('#delete-item').hidden=!id;$('#item-save-status').textContent=id ? 'Changes apply to this entire review.' : 'New item · no values will be copied from other papers.';
  renderEditorList();$('#edit-name').focus();
}
function renderEditorList() {
  const list=$('#editor-item-list');const deleted=$('#deleted-item-list');list.replaceChildren();deleted.replaceChildren();
  const active=activeItems();
  active.forEach((item,index)=>{
    const row=el('div',null,'editor-item');
    const select=button(`${index+1}. ${item.worksheet_name ? `${item.worksheet_name} · ` : ''}${item.name}${item.section==='meta_analysis'?' · outcome row':''}`,()=>editItem(item.id),state.editorId===item.id?'selected':'');
    select.dataset.itemId=item.id;row.append(select);
    for(const [direction,label] of [[-1,'↑'],[1,'↓']]) {
      const move=button(label,async()=>{
        const ids=activeItems().map(i=>i.id);const at=ids.indexOf(item.id);const target=at+direction;if(target<0||target>=ids.length)return;
        [ids[at],ids[target]]=[ids[target],ids[at]];await api(reviewPath('/items/reorder'),{method:'POST',body:{ids}});await reloadDesign();saved('Column order saved');
      },'move');move.setAttribute('aria-label',`Move ${item.name} ${direction<0?'up':'down'}`);move.disabled=index+direction<0||index+direction>=active.length;row.append(move);
    }
    list.append(row);
  });
  state.items.filter(item=>item.deleted).forEach(item=>{
    const row=el('div',null,'editor-item');row.append(el('span',item.name,'hint'),button('Restore',async()=>{await api(reviewPath(`/items/${item.id}/restore`),{method:'POST'});await reloadDesign();editItem(item.id);saved('Item and previous work restored');toast('Item restored with its saved evidence and decisions.');},'small-button'));deleted.append(row);
  });
  if(!deleted.childNodes.length)deleted.append(el('p','No deleted items.','hint'));
}
async function fillSuggestedKeywords(merge=false) {
  clearTimeout(keywordTimer);
  if(!merge && (state.editorId || keywordsEdited))return;
  const request=++keywordRequest;
  const data={name:$('#edit-name').value,export_heading:$('#edit-heading').value,definition:$('#edit-definition').value};
  const result=await api('/api/keyword-suggestions',{method:'POST',body:data});
  if(request!==keywordRequest || (!merge && keywordsEdited))return;
  const terms=merge ? [...lines('#edit-keywords'),...result.keywords] : result.keywords;
  const seen=new Set();
  $('#edit-keywords').value=terms.filter(term=>{const key=term.toLocaleLowerCase();if(seen.has(key))return false;seen.add(key);return true;}).join('\n');
  if(merge)keywordsEdited=true;
  $('#keyword-status').textContent='Suggested terms added. Edit the list, then save to apply it to all papers.';
}
function queueKeywords() {
  clearTimeout(keywordTimer);keywordRequest++;
  if(!state.editorId && !keywordsEdited)keywordTimer=setTimeout(()=>run(()=>fillSuggestedKeywords()),250);
}
bind('#edit-name','input',()=>{if(!state.editorId && !$('#edit-heading').dataset.edited)$('#edit-heading').value=$('#edit-name').value;queueKeywords();});
bind('#edit-heading','input',()=>{$('#edit-heading').dataset.edited='true';queueKeywords();});
bind('#edit-definition','input',queueKeywords);
bind('#edit-keywords','input',()=>{keywordsEdited=true;clearTimeout(keywordTimer);keywordRequest++;$('#keyword-status').textContent='Your edits will be kept. Save to apply them.';});
bind('#suggest-keywords','click',()=>fillSuggestedKeywords(true));
bind('#item-form','submit',async()=>{
  await fillSuggestedKeywords();
  const context=jsonInput('#edit-context',{},'Context');const rules=jsonInput('#edit-rules',[],'Matching rules');
  if(!context || Array.isArray(context) || typeof context!=='object')throw new Error('Context must be a JSON object.');
  if(!Array.isArray(rules))throw new Error('Matching rules must be a JSON array.');
  const retrieval=jsonInput('#edit-retrieval',{},'Retrieval settings');
  const allowedRetrieval=['aliases','positive_examples','negative_examples','expected_type','expected_units','preferred_sections','negative_penalty'];
  if(!retrieval || Array.isArray(retrieval) || typeof retrieval!=='object' || Object.keys(retrieval).some(key=>!allowedRetrieval.includes(key)))throw new Error('Use the supported retrieval settings shown above the editor.');
  const item={name:$('#edit-name').value.trim(),section:$('#edit-section').value,worksheet_name:$('#edit-worksheet').value.trim(),export_heading:$('#edit-heading').value.trim(),data_type:$('#edit-type').value,units:$('#edit-units').value.trim(),required:$('#edit-required').checked,definition:$('#edit-definition').value,instructions:$('#edit-instructions').value,keywords:lines('#edit-keywords'),allowed_values:allowedValues(),context,rules,...retrieval,search_mode:$('#edit-search-mode').value};
  $('#item-save-status').textContent='Saving review-wide item…';saved('Saving extraction design…');
  try {
    const result=await api(reviewPath(`/items${state.editorId ? `/${state.editorId}` : ''}`),{method:state.editorId?'PATCH':'POST',body:item});
    state.editorId=result.id;await reloadDesign();editItem(result.id);$('#item-save-status').textContent='Saved for all papers. Scan manually to update suggestions.';saved('Review-wide item saved');toast('Item saved for every paper. Start a scan when you want updated suggestions.');
  } catch(error){$('#item-save-status').textContent=`Save failed: ${error.message}`;throw error;}
});
bind('#delete-item','click',async()=>{
  if(!state.editorId)return;
  const item=state.items.find(i=>i.id===state.editorId);
  if(!window.confirm(`Delete “${item.name}” from active searching and the main export? Its history and previous work will be retained for restoration.`))return;
  await api(reviewPath(`/items/${state.editorId}`),{method:'DELETE'});await reloadDesign();editItem(activeItems()[0]?.id || null);saved('Item deleted; history retained');
});

bind('#paper-select','change',async()=>{
  if(!keepUnsaved()){$('#paper-select').value=state.paper?.id;return;}
  state.paper=state.papers.find(p=>p.id===$('#paper-select').value);await loadWorkspace(true);await rememberPosition();
});
bind('#item-select','change',async()=>{
  if(!keepUnsaved()){$('#item-select').value=state.item?.id;return;}
  state.item=workspaceItems().find(i=>i.id===$('#item-select').value);await loadWorkspace(false);await rememberPosition();
});
async function moveItem(direction) {
  if(!keepUnsaved())return;
  const items=workspaceItems();const index=items.findIndex(item=>item.id===state.item?.id);const next=items[index+direction];
  if(next){state.item=next;$('#item-select').value=next.id;await loadWorkspace(false);await rememberPosition();}
}
bind('#previous-item','click',()=>moveItem(-1));bind('#next-item','click',()=>moveItem(1));
async function startScan(allPapers=false) {
  const reviewId=state.review?.id;
  const path=reviewPath('/refresh') + (allPapers ? '' : `?paper_id=${encodeURIComponent(state.paper.id)}`);
  const result=await api(path,{method:'POST'});
  if(state.review?.id!==reviewId)return;
  const message=result.queued_count ? `${result.queued_count} paper(s) queued to scan. Saved decisions are retained.` : 'The selected papers are already queued or scanning.';
  $('#scan-status').textContent=message;
  state.papers=await api(reviewPath('/papers'));
  if(state.review?.id!==reviewId)return;
  renderPapers();renderSelectors();renderReviewSummary();
  await loadWorkspace(true, true);
  toast(message);
}
bind('#refresh-search','click',()=>startScan());
bind('#scan-all-papers','click',()=>startScan(true));
bind('#previous-page','click',()=>viewer.goTo(viewer.pageNumber-1));bind('#next-page','click',()=>viewer.goTo(viewer.pageNumber+1));
bind('#page-number','change',()=>viewer.goTo($('#page-number').value));bind('#zoom-out','click',()=>viewer.setZoom(viewer.zoom-.2));bind('#zoom-in','click',()=>viewer.setZoom(viewer.zoom+.2));bind('#text-toggle','click',()=>viewer.toggleText());
bind('#document-search-form','submit',async()=>{
  const query=$('#document-query').value.trim();if(!query)throw new Error('Enter text to search across this PDF.');
  state.searchResults=await api(paperPath(`/search?q=${encodeURIComponent(query)}`));
  const container=$('#document-search-results');container.replaceChildren();container.hidden=false;$('#clear-document-search').hidden=false;
  container.append(el('p',`${state.searchResults.length} match(es) across searchable pages. Image-only content is not included.`,'hint'));
  state.searchResults.forEach((result,index)=>{result.id=result.id || `document-search-${index}`;container.append(button(`PDF p. ${result.page_number} · ${result.context || result.quote}`,()=>viewer.goTo(result.page_number,result.id),'search-result'));});updateHighlights();
});
bind('#clear-document-search','click',()=>{state.searchResults=[];$('#document-query').value='';$('#document-search-results').hidden=true;$('#clear-document-search').hidden=true;updateHighlights();});

let selectedEvidence=null,selectionContext=null;
function openEvidence(selection = null) {
  if(!workspaceReady())throw new Error('Wait for this extraction row to finish loading.');
  if(!state.pages.length)throw new Error('Scan this paper before selecting source evidence.');
  itemPath();
  if(!state.loadedSource || state.loadedSource.reviewId!==state.review.id || state.loadedSource.paperId!==state.paper.id || state.loadedSource.documentVersion!==state.paper.document_version)throw new Error('The document source is still loading or has changed. Wait for the current page, then select the text again.');
  selectedEvidence=selection;$('#manual-evidence-form').reset();
  selectionContext={reviewId:state.review.id,paperId:state.paper.id,itemId:state.item.id,outcomeId:state.extractionSection==='meta_analysis'?state.outcome?.id:null,documentVersion:state.paper.document_version};
  $('#evidence-dialog-title').textContent=selection?'Create candidate for this item':'Add supporting evidence';
  $('#evidence-item').textContent=`${state.item.name}${selectionContext.outcomeId?` · ${outcomeLabel(state.outcome)}`:''} · ${state.paper.title || state.paper.filename}`;
  $('#save-pending-candidate').hidden=!selection;
  $('#evidence-page').readOnly=!!selection;$('#evidence-quote').readOnly=!!selection;
  $('#evidence-transcription').closest('label').hidden=!!selection;$('#evidence-ocr').closest('label').hidden=!!selection;
  $('#evidence-selection-hint').textContent=selection?'This exact selection will be saved for the item shown above. Accepting evidence does not approve a final value.':'For searchable pages, the quotation must occur exactly in the extracted source text. Use the extracted-text view if the PDF selection differs.';
  $('#evidence-page').value=selection?.page_number || viewer.pageNumber;$('#evidence-page').max=viewer.document?.numPages || state.paper.page_count;
  $('#evidence-quote').value=selection?.quote || '';$('#evidence-dialog').showModal();
}
// Preserve the document selection when the capture button receives focus.
$('#capture-selection').addEventListener('pointerdown',event=>event.preventDefault());
bind('#capture-selection','click',()=>openEvidence(viewer.selection()));bind('#manual-evidence','click',()=>openEvidence());
bind('#manual-evidence-form','submit',async event=>{
  const context=selectionContext;
  if(!context || context.reviewId!==state.review?.id || context.paperId!==state.paper?.id || context.itemId!==state.item?.id || context.outcomeId!==(state.extractionSection==='meta_analysis'?state.outcome?.id:null) || context.documentVersion!==state.paper?.document_version)throw new Error('The paper, item, or document version changed, or a different outcome row was selected. Close this dialog and select the text again.');
  const pageNumber=Number($('#evidence-page').value);const quote=$('#evidence-quote').value;const notes=$('#evidence-notes').value;
  const manual=$('#evidence-transcription').checked;const page=state.pages.find(p=>p.page_number===pageNumber);
  if(manual && !notes.trim())throw new Error('Add a rationale describing your manual transcription from the page or image.');
  const decision=selectedEvidence && event.submitter?.value==='pending'?'pending':'accepted';
  const data={page_number:pageNumber,quote,notes,source_location:$('#evidence-location').value.trim(),rationale:manual?notes:'',decision,document_version:context.documentVersion,manual_transcription:manual,manual,ocr:!!page?.ocr || $('#evidence-ocr').checked};
  if(selectedEvidence && selectedEvidence.page_number===pageNumber && selectedEvidence.quote===quote)Object.assign(data,selectedEvidence);
  data.ocr=!!data.ocr || $('#evidence-ocr').checked;
  const result=await api(scopedPath(`/api/reviews/${context.reviewId}/papers/${context.paperId}/items/${context.itemId}/${selectedEvidence?'candidates':'evidence'}`,context.outcomeId),{method:'POST',body:data});
  if(context.reviewId!==state.review?.id || context.paperId!==state.paper?.id || context.itemId!==state.item?.id || context.outcomeId!==(state.extractionSection==='meta_analysis'?state.outcome?.id:null))return;
  $('#evidence-dialog').close();await loadFieldWorkspace(state.loadSequence,true);saved(selectedEvidence?'Candidate saved':'Manual evidence saved');
  toast(result.decision==='accepted'?'Source passage accepted. Approve the final extraction value separately.':result.decision==='rejected'?'Existing candidate retained with its previous rejection. Use Reconsider to change the decision.':'Pending candidate saved. Review and accept it when ready.');
});

async function loadLearning() {
  const reviewId=state.review?.id;if(!reviewId)return;
  const learning=await api(`/api/reviews/${reviewId}/learning`);
  if(reviewId!==state.review?.id)return;
  state.learning=learning;renderLearning();
}
async function openLearning() {reviewPath();await loadLearning();$('#learning-dialog').showModal();}
bind('#learning-settings','click',openLearning);bind('#setup-learning-settings','click',openLearning);
function renderLearning() {
  const learning=state.learning;if(!learning)return;
  $('#learning-review-name').textContent=state.review.name;
  $('#learning-enabled').checked=!!learning.config.enabled;
  $('#learning-current-item').disabled=!state.item;
  const examples=learning.examples || [];
  const eligible=examples.filter(example=>example.eligible && !example.excluded && example.decision==='accepted' && (!state.item || example.item_id===state.item.id));
  const documents=new Set(eligible.map(example=>example.paper_id)).size;
  $('#learning-status').textContent=`${learning.config.enabled?'Learning enabled':'Learning disabled'} · Configuration ${learning.config.version} · reset ${learning.config.epoch} · revision ${learning.config.revision}. ${state.item?`${state.item.name}: `:''}${documents} paper record(s) with eligible accepted examples. An adjustment requires at least two accepted source documents outside the paper being searched; copies of that paper are excluded.`;
  const container=$('#learning-examples');container.replaceChildren();
  const filtered=examples.filter(example=>!$('#learning-current-item').checked || !state.item || example.item_id===state.item.id);
  for(const example of filtered) {
    const row=el('article',null,'learning-example');row.dataset.exampleId=example.example_id;
    const item=state.items.find(record=>record.id===example.item_id),paper=state.papers.find(record=>record.id===example.paper_id);
    row.append(el('p',`${item?.name || 'Historical item'} · ${paper?.title || paper?.filename || 'Historical paper'} · ${example.decision}`,'hint'),el('blockquote',example.quote,'quote'));
    if(example.rejection_reason)row.append(el('p',`Rejection reason: ${String(example.rejection_reason).replaceAll('_',' ')}`,'hint'));
    let eligibility=`Not used: ${String(example.excluded_reason || (example.excluded?'excluded by reviewer':'no effective training label')).replaceAll('_',' ')}.`;
    if(example.eligible && !example.excluded)eligibility=example.decision==='rejected' && example.rejection_reason!=='irrelevant'?'Kept as review feedback. Only “Irrelevant” rejections are used as general negative examples.':'Eligible for matching other papers.';
    row.append(el('p',eligibility,'hint'));
    const include=el('label',null,'check-label'),checkbox=el('input');checkbox.type='checkbox';checkbox.checked=!example.excluded;checkbox.setAttribute('aria-label','Allow this example for learning');
    checkbox.addEventListener('change',()=>run(async()=>{
      await api(reviewPath(`/learning/examples/${encodeURIComponent(example.example_id)}`),{method:'PATCH',body:{excluded:!checkbox.checked}});await loadLearning();saved('Learning example preference saved');
    },checkbox));include.append(checkbox,el('span','Allow this example for learning'));row.append(include);
    if(paper && item && !item.deleted)row.append(button('Open source paper and item',()=>openLearningSource(example),'small-button'));
    container.append(row);
  }
  if(!filtered.length)container.append(el('p','No reviewer feedback for this selection yet. Accept or reject a passage to record an example.','hint'));
}
bind('#learning-current-item','change',renderLearning);
bind('#learning-enabled','change',async()=>{
  await api(reviewPath('/learning'),{method:'PATCH',body:{enabled:$('#learning-enabled').checked}});await loadLearning();saved('Learning setting saved');toast('Learning setting saved. Refreshed searches will use this setting.');
});
bind('#reset-learning','click',async()=>{
  await api(reviewPath('/learning'),{method:'PATCH',body:{reset:true}});await loadLearning();saved('Learned adjustments reset');toast('Learning reset. Evidence, decisions and history are retained; new feedback can provide new examples.');
});
async function openLearningSource(example) {
  if(!keepUnsaved())return;
  const paper=state.papers.find(record=>record.id===example.paper_id),item=state.items.find(record=>record.id===example.item_id && !record.deleted);
  if(!paper || !item)throw new Error('This historical source is no longer in the active workspace. Its feedback remains in the decision history.');
  state.extractionSection=item.section || 'study';
  $('#learning-dialog').close();renderSelectors(paper.id,item.id);showSection('extract');await loadWorkspace(true);showPanel(false);await rememberPosition();
  const source=[...state.evidence,...state.suggestions].find(record=>(example.evidence_id && record.id===example.evidence_id) || (example.quote && record.quote===example.quote));
  if(source)await viewer.goTo(source.page_number,sourceHighlightId(source));
}

bind('#edit-paper','click',()=>{
  paperPath();const paper=state.paper;$('#paper-title').value=paper.title;$('#paper-citation').value=paper.citation || '';$('#paper-url').value=paper.url || '';$('#paper-included').checked=paper.included!==false;$('#paper-manual-ack').checked=!!paper.manual_review_ack;
  $('#paper-processing-detail').textContent=`${paper.filename} · ${paper.page_count || 0} pages · ${paper.processing_status}${paper.processing_error ? `: ${paper.processing_error}` : ''}`;$('#paper-dialog').showModal();
});
bind('#paper-form','submit',async()=>{
  const paper=await api(paperPath(),{method:'PATCH',body:{title:$('#paper-title').value.trim(),citation:$('#paper-citation').value,url:$('#paper-url').value.trim(),included:$('#paper-included').checked,manual_review_ack:$('#paper-manual-ack').checked}});
  state.paper=paper;state.papers=state.papers.map(p=>p.id===paper.id?paper:p);renderPapers();renderSelectors();$('#paper-dialog').close();saved('Paper details saved');
});
bind('#reprocess-paper','click',async()=>{await api(paperPath('/reprocess'),{method:'POST'});$('#paper-dialog').close();toast('PDF processing queued. Existing decisions remain saved.');});

let exportLoadSequence=0;
async function loadValidation() {
  const summary=$('#validation-summary');const table=$('#export-table');
  if(!state.review){summary.textContent='Select a review to validate and export.';table.replaceChildren();return;}
  const id=state.review.id;
  const sequence=++exportLoadSequence;
  const preview=await api(reviewPath('/export-preview'));
  if(id!==state.review?.id || sequence!==exportLoadSequence)return;
  const validation=preview.validation;
  const attention=validation.counts.attention_fields;
  summary.replaceChildren(el('h2',`${attention} ${attention===1?'item needs':'items need'} attention`));
  summary.append(el('p','Saved final values take priority; accepted source passages fill the remaining cells.','hint'));
  renderExportTables(preview);
}
function renderExportTables(preview) {
  const table=$('#export-table');table.replaceChildren();
  let worksheets=preview.worksheets;
  if(!worksheets){
    const groups=new Map();
    (preview.columns || []).forEach((column,index)=>{const name=column.worksheet_name || 'Data';if(!groups.has(name))groups.set(name,[]);groups.get(name).push(index);});
    worksheets=[...groups].map(([name,indices])=>({name,columns:indices.map(index=>preview.columns[index]),rows:(preview.rows || []).map(row=>({...row,values:indices.map(index=>row.values?.[index]),statuses:indices.map(index=>row.statuses?.[index]),sources:indices.map(index=>row.sources?.[index])}))}));
  }
  worksheets=worksheets.map((sheet,index)=>({...sheet,tabKey:JSON.stringify([sheet.name,sheet.row_scope,index])}));
  if(!worksheets.some(sheet=>sheet.tabKey===state.exportWorksheet))state.exportWorksheet=worksheets[0]?.tabKey;
  if(worksheets.length>1){
    const tabs=el('div',null,'panel-tabs');tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','Export worksheets');
    for(const sheet of worksheets){
      const duplicate=worksheets.filter(other=>other.name===sheet.name).length>1;
      const label=duplicate?`${sheet.name} (${sheet.row_scope==='outcome'?'outcomes':'studies'})`:sheet.name;
      const tab=button(label,()=>{state.exportWorksheet=sheet.tabKey;renderExportTables(preview);});tab.setAttribute('role','tab');tab.setAttribute('aria-selected',String(state.exportWorksheet===sheet.tabKey));tabs.append(tab);
    }
    table.append(tabs);
  }
  const sheet=worksheets.find(sheet=>sheet.tabKey===state.exportWorksheet) || {columns:[],rows:[]};
  if(sheet.row_scope==='outcome' || sheet.row_scope==='meta_analysis')table.append(el('p',`${sheet.rows.length} selected outcome row${sheet.rows.length===1?'':'s'}. Each row represents one outcome, study arm and assessment time.`,'hint'));
  const grid=el('table');const head=el('thead');const headingRow=el('tr');sheet.columns.forEach(column=>headingRow.append(el('th',column.heading)));head.append(headingRow);grid.append(head);
  const body=el('tbody');sheet.rows.forEach(row=>{
    const tr=el('tr');if(row.outcome_id)tr.dataset.outcomeId=row.outcome_id;
    sheet.columns.forEach((_column,index)=>{const value=row.values?.[index];const status=row.statuses?.[index];const td=el('td',value===null || value===undefined?'':typeof value==='object'?JSON.stringify(value):String(value));td.title=`${row.title} · ${row.sources?.[index]==='accepted_passage'?'Accepted source passage':statuses[status] || status || ''}`;tr.append(td);});body.append(tr);
  });grid.append(body);table.append(grid);if(!sheet.rows.length)table.append(el('p',sheet.row_scope==='outcome'?'No selected outcome rows yet. Add usable outcomes in Read & extract.':'No included paper records to preview.','empty-state'));
}

async function loadExportHistory() {
  if(!state.review || !$('#export-history').open)return;
  const id=state.review.id;
  const history=await api(reviewPath('/history'));
  if(id!==state.review?.id)return;
  const historyList=$('#history-list');historyList.replaceChildren();
  history.slice(-100).reverse().forEach(record=>{const row=el('details',null,'history-record');row.append(el('summary',`${new Date(record.created_at).toLocaleString()} · ${record.reviewer || 'reviewer'} · ${record.action}`),el('pre',JSON.stringify({paper_id:record.paper_id,item_id:record.item_id,before:record.before,after:record.after},null,2)));historyList.append(row);});
  if(history.length>100)historyList.prepend(el('p',`Showing the latest 100 of ${history.length} entries. The complete history remains saved in this review.`,'hint'));
}
bind('#refresh-validation','click',loadValidation);
bind('#export-history','toggle',loadExportHistory);
async function downloadExport(view) {
  if(state.dirty)throw new Error('Save your extraction edits before exporting.');
  const review=state.review;
  toast('Preparing Excel file…');
  const response=await fetch(reviewPath(`/export?view=${view}`));
  if(!response.ok){const error=await response.json();throw new Error(typeof error.detail==='string'?error.detail:JSON.stringify(error.detail || error));}
  const blob=await response.blob();const url=URL.createObjectURL(blob);const link=el('a');link.href=url;
  link.download=`${review.name.replace(/[<>:"/\\|?*]/g,'_')}-${view}.xlsx`;
  document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
  toast('Excel file ready. Check your browser downloads.');
}
bind('#data-export','click',()=>downloadExport('data'));
bind('#verification-export','click',()=>downloadExport('verification'));
bind('#combined-export','click',()=>downloadExport('combined'));

let polling=false;
setInterval(async()=>{
  if(polling || !state.review || document.hidden)return;
  polling=true;
  const id=state.review.id;
  try {
    const papers=await api(`/api/reviews/${id}/papers`);if(id!==state.review?.id)return;
    const previous=state.paper;
    const changed=JSON.stringify(papers.map(p=>[p.id,p.processing_status,p.processing_progress,p.search_status,p.search_progress,p.search_version,p.document_version]))!==JSON.stringify(state.papers.map(p=>[p.id,p.processing_status,p.processing_progress,p.search_status,p.search_progress,p.search_version,p.document_version]));
    if(!changed)return;
    state.papers=papers;renderPapers();renderReviewSummary();renderSelectors();
    if(state.paper && previous?.id===state.paper.id) {
      const documentReady=previous.processing_status!=='ready' && state.paper.processing_status==='ready';
      const sourceChanged=previous.document_version!==state.paper.document_version;
      if(documentReady || sourceChanged)await loadWorkspace(true, true);
      else {renderProcessingNotice();if(previous.search_status!==state.paper.search_status || previous.search_version!==state.paper.search_version)await loadFieldWorkspace(state.loadSequence,true);}
    } else if(state.paper)await loadWorkspace(true);
  } catch(error){$('#save-status').textContent=`Connection issue: ${error.message}`;}
  finally{polling=false;}
},2500);

await run(()=>loadReviews());
