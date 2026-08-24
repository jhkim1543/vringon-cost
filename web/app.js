// 워크플로 UI. 각 단계는 서버 상태(state.json)를 그대로 반영한다.
import { Viewer, PALETTE } from './viewer.js';

const $ = s => document.querySelector(s);
const PID = new URLSearchParams(location.search).get('p') || 'DEMO-RUN-001';

const S = {
  pid: PID, state: null, catalog: null, cost: null,
  step: 'scale', selected: null, viewMode: 'parts',
};

const STEPS = [
  ['design', '디자인'], ['scale', '스케일'], ['segment', '세그먼트'],
  ['bom', 'BOM'], ['consumption', '소요량'], ['pricing', '단가'],
  ['cost', '원가·승인'],
];

const fmt = (v, d = 3) => v == null ? '—' : Number(v).toLocaleString('en-US',
  { minimumFractionDigits: d, maximumFractionDigits: d });
const usd = v => v == null ? '—' : '$' + fmt(v, 3);

let toastTimer;
function toast(msg, bad) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast on' + (bad ? ' bad' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.className = 'toast', 3600);
}

async function api(path, body, method) {
  const r = await fetch('/api' + path, body || method ? {
    method: method || 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  } : undefined);
  const j = await r.json().catch(() => ({}));
  if (j.error) { toast(j.error, true); throw new Error(j.error); }
  return j;
}

// ── 3D ────────────────────────────────────────────────────────────────
const viewer = new Viewer($('#viewer'));
viewer.onPick = name => { S.selected = name; renderEvidence(); render(); };
viewer.onLandmark = async (which, pt) => {
  const lm = S.state.landmarks || {};
  const toe = which === 'toe' ? pt : lm.toe;
  const heel = which === 'heel' ? pt : lm.heel;
  await api(`/project/${S.pid}/calibrate`, {
    target_length_mm: S.state.calibration?.target_length_mm || 300,
    toe, heel, confirmed: false,
  });
  await reload();
  toast(`${which === 'toe' ? '앞코' : '뒤꿈치'} 지점을 옮겼습니다. 길이를 다시 확인하세요.`);
};

function colorMap() {
  const map = {};
  const mapping = S.state?.mapping || [];
  if (S.viewMode === 'qa') {
    mapping.forEach(m => {
      const repaired = S.state.repairs?.[m.segment_id]?.ok;
      map[m.segment_id] = {
        color: m.qa?.is_volume ? 0x4fbf7d : repaired ? 0xe0a33a : 0xe05c5c,
        opacity: 0.95,
      };
    });
  } else if (S.viewMode === 'cost') {
    const byPart = {};
    (S.cost?.lines || []).forEach(l => {
      (l.segments || []).forEach(s => byPart[s] = l.cost_p50);
    });
    const max = Math.max(...Object.values(byPart).filter(v => v), 1e-9);
    mapping.forEach(m => {
      const v = byPart[m.segment_id];
      if (v == null) { map[m.segment_id] = { color: 0x2a323d, opacity: 0.35 }; return; }
      const t = Math.sqrt(v / max);
      map[m.segment_id] = {
        color: new (window.__THREE_COLOR__ || Object)(),
        opacity: 0.95,
      };
      // 값이 클수록 붉게
      const r = Math.round(40 + 190 * t), g = Math.round(180 - 120 * t), b = Math.round(150 - 90 * t);
      map[m.segment_id].color = (r << 16) | (g << 8) | b;
    });
  } else {
    mapping.forEach((m, i) => {
      map[m.segment_id] = { color: PALETTE[i % PALETTE.length], opacity: 1 };
    });
  }
  return map;
}

function renderLegend() {
  const el = $('#legend');
  if (S.viewMode === 'qa') {
    el.innerHTML = `<span><i style="background:#4fbf7d"></i>watertight (부피 사용 가능)</span>
      <span><i style="background:#e0a33a"></i>복구본</span>
      <span><i style="background:#e05c5c"></i>열린 메시 (부피 차단)</span>`;
  } else if (S.viewMode === 'cost') {
    el.innerHTML = `<span><i style="background:#28b496"></i>낮은 기여</span>
      <span><i style="background:#e63c3c"></i>높은 기여</span>
      <span><i style="background:#2a323d"></i>원가 없음/차단</span>`;
  } else {
    el.innerHTML = (S.state?.mapping || []).map((m, i) =>
      `<span><i style="background:#${PALETTE[i % PALETTE.length].toString(16).padStart(6, '0')}"></i>${m.canonical_part}</span>`
    ).join('');
  }
}

// ── 상단 플로우 ────────────────────────────────────────────────────────
function renderFlow() {
  const st = S.state?.steps || {};
  const status = {
    design: st.generate3d ? 'done' : '',
    scale: S.state?.calibration?.confirmed ? 'done' : st.scale ? 'review' : '',
    segment: st.segment?.status === 'confirmed' ? 'done' : st.segment ? 'review' : '',
    bom: st.bom ? 'done' : '',
    consumption: st.bom ? 'done' : '',
    pricing: st.cost ? 'done' : '',
    cost: S.cost ? (S.cost.rollup.fob_status === 'Calculated' ? 'done' : 'blocked') : '',
  };
  $('#flow').innerHTML = STEPS.map(([k, label]) =>
    `<button data-step="${k}" class="${S.step === k ? 'on' : ''}">
       <span class="dot ${status[k] || ''}"></span>${label}</button>`).join('');
  $('#flow').querySelectorAll('button').forEach(b =>
    b.onclick = () => { S.step = b.dataset.step; render(); });

  const g = S.cost?.grade?.class;
  const gc = $('#gradeChip');
  gc.textContent = '등급 ' + (g || '—');
  gc.className = 'chip ' + (g === 'C2' ? 'ok' : g === 'C1' ? 'warn' : '');
  const f = S.cost?.rollup?.fob_status;
  const fc = $('#fobChip');
  fc.textContent = f ? 'FOB ' + f : 'FOB —';
  fc.className = 'chip ' + (f === 'Calculated' ? 'ok' : f ? 'bad' : '');
}

// ── 단계 본문 ──────────────────────────────────────────────────────────
function render() {
  renderFlow();
  renderLegend();
  viewer.applyColors(colorMap());
  const body = $('#stepBody');
  const title = { design: '1 · 디자인 → 3D', scale: '2 · Metric Calibration',
    segment: '3 · 세그먼트 → Canonical Part', bom: '4 · Manufacturing BOM',
    consumption: '5 · 소요량', pricing: '6 · 단가', cost: '7 · 원가 · 승인' }[S.step];
  $('#stepTitle').textContent = title;
  ({ design: stepDesign, scale: stepScale, segment: stepSegment, bom: stepBom,
     consumption: stepConsumption, pricing: stepPricing, cost: stepCost }[S.step])(body);
}

function stepDesign(el) {
  const st = S.state.steps || {};
  const gen = S.state.tripo_generate || {};
  el.innerHTML = `
    <p class="muted">디자인 이미지를 Tripo v3 로 3D 화하고, 이어서 파트 세그멘테이션을 돌립니다.
    생성 결과 URL은 5분만 유효해서 성공 즉시 내부 저장소로 내려받습니다.</p>
    ${S.state.input_image ? `<img src="/api/project/${S.pid}/image" style="width:100%;border-radius:8px;border:1px solid var(--line);margin:8px 0">` : ''}
    <dl class="kv">
      <dt>프로젝트</dt><dd>${S.pid}</dd>
      <dt>3D 생성</dt><dd>${st.generate3d?.status || '—'}</dd>
      <dt>세그멘테이션</dt><dd>${st.segment3d?.status || (st.generate3d ? '—' : '—')}</dd>
      <dt>파트 수</dt><dd>${(S.state.mapping || []).length || '—'}</dd>
    </dl>
    <h4>새 이미지로 실행</h4>
    <input type="file" id="imgFile" accept="image/*">
    <label>프로젝트 ID</label>
    <input type="text" id="newPid" value="RUN-${Date.now().toString().slice(-6)}">
    <div class="row">
      <button class="btn primary" id="genBtn">Tripo 생성 + 세그멘테이션</button>
      <span class="muted" id="genStatus"></span>
    </div>
    <div class="note">실제 API 를 호출하며 크레딧이 소모됩니다 (생성 30 + 세그멘테이션 40).
      3~6분 걸립니다.</div>`;

  $('#genBtn').onclick = async () => {
    const f = $('#imgFile').files[0];
    if (!f) return toast('이미지를 선택하세요', true);
    const fd = new FormData();
    fd.append('image', f);
    fd.append('project_id', $('#newPid').value.trim());
    fd.append('segment', 'true');
    $('#genBtn').disabled = true;
    const r = await fetch('/api/tripo/generate', { method: 'POST', body: fd }).then(x => x.json());
    const pid = r.project_id;
    const tick = setInterval(async () => {
      const j = await fetch('/api/tripo/job/' + pid).then(x => x.json());
      $('#genStatus').innerHTML = j.stage === 'error'
        ? `<span style="color:var(--bad)">${j.error}</span>`
        : `<span class="spinner"></span>${j.stage} ${j.status} ${j.progress || 0}%`;
      if (j.stage === 'done') {
        clearInterval(tick);
        toast('생성 완료. 새 프로젝트로 이동합니다.');
        location.search = '?p=' + pid;
      }
      if (j.stage === 'error') { clearInterval(tick); $('#genBtn').disabled = false; }
    }, 2500);
  };
}

function stepScale(el) {
  const cal = S.state.calibration, lm = S.state.landmarks || {};
  el.innerHTML = `
    <p class="muted"><b>사이즈 260 은 외부 길이가 아닙니다.</b> 브랜드·모델별 실제
    외부 outsole 앞코–뒤꿈치 길이(mm)를 넣어야 metric scale 이 맞습니다.</p>
    <label>표시 사이즈</label>
    <input type="number" id="sizeLabel" value="${S.state.scenario.reference_size_label}">
    <label>측정 유형</label>
    <select id="mtype"><option>External outsole toe-to-heel</option></select>
    <label>실제 외부 길이 (mm)</label>
    <input type="number" id="targetLen" value="${cal?.target_length_mm || 300}" step="1">
    <div class="row">
      <button class="btn" id="proposeBtn">landmark 자동 제안</button>
      <button class="btn primary" id="calBtn">이 길이로 확정</button>
    </div>
    ${cal ? `<h4>결과</h4><dl class="kv">
      <dt>raw 길이</dt><dd>${fmt(cal.raw_length, 4)} model unit</dd>
      <dt>scale s</dt><dd>${fmt(cal.scale, 4)} mm/unit</dd>
      <dt>면적 계수 s²</dt><dd>${fmt(cal.area_scale, 1)}</dd>
      <dt>부피 계수 s³</dt><dd>${fmt(cal.volume_scale, 1)}</dd>
      <dt>신뢰도</dt><dd>${cal.confidence} ${cal.confidence === 'B' ? '(길이만 입력)' : ''}</dd>
      <dt>확정</dt><dd>${cal.confirmed ? '예' : '아니오'}</dd>
    </dl>` : ''}
    <div class="note">길이만 입력하면 uniform scale 을 씁니다. 폭·높이를 AI로 임의 보정해
      비균일 scale 하지 않습니다.</div>`;

  $('#proposeBtn').onclick = async () => {
    await api(`/project/${S.pid}/landmarks`);
    await reload();
    toast('앞코·뒤꿈치 후보를 표시했습니다. 3D에서 확인하세요.');
  };
  $('#calBtn').onclick = async () => {
    await api(`/project/${S.pid}/calibrate`, {
      target_length_mm: Number($('#targetLen').value),
      toe: lm.toe, heel: lm.heel, confirmed: true,
    });
    await reload();
    toast('metric scale 을 확정했습니다.');
  };
  renderLandmarkBar();
}

function renderLandmarkBar() {
  const lm = S.state?.landmarks;
  const bar = $('#landmarkBar');
  if (!lm) { bar.innerHTML = '<span class="muted">landmark 미설정</span>'; return; }
  bar.innerHTML = `
    <span class="tag ${lm.confirmed ? 'ok' : 'warn'}">${lm.confirmed ? '확정' : 'AI 제안'}</span>
    <span class="muted">raw 길이 ${fmt(lm.raw_length, 4)}</span>
    <button class="btn" id="mvToe" style="padding:3px 9px;font-size:11.5px">앞코 옮기기</button>
    <button class="btn" id="mvHeel" style="padding:3px 9px;font-size:11.5px">뒤꿈치 옮기기</button>`;
  $('#mvToe').onclick = () => { viewer.landmarkMode = 'toe'; toast('3D에서 앞코 지점을 클릭하세요'); };
  $('#mvHeel').onclick = () => { viewer.landmarkMode = 'heel'; toast('3D에서 뒤꿈치 지점을 클릭하세요'); };
  viewer.setLandmarks(lm.toe, lm.heel);
}

function stepSegment(el) {
  const mapping = S.state.mapping || [];
  const parts = S.catalog.signature_parts.concat(
    S.catalog.canonical_parts.filter(p => !S.catalog.signature_parts.includes(p)));
  el.innerHTML = `
    <p class="muted">세그멘테이션 결과는 BOM 이 아닙니다. 기하 특징으로 canonical part 를
    <b>제안</b>할 뿐이며, 확정 전에는 원가 계산에 쓰지 않습니다.</p>
    <div class="row">
      <button class="btn" id="segPropose">다시 제안</button>
      <button class="btn primary" id="segConfirm">전부 확정</button>
      <button class="btn" id="repairBtn">부피 막힌 파트 복구</button>
    </div>
    <table style="margin-top:12px"><thead><tr>
      <th>세그먼트</th><th>Canonical Part</th><th class="num">면적%</th>
      <th class="num">신뢰</th><th>Mesh QA</th></tr></thead><tbody>
    ${mapping.map(m => {
      const rep = S.state.repairs?.[m.segment_id];
      const qa = m.qa?.is_volume ? '<span class="tag ok">watertight</span>'
        : rep?.ok ? `<span class="tag warn">복구 ${fmt(rep.volume_cm3, 1)}cm³</span>`
        : '<span class="tag bad">열린 메시</span>';
      return `<tr data-seg="${m.segment_id}" class="${S.selected === m.segment_id ? 'sel' : ''}">
        <td>${m.segment_id}</td>
        <td><select data-seg="${m.segment_id}" class="cpSel">
          ${parts.map(p => `<option ${p === m.canonical_part ? 'selected' : ''}>${p}</option>`).join('')}
        </select></td>
        <td class="num">${(m.features.area_share * 100).toFixed(1)}</td>
        <td class="num"><span class="tag ${m.confirmed ? 'ok' : m.confidence >= .75 ? '' : 'warn'}">${fmt(m.confidence, 2)}</span></td>
        <td>${qa}</td></tr>`;
    }).join('')}</tbody></table>`;

  el.querySelectorAll('tbody tr').forEach(tr => tr.onclick = e => {
    if (e.target.tagName === 'SELECT') return;
    S.selected = tr.dataset.seg; viewer.select(S.selected); renderEvidence(); render();
  });
  el.querySelectorAll('.cpSel').forEach(s => s.onchange = async () => {
    await api(`/project/${S.pid}/segment/confirm`,
      { overrides: { [s.dataset.seg]: s.value } });
    await reload();
    toast(`${s.dataset.seg} → ${s.value} 로 확정`);
  });
  $('#segPropose').onclick = async () => {
    await api(`/project/${S.pid}/segment/propose`); await reload();
  };
  $('#segConfirm').onclick = async () => {
    await api(`/project/${S.pid}/segment/confirm`, { confirm_all: true });
    await reload(); toast('매핑을 확정했습니다.');
  };
  $('#repairBtn').onclick = async () => {
    toast('복구 중… 수십 초 걸립니다');
    await api(`/project/${S.pid}/repair`, {});
    await api(`/project/${S.pid}/bom`, {});
    await reload(); toast('복구 완료. 부피가 열린 파트를 닫았습니다.');
  };
}

function bomTable(lines, showCost) {
  return `<table><thead><tr>
    <th>파트</th><th>출처</th><th class="num">소요량</th><th>UOM</th>
    ${showCost ? '<th class="num">P50 $</th>' : '<th>소재</th>'}
    </tr></thead><tbody>
    ${lines.map(l => {
      const c = l.consumption || {};
      const blocked = (l.blocked || []).length;
      return `<tr data-line="${l.line_id}" class="${S.selected === l.line_id ? 'sel' : ''}">
        <td>${l.canonical_part}
          ${l.visibility?.startsWith('Hidden') ? '<span class="tag hidden">Hidden</span>' : ''}</td>
        <td>${l.origin === 'construction_rule'
          ? `<span class="tag rule">${l.rule_id}</span>` : '<span class="tag">측정</span>'}</td>
        <td class="num">${c.gross_qty == null ? '<span class="blocked-cell">차단</span>' : fmt(c.gross_qty, 5)}</td>
        <td>${c.uom || '—'}</td>
        ${showCost
          ? `<td class="num">${l.cost_p50 == null ? '<span class="blocked-cell">—</span>' : fmt(l.cost_p50, 4)}</td>`
          : `<td>${l.material_spec || '<span class="tag bad">미배정</span>'}</td>`}
      </tr>`;
    }).join('')}</tbody></table>`;
}

function wireBomRows(el) {
  el.querySelectorAll('tbody tr').forEach(tr => tr.onclick = () => {
    S.selected = tr.dataset.line;
    const l = (S.cost?.lines || []).find(x => x.line_id === S.selected);
    if (l?.segments?.length) viewer.select(l.segments[0]);
    renderEvidence(); render();
  });
}

function stepBom(el) {
  const bom = S.state.bom || [];
  const hidden = bom.filter(l => l.origin === 'construction_rule').length;
  el.innerHTML = `
    <p class="muted">보이는 파트는 세그멘테이션에서, 숨은 파트는 워크북
    <code>14_ConstructionRecipe</code> 규칙에서 나옵니다. LLM 문장을 실행하지 않습니다.</p>
    <dl class="kv"><dt>총 라인</dt><dd>${bom.length}</dd>
      <dt>측정 유래</dt><dd>${bom.length - hidden}</dd>
      <dt>규칙 유래(Hidden)</dt><dd>${hidden}</dd></dl>
    <div class="row"><button class="btn" id="rebuild">BOM 다시 생성</button></div>
    ${bomTable(S.cost?.lines || bom, false)}`;
  wireBomRows(el);
  $('#rebuild').onclick = async () => {
    await api(`/project/${S.pid}/bom`, {}); await reload(); toast('BOM 재생성');
  };
}

function stepConsumption(el) {
  const lines = S.cost?.lines || [];
  const assumed = lines.reduce((n, l) => n + (l.assumptions?.length || 0), 0);
  el.innerHTML = `
    <p class="muted">순 지오메트리는 구매 수량이 아닙니다. 패턴 여유·네스팅 수율·공정 수율을
    거쳐야 실제 구매량이 됩니다. 3D 는 한 짝이므로 켤레로 ×2 합니다.</p>
    <dl class="kv"><dt>가정 파라미터</dt><dd>${assumed}건</dd>
      <dt>차단 라인</dt><dd>${lines.filter(l => l.status === 'blocked').length} / ${lines.length}</dd></dl>
    ${bomTable(lines, false)}`;
  wireBomRows(el);
}

function stepPricing(el) {
  const lines = S.cost?.lines || [];
  const byBasis = {};
  lines.forEach(l => { const b = l.price?.basis || '—'; byBasis[b] = (byBasis[b] || 0) + 1; });
  el.innerHTML = `
    <p class="muted">공개 리스팅 가격은 자동으로 Engineering 단가로 승격되지 않습니다.
    새 분기에 데이터가 없으면 stale 로 이관하고 C2 를 막습니다.</p>
    <label>분기</label>
    <select id="qtr">${S.catalog.quarters.map(q =>
      `<option ${q === S.state.scenario.quarter ? 'selected' : ''}>${q}</option>`).join('')}</select>
    <h4>가격 근거 분포</h4>
    <dl class="kv">${Object.entries(byBasis).map(([k, v]) => `<dt>${k}</dt><dd>${v}건</dd>`).join('')}</dl>
    <div class="row"><button class="btn" id="snapBtn">2026Q4 스냅샷 시뮬레이션</button></div>
    <div id="snapOut"></div>
    <table style="margin-top:12px"><thead><tr><th>소재</th><th class="num">P50</th>
      <th>UOM</th><th>자격</th><th>신뢰</th></tr></thead><tbody>
      ${[...new Map(lines.filter(l => l.material_spec)
        .map(l => [l.material_spec, l])).values()].map(l => `<tr data-line="${l.line_id}">
        <td>${l.material_spec}</td><td class="num">${fmt(l.price?.p50, 3)}</td>
        <td>${l.price?.uom || '—'}</td>
        <td><span class="tag ${l.price?.eligibility === 'Engineering' ? 'ok' : 'warn'}">${l.price?.eligibility || '—'}</span></td>
        <td>${l.price?.confidence || '—'}${l.price?.stale ? ' <span class="tag bad">stale</span>' : ''}</td>
      </tr>`).join('')}</tbody></table>`;
  wireBomRows(el);
  $('#qtr').onchange = async e => {
    await api(`/project/${S.pid}/scenario`, { quarter: e.target.value });
    await api(`/project/${S.pid}/cost`); await reload();
  };
  $('#snapBtn').onclick = async () => {
    const r = await api('/prices/snapshot', { quarter: '2026Q4' });
    $('#snapOut').innerHTML = `<div class="note">2026Q4 에 신규 관측이 없다고 가정:
      fresh ${r.fresh}건, <b>stale 이관 ${r.stale}건</b> → 전부 Concept only / 신뢰도 D 로
      떨어지고 C2 계산이 차단됩니다.</div>`;
  };
}

function stepCost(el) {
  if (!S.cost) {
    el.innerHTML = `<div class="row"><button class="btn primary" id="calc">원가 계산</button></div>`;
    $('#calc').onclick = async () => { await api(`/project/${S.pid}/cost`); await reload(); };
    return;
  }
  const r = S.cost.rollup, g = S.cost.grade, sc = S.cost.scenario;
  const row = (label, o, cls = '') => `<tr class="${cls}"><td>${label}</td>
    <td class="num">${usd(o.p10)}</td><td class="num">${usd(o.p50)}</td>
    <td class="num">${usd(o.p90)}</td></tr>`;

  el.innerHTML = `
    <div class="grid2">
      <div><label>주문 수량 (pairs)</label>
        <input type="number" id="oq" value="${sc.order_quantity}"></div>
      <div><label>공급사 마진 %</label>
        <input type="number" id="sm" value="${sc.supplier_margin_pct}" step="0.5"></div>
    </div>
    <table class="buckets"><thead><tr><th>버킷</th><th class="num">P10</th>
      <th class="num">P50</th><th class="num">P90</th></tr></thead><tbody>
      ${r.buckets.map(b => b.p50 == null
        ? `<tr><td>${b.bucket}<div class="muted" style="font-size:11px">${b.coverage}</div></td>
           <td class="num blocked-cell" colspan="3">Blocked — ${b.note || b.coverage}</td></tr>`
        : row(b.bucket, b)).join('')}
      ${row('Direct Subtotal', r.direct_subtotal)}
      ${row('Reject Allowance', r.reject_allowance)}
      ${row('Factory Overhead', r.factory_overhead)}
      ${row('Manufacturing Should-Cost', r.manufacturing_should_cost)}
      ${row('Supplier Margin', r.supplier_margin)}
      ${row('Provisional Total', r.provisional_total, 'total-row')}
    </tbody></table>

    <div class="note ${r.fob_status === 'Calculated' ? 'ok' : 'bad'}">
      <b>${r.fob_status}</b> — 노무·기계·금형 입력이 없으면 0 으로 감추지 않고 차단합니다.
      ${r.blocked.length ? `<br>차단 ${r.blocked.length}건: ${r.blocked.slice(0, 3).join('; ')}…` : ''}
    </div>

    <h4>등급 ${g.class}</h4>
    ${['C1', 'C2'].map(c => g.blocked_reasons[c].length ? `
      <div class="note"><b>${c} 미충족</b><ul style="margin:4px 0 0 16px;padding:0">
        ${g.blocked_reasons[c].map(x => `<li>${x}</li>`).join('')}</ul></div>` : '').join('')}

    <h4>원가 라인</h4>
    ${bomTable(S.cost.lines.slice().sort((a, b) => (b.cost_p50 || 0) - (a.cost_p50 || 0)), true)}`;

  wireBomRows(el);
  const push = async () => {
    await api(`/project/${S.pid}/scenario`, {
      order_quantity: Number($('#oq').value),
      supplier_margin_pct: Number($('#sm').value),
    });
    await api(`/project/${S.pid}/cost`); await reload();
  };
  $('#oq').onchange = push; $('#sm').onchange = push;
}

// ── 근거 패널 ──────────────────────────────────────────────────────────
function renderEvidence() {
  const el = $('#evidence');
  if (!S.selected) { el.innerHTML = '<p class="muted">BOM 행이나 파트를 선택하세요.</p>'; return; }

  const line = (S.cost?.lines || S.state.bom || []).find(
    l => l.line_id === S.selected || (l.segments || []).includes(S.selected));
  const seg = (S.state.mapping || []).find(m => m.segment_id === S.selected);

  let h = '';
  if (seg) {
    const f = seg.features, rep = S.state.repairs?.[seg.segment_id];
    h += `<div class="ev-h">${seg.canonical_part}</div>
      <div class="ev-sub">${seg.segment_id} · ${seg.status}</div>
      <dl class="kv">
        <dt>신뢰도</dt><dd>${fmt(seg.confidence, 2)} (점수 ${fmt(seg.score, 2)}, 2위 격차 ${fmt(seg.margin, 2)})</dd>
        <dt>면적 비중</dt><dd>${(f.area_share * 100).toFixed(1)}%</dd>
        <dt>길이 위치</dt><dd>${fmt(f.len_lo, 2)} – ${fmt(f.len_hi, 2)} (0=뒤꿈치)</dd>
        <dt>높이 위치</dt><dd>${fmt(f.hgt_lo, 2)} – ${fmt(f.hgt_hi, 2)} (0=접지면)</dd>
        <dt>폭 비중</dt><dd>${fmt(f.wid_span, 2)}</dd>
        <dt>watertight</dt><dd>${seg.qa?.watertight ? '예' : '아니오'}</dd>
        <dt>면 수</dt><dd>${seg.qa?.faces?.toLocaleString?.() || '—'}</dd>
      </dl>
      <h4>대안 후보</h4>
      <div class="steps">${(seg.alternatives || []).map(a =>
        `${a.canonical_part.padEnd(24, ' ')} ${fmt(a.score, 2)}`).join('\n')}</div>`;
    if (rep?.ok) h += `<div class="note"><b>복구본 사용</b><br>${rep.method}<br>
      부피 ${fmt(rep.volume_cm3, 1)} cm³<br>${rep.note}</div>`;
    else if (seg.qa && !seg.qa.is_volume) h += `<div class="note bad">
      부피 계산 차단: ${(seg.qa.blocked_reasons || []).join('; ')}</div>`;
  }

  if (line) {
    const c = line.consumption || {}, p = line.price || {};
    h += `${seg ? '<hr style="border:0;border-top:1px solid var(--line);margin:16px 0">' : ''}
      <div class="ev-h">${line.canonical_part}</div>
      <div class="ev-sub">${line.line_id} · ${line.assembly} ·
        ${line.origin === 'construction_rule' ? `규칙 ${line.rule_id}` : '측정'}</div>`;

    if (line.rule_condition) h += `<div class="note">
      <b>규칙 ${line.rule_id}</b><br>조건: <code>${line.rule_condition}</code><br>
      파라미터: <code>${JSON.stringify(line.rule_parameters)}</code><br>
      근거: ${line.rule_evidence || '—'} · 승인: ${line.approval_role || '—'}</div>`;

    if (c.steps?.length) h += `<h4>소요량 계산</h4><div class="steps">${c.steps.join('\n')}</div>`;

    if (p.p50 != null) h += `<h4>단가</h4><dl class="kv">
      <dt>P10/P50/P90</dt><dd>${fmt(p.p10, 3)} / ${fmt(p.p50, 3)} / ${fmt(p.p90, 3)} ${p.currency || ''}/${p.uom || ''}</dd>
      <dt>근거</dt><dd>${p.basis}</dd>
      <dt>자격</dt><dd>${p.eligibility} (최대 ${p.max_class || '—'})</dd>
      <dt>신뢰도</dt><dd>${p.confidence}${p.stale ? ' · stale' : ''}</dd>
      ${p.source_url ? `<dt>출처</dt><dd><a href="${p.source_url}" target="_blank" rel="noopener">링크</a></dd>` : ''}
    </dl>${p.note ? `<div class="muted" style="font-size:11.5px">${p.note}</div>` : ''}`;

    if (line.cost_p50 != null) h += `<h4>원가</h4><div class="steps">${
      fmt(c.gross_qty, 6)} ${c.uom} × $${fmt(p.p50, 3)} = $${fmt(line.cost_p50, 4)}</div>`;

    if (line.assumptions?.length) h += `<h4>가정</h4>` + line.assumptions.map(a =>
      `<div class="note"><b>${a.param} = ${a.value}</b> <span class="tag proxy">${a.source}</span>
       ${a.note ? `<br>${a.note}` : ''}</div>`).join('');

    if (line.blocked?.length) h += `<h4>차단</h4>` +
      line.blocked.map(b => `<div class="note bad">${b}</div>`).join('');
  }

  el.innerHTML = h || '<p class="muted">선택한 항목의 근거가 없습니다.</p>';
}

// ── 부팅 ──────────────────────────────────────────────────────────────
async function reload() {
  S.state = await api(`/project/${S.pid}`, null, 'GET');
  try { S.cost = await api(`/project/${S.pid}/cost`, null, 'GET'); } catch { S.cost = null; }
  render(); renderEvidence(); renderLandmarkBar();
}

(async function boot() {
  S.catalog = await api('/catalog', null, 'GET');
  $('#viewMode').querySelectorAll('button').forEach(b => b.onclick = () => {
    S.viewMode = b.dataset.mode;
    $('#viewMode').querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b));
    render();
  });
  try {
    await viewer.load(`/api/project/${S.pid}/model.glb`);
  } catch (e) {
    toast('3D 모델을 불러오지 못했습니다: ' + e.message, true);
  }
  await reload();
})();
