"""桌面应用 API 端点全量验收 (HTTP 驱动真实应用进程)"""
import os, sys, json, time, hashlib, shutil, urllib.request, urllib.error
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
PORT = open(os.path.join(os.environ['TMP'], 'lxapp_port.txt')).read().strip()
BASE = f'http://127.0.0.1:{PORT}'
passed = failed = 0

def check(name, ok, detail=''):
    global passed, failed
    print(f'  [{"PASS" if ok else "FAIL"}] {name}' + (f' — {detail}' if detail and not ok else ''))
    if ok: passed += 1
    else: failed += 1

def api(endpoint, payload=None, raw=None):
    data = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None)
    req = urllib.request.Request(BASE + endpoint, data=data, method='POST')
    if payload is not None and raw is None:
        req.add_header('Content-Type', 'application/json')
    r = urllib.request.urlopen(req, timeout=600)
    ct = r.headers.get('Content-Type', '')
    return (json.loads(r.read().decode('utf-8')) if 'json' in ct else r.read())

# 准备测试文件夹
T = os.path.abspath('full_test_src')
shutil.rmtree(T, ignore_errors=True)
os.makedirs(os.path.join(T, 'sub', 'deep'))
open(os.path.join(T, 'text.txt'), 'wb').write(b'test content ' * 2000)
open(os.path.join(T, 'sub', 'binary.bin'), 'wb').write(bytes(range(256)) * 100)
open(os.path.join(T, 'sub', 'deep', 'nested.txt'), 'wb').write(b'nested data ' * 500)
os.makedirs(os.path.join(T, 'empty_dir'), exist_ok=True)

print('=== A1 状态 ===')
import urllib.request as ur
st = json.loads(ur.urlopen(BASE + '/api/status', timeout=10).read().decode('utf-8'))
check('A1a status 返回', st.get('ok') == 1)
check('A1b 版本', st.get('version') == 'v0.3-app')

print('=== A2 单文件 path 模式 ===')
S = os.path.abspath('full_test_single.dat')
open(S, 'wb').write(bytes(range(256)) * 500)
r = api('/api/compress', {'path': S})
check('A2a 压缩', r['ok'], r.get('msg', ''))
lx = r['saved']
r = api('/api/decompress', {'path': lx})
check('A2b 解压', r['ok'], r.get('msg', ''))
h1 = hashlib.sha256(open(S, 'rb').read()).hexdigest()
h2 = hashlib.sha256(open(r['saved'], 'rb').read()).hexdigest()
check('A2c 逐字节一致', h1 == h2)
os.remove(lx); os.remove(r['saved']); os.remove(S)

print('=== A3 文件夹 path 模式 ===')
r = api('/api/compress', {'path': T})
check('A3a 文件夹压缩', r['ok'], r.get('msg', ''))
lx = r['saved']
r = api('/api/decompress', {'path': lx})
check('A3b 文件夹归档解压', r['ok'] and r.get('isFolder'), r.get('msg', ''))
dest = r['saved']
all_ok = True
for r2, ds, fs in os.walk(T):
    rel = os.path.relpath(r2, T)
    for f in fs:
        new_p = os.path.join(dest, rel, f)
        orig_p = os.path.join(T, rel, f)
        if not os.path.exists(new_p) or \
           hashlib.sha256(open(orig_p,'rb').read()).hexdigest() != hashlib.sha256(open(new_p,'rb').read()).hexdigest():
            all_ok = False
check('A3c 递归逐字节一致 (含中文/空目录/嵌套)', all_ok)
os.remove(lx); shutil.rmtree(dest, ignore_errors=True)

print('=== A4 拖拽 body 模式 ===')
data = os.urandom(500_000)
r = api('/api/compress-body', raw=data)
check('A4a body 压缩', r['ok'], r.get('msg', ''))
saved = r['saved']
r = api('/api/decompress-body', raw=open(saved, 'rb').read())
check('A4b body 解压', r['ok'], r.get('msg', ''))
h1 = hashlib.sha256(data).hexdigest()
h2 = hashlib.sha256(open(r['saved'], 'rb').read()).hexdigest()
check('A4c 逐字节一致', h1 == h2)
os.remove(saved); os.remove(r['saved'])

print('=== A5 预览 (文本 + 二进制) ===')
text_lx = os.path.abspath('full_test_text.lx')
text_data = ('预览测试 测试文本内容\n' * 500).encode('utf-8')
open('full_test_text_src.txt', 'wb').write(text_data)
api('/api/compress', {'path': os.path.abspath('full_test_text_src.txt')})
shutil.move(os.path.abspath('full_test_text_src.txt.lx') if os.path.exists(os.path.abspath('full_test_text_src.txt.lx')) else text_lx, text_lx)
r = api('/api/preview', raw=open(text_lx, 'rb').read())
check('A5a 文本预览', r.get('ok') and r.get('kind') == 'text' and '预览测试' in r.get('content', ''), r.get('label', ''))
bin_lx = os.path.abspath('full_test_bin.lx')
bin_data = os.urandom(500_000)
open('full_test_bin_src.bin', 'wb').write(bin_data)
api('/api/compress', {'path': os.path.abspath('full_test_bin_src.bin')})
shutil.move(os.path.abspath('full_test_bin_src.bin.lx'), bin_lx)
r = api('/api/preview', raw=open(bin_lx, 'rb').read())
check('A5b 二进制预览', r.get('ok') and r.get('kind') == 'hex', r.get('label', ''))
os.remove(text_lx); os.remove(bin_lx)
os.remove('full_test_text_src.txt'); os.remove('full_test_bin_src.bin')

print('=== A6 自启动 ===')
r = api('/api/autostart', raw=b'1')
check('A6a 开启', r.get('enabled') == True, r.get('msg', ''))
r = api('/api/autostart', raw=b'0')
check('A6b 关闭', r.get('enabled') == False, r.get('msg', ''))

print('=== A7 空文件夹 ===')
ed = os.path.abspath('full_test_empty')
shutil.rmtree(ed, ignore_errors=True); os.makedirs(ed)
r = api('/api/compress', {'path': ed})
check('A7a 压缩', r['ok'], r.get('msg', ''))
lx = r['saved']
r = api('/api/decompress', {'path': lx})
check('A7b 解压', r['ok'] and r.get('isFolder'), r.get('msg', ''))
check('A7c 空目录还原', os.path.isdir(r['saved']) and len(os.listdir(r['saved'])) == 0)
os.remove(lx); shutil.rmtree(r['saved'], ignore_errors=True); shutil.rmtree(ed, ignore_errors=True)

print('=== A8 错误路径 ===')
try:
    api('/api/compress', {'path': 'X:\\不存在\\test.txt'})
    check('A8a 不存在路径', False)
except urllib.error.HTTPError as e:
    check('A8a 不存在路径', e.code == 400, str(e.code))

print('=== A9 清理 ===')
shutil.rmtree(T, ignore_errors=True)
check('A9 清理完成', not os.path.exists(T))

print(f'\n===== 桌面应用验收: {passed} 通过, {failed} 失败 =====')
sys.exit(1 if failed else 0)
