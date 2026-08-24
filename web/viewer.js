// 3D 뷰어: 파트 하이라이트 + toe/heel landmark 편집.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

// 단일 색상 축: 파랑. 파트 구분은 색상이 아니라 명도로 한다.
const PALETTE = [
  0x1d4ed8, 0x2563eb, 0x3b82f6, 0x60a5fa, 0x93c5fd,
  0x1e40af, 0x38699e, 0x5b8fc7, 0x7fb0e0, 0xa9cbee,
];

export class Viewer {
  constructor(el) {
    this.el = el;
    this.parts = new Map();          // name -> Mesh
    this.mode = 'parts';
    this.onPick = null;
    this.selected = null;
    this.landmarkMode = null;        // 'toe' | 'heel' | null
    this.onLandmark = null;
    this._init();
  }

  _init() {
    const w = this.el.clientWidth || 600, h = this.el.clientHeight || 400;
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x0e0e13);

    this.camera = new THREE.PerspectiveCamera(38, w / h, 0.01, 200);
    this.camera.position.set(2.2, 1.4, 2.2);

    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.setSize(w, h);
    this.el.appendChild(this.renderer.domElement);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;

    this.scene.add(new THREE.HemisphereLight(0xe4e6f5, 0x16161d, 1.6));
    const key = new THREE.DirectionalLight(0xffffff, 1.5);
    key.position.set(3, 5, 2);
    this.scene.add(key);
    const rim = new THREE.DirectionalLight(0x8ab4e8, 0.8);
    rim.position.set(-3, 2, -3);
    this.scene.add(rim);

    this.group = new THREE.Group();
    this.scene.add(this.group);
    this.marks = new THREE.Group();
    this.scene.add(this.marks);

    this.ray = new THREE.Raycaster();
    this.ptr = new THREE.Vector2();
    this.renderer.domElement.addEventListener('pointerdown', e => this._down(e));
    this.renderer.domElement.addEventListener('pointerup', e => this._up(e));

    new ResizeObserver(() => this._resize()).observe(this.el);
    this._loop();
  }

  _resize() {
    const w = this.el.clientWidth, h = this.el.clientHeight;
    if (!w || !h) return;
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h);
  }

  _loop() {
    requestAnimationFrame(() => this._loop());
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }

  _down(e) { this._dx = e.clientX; this._dy = e.clientY; }

  _up(e) {
    // 드래그로 회전한 경우는 선택으로 치지 않는다.
    if (Math.hypot(e.clientX - this._dx, e.clientY - this._dy) > 4) return;
    const r = this.renderer.domElement.getBoundingClientRect();
    this.ptr.x = ((e.clientX - r.left) / r.width) * 2 - 1;
    this.ptr.y = -((e.clientY - r.top) / r.height) * 2 + 1;
    this.ray.setFromCamera(this.ptr, this.camera);
    const hit = this.ray.intersectObjects([...this.parts.values()], false)[0];
    if (!hit) return;

    if (this.landmarkMode) {
      const p = hit.point.clone();
      // 월드 -> 원본 모델 좌표 (그룹 변환 되돌리기)
      const local = this.group.worldToLocal(p.clone());
      this.onLandmark?.(this.landmarkMode, [local.x, local.y, local.z]);
      this.landmarkMode = null;
      return;
    }
    this.select(hit.object.name);
    this.onPick?.(hit.object.name);
  }

  async load(url) {
    const gltf = await new GLTFLoader().loadAsync(url);
    this.group.clear();
    this.parts.clear();

    const root = new THREE.Group();
    gltf.scene.traverse(o => {
      if (!o.isMesh) return;
      const name = o.name || `part_${this.parts.size}`;
      // 경량화한 GLB 에는 법선이 없을 수 있다. 그대로 두면 조명을 못 받아
      // 새까맣게 렌더된다.
      if (!o.geometry.getAttribute('normal')) o.geometry.computeVertexNormals();
      const mesh = new THREE.Mesh(o.geometry, null);
      mesh.name = name;
      o.updateWorldMatrix(true, false);
      mesh.applyMatrix4(o.matrixWorld);
      root.add(mesh);
      this.parts.set(name, mesh);
    });
    this.group.add(root);

    // 원점 중심으로 맞추고 1 단위 크기로 정규화 (원본 좌표는 group 변환으로 복원 가능)
    const box = new THREE.Box3().setFromObject(root);
    const size = box.getSize(new THREE.Vector3());
    const ctr = box.getCenter(new THREE.Vector3());
    const s = 1.6 / Math.max(size.x, size.y, size.z);
    root.position.sub(ctr);
    this.group.scale.setScalar(s);
    this._modelCenter = ctr;
    this._modelScale = s;

    this.applyColors({});
    this.controls.target.set(0, 0, 0);
    this.camera.position.set(1.9, 1.15, 1.9);
    // 스크린샷 자동화가 이 시점을 기다린다.
    window.dispatchEvent(new CustomEvent('vringon-viewer-ready',
      { detail: { parts: this.parts.size } }));
    return [...this.parts.keys()];
  }

  /** colorBy: {segment_id: {color, opacity, label}} */
  applyColors(map) {
    let i = 0;
    for (const [name, mesh] of this.parts) {
      const cfg = map[name];
      const color = cfg?.color ?? PALETTE[i % PALETTE.length];
      const opacity = cfg?.opacity ?? 1;
      mesh.material?.dispose?.();
      mesh.material = new THREE.MeshStandardMaterial({
        color, roughness: 0.72, metalness: 0.02,
        transparent: opacity < 1, opacity,
        depthWrite: opacity > 0.6,
      });
      i++;
    }
    if (this.selected) this.select(this.selected);
  }

  select(name) {
    this.selected = name;
    for (const [n, mesh] of this.parts) {
      const on = n === name;
      mesh.material.emissive?.setHex(on ? 0x123253 : 0x000000);
      if (mesh.material.emissiveIntensity !== undefined)
        mesh.material.emissiveIntensity = on ? 1 : 0;
    }
  }

  isolate(names) {
    const keep = names ? new Set(names) : null;
    for (const [n, mesh] of this.parts) {
      mesh.visible = !keep || keep.has(n);
    }
  }

  /** 원본 모델 좌표를 뷰어 월드로 옮겨 landmark 구를 그린다. */
  setLandmarks(toe, heel) {
    this.marks.clear();
    const mk = (p, hex) => {
      if (!p) return;
      const local = new THREE.Vector3(p[0], p[1], p[2]).sub(this._modelCenter);
      const world = local.multiplyScalar(this._modelScale);
      const s = new THREE.Mesh(
        new THREE.SphereGeometry(0.028, 20, 14),
        new THREE.MeshBasicMaterial({ color: hex }));
      s.position.copy(world);
      this.marks.add(s);
      return world;
    };
    const a = mk(toe, 0x60a5fa), b = mk(heel, 0x1d4ed8);
    if (a && b) {
      const g = new THREE.BufferGeometry().setFromPoints([a, b]);
      this.marks.add(new THREE.Line(g, new THREE.LineDashedMaterial({
        color: 0x63657a, dashSize: 0.05, gapSize: 0.03,
      })).computeLineDistances());
    }
  }
}

export { PALETTE };
