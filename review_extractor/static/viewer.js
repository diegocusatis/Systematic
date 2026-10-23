import './polyfills.js';
import { getDocument, GlobalWorkerOptions, TextLayer } from '/static/vendor/pdfjs/build/pdf.mjs';

GlobalWorkerOptions.workerSrc = '/static/pdf-worker.js';

/** Original PDF renderer. Stored boxes use visible CropBox top-left coordinates,
 * including rotation, so viewport/stored dimensions supply the exact transform. */
export class DocumentViewer {
  constructor(onPage, onError) {
    this.onPage = onPage;
    this.onError = onError;
    this.document = null;
    this.pages = [];
    this.pageNumber = 1;
    this.zoom = 1;
    this.highlights = [];
    this.focusId = null;
    this.extracted = false;
    this.renderSequence = 0;
    this.loadSequence = 0;
    this.stage = document.querySelector('#pdf-stage');
    this.canvas = document.querySelector('#pdf-canvas');
    this.overlay = document.querySelector('#pdf-highlights');
    this.textLayerNode = document.querySelector('#pdf-text-layer');
    this.extractedNode = document.querySelector('#extracted-text');
    this.placeholder = document.querySelector('#viewer-placeholder');
    this.stage.hidden = true;
  }

  async open(url, pages) {
    const sequence = ++this.loadSequence;
    ++this.renderSequence;
    this.renderTask?.cancel();
    this.textLayer?.cancel();
    this.loadingTask?.destroy();
    this.document = null;
    this.pages = pages;
    this.highlights = [];
    this.focusId = null;
    this.pageNumber = 1;
    this.placeholder.textContent = 'Loading original PDF…';
    this.placeholder.hidden = false;
    this.stage.hidden = true;
    this.extractedNode.hidden = true;
    try {
      this.loadingTask = getDocument({url, cMapUrl:'/static/vendor/pdfjs/cmaps/',cMapPacked:true,
        standardFontDataUrl:'/static/vendor/pdfjs/standard_fonts/',wasmUrl:'/static/vendor/pdfjs/wasm/',
        isEvalSupported:false});
      const pdf = await this.loadingTask.promise;
      if (sequence !== this.loadSequence) return;
      this.document = pdf;
      await this.render();
    } catch(error) {
      if (sequence !== this.loadSequence) return;
      this.placeholder.textContent = `The original PDF could not be displayed: ${error.message}`;
      this.onError(error);
    }
  }

  clear() {
    ++this.loadSequence;
    ++this.renderSequence;
    this.renderTask?.cancel();
    this.textLayer?.cancel();
    this.loadingTask?.destroy();
    this.document = null;
    this.pages = [];
    this.stage.hidden = true;
    this.extractedNode.hidden = true;
    this.placeholder.hidden = false;
    this.placeholder.textContent = 'Choose a processed PDF to start reading.';
  }

  async render() {
    if (!this.document) return;
    const sequence = ++this.renderSequence;
    this.renderTask?.cancel();
    this.textLayer?.cancel();
    const pageNumber = this.pageNumber;
    const page = await this.document.getPage(pageNumber);
    if (sequence !== this.renderSequence) return;
    const viewport = page.getViewport({scale:this.zoom});
    const ratio = window.devicePixelRatio || 1;
    // A fresh canvas prevents a cancelled render from retaining this canvas.
    const newCanvas = document.createElement('canvas');
    newCanvas.id = 'pdf-canvas';
    newCanvas.width = Math.floor(viewport.width * ratio);
    newCanvas.height = Math.floor(viewport.height * ratio);
    newCanvas.style.width = `${viewport.width}px`;
    newCanvas.style.height = `${viewport.height}px`;
    this.canvas.replaceWith(newCanvas);
    this.canvas = newCanvas;
    this.stage.style.width = `${viewport.width}px`;
    this.stage.style.height = `${viewport.height}px`;
    this.stage.style.setProperty('--scale-factor', this.zoom);
    this.stage.style.setProperty('--total-scale-factor', this.zoom);
    this.textLayerNode.replaceChildren();
    this.placeholder.hidden = true;
    this.stage.hidden = this.extracted;
    this.extractedNode.hidden = !this.extracted;
    this.viewport = viewport;
    this.onPage(pageNumber, this.document.numPages, this.pages.find(p => p.page_number === pageNumber));
    this.drawHighlights();
    this.renderExtracted();
    try {
      this.renderTask = page.render({canvasContext:this.canvas.getContext('2d'),viewport,
        transform:ratio === 1 ? undefined : [ratio,0,0,ratio,0,0]});
      await this.renderTask.promise;
      if (sequence !== this.renderSequence) return;
      const content = await page.getTextContent();
      if (sequence !== this.renderSequence) return;
      this.textLayer = new TextLayer({textContentSource:content,container:this.textLayerNode,viewport});
      await this.textLayer.render();
    } catch(error) {
      if (sequence === this.renderSequence && error.name !== 'RenderingCancelledException' && error.name !== 'AbortException') this.onError(error);
    }
  }

  async goTo(pageNumber, focusId = null) {
    if (!this.document) return;
    this.focusId = focusId;
    const target = Math.max(1, Math.min(this.document.numPages, Number(pageNumber) || 1));
    if (target !== this.pageNumber) {
      this.pageNumber = target;
      await this.render();
      document.querySelector('#pdf-scroll').scrollTop = 0;
    } else {
      this.drawHighlights();
      this.renderExtracted();
    }
    const highlight = this.overlay.querySelector('.focused');
    if (highlight && !this.extracted) {
      const scroll = document.querySelector('#pdf-scroll');
      scroll.scrollTop = Math.max(0, highlight.offsetTop - scroll.clientHeight / 3);
      scroll.scrollLeft = Math.max(0, highlight.offsetLeft - scroll.clientWidth / 3);
    }
  }

  async setZoom(zoom) {
    this.zoom = Math.min(3, Math.max(.4, zoom));
    document.querySelector('#zoom-value').textContent = `${Math.round(this.zoom*100)}%`;
    await this.render();
  }

  setHighlights(highlights) {
    this.highlights = highlights || [];
    this.drawHighlights();
    this.renderExtracted();
  }

  drawHighlights() {
    this.overlay.replaceChildren();
    const source = this.pages.find(p => p.page_number === this.pageNumber);
    if (!this.viewport || !source?.width || !source?.height) return;
    const sx = this.viewport.width/source.width;
    const sy = this.viewport.height/source.height;
    for (const hit of this.highlights.filter(h => h.page_number === this.pageNumber)) {
      for (const box of hit.boxes || []) {
        const mark = document.createElement('div');
        mark.className = `pdf-highlight${hit.id === this.focusId ? ' focused' : ''}${hit.decision === 'accepted' ? ' accepted' : ''}`;
        mark.dataset.sourceId = hit.id;
        Object.assign(mark.style,{left:`${box.x0*sx}px`,top:`${box.top*sy}px`,width:`${(box.x1-box.x0)*sx}px`,height:`${(box.bottom-box.top)*sy}px`});
        this.overlay.append(mark);
      }
    }
  }

  renderExtracted() {
    const page = this.pages.find(p => p.page_number === this.pageNumber);
    const selection = window.getSelection();
    let selectedOffsets = null;
    // Suggestions can arrive while the reviewer is selecting a quotation.
    // Preserve the precise DOM offsets (including repeated text and direction)
    // only while rendering the same source page in the same document load.
    if(page && this.extractedSource === page && this.extractedLoadSequence === this.loadSequence &&
       this.extractedNode.textContent === page.text && selection && !selection.isCollapsed &&
       this.extractedNode.contains(selection.anchorNode) && this.extractedNode.contains(selection.focusNode)) {
      const offset = (node,position) => {
        const prefix = document.createRange();prefix.selectNodeContents(this.extractedNode);
        prefix.setEnd(node,position);return prefix.toString().length;
      };
      selectedOffsets = [offset(selection.anchorNode,selection.anchorOffset),offset(selection.focusNode,selection.focusOffset)];
    }
    this.extractedSource = page;
    this.extractedLoadSequence = this.loadSequence;
    this.extractedNode.replaceChildren();
    if (!page?.text) {
      this.extractedNode.textContent = 'No searchable text is available on this physical page. Review the original PDF and add a labeled manual transcription if needed.';
      return;
    }
    const hits = this.highlights.filter(h => h.page_number === this.pageNumber && Number.isInteger(h.start) && Number.isInteger(h.end)).sort((a,b)=>a.start-b.start);
    // Python source spans count Unicode code points; JavaScript string offsets
    // count UTF-16 code units (different for some mathematical characters).
    const sourceCharacters = Array.from(page.text);
    let cursor = 0;
    for (const hit of hits) {
      if (hit.start < cursor || hit.end > sourceCharacters.length) continue;
      this.extractedNode.append(document.createTextNode(sourceCharacters.slice(cursor,hit.start).join('')));
      const mark = document.createElement('mark');
      mark.textContent = sourceCharacters.slice(hit.start,hit.end).join('');
      if (hit.decision === 'accepted') mark.className = 'accepted';
      this.extractedNode.append(mark);
      cursor = hit.end;
    }
    this.extractedNode.append(document.createTextNode(sourceCharacters.slice(cursor).join('')));
    if(selectedOffsets) {
      const point = target => {
        const walker = document.createTreeWalker(this.extractedNode,NodeFilter.SHOW_TEXT);
        let node,total=0;
        while(node=walker.nextNode()) {
          if(target<=total+node.textContent.length)return [node,target-total];
          total+=node.textContent.length;
        }
        return null;
      };
      const anchor=point(selectedOffsets[0]),focus=point(selectedOffsets[1]);
      if(anchor && focus)selection.setBaseAndExtent(...anchor,...focus);
    }
  }

  toggleText() {
    this.extracted = !this.extracted;
    if (this.document) {
      this.stage.hidden = this.extracted;
      this.extractedNode.hidden = !this.extracted;
    }
    const button = document.querySelector('#text-toggle');
    button.textContent = this.extracted ? 'Original PDF' : 'Extracted text';
    button.setAttribute('aria-pressed',String(this.extracted));
  }

  selection() {
    const selection = window.getSelection();
    const page = this.pages.find(p => p.page_number === this.pageNumber);
    if (!selection || selection.isCollapsed || !page?.text) throw new Error('Select a passage in the original PDF or extracted-text view first.');
    if (![this.textLayerNode,this.extractedNode].some(node => node.contains(selection.anchorNode) && node.contains(selection.focusNode))) throw new Error('Select source text inside the document viewer.');
    let start = -1;
    let end = -1;
    if (this.extractedNode.contains(selection.anchorNode)) {
      const range = selection.getRangeAt(0);
      const prefix = range.cloneRange();
      prefix.selectNodeContents(this.extractedNode);
      prefix.setEnd(range.startContainer,range.startOffset);
      start = prefix.toString().length;
      end = start + range.toString().length;
    } else {
      const selected = selection.toString().trim();
      start = page.text.indexOf(selected);
      end = start + selected.length;
      if (start >= 0 && page.text.indexOf(selected,start+1) >= 0) throw new Error('This wording occurs more than once on this page. Select it in Extracted text to preserve the exact occurrence.');
      if (start === -1) {
        // PDF.js and pdfplumber split lines differently; match collapsed space,
        // then recover exact source offsets, never store rewritten quotations.
        const offsets = [];
        let normalized = '';
        for(let i=0;i<page.text.length;i++) {
          const c = /\s/.test(page.text[i]) ? ' ' : page.text[i];
          if(c === ' ' && normalized.endsWith(' ')) continue;
          normalized += c;
          offsets.push(i);
        }
        const needle = selected.replace(/\s+/g,' ');
        const at = normalized.indexOf(needle);
        if (at >= 0 && normalized.indexOf(needle,at+1) >= 0) throw new Error('This wording occurs more than once on this page. Select it in Extracted text to preserve the exact occurrence.');
        if (at >= 0) { start = offsets[at]; end = offsets[at+needle.length-1]+1; }
      }
    }
    if (start < 0 || end <= start) throw new Error('That PDF selection could not be matched to exact source text. Switch to Extracted text and select the passage there.');
    const sourceStart=Array.from(page.text.slice(0,start)).length;
    const sourceEnd=Array.from(page.text.slice(0,end)).length;
    return {page_number:this.pageNumber,start:sourceStart,end:sourceEnd,quote:page.text.slice(start,end),
      boxes:(page.words || []).filter(w=>w.start<sourceEnd && w.end>sourceStart).map(({x0,top,x1,bottom})=>({x0,top,x1,bottom})),ocr:!!page.ocr};
  }
}
