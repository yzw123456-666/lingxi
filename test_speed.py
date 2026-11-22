"""速度判别测试: 存储模式必须秒级; 文件夹含不可压数据必须走存储直拷"""
import os, sys, json, time, urllib.request
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
PORT = open(os.path.join(os.environ['TMP'], 'lxapp_port.txt')).read().strip()
BASE = f'http://127.0.0.1:{PORT}'
DL = os.path.expanduser('~\\Downloads')

def post(endpoint, data):
    body = data if isinstance(data, (bytes, bytearray)) else json.dumps(data).encode()
    req = urllib.request.Request(BASE + endpoint, data=body, method='POST')
    req.add_header('Content-Type', 'application/json')
    return json.loads(urllib.request.urlopen(req, timeout=600).read().decode('utf-8'))

passed = failed = 0
def check(name, ok, detail=''):
    global passed, failed
    print(f'  [{"PASS" if ok else "FAIL"}] {name}' + (f' — {detail}' if detail else ''))
    if ok: passed += 1
    else: failed += 1

# S1: 20MB 随机 (不可压) — 必须走存储模式, 秒级
print('=== S1 存储模式速度 (20MB 随机) ===')
data = os.urandom(20 * 1048576)
t0 = time.time()
r = post('/api/compress-body', data)
dt = time.time() - t0
check('S1a 存储模式秒级完成 (<10s)', r['ok'] and dt < 10, f'{dt:.1f}s | {r["msg"][:60]}')
print(f'    实测: {dt:.2f}s')
saved = r['saved']
# 解压还原
t0 = time.time()
r2 = post('/api/decompress', {'path': saved})
dt2 = time.time() - t0
check('S1b 存储解压秒级 (<10s)', r2['ok'] and dt2 < 10, f'{dt2:.1f}s')
import hashlib
h1 = hashlib.sha256(data).hexdigest()
h2 = hashlib.sha256(open(r2['saved'], 'rb').read()).hexdigest()
check('S1c 逐字节一致', h1 == h2)
os.remove(saved); os.remove(r2['saved'])

# S2: 文件夹含 20MB 随机 + 文本 — LXF2 逐文件决策, 总耗时 << 全建模
print('=== S2 文件夹混合内容速度 ===')
import shutil
tf = os.path.abspath('speed_test_folder')
shutil.rmtree(tf, ignore_errors=True)
os.makedirs(os.path.join(tf, 'sub'))
with open(os.path.join(tf, 'random20m.bin'), 'wb') as f: f.write(os.urandom(20 * 1048576))
with open(os.path.join(tf, 'text.txt'), 'wb') as f:
    f.write(('可压缩文本内容 compressible ' * 300).encode('utf-8') * 8)   # ~9MB 可压缩
t0 = time.time()
r = post('/api/compress', {'path': tf})
dt = time.time() - t0
# 旧引擎: 29MB 全建模 ≈ 100s+; 新 LXF2: 随机存储 + 文本建模 ≈ 30s 内
check('S2a 混合文件夹 <45s (旧引擎需 100s+)', r['ok'] and dt < 45, f'{dt:.1f}s | {r["msg"][:60]}')
print(f'    实测: {dt:.1f}s | 输出: {os.path.getsize(r["saved"])/1048576:.1f} MB')
saved_lx = r['saved']

# 解压还原整个文件夹
t0 = time.time()
r2 = post('/api/decompress', {'path': saved_lx})
dt2 = time.time() - t0
dest = r2['saved']
ok = open(os.path.join(dest, 'random20m.bin'), 'rb').read() == open(os.path.join(tf, 'random20m.bin'), 'rb').read()
check('S2b 解压还原 (含随机文件) 逐字节一致', ok and r2.get('isFolder'), f'{dt2:.1f}s')
shutil.rmtree(dest, ignore_errors=True)
os.remove(saved_lx)
shutil.rmtree(tf, ignore_errors=True)

print(f'===== 速度测试: {passed} 通过, {failed} 失败 =====')
sys.exit(1 if failed else 0)
