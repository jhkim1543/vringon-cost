// 워크플로 UI. 각 단계는 서버 상태(state.json)를 그대로 반영한다.
import { Viewer, PALETTE } from './viewer.js';

const $ = s => document.querySelector(s);
const PID = new URLSearchParams(location.search).get('p') || 'DEMO-RUN-001';

const S = {
  pid: PID, state: null, catalog: null, cost: null,
  step: 'design', selected: null, viewMode: 'parts',
};

const STEPS = [
  ['design', '디자인'], ['scale', '스케일'], ['segment', '세그먼트'],
  ['bom', 'BOM'], ['consumption', '소요량'], ['pricing', '단가'],
  ['cost', '원가·승인'],
];

const fmt = (v, d = 3) => v == null ? '없음' : Number(v).toLocaleString('en-US',
  { minimumFractionDigits: d, maximumFractionDigits: d });
const usd = v => v == null ? '없음' : '$' + fmt(v, 3);

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

// img 태그는 fetch 가로채기를 거치지 않으므로, 정적 모드에서는 직접 경로를,
// 실서버 연결 시(__apiBase)에는 백엔드 절대 경로를 쓴다.
const exImgUrl = name => window.__staticDemo ? 'data/ex_' + name
  : (window.__apiBase || '') + '/api/examples/' + name;
const projImgUrl = pid => window.__staticDemo ? 'data/' + pid + '.jpg'
  : `${window.__apiBase || ''}/api/project/${pid}/image`;

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
        color: m.qa?.is_volume ? 0x60a5fa : repaired ? 0x2563eb : 0x475569,
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
      if (v == null) { map[m.segment_id] = { color: 0x22262c, opacity: 0.35 }; return; }
      // 기여가 클수록 밝은 파랑. 색상은 하나만 쓰고 명도로 크기를 표현한다.
      const t = Math.sqrt(v / max);
      const r = Math.round(20 + 140 * t), g = Math.round(58 + 130 * t), b = Math.round(110 + 145 * t);
      map[m.segment_id] = { color: (r << 16) | (g << 8) | b, opacity: 0.95 };
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
    el.innerHTML = `<span><i style="background:#60a5fa"></i>닫힌 메시, 부피 사용 가능</span>
      <span><i style="background:#2563eb"></i>복구본, C1 한정</span>
      <span><i style="background:#475569"></i>열린 메시, 부피 차단</span>`;
  } else if (S.viewMode === 'cost') {
    el.innerHTML = `<span><i style="background:#143a6e"></i>낮은 기여</span>
      <span><i style="background:#a0c8ff"></i>높은 기여</span>
      <span><i style="background:#22262c"></i>원가 없음 또는 차단</span>`;
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
    cost: S.cost ? (S.cost.rollup.cost_status === 'COMPLETE' ? 'done' : 'blocked') : '',
  };
  $('#flow').innerHTML = STEPS.map(([k, label]) =>
    `<button data-step="${k}" class="${S.step === k ? 'on' : ''}">
       <span class="dot ${status[k] || ''}"></span>${label}</button>`).join('');
  $('#flow').querySelectorAll('button').forEach(b =>
    b.onclick = () => { S.step = b.dataset.step; render(); });

  const g = S.cost?.grade?.class;
  const gc = $('#gradeChip');
  gc.textContent = '등급 ' + (g || '확인중');
  gc.className = 'chip ' + (g === 'C2' ? 'ok' : g === 'C1' ? 'warn' : '');
  const cs = S.cost?.rollup?.cost_status;
  const fc = $('#fobChip');
  fc.textContent = cs === 'COMPLETE' ? '원가 산출됨'
    : cs === 'PARTIAL' ? '부분 원가, FOB 불가' : '원가 상태 확인중';
  fc.className = 'chip ' + (cs === 'COMPLETE' ? 'ok' : cs ? 'warn' : '');
}

// ── 위저드 내비게이션 ──────────────────────────────────────────────────
// 각 단계 하단에 이전/다음 버튼을 붙인다. '다음'은 그 단계의 확정 동작을
// 실행한 뒤 넘어간다. 탭 클릭 이동도 계속 가능하다.
const STEP_ORDER = STEPS.map(x => x[0]);

function wizardNav(el, opts = {}) {
  const i = STEP_ORDER.indexOf(S.step);
  const bar = document.createElement('div');
  bar.className = 'wizard-nav';
  bar.innerHTML = `
    ${opts.hint ? `<span class="wz-hint">${opts.hint}</span>` : ''}
    ${i > 0 ? `<button class="btn" id="wzPrev">이전</button>` : ''}
    ${opts.next === false || i >= STEP_ORDER.length - 1 ? '' :
      `<button class="btn primary" id="wzNext" ${opts.disabled ? 'disabled' : ''}>
         ${opts.nextLabel || '다음 단계로'}</button>`}`;
  el.appendChild(bar);
  const prev = bar.querySelector('#wzPrev');
  if (prev) prev.onclick = () => { S.step = STEP_ORDER[i - 1]; render(); };
  const next = bar.querySelector('#wzNext');
  if (next) next.onclick = async () => {
    next.disabled = true;
    try {
      if (opts.onNext) await opts.onNext();
      S.step = STEP_ORDER[i + 1];
      render();
    } catch (e) { next.disabled = false; }
  };
}

// ── 단계 본문 ──────────────────────────────────────────────────────────
function render() {
  renderFlow();
  renderLegend();
  viewer.applyColors(colorMap());
  const body = $('#stepBody');
  const title = { design: '1 디자인에서 3D', scale: '2 Metric Calibration',
    segment: '3 세그먼트를 Canonical Part 로', bom: '4 Manufacturing BOM',
    consumption: '5 소요량', pricing: '6 단가', cost: '7 원가와 승인' }[S.step];
  $('#stepTitle').textContent = title;
  ({ design: stepDesign, scale: stepScale, segment: stepSegment, bom: stepBom,
     consumption: stepConsumption, pricing: stepPricing, cost: stepCost }[S.step])(body);
}

function stepDesign(el) {
  const st = S.state.steps || {};
  const isStatic = !!window.__staticDemo;

  // 정적 배포본에서는 업로드가 원래 안 된다. 끝까지 가서 실패하게 두지 말고
  // 처음부터 무엇이 되는지, 어디서 되는지 알려준다.
  const uploadPanel = isStatic ? `
    <h4>내 디자인으로 실행</h4>
    <div class="note info"><b>이 공개 데모는 미리 계산된 결과를 보는 화면입니다.</b><br>
      새 이미지 업로드와 3D 생성은 로컬 실행에서만 동작합니다.<br><br>
      로컬 실행 방법<br>
      1. 저장소를 받고 <code>run.cmd</code> 실행<br>
      2. 브라우저에서 <code>127.0.0.1:5270</code> 접속<br>
      3. 이 화면에서 이미지를 올리면 3 에서 6분 뒤 결과가 나옵니다</div>` : `
    <h4>내 디자인으로 실행</h4>
    <div class="dropzone" id="dz">
      <div class="dz-icon">🖼</div>
      <div class="dz-main">신발 이미지를 여기에 끌어다 놓거나 클릭해서 선택</div>
      <div class="dz-sub">정측면(옆모습) 사진 한 장이면 됩니다</div>
      <div class="dz-req">
        <span>측면 뷰</span><span>배경 단색 권장</span>
        <span>신발 전체가 프레임 안에</span><span>긴 변 1024px 이상</span>
        <span>JPG PNG WEBP</span>
      </div>
    </div>
    <input type="file" id="imgFile" accept="image/jpeg,image/png,image/webp" style="display:none">
    <label>프로젝트 ID <span class="muted">(결과를 구분하는 이름표입니다)</span></label>
    <input type="text" id="newPid" value="RUN-${Date.now().toString().slice(-6)}">
    <div class="row">
      <button class="btn primary" id="genBtn" disabled>이미지를 먼저 선택하세요</button>
      <span class="muted" id="genStatus"></span>
    </div>
    <div class="note">실제 생성 엔진을 호출하며 크레딧이 소모됩니다.
      생성 30, 세그멘테이션 40 크레딧이고 3 에서 6분 걸립니다. 완료되면
      새 프로젝트 화면으로 자동 이동합니다.</div>`;

  el.innerHTML = `
    <p class="muted">이미지 한 장에서 3D 를 만들고 파트를 나눈 뒤,
    실측 길이로 보정해 소재 소요량과 원가까지 계산합니다.
    예시 디자인을 고르거나 이미지를 올려 시작하세요.</p>
    <h4>예시 디자인으로 시작</h4>
    <div class="ex-grid" id="exGrid"><span class="muted">불러오는 중</span></div>
    <dl class="kv">
      <dt>프로젝트</dt><dd>${S.pid}</dd>
      <dt>3D 생성</dt><dd>${st.generate3d?.status || '없음'}</dd>
      <dt>세그멘테이션</dt><dd>${st.segment3d?.status || '없음'}</dd>
      <dt>파트 수</dt><dd>${(S.state.mapping || []).length || '없음'}</dd>
    </dl>
    ${S.state.input_image ? `<h4>이 프로젝트의 입력 이미지</h4>
      <img src="${projImgUrl(S.pid)}" alt="입력 디자인"
        style="width:100%;border-radius:8px;border:1px solid var(--line);margin:4px 0 8px">` : ''}
    ${uploadPanel}`;

  // 예시 갤러리
  (async () => {
    try {
      const d = await api('/examples', null, 'GET');
      $('#exGrid').innerHTML = (d.examples || []).map(ex => `
        <div class="ex-card ${ex.project === S.pid || ex.alt?.project === S.pid ? 'on' : ''}
                    ${ex.ready ? '' : 'notready'}" data-p="${ex.ready ? ex.project : ''}">
          <img src="${exImgUrl(ex.image)}" alt="${ex.title}">
          <div class="ex-body">
            <div class="ex-title">${ex.title}</div>
            <div class="ex-desc">${ex.desc}${ex.ready ? '' : ' · 준비 중'}</div>
            ${ex.alt ? `<a class="ex-alt" data-p="${ex.alt.project}">${ex.alt.label}</a>` : ''}
          </div>
        </div>`).join('');
      $('#exGrid').querySelectorAll('[data-p]').forEach(n => n.onclick = e => {
        e.stopPropagation();
        const pid = n.dataset.p;
        if (!pid) return;
        if (pid === S.pid) { S.step = 'scale'; render(); return; }
        location.search = '?p=' + pid;
      });
    } catch (e) { $('#exGrid').innerHTML = '<span class="muted">예시 목록을 불러오지 못했습니다</span>'; }
  })();

  const hasModel = !!(S.state.steps || {}).generate3d || (S.state.mapping || []).length;
  wizardNav(el, {
    nextLabel: '이 디자인으로 시작 (스케일 입력)',
    hint: hasModel ? `현재 프로젝트: ${S.pid}` : '예시를 고르거나 이미지를 올리면 시작합니다',
    disabled: !hasModel,
  });

  if (isStatic) return;

  // ── 드래그 앤 드롭 + 미리보기 + 검증 ─────────────────────────────
  const dz = $('#dz'), fileInput = $('#imgFile'), btn = $('#genBtn');
  let picked = null;

  const accept = f => {
    if (!f) return;
    if (!/^image\/(jpeg|png|webp)$/.test(f.type)) {
      toast('JPG, PNG, WEBP 이미지만 받을 수 있습니다', true); return;
    }
    if (f.size > 20 * 1024 * 1024) {
      toast('20MB 이하 이미지를 사용하세요', true); return;
    }
    picked = f;
    const url = URL.createObjectURL(f);
    const img = new Image();
    img.onload = () => {
      const small = Math.max(img.width, img.height) < 512;
      dz.classList.add('haspic');
      dz.innerHTML = `<img src="${url}" alt="선택한 이미지">
        <div class="dz-meta"><b>${f.name}</b>
          <div class="dim">${img.width} x ${img.height}px ·
            ${(f.size / 1024 / 1024).toFixed(1)}MB · 클릭하면 다른 이미지로 바꿉니다</div>
          ${small ? '<div class="dim" style="color:var(--on-tint)">해상도가 낮습니다. 결과 품질이 떨어질 수 있습니다.</div>' : ''}
        </div>`;
      btn.disabled = false;
      btn.textContent = '3D 생성과 세그멘테이션 실행';
    };
    img.src = url;
  };

  dz.onclick = () => fileInput.click();
  fileInput.onchange = () => accept(fileInput.files[0]);
  ['dragenter', 'dragover'].forEach(ev => dz.addEventListener(ev, e => {
    e.preventDefault(); dz.classList.add('drag');
  }));
  ['dragleave', 'drop'].forEach(ev => dz.addEventListener(ev, e => {
    e.preventDefault(); dz.classList.remove('drag');
  }));
  dz.addEventListener('drop', e => accept(e.dataTransfer.files[0]));

  btn.onclick = async () => {
    if (!picked) return;
    const fd = new FormData();
    fd.append('image', picked);
    fd.append('project_id', $('#newPid').value.trim());
    fd.append('segment', 'true');
    btn.disabled = true;
    btn.textContent = '실행 중';
    const r = await fetch('/api/mesh/generate', { method: 'POST', body: fd }).then(x => x.json());
    const pid = r.project_id;
    const tick = setInterval(async () => {
      const j = await fetch('/api/mesh/job/' + pid).then(x => x.json());
      const label = { generate: '3D 생성', segment: '파트 분리', upload: '업로드' }[j.stage] || j.stage;
      $('#genStatus').innerHTML = j.stage === 'error'
        ? `<span style="color:var(--on-tint)">${j.error}</span>`
        : `<span class="spinner"></span>${label} ${j.progress || 0}%`;
      if (j.stage === 'done') {
        clearInterval(tick);
        toast('생성 완료. 새 프로젝트로 이동합니다.');
        location.search = '?p=' + pid;
      }
      if (j.stage === 'error') {
        clearInterval(tick); btn.disabled = false;
        btn.textContent = '3D 생성과 세그멘테이션 실행';
      }
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
    <p class="muted" style="margin-top:6px">참고: 러닝화 260 라벨의 외부 outsole 길이는
      브랜드에 따라 통상 285 에서 300mm 입니다. 정확한 값은 실물 실측이 필요하며,
      라벨 값 260 을 그대로 넣으면 모든 면적·부피가 작게 나옵니다.</p>
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
    </dl>
    ${cal.width_check && cal.width_check.verdict !== 'ok' ? `<div class="note">
      <b>폭 비율 경고</b> 폭/길이 = ${cal.width_check.width_over_length}
      (통상 ${cal.width_check.expected_range[0]} 에서 ${cal.width_check.expected_range[1]}).
      ${cal.width_check.note}</div>` : ''}` : ''}
    <div class="note">길이만 입력하면 uniform scale 을 씁니다. 폭·높이를 AI로 임의 보정해
      비균일 scale 하지 않습니다.</div>`;

  $('#proposeBtn').onclick = async () => {
    await api(`/project/${S.pid}/landmarks`);
    await reload();
    toast('앞코·뒤꿈치 후보를 표시했습니다. 3D에서 확인하세요.');
  };
  const doCalibrate = async () => {
    await api(`/project/${S.pid}/calibrate`, {
      target_length_mm: Number($('#targetLen').value),
      toe: lm.toe, heel: lm.heel, confirmed: true,
    });
    await reload();
    toast('실측 길이를 확정했습니다.');
  };
  $('#calBtn').onclick = doCalibrate;
  wizardNav(el, {
    nextLabel: '길이 확정하고 세그먼트로',
    hint: '3D 의 앞코·뒤꿈치 점을 확인한 뒤 넘어가세요',
    onNext: doCalibrate,
  });
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
  const sum = S.state.mapping_summary || {};
  el.innerHTML = `
    <p class="muted">세그멘테이션 결과는 BOM 이 아닙니다. 기하 특징으로 canonical part 를
    <b>제안</b>할 뿐이며, 확정 전에는 원가 계산에 쓰지 않습니다.</p>
    <div class="note info">
      ${S.state.segmentation_source?.kind === 'internal_model'
        ? `세그멘테이션 출처: <b>사내 신발 파트 모델</b> (display 파트 ${S.state.segmentation_source.display_parts_merged}개를 클래스로 병합, face 커버리지 ${((S.state.segmentation_source.face_coverage||0)*100).toFixed(0)}%)`
        : '세그멘테이션 출처: 기하 특징 추정 (모델 라벨 없음)'}<br>
      정답 데이터가 없으므로 아래는 <b>정확도가 아니라 배정 커버리지</b>입니다.
      <div class="kv" style="margin:8px 0 0">
        <dt>입력 세그먼트</dt><dd>${sum.input_segments ?? '없음'}</dd>
        <dt>배정됨</dt><dd>${sum.assigned_segments ?? '없음'}</dd>
        <dt>자동 채택</dt><dd>${sum.auto_accepted ?? '없음'}</dd>
        <dt>검토 필요</dt><dd>${sum.needs_review ?? '없음'} (${((sum.review_rate||0)*100).toFixed(0)}%)</dd>
        <dt>엔지니어 확정</dt><dd>${sum.engineer_confirmed ?? '없음'}</dd>
      </div></div>
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
      const qa = m.qa?.is_volume ? '<span class="tag ok">닫힌 메시</span>'
        : rep?.ok ? `<span class="tag warn">복구 ${fmt(rep.volume_cm3, 1)}cm³ ${rep.sensitivity?.verdict || ''}</span>`
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
    toast(`${s.dataset.seg}  다음  ${s.value} 로 확정`);
  });
  $('#segPropose').onclick = async () => {
    await api(`/project/${S.pid}/segment/propose`); await reload();
  };
  $('#segConfirm').onclick = async () => {
    await api(`/project/${S.pid}/segment/confirm`, { confirm_all: true });
    await reload(); toast('매핑을 확정했습니다.');
  };
  $('#repairBtn').onclick = async () => {
    toast('복구 중. 수십 초 걸립니다');
    await api(`/project/${S.pid}/repair`, {});
    await api(`/project/${S.pid}/bom`, {});
    await reload(); toast('복구 완료. 부피가 열린 파트를 닫았습니다.');
  };
  wizardNav(el, {
    nextLabel: '매핑 확정하고 BOM 으로',
    hint: '파트 이름이 틀리면 표에서 바꾼 뒤 확정하세요',
    onNext: async () => {
      await api(`/project/${S.pid}/segment/confirm`, { confirm_all: true });
      await api(`/project/${S.pid}/bom`, {});
      await api(`/project/${S.pid}/cost`);
      await reload();
    },
  });
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
        <td>${c.uom || ' '}</td>
        ${showCost
          ? `<td class="num">${l.cost_p50 == null ? '<span class="blocked-cell"> </span>' : fmt(l.cost_p50, 4)}</td>`
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
  wizardNav(el, { nextLabel: 'BOM 확인, 소요량으로' });
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
  wizardNav(el, { nextLabel: '소요량 확인, 단가로' });
}

function stepPricing(el) {
  const lines = S.cost?.lines || [];
  const byBasis = {};
  lines.forEach(l => { const b = l.price?.basis || ' '; byBasis[b] = (byBasis[b] || 0) + 1; });
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
      <th>UOM</th><th>자격</th><th>신뢰</th><th>출처</th></tr></thead><tbody>
      ${[...new Map(lines.filter(l => l.material_spec)
        .map(l => [l.material_spec, l])).values()].map(l => {
        const src = l.price?.source_url;
        let host = '';
        try { host = src ? new URL(src).hostname.replace(/^www\./, '') : ''; } catch (e) {}
        return `<tr data-line="${l.line_id}">
        <td>${l.material_spec}</td><td class="num">${fmt(l.price?.p50, 3)}</td>
        <td>${l.price?.uom || ' '}</td>
        <td><span class="tag ${l.price?.eligibility === 'Engineering' ? 'ok' : 'warn'}">${l.price?.eligibility || ' '}</span></td>
        <td>${l.price?.confidence || ' '}${l.price?.stale ? ' <span class="tag bad">stale</span>' : ''}</td>
        <td>${src ? `<a href="${src}" target="_blank" rel="noopener"
              onclick="event.stopPropagation()" title="${l.price?.price_basis || ''} · 워크북 16_분기기준단가">${host}</a>`
            : `<span class="muted">${l.price?.basis === 'price_proxy' ? '연관 소재 비율' : '워크북'}</span>`}</td>
      </tr>`; }).join('')}</tbody></table>
    <p class="muted" style="margin-top:8px">모든 단가는 워크북 16_분기기준단가 시트가 원본이며,
      출처 링크는 그 시트에 기록된 공개 리스팅·시장지수 페이지입니다. 근거 패널에서
      라인별 P10/P50/P90 과 자격도 확인할 수 있습니다.</p>`;
  wireBomRows(el);
  $('#qtr').onchange = async e => {
    await api(`/project/${S.pid}/scenario`, { quarter: e.target.value });
    await api(`/project/${S.pid}/cost`); await reload();
  };
  wizardNav(el, { nextLabel: '단가 확인, 원가로' });
  $('#snapBtn').onclick = async () => {
    const r = await api('/prices/snapshot', { quarter: '2026Q4' });
    $('#snapOut').innerHTML = `<div class="note">2026Q4 에 신규 관측이 없다고 가정:
      fresh ${r.fresh}건, <b>stale 이관 ${r.stale}건</b>  다음  전부 Concept only / 신뢰도 D 로
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
  if (!r.known_cost_subtotal) {
    el.innerHTML = `<div class="note bad">원가 결과가 예전 형식입니다. 다시 계산하세요.</div>
      <div class="row"><button class="btn primary" id="recalc">원가 재계산</button></div>`;
    $('#recalc').onclick = async () => { await api(`/project/${S.pid}/cost`); await reload(); };
    return;
  }
  const partial = r.cost_status !== 'COMPLETE';
  const cov = r.coverage || {};
  const mb = S.cost.mass_balance;
  const row = (label, o, cls = '') => o ? `<tr class="${cls}"><td>${label}</td>
    <td class="num">${usd(o.p10)}</td><td class="num">${usd(o.p50)}</td>
    <td class="num">${usd(o.p90)}</td></tr>` : '';

  el.innerHTML = `
    <div class="grid2">
      <div><label>주문 수량 (pairs)</label>
        <input type="number" id="oq" value="${sc.order_quantity}"></div>
      <div><label>공급사 마진 %</label>
        <input type="number" id="sm" value="${sc.supplier_margin_pct}" step="0.5"></div>
    </div>

    <div class="stat">
      <div class="lbl">표시 중인 값: 가격 확정된 BOM ${cov.priced_lines}/${cov.bom_lines}라인의 소재·부자재 소계 (켤레당)</div>
      <div class="big">${usd(r.known_cost_subtotal.p50)}</div>
      <div class="sub">P10 ${usd(r.known_cost_subtotal.p10)} · P90 ${usd(r.known_cost_subtotal.p90)}</div>
      ${r.material_breakdown ? `<div class="sub" style="margin-top:8px">${
        Object.entries(r.material_breakdown).map(([k, v]) =>
          `${({Upper:'어퍼',Sole:'솔',Chemical:'접착·화학',Packaging:'포장',Trim:'부자재'})[k] || k} ${usd(v.p50)}`
        ).join(' · ')}</div>` : ''}
      <div class="bar"><i style="width:${((cov.priced_ratio || 0) * 100).toFixed(0)}%"></i></div>
      <div class="sub">라인 가격 커버리지 ${((cov.priced_ratio||0)*100).toFixed(0)}% (전체 원가 커버리지와는 다릅니다. 노무·기계·금형 제외)</div>
    </div>

    <div class="stat unavail">
      <div class="lbl">전체 제조원가와 FOB</div>
      <div class="big">${partial ? '계산 불가' : usd(r.fob.p50)}</div>
      <div class="sub">${partial ? '차단: ' + r.blocked_buckets.join(', ')
        : 'Manufacturing Should Cost ' + usd(r.manufacturing_should_cost.p50)}</div>
    </div>

    ${partial ? `<div class="note bad"><b>부분 원가입니다.</b> 소재비 소계에
      간접비와 마진을 곱해 총액을 만들지 않습니다. 그 비율의 기준은 전체 제조비인데
      분자만 소재비면 숫자가 조용히 왜곡됩니다.<br><br>
      원가를 완성하려면 필요한 것<ul style="margin:6px 0 0 16px;padding:0">
      <li>공장 Routing 과 SAM</li><li>Loaded Labor Rate</li>
      <li>Machine Rate</li><li>Midsole, Outsole 금형 견적</li></ul></div>` : ''}

    ${(r.sanity_warnings || []).length ? `<div class="note">
      <b>부피 타당성 경고 ${r.sanity_warnings.length}건</b><br>
      ${r.sanity_warnings.join('<br>')}</div>` : ''}
    ${mb ? `<div class="note ${mb.verdict === 'ok' ? 'ok' : ''}">
      <b>질량 정합성</b> 완제품 ${(mb.finished_pair_mass_g ?? mb.known_mass_g).toFixed(0)} g
      / 구매 투입 ${(mb.purchased_input_mass_g ?? 0).toFixed(0)} g
      / 목표 ${mb.target_pair_g} g (${((mb.coverage || 0) * 100).toFixed(0)}%)
      판정 ${mb.verdict}<br>${mb.note}</div>` : ''}

    <table class="buckets"><thead><tr><th>버킷</th><th class="num">P10</th>
      <th class="num">P50</th><th class="num">P90</th></tr></thead><tbody>
      ${r.buckets.map(b => b.p50 == null
        ? `<tr><td>${b.bucket}<div class="muted" style="font-size:11px">${b.coverage}</div></td>
           <td class="num blocked-cell" colspan="3">차단. ${b.note || b.coverage}</td></tr>`
        : row(b.bucket, b)).join('')}
      ${row('확인된 소계', r.known_cost_subtotal, 'total-row')}
      ${row('Reject Allowance', r.reject_allowance)}
      ${row('Factory Overhead', r.factory_overhead)}
      ${row('Manufacturing Should Cost', r.manufacturing_should_cost)}
      ${row('Supplier Margin', r.supplier_margin)}
      ${row('FOB', r.fob, 'total-row')}
    </tbody></table>

    <div class="note ${partial ? '' : 'ok'}">
      <b>${r.fob_status}</b> 노무, 기계, 금형 입력이 없으면 0 으로 감추지 않고 차단합니다.
      ${r.blocked.length ? `<br>차단 ${r.blocked.length}건. ${r.blocked.slice(0, 3).join('; ')}` : ''}
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
        <dt>길이 위치</dt><dd>${fmt(f.len_lo, 2)} ${fmt(f.len_hi, 2)} (0=뒤꿈치)</dd>
        <dt>높이 위치</dt><dd>${fmt(f.hgt_lo, 2)} ${fmt(f.hgt_hi, 2)} (0=접지면)</dd>
        <dt>폭 비중</dt><dd>${fmt(f.wid_span, 2)}</dd>
        <dt>watertight</dt><dd>${seg.qa?.watertight ? '예' : '아니오'}</dd>
        <dt>면 수</dt><dd>${seg.qa?.faces?.toLocaleString?.() || ' '}</dd>
      </dl>
      <h4>대안 후보</h4>
      <div class="steps">${(seg.alternatives || []).map(a =>
        `${a.canonical_part.padEnd(24, ' ')} ${fmt(a.score, 2)}`).join('\n')}</div>`;
    if (rep?.ok) {
      const sn = rep.sensitivity || {};
      h += `<div class="note"><b>복구본 사용 (실측 아님)</b><br>${rep.method}<br>
        부피 ${fmt(rep.volume_cm3, 1)} cm³ · 상한 등급 ${rep.max_class || 'C1'}<br>
        ${sn.cv != null ? `해상도 민감도 CV ${(sn.cv * 100).toFixed(1)}% 판정 <b>${sn.verdict}</b><br>
          ${(sn.results || []).filter(x => x.volume).map(x => `pitch ${x.pitch_mm}mm`).join(' · ')}<br>` : ''}
        ${rep.note}</div>`;
    }
    else if (seg.qa && !seg.qa.is_volume) h += `<div class="note bad">
      부피 계산 차단: ${(seg.qa.blocked_reasons || []).join('; ')}</div>`;
  }

  if (line) {
    const c = line.consumption || {}, p = line.price || {};
    h += `${seg ? '<hr style="border:0;border-top:1px solid var(--line);margin:16px 0">' : ''}
      <div class="ev-h">${line.canonical_part}</div>
      <div class="ev-sub">${line.line_id} · ${line.assembly} ·
        ${line.origin === 'construction_rule' ? `규칙 ${line.rule_id}` : '측정'}
        ${line.geometry_role ? ` · <span class="tag ${line.max_class === 'C2' ? '' : 'proxy'}">${line.geometry_role} 최대 ${line.max_class || 'C1'}</span>` : ''}
        ${line.quantity_basis ? ` · ${line.quantity_basis}` : ''}</div>`;

    if (line.rule_condition) h += `<div class="note">
      <b>규칙 ${line.rule_id}</b><br>조건: <code>${line.rule_condition}</code><br>
      파라미터: <code>${JSON.stringify(line.rule_parameters)}</code><br>
      근거: ${line.rule_evidence || ' '} · 승인: ${line.approval_role || ' '}</div>`;

    if (c.steps?.length) h += `<h4>소요량 계산</h4><div class="steps">${c.steps.join('\n')}</div>`;

    if (p.p50 != null) h += `<h4>단가</h4><dl class="kv">
      <dt>P10/P50/P90</dt><dd>${fmt(p.p10, 3)} / ${fmt(p.p50, 3)} / ${fmt(p.p90, 3)} ${p.currency || ''}/${p.uom || ''}</dd>
      <dt>근거</dt><dd>${p.basis}</dd>
      <dt>자격</dt><dd>${p.eligibility} (최대 ${p.max_class || ' '})</dd>
      <dt>신뢰도</dt><dd>${p.confidence}${p.stale ? ' · stale' : ''}</dd>
      ${p.source_url ? `<dt>출처</dt><dd><a href="${p.source_url}" target="_blank" rel="noopener">링크</a></dd>` : ''}
    </dl>${p.note ? `<div class="muted" style="font-size:11.5px">${p.note}</div>` : ''}`;

    if (line.cost_p50 != null) h += `<h4>원가</h4><div class="steps">${
      fmt(c.gross_qty, 6)} ${c.uom} × $${fmt(p.p50, 3)} = $${fmt(line.cost_p50, 4)}</div>`;

    if (line.assumptions?.length) h += `<h4>가정</h4>` + line.assumptions.map(a =>
      `<div class="note"><b>${a.param} = ${a.value}</b> <span class="tag proxy">${a.source}</span>
       ${a.note ? `<br>${a.note}` : ''}</div>`).join('');

    if (line.warnings?.length) h += `<h4>타당성 경고</h4>` +
      line.warnings.map(w => `<div class="note">${w}</div>`).join('');

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

window.__vringon = { S, viewer };

async function renderProjectSwitcher() {
  try {
    const r = await api('/projects', null, 'GET');
    const pids = (r.projects || []).map(p => p.project_id).filter(Boolean);
    if (pids.length < 2) return;
    const host = document.querySelector('.topright');
    const sel = document.createElement('select');
    sel.style.cssText = 'width:auto;font-size:12px;padding:4px 8px';
    sel.innerHTML = pids.map(p =>
      `<option ${p === S.pid ? 'selected' : ''}>${p}</option>`).join('');
    sel.onchange = () => { location.search = '?p=' + sel.value; };
    host.prepend(sel);
  } catch (e) { /* 프로젝트 목록이 없으면 그냥 넘어간다 */ }
}

(async function boot() {
  renderProjectSwitcher();
  S.catalog = await api('/catalog', null, 'GET');
  $('#viewMode').querySelectorAll('button').forEach(b => b.onclick = () => {
    S.viewMode = b.dataset.mode;
    $('#viewMode').querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b));
    render();
  });
  // 상태를 먼저 받아 갱신 시각을 캐시 무효화 키로 쓴다. 이게 없으면
  // 지오메트리를 고쳐도 브라우저가 예전 GLB 를 계속 쓴다.
  await reload();
  const ver = encodeURIComponent(S.state?.updated_at || '0');
  try {
    await viewer.load(`/api/project/${S.pid}/model.glb?v=${ver}`);
  } catch (e) {
    toast('3D 모델을 불러오지 못했습니다: ' + e.message, true);
  }
  render();
})();
