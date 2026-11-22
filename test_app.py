"""灵犀压缩盒 .NET 应用 完整验收测试套件
覆盖: 单文件往返 / 文件夹往返(嵌套+中文+空目录) / 拖拽等效通道 / 错误路径
      / CLI 互通 / 边界情况(空文件、空文件夹) — 全部通过 HTTP 驱动真实应用
"""
import os, sys, json, shutil, hashlib, urllib.request, urllib.error, random, subprocess

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
PORT = open(os.path.join(os.environ['TMP'], 'lxapp_port.txt')).read().strip()
BASE = f'http://127.0.0.1:{PORT}'
DL = os.path.expanduser('~\\Downloads')
CLI = os.path.abspath('lingxi.exe')
passed = failed = 0

def api(endpoint, payload=None, raw=None):
    data = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None)
    req = urllib.request.Request(BASE + endpoint, data=data, method='POST')
    if payload is not None and raw is None:
        req.add_header('Content-Type', 'application/json')
    r = urllib.request.urlopen(req, timeout=600)
    ct = r.headers.get('Content-Type', '')
    return (json.loads(r.read().decode('utf-8')) if 'json' in ct else r.read())

def check(name, ok, detail=''):
    global passed, failed
    mark = 'PASS' if ok else 'FAIL'
    if ok: passed += 1
    else: failed += 1
    print(f'  [{mark}] {name}' + (f' — {detail}' if detail and not ok else ''))

# ---------- 测试数据 ----------
print('=== 准备测试数据 ===')
T = os.path.abspath('app_test_src')
shutil.rmtree(T, ignore_errors=True)
rng = random.Random(42)
files = {
    'readme.txt': ('测试内容 Test content\n' * 500).encode('utf-8'),
    '数据/中文文件.txt': '中文内容：灵犀压缩盒文件夹测试。' .encode('utf-8') * 50,
    '数据/二进制.bin': bytes(rng.randrange(256) for _ in range(300_000)),
    'deep/a/b/c/深层.txt': b'nested deep ' * 3000,
    '数据/空文件.txt': b'',
}
for rel, data in files.items():
    p = os.path.join(T, rel.replace('/', os.sep))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'wb') as f: f.write(data)
os.makedirs(os.path.join(T, '数据', '空文件夹'), exist_ok=True)   # 空目录
FOLDER_LX = os.path.abspath('app_test_src.lx')
SINGLE = os.path.abspath('app_test_single.bin')
with open(SINGLE, 'wb') as f: f.write(bytes(rng.randrange(256) for _ in range(150_000)))
print('测试文件夹 + 单文件就绪')

# ---------- T1: 单文件 path-mode 往返 ----------
print('=== T1 单文件压缩/解压 (path 模式) ===')
r = api('/api/compress', {'path': SINGLE})
check('T1a 压缩成功', r['ok'], r['msg'])
lx_path = r['saved']
src_hash = hashlib.sha256(open(SINGLE, 'rb').read()).hexdigest()
r = api('/api/decompress', {'path': lx_path})
check('T1b 解压成功 (单文件, 非文件夹)', r['ok'] and not r.get('isFolder'), r.get('msg', ''))
out_hash = hashlib.sha256(open(r['saved'], 'rb').read()).hexdigest()
check('T1c 逐字节一致', src_hash == out_hash)
os.remove(lx_path); os.remove(r['saved'])

# ---------- T2: 文件夹压缩/解压 (path 模式) ----------
print('=== T2 文件夹压缩/解压 (path 模式) ===')
r = api('/api/compress', {'path': T})
check('T2a 文件夹压缩成功', r['ok'], r.get('msg', ''))
lx_path = r['saved']
r = api('/api/decompress', {'path': lx_path})
check('T2c 识别为文件夹归档并解压', r['ok'] and r.get('isFolder'), r.get('msg', ''))
dest = r['saved']
all_ok = True
for rel, data in files.items():
    p = os.path.join(dest, rel.replace('/', os.sep))
    try:
        if hashlib.sha256(open(p, 'rb').read()).hexdigest() != hashlib.sha256(data).hexdigest():
            all_ok = False; print('    MISMATCH:', rel)
    except FileNotFoundError:
        all_ok = False; print('    MISSING:', rel)
check('T2d 全部文件逐字节一致 (含中文/深层/空文件)', all_ok)
check('T2e 空目录还原', os.path.isdir(os.path.join(dest, '数据', '空文件夹')))
os.remove(lx_path); shutil.rmtree(dest, ignore_errors=True)

# ---------- T3: 拖拽等效 (body 通道, 结果进下载文件夹) ----------
print('=== T3 拖拽等效通道 (body 模式) ===')
data = open(SINGLE, 'rb').read()
r = api('/api/compress-body', raw=data)
check('T3a 拖拽压缩成功', r['ok'], r.get('msg', ''))
saved_lx = r['saved']
check('T3b 结果在下载文件夹', os.path.dirname(saved_lx) == os.path.normpath(DL), saved_lx)
r = api('/api/decompress-body', raw=open(saved_lx, 'rb').read())
check('T3c 拖拽解压成功', r['ok'], r.get('msg', ''))
out_hash = hashlib.sha256(open(r['saved'], 'rb').read()).hexdigest()
check('T3d 逐字节一致', out_hash == src_hash)
os.remove(saved_lx); os.remove(r['saved'])

# ---------- T4: 文件夹归档的拖拽解压 (自动还原到下载文件夹) ----------
print('=== T4 文件夹归档拖拽解压 ===')
r = api('/api/compress', {'path': T})
lx_path = r['saved']
r = api('/api/decompress-body', raw=open(lx_path, 'rb').read())
check('T4a 拖拽解压文件夹归档 → 自动还原', r['ok'] and r.get('isFolder'), r.get('msg', ''))
dest = r['saved']
ok = all(open(os.path.join(dest, rel.replace('/', os.sep)), 'rb').read() == d
         for rel, d in files.items())
check('T4b 还原内容逐字节一致', ok)
shutil.rmtree(dest, ignore_errors=True)
os.remove(lx_path)

# ---------- T5: CLI 互通 ----------
print('=== T5 CLI 互通 ===')
r = api('/api/compress', {'path': SINGLE})
lx_path = r['saved']
subprocess.run([CLI, 'd', lx_path, os.path.abspath('app_t5.out')], capture_output=True)
h1 = hashlib.sha256(open(SINGLE, 'rb').read()).hexdigest()
h2 = hashlib.sha256(open('app_t5.out', 'rb').read()).hexdigest()
check('T5a 应用压缩 → CLI 解压一致', h1 == h2)
cli_lx = os.path.abspath('app_t5_cli.lx')
subprocess.run([CLI, 'c', SINGLE, cli_lx], capture_output=True)
r = api('/api/decompress', {'path': cli_lx})
h3 = hashlib.sha256(open(r['saved'], 'rb').read()).hexdigest()
check('T5b CLI 压缩 → 应用解压一致', h3 == h1)
os.remove(lx_path); os.remove('app_t5.out'); os.remove(cli_lx); os.remove(r['saved'])

# ---------- T6: 错误路径 ----------
print('=== T6 错误路径 ===')
try:
    api('/api/compress', {'path': 'X:\\不存在的路径\\不存在.txt'})
    check('T6a 不存在路径报错', False)
except urllib.error.HTTPError as e:
    j = json.loads(e.read().decode('utf-8'))
    check('T6a 不存在路径报错', e.code == 400 and not j['ok'], j.get('msg', ''))
bad = os.path.abspath('app_t6_bad.bin')
open(bad, 'wb').write(b'this is not a lingxi file' * 10)
try:
    j = api('/api/decompress', {'path': bad})
    check('T6b 非灵犀文件报错', j.get('ok') == False and '无法识别' in j.get('msg', ''), j.get('msg', ''))
except urllib.error.HTTPError as e:
    j = json.loads(e.read().decode('utf-8'))
    check('T6b 非灵犀文件报错', e.code == 400 and 'LX1' in j.get('msg', ''), j.get('msg', ''))
os.remove(bad)

# ---------- T7: 边界情况: 空文件夹 ----------
print('=== T7 空文件夹 ===')
empty_dir = os.path.abspath('app_test_empty')
shutil.rmtree(empty_dir, ignore_errors=True)
os.makedirs(empty_dir)
r = api('/api/compress', {'path': empty_dir})
check('T7a 空文件夹压缩成功', r['ok'], r.get('msg', ''))
lx_path = r['saved']
r = api('/api/decompress', {'path': lx_path})
check('T7b 空文件夹解压成功', r['ok'] and r.get('isFolder'), r.get('msg', ''))
check('T7c 还原为空文件夹', os.path.isdir(r['saved']) and len(os.listdir(r['saved'])) == 0)
os.remove(lx_path); shutil.rmtree(r['saved'], ignore_errors=True)
shutil.rmtree(empty_dir, ignore_errors=True)

# ---------- T8: 后台大任务健康检查 ----------
print('=== T8 7.67GB 后台任务 ===')
out = 'D:\\下载文件\\2026-11-22日以前.lx'
sz = os.path.getsize(out) if os.path.exists(out) else 0
import urllib.request as ureq
st = json.loads(ureq.urlopen(BASE + '/api/status', timeout=30).read().decode('utf-8'))
check('T8a 应用运行正常', st.get('ok') == 1)
print(f'  后台压缩输出: {sz/1048576:.0f} MB (持续增长中)')

print(f'\n===== 结果: {passed} 通过, {failed} 失败 =====')
sys.exit(1 if failed else 0)
