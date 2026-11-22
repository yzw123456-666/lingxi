"""把 WASM 引擎内嵌进单文件 HTML: 灵犀压缩盒.html (默认浅色主题, 可切换深色并记忆)"""
import base64

wasm = open('lx_engine.wasm', 'rb').read()
b64 = base64.b64encode(wasm).decode()

html = r"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>灵犀压缩盒</title>
<style>
:root{
 --bg1:#eef1fa;--bg2:#e8edf9;--bg3:#f2f4fc;--card:#ffffff;--card2:#eef1fb;
 --cardbr:#e4e9f5;--btnbr:#d9e0f2;--dropbr:#c3cdea;--dropbg:#eef2ff;
 --chipbg:#eef1fb;--progtr:#e4e9f5;--pvbg:#f7f8fd;--pvbr:#e4e9f5;--pvtx:#333a52;
 --tx:#1d2333;--dim:#6a7089;--ac:#6366f1;--ok:#159570;--okbg:#e7f7ef;
 --bad:#dc4b5e;--badbg:#fdeaea;--warn:#d97706;--ft:#8a90ab;
 --shadow:0 8px 30px rgba(31,41,88,.08);
}
body.dark{
 --bg1:#0f1220;--bg2:#12172b;--bg3:#0f1220;--card:#171b2e;--card2:#1e2340;
 --cardbr:#262c4d;--btnbr:#343b63;--dropbr:#343b63;--dropbg:#1a2044;
 --chipbg:#20264a;--progtr:#20264a;--pvbg:#10131f;--pvbr:#262c4d;--pvtx:#c7cbef;
 --tx:#e8eaf6;--dim:#9aa0c3;--ac:#7c83ff;--ok:#3ecf8e;--okbg:#12291f;
 --bad:#ff6b81;--badbg:#2d1a20;--warn:#ffb454;--ft:#565d85;
 --shadow:none;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:linear-gradient(160deg,var(--bg1),var(--bg2) 60%,var(--bg3));color:var(--tx);
 font-family:system-ui,"Microsoft YaHei",sans-serif;min-height:100vh;display:flex;justify-content:center;padding:28px 16px;
 transition:background .25s,color .25s}
.wrap{width:100%;max-width:760px}
header{display:flex;align-items:center;gap:14px;margin-bottom:18px}
.logo{width:52px;height:52px;border-radius:14px;background:linear-gradient(135deg,#7c83ff,#4cc9f0);
 display:flex;align-items:center;justify-content:center;font-size:26px;font-weight:700;color:#fff}
.htxt{flex:1}
h1{font-size:22px} .sub{color:var(--dim);font-size:13px;margin-top:2px}
#theme{padding:8px 14px;font-size:13px}
.card{background:var(--card);border:1px solid var(--cardbr);border-radius:16px;padding:20px;margin-bottom:16px;
 box-shadow:var(--shadow);transition:background .25s,border-color .25s}
.drop{border:2px dashed var(--dropbr);border-radius:16px;padding:34px 20px;text-align:center;cursor:pointer;transition:.18s}
.drop.on{border-color:var(--ac);background:var(--dropbg)}
.d1{font-size:17px;margin-bottom:6px} .d2{color:var(--dim);font-size:13px;margin-bottom:14px}
.hint{color:var(--dim);font-size:12px;margin-top:14px}
button{background:var(--card2);color:var(--tx);border:1px solid var(--btnbr);border-radius:10px;
 padding:10px 18px;font-size:14px;cursor:pointer;transition:.15s}
button:hover{border-color:var(--ac)} button:disabled{opacity:.45;cursor:not-allowed}
button.primary{background:var(--ac);border:1px solid var(--ac);color:#fff;font-weight:600;padding:11px 26px}
button.primary:hover{filter:brightness(1.12)}
button.danger{color:var(--bad)}
.btns{display:flex;gap:10px;margin-top:16px;align-items:center;flex-wrap:wrap}
.mode{font-size:12px;padding:5px 12px;border-radius:99px;background:var(--chipbg);color:var(--dim)}
.mode.c{background:#e0f2fe;color:#0369a1} .mode.d{background:#dcfce7;color:#15803d}
body.dark .mode.c{background:#173247;color:#4cc9f0} body.dark .mode.d{background:#1c2a3a;color:#3ecf8e}
.fi{margin-top:14px;font-size:13px;color:var(--dim)} .fi b{color:var(--tx)}
.prog{height:8px;background:var(--progtr);border-radius:99px;margin-top:16px;overflow:hidden;display:none}
.prog>div{height:100%;width:0;background:linear-gradient(90deg,#7c83ff,#4cc9f0);border-radius:99px;transition:width .2s}
.status{margin-top:10px;font-size:13px;color:var(--dim);display:flex;align-items:center;gap:8px}
.dot{width:8px;height:8px;border-radius:99px;background:var(--ok)} .dot.busy{background:var(--warn);animation:pu 1s infinite}
@keyframes pu{50%{opacity:.4}}
.rmsg{margin-top:12px;font-size:13px;padding:10px 14px;border-radius:10px;display:none}
.rmsg.ok{display:block;background:var(--okbg);color:var(--ok)} .rmsg.bad{display:block;background:var(--badbg);color:var(--bad)}
pre.pv{margin-top:14px;background:var(--pvbg);border:1px solid var(--pvbr);border-radius:12px;padding:14px;
 font:12px/1.5 Consolas,monospace;max-height:340px;overflow:auto;white-space:pre-wrap;word-break:break-all;color:var(--pvtx)}
footer{text-align:center;color:var(--ft);font-size:12px;margin-top:8px;line-height:1.7}
.row2{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px}
label.sw{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--dim);cursor:pointer}
label.sw input{accent-color:var(--ac);width:16px;height:16px}
</style></head><body><div class="wrap">
<header><div class="logo">犀</div><div class="htxt"><h1>灵犀压缩盒</h1>
<div class="sub">上下文混合 · 无损压缩 · WebAssembly 引擎内嵌 · 完全离线 <span id="ver"></span></div></div>
<button id="theme">🌙 深色</button></header>

<div class="card">
 <div id="drop" class="drop">
  <div class="d1">把文件拖到这里</div>
  <div class="d2">或</div>
  <button id="pick">选择文件</button>
  <div class="hint">普通文件 → 压缩为 .lx &nbsp;·&nbsp; .lx 文件 → 解压还原（自动识别）&nbsp;·&nbsp; 受浏览器内存约束, 超大文件请用桌面版</div>
 </div>
 <input type="file" id="file" hidden>
 <div class="fi" id="fi" hidden>已选: <b id="fn"></b> <span id="fs"></span></div>
 <div class="btns">
  <span id="mode" class="mode">引擎加载中…</span>
  <button id="go" class="primary" disabled>开始</button>
  <button id="pv" disabled>预览内容</button>
  <button id="save" hidden>重新保存结果</button>
 </div>
 <div class="prog" id="pg"><div id="bar"></div></div>
 <div class="status"><span class="dot" id="dot"></span><span id="st">正在加载引擎…</span></div>
 <div class="rmsg" id="rmsg"></div>
</div>

<pre class="pv" id="pv" hidden></pre>
<footer>数据不出本机 · 无需联网 · 引擎以 WebAssembly 内嵌于本页<br>
提示: 纯 HTML 版没有后台常驻与开机自启能力, 如需要请使用配套的 LingXiBox.exe</footer>
</div>
<script>
const WASM_B64 = "__WASM_B64__";
const $ = id => document.getElementById(id);
const fmt = n => n > 1048576 ? (n/1048576).toFixed(2)+' MB' : n > 1024 ? (n/1024).toFixed(1)+' KB' : n+' B';
let E = null, mem = null, file = null, lastBlob = null, lastName = '';
const isLx = n => n.toLowerCase().endsWith('.lx');
const yieldUI = () => new Promise(r => setTimeout(r, 0));
const ERR = {'-1':'不是灵犀文件(缺少 LX1 头)','-2':'内存不足','-3':'CRC 校验失败: 数据已损坏','-4':'内存不足: 文件超出浏览器可处理范围 (约3GB), 超大文件请用桌面版'};
const errText = rc => ERR[String(rc)] || ('引擎错误 ' + rc);

/* ---------- 主题: 默认浅色, 可切换深色并记忆 ---------- */
const tbtn = $('theme');
function applyTheme(t) {
  document.body.classList.toggle('dark', t === 'dark');
  tbtn.textContent = t === 'dark' ? '☀️ 浅色' : '🌙 深色';
}
let theme = 'light';
try { theme = localStorage.getItem('lx-theme') || 'light'; } catch (e) {}
applyTheme(theme);
tbtn.onclick = () => {
  theme = document.body.classList.contains('dark') ? 'light' : 'dark';
  try { localStorage.setItem('lx-theme', theme); } catch (e) {}
  applyTheme(theme);
};

let WASM_BYTES = null;
let WASM_MODULE = null;
async function boot() {
  WASM_BYTES = Uint8Array.from(atob(WASM_B64), c => c.charCodeAt(0));
  WASM_MODULE = new WebAssembly.Module(WASM_BYTES);
  const inst = new WebAssembly.Instance(WASM_MODULE);
  E = inst.exports; mem = E.memory;
  $('ver').textContent = '引擎 v' + E.lx_ver();
  $('mode').textContent = '未选择文件';
  $('st').textContent = '空闲 (引擎就绪)';
}
/* 每操作新建实例: 全新内存, 从出厂状态开始, 彻底消除状态共享问题 */
async function newInstance() {
  while (!WASM_MODULE) await new Promise(r => setTimeout(r, 20));
  const inst = new WebAssembly.Instance(WASM_MODULE);
  E = inst.exports; mem = E.memory;
  return E;
}
boot().catch(e => { $('st').textContent = '引擎加载失败: ' + e; });

const dz = $('drop');
dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('on'); });
dz.addEventListener('dragleave', () => dz.classList.remove('on'));
dz.addEventListener('drop', e => {
  e.preventDefault(); dz.classList.remove('on');
  if (!e.dataTransfer.files.length) return;
  const entry = e.dataTransfer.items && e.dataTransfer.items[0] && e.dataTransfer.items[0].webkitGetAsEntry
    ? e.dataTransfer.items[0].webkitGetAsEntry() : null;
  if (entry && entry.isDirectory) { showMsg('拖入的是文件夹，请拖单个文件', false); return; }
  setFile(e.dataTransfer.files[0]);
});
function friendly(e) {
  if (e && (e.name === 'NotReadableError' || e.name === 'SecurityError'))
    return '无法读取该文件: 它可能已被移动/删除、是文件夹、或正被其他程序占用。请重新选择该文件再试。';
  return '失败: ' + (e.message || e);
}
$('file').onchange = e => setFile(e.target.files[0]);
$('pick').onclick = e => { e.stopPropagation(); $('file').click(); };
dz.onclick = ev => { if (ev.target.tagName !== 'BUTTON') $('file').click(); };

function setFile(f) {
  file = f; lastBlob = null;
  $('pv').hidden = true; $('save').hidden = true; showMsg('');
  $('fi').hidden = false; $('fn').textContent = f.name; $('fs').textContent = ' (' + fmt(f.size) + ')';
  const lx = isLx(f.name);
  $('mode').textContent = lx ? '模式: 解压还原' : '模式: 压缩';
  $('mode').className = 'mode ' + (lx ? 'd' : 'c');
  $('go').disabled = false;
  $('go').textContent = lx ? '开始解压' : '开始压缩';
  $('pv').disabled = !lx;
}
function showMsg(t, ok) {
  const m = $('rmsg'); if (!t) { m.className = 'rmsg'; return; }
  m.textContent = t; m.className = 'rmsg ' + (ok ? 'ok' : 'bad');
}
function setBusy(b, pct) {
  $('go').disabled = b || !file;
  $('pv').disabled = b || !file || !isLx(file.name);
  $('pg').style.display = b ? 'block' : 'none';
  $('bar').style.width = (pct || 0) + '%';
  $('dot').className = 'dot' + (b ? ' busy' : '');
  $('st').textContent = b ? ('任务进行中 ' + pct + '%') : '空闲 (引擎就绪)';
}
function freshU8() { return new Uint8Array(mem.buffer); }

/* ---------- 文件夹归档 (LXF1) -> 标准 ZIP (无压缩存储法) ---------- */
function crc32table() {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) { let c = n;
    for (let k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
    t[n] = c >>> 0; }
  return t;
}
const CRC_T = crc32table();
function crc32(u8) { let c = 0xFFFFFFFF;
  for (let i = 0; i < u8.length; i++) c = CRC_T[(c ^ u8[i]) & 0xFF] ^ (c >>> 8);
  return (c ^ 0xFFFFFFFF) >>> 0; }

function lxfToZip(u8) {
  if (!(u8[0] === 0x4C && u8[1] === 0x58 && u8[2] === 0x46 && u8[3] === 0x31)) return null;
  const dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
  let off = 4;
  const count = dv.getUint32(off, true); off += 4;
  const utf8 = new TextDecoder();
  const entries = [];
  for (let i = 0; i < count; i++) {
    const pl = dv.getUint16(off, true); off += 2;
    const path = utf8.decode(u8.subarray(off, off + pl)); off += pl;
    const isDir = u8[off] === 1; off += 1;
    const size = Number(dv.getBigUint64(off, true)); off += 8;
    entries.push({ path, isDir, size, dataOff: 0 });
  }
  let p = off;
  for (const e of entries) { if (!e.isDir) { e.dataOff = p; p += e.size; } }

  const enc = new TextEncoder();
  const chunks = [], central = [];
  let offset = 0;
  const w16 = v => { const b = new Uint8Array(2); new DataView(b.buffer).setUint16(0, v, true); chunks.push(b); offset += 2; };
  const w32 = v => { const b = new Uint8Array(4); new DataView(b.buffer).setUint32(0, v >>> 0, true); chunks.push(b); offset += 4; };
  const wbytes = b => { chunks.push(b); offset += b.length; };

  for (const e of entries) {
    const nameB = enc.encode((e.isDir ? e.path.replace(/\/+$/, '') : e.path) + (e.isDir ? '/' : ''));
    const data = e.isDir ? new Uint8Array(0) : u8.subarray(e.dataOff, e.dataOff + e.size);
    const crc = crc32(data);
    central.push({ nameB, crc, size: data.length, off: offset });
    w32(0x04034b50); w16(20); w16(0x0800); w16(0); w16(0); w16(0x0021);
    w32(crc); w32(data.length); w32(data.length); w16(nameB.length); w16(0);
    wbytes(nameB); if (data.length) wbytes(data);
  }
  const cdStart = offset;
  for (const c of central) {
    w32(0x02014b50); w16(20); w16(20); w16(0x0800); w16(0); w16(0); w16(0x0021);
    w32(c.crc); w32(c.size); w32(c.size); w16(c.nameB.length); w16(0);
    w16(0); w16(0); w32(0); w32(c.off); wbytes(c.nameB);
  }
  const cdSize = offset - cdStart;
  w32(0x06054b50); w16(0); w16(0); w16(central.length); w16(central.length);
  w32(cdSize); w32(cdStart); w16(0);
  const total = chunks.reduce((a, b) => a + b.length, 0);
  const out = new Uint8Array(total);
  let pos = 0;
  for (const b of chunks) { out.set(b, pos); pos += b.length; }
  return out;
}

async function runJob(decompress) {
  const n = file.size;
  setBusy(true, 0);
  try {
    await newInstance();
    let rc, total;
    let out = null, outLen = 0, pct = 0;
    if (decompress) {
      const inPtr = E.lx_in_alloc(n);
      freshU8().set(new Uint8Array(await file.arrayBuffer()), inPtr);
      total = E.lx_d_start(inPtr, n);
      if (total < 0) throw new Error(errText(total));
      const CH = 1 << 19;
      for (let k = 0; k < total; k += CH) {
        E.lx_d_run(Math.min(k + CH, total));
        setBusy(true, Math.min(100, Math.round((k + CH) * 100 / total)));
        await yieldUI();
      }
      rc = E.lx_d_end();
      if (rc < 0) throw new Error(errText(rc));
    } else {
      rc = E.lx_c_start(n);
      if (rc < 0) throw new Error(errText(rc));
      freshU8().set(new Uint8Array(await file.arrayBuffer()), E.lx_c_hbuf());
      const CH = 1 << 19;
      for (let k = 0; k < n; k += CH) {
        E.lx_c_run(k, Math.min(k + CH, n));
        setBusy(true, Math.min(100, Math.round((k + CH) * 100 / n)));
        await yieldUI();
      }
      rc = E.lx_c_end();
      if (rc < 0) throw new Error(errText(rc));
    }
    outLen = E.lx_out_len();
    out = freshU8().slice(E.lx_out_ptr(), E.lx_out_ptr() + outLen);
    pct = outLen * 100 / n;
    /* LX0 存储容器: 原始字节在头 15 字节之后, 直接 CRC 校验 + 切片 (无需引擎) */
    if (out[0] === 0x4C && out[1] === 0x58 && out[2] === 0x30) {
      const dv2 = new DataView(out.buffer, out.byteOffset, out.byteLength);
      const origSize = Number(dv2.getBigUint64(3, true));
      let crc = 0xFFFFFFFF;
      for (let i = 0; i < origSize; i++) crc = CRC_T[(crc ^ out[15 + i]) & 0xFF] ^ (crc >>> 8);
      crc = (crc ^ 0xFFFFFFFF) >>> 0;
      const want = dv2.getUint32(11, true);
      if (crc !== want) throw new Error('CRC 校验失败: 数据已损坏');
      const payload = out.slice(15, 15 + origSize);
      lastBlob = new Blob([payload]);
      lastName = file.name.replace(/\.lx$/i, '');
      saveResult();
      showMsg(`解压完成 (存储模式): ${fmt(origSize)}　已保存: ${lastName}　已保存到: ${lastName}`, true);
      return { blob: lastBlob, bytes: payload };
    }
    if (decompress && out[0] === 0x4C && out[1] === 0x58 && out[2] === 0x46 && out[3] === 0x31) {
      const zipBytes = lxfToZip(out);
      if (!zipBytes) throw new Error('文件夹归档解析失败');
      lastBlob = new Blob([zipBytes]);
      lastName = file.name.replace(/\.lx$/i, '') + '.zip';
      saveResult();
      showMsg(`文件夹归档已转为 ZIP (${fmt(zipBytes.length)})　已保存: ${lastName}　解压 ZIP 即得原文件夹`, true);
      return { blob: lastBlob, bytes: zipBytes };
    }
    lastBlob = new Blob([out]);
    lastName = decompress ? file.name.replace(/\.lx$/i, '') : file.name + '.lx';
    const msg = decompress
      ? `解压完成: ${fmt(n)} → ${fmt(outLen)}, CRC 校验通过`
      : `压缩完成: ${fmt(n)} → ${fmt(outLen)} (${pct.toFixed(1)}%, ${(outLen*8/n).toFixed(3)} bpc)　已保存: ${lastName}`;
    saveResult();
    showMsg(msg, true);
    return { blob: lastBlob, bytes: out };
  } catch (e) {
    showMsg(friendly(e), false);
    return null;
  } finally {
    setBusy(false, 0);
  }
}
function saveResult() {
  if (!lastBlob) return;
  const url = URL.createObjectURL(lastBlob), a = document.createElement('a');
  a.href = url; a.download = lastName; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 3000);
  $('save').hidden = false;
}
$('go').onclick = () => file && runJob(isLx(file.name));
$('save').onclick = saveResult;

function utf8Ok(u8) {
  let bad = 0;
  for (let i = 0; i < u8.length; ) {
    const c = u8[i]; let need = 0, ok = true;
    if (c < 0x80) need = 0;
    else if ((c & 0xE0) === 0xC0) need = 1;
    else if ((c & 0xF0) === 0xE0) need = 2;
    else if ((c & 0xF8) === 0xF0) need = 3;
    else { bad++; i++; continue; }
    for (let k = 1; k <= need; k++)
      if (i + k >= u8.length || (u8[i+k] & 0xC0) !== 0x80) { ok = false; break; }
    if (!ok) bad++;
    i += need + 1;
  }
  return bad * 1000 / (u8.length || 1) < 20;
}
function hexdump(u8) {
  let s = '';
  for (let i = 0; i < u8.length; i += 16) {
    s += i.toString(16).padStart(4, '0') + '  ';
    for (let k = 0; k < 16; k++) {
      s += i + k < u8.length ? u8[i+k].toString(16).padStart(2, '0') + ' ' : '   ';
      if (k === 7) s += ' ';
    }
    s += '|';
    for (let k = 0; k < 16 && i + k < u8.length; k++) {
      const c = u8[i+k]; s += (c >= 0x20 && c < 0x7f) ? String.fromCharCode(c) : '.';
    }
    s += '\n';
  }
  return s;
}
$('pv').onclick = async () => {
  if (!file) return;
  const r = await runJob(true);
  if (!r) return;
  const u8 = r.bytes;
  const p = $('pv'); p.hidden = false;
  const cap = u8.length > 524288 ? u8.slice(0, 524288) : u8;
  if (utf8Ok(cap)) {
    let txt = new TextDecoder('utf-8').decode(cap);
    if (txt.length > 65536) txt = txt.slice(0, 65536) + '\n… (截断)';
    p.textContent = `原始大小: ${fmt(u8.length)} · 文本 (UTF-8 有效)\n\n` + txt;
  } else {
    p.textContent = `原始大小: ${fmt(u8.length)} · 二进制 (十六进制转储, 前 512 字节)\n\n` + hexdump(u8.slice(0, 512));
  }
};
</script></body></html>
"""

html = html.replace('__WASM_B64__', b64)
out = '灵犀压缩盒.html'
with open(out, 'w', encoding='utf-8', newline='\n') as f:
    f.write(html)
import os
print(f'{out}: {os.path.getsize(out):,} bytes (wasm {len(wasm):,}B embedded)')
