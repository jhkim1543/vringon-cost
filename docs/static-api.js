// 정적 배포용 shim. app.js 보다 먼저 실행되어 fetch('/api/...') 를 가로챈다.
// 로컬 서버로 띄우면 data/index.json 이 없으므로 아무 일도 하지 않는다.
(function () {
 const REAL = window.fetch.bind(window);

 // 미리 구워둔 결과가 있는지로 정적 모드를 판정한다.
 // (경로 감지나 host 검사보다 확실하다 어느 base 에 올려도 동작한다)
 let manifest = null;
 const ready = REAL('data/index.json', { cache: 'no-store' })
    .then(r => r.ok ? r.json() : null)
    .then(j => { manifest = j; window.__staticDemo = !!j; return j; })
    .catch(() => null);

  const json = obj => new Response(JSON.stringify(obj),
    { status: 200, headers: { 'Content-Type': 'application/json' } });

  const DISABLED = {
    error: '정적 배포본입니다. 이 동작은 백엔드가 필요합니다 ' +
           '로컬에서 run.cmd 로 실행하면 전부 동작합니다.',
  };

  window.fetch = async function (input, init) {
    const url = typeof input === 'string' ? input : input.url;
    await ready;
    if (!manifest || !url || !url.includes('/api/')) return REAL(input, init);

    const path = url.slice(url.indexOf('/api/') + 4);
    const method = (init?.method || 'GET').toUpperCase();

 // 쓰기 동작은 전부 막는다. 조용히 실패하면 화면이 거짓말을 한다.
 if (method !== 'GET') return json(DISABLED);

    if (path === '/catalog') return REAL('data/catalog.json');

    if (path === '/examples') return REAL('data/examples.json');
    const exm = path.match(/^\/examples\/(.+)$/);
    if (exm) return REAL('data/ex_' + exm[1]);

    const m = path.match(/^\/project\/([^/]+)(\/.*)?$/);
    if (m) {
      const pid = m[1], sub = m[2] || '';
      if (sub === '/model.glb') return REAL(`data/${pid}.glb`);
      if (sub === '/cost') {
        const r = await REAL(`data/cost_${pid}.json`);
        return r.ok ? r : json({ error: '계산 결과 없음' });
      }
      if (sub === '/image') {
        for (const ext of ['jpg', 'jpeg', 'png', 'webp']) {
          const r = await REAL(`data/${pid}.${ext}`);
          if (r.ok) return r;
        }
        return new Response('', { status: 404 });
      }
      if (!sub) return REAL(`data/project_${pid}.json`);
    }

    if (path === '/projects') {
 return json({ projects: (manifest.projects || []).map(p => ({ project_id: p })) });
 }
 return json(DISABLED);
 };

 // 정적 모드 배너
 ready.then(j => {
 if (!j) return;
 document.addEventListener('DOMContentLoaded', () => {
      const b = document.createElement('div');
      b.textContent = '정적 데모 미리 계산된 결과를 봅니다. ' +
        '3D 생성·재계산·매핑 확정은 로컬 실행(run.cmd)에서 동작합니다.';
      b.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:60;' +
        'background:#0d1420;color:#9dbbd8;border-top:1px solid #26456b;' +
        'font-size:11.5px;padding:5px 14px;text-align:center';
      document.body.appendChild(b);
      document.querySelector('.layout').style.paddingBottom = '26px';
    });
  });
})();
