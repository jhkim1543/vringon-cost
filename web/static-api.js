// 배포 페이지용 연결 계층. app.js 보다 먼저 실행된다.
// 두 가지 모드를 스스로 판정한다.
//   실서버: window.__backendBase 가 있고 그 서버가 응답하면, 모든 /api/ 호출을
//           그 주소로 보낸다 (업로드·생성·재계산 전부 동작).
//   정적:   백엔드가 없으면 미리 구워둔 data/ 결과를 읽기 전용으로 보여준다.
// 로컬 서버(run.cmd)에서는 둘 다 아니므로 아무 일도 하지 않는다.
(function () {
  const REAL = window.fetch.bind(window);
  const BASE = (window.__backendBase || '').replace(/\/+$/, '');

  let mode = 'none'; // 'live' | 'static' | 'none'
  let manifest = null;

  const probeLive = () => {
    if (!BASE) return Promise.resolve(false);
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 6000);
    return REAL(BASE + '/api/catalog', { signal: ctl.signal })
      .then(r => { clearTimeout(t); return r.ok; })
      .catch(() => { clearTimeout(t); return false; });
  };

  // 정적 빌드는 index.html 에 표시를 남긴다. 표시가 없는데 파일을 찔러보면
  // 실서버에서 늘 404 가 찍혀, 콘솔에 없는 오류가 있는 것처럼 보인다.
  const probeStatic = () => (window.__staticBuild
    ? REAL('data/index.json', { cache: 'no-store' })
        .then(r => r.ok ? r.json() : null)
        .catch(() => null)
    : Promise.resolve(null));

  const ready = probeLive().then(live => {
    if (live) {
      mode = 'live';
      window.__apiBase = BASE;
      window.__staticDemo = false;
      return null;
    }
    return probeStatic().then(j => {
      manifest = j;
      if (j) { mode = 'static'; window.__staticDemo = true; }
      return j;
    });
  });

  const json = obj => new Response(JSON.stringify(obj),
    { status: 200, headers: { 'Content-Type': 'application/json' } });

  const DISABLED = {
    error: '정적 배포본입니다. 이 동작은 백엔드가 필요합니다 ' +
           '로컬에서 run.cmd 로 실행하면 전부 동작합니다.',
  };

  window.fetch = async function (input, init) {
    const url = typeof input === 'string' ? input : input.url;
    await ready;
    if (!url || !url.includes('/api/')) return REAL(input, init);

    // 실서버: 상대 /api/ 경로만 백엔드로 돌린다 (쿼리 유지, 전 메서드 허용)
    if (mode === 'live') {
      if (/^https?:/i.test(url)) return REAL(input, init);
      const p = url.slice(url.indexOf('/api/'));
      return REAL(BASE + p, init);
    }
    if (mode !== 'static') return REAL(input, init);

    // 캐시 무효화용 쿼리(?v=...)를 떼고 경로만 본다
    const path = url.slice(url.indexOf('/api/') + 4).split('?')[0];
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

  // 하단 상태 배너
  ready.then(() => {
    if (mode === 'none') return;
    document.addEventListener('DOMContentLoaded', () => {
      const b = document.createElement('div');
      if (mode === 'live') {
        b.textContent = '실서버 연결됨 업로드와 3D 생성, 재계산이 전부 동작합니다.';
      } else {
        b.textContent = '정적 데모 미리 계산된 결과를 봅니다. ' +
          '3D 생성·재계산·매핑 확정은 로컬 실행(run.cmd)에서 동작합니다.';
      }
      b.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:60;' +
        'background:#0d1420;color:#9dbbd8;border-top:1px solid #26456b;' +
        'font-size:11.5px;padding:5px 14px;text-align:center';
      document.body.appendChild(b);
      document.querySelector('.layout').style.paddingBottom = '26px';
    });
  });
})();
