"""LXF3 诊断: 打包 → 提取 CM 池 → CLI 解压 → 逐字节比对"""
import os, sys, json, struct, urllib.request, subprocess, shutil
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
PORT = open(os.path.join(os.environ['TMP'], 'lxapp_port.txt')).read().strip()
BASE = f'http://127.0.0.1:{PORT}'

def api(ep, payload):
    return json.loads(urllib.request.urlopen(urllib.request.Request(
        BASE + ep, data=json.dumps(payload).encode(), method='POST'), timeout=300).read().decode('utf-8'))

# 1) 构造测试文件夹 (与验收套件一致)
T = os.path.abspath('diag_src')
shutil.rmtree(T, ignore_errors=True)
rng_data = bytes((i * 37 + 11) & 255 for i in range(25600))
files = {
    'readme.txt': ('测试内容 Test content\n' * 500).encode('utf-8'),
    '数据/中文文件.txt': '中文内容：灵犀压缩盒文件夹测试。'.encode('utf-8') * 50,
    '数据/二进制.bin': rng_data,
    'deep/a/b/c/深层.txt': b'nested deep ' * 3000,
    '数据/空文件.txt': b'',
}
for rel, data in files.items():
    p = os.path.join(T, rel.replace('/', os.sep))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, 'wb').write(data)

# 2) 应用压缩 (LXF3)
r = api('/api/compress', {'path': T})
lx = r['saved']
print('lx:', lx, os.path.getsize(lx), 'bytes')

# 3) 解析 LXF3: 清单 + CM 池区域
d = open(lx, 'rb').read()
off = 4
count = struct.unpack('<I', d[off:off+4])[0]; off += 4
meta = []
for i in range(count):
    pl = struct.unpack('<H', d[off:off+2])[0]; off += 2
    path = d[off:off+pl].decode('utf-8'); off += pl
    isdir, srcb = d[off], d[off+1]; off += 2
    orig, o2, ln = struct.unpack('<QQQ', d[off:off+24]); off += 24
    meta.append((path, isdir, srcb, orig, o2, ln))
cmLxSize = struct.unpack('<Q', d[off:off+8])[0]; off += 8
cmPoolLx = d[off:off+cmLxSize]
rawPoolStart = off + cmLxSize
print('entries:', count, '| cmLxSize:', cmLxSize, '| rawPoolStart:', rawPoolStart)

# 4) CM 池解压 (CLI) → 与原文件拼接比对
open('diag_cmpool.lx', 'wb').write(cmPoolLx)
subprocess.run([CLI, 'd', 'diag_cmpool.lx', 'diag_cmpool.raw'], capture_output=True)
cmraw = open('diag_cmpool.raw', 'rb').read()
print('decoded cmPool:', len(cmraw), 'bytes')
concat = b''
for m in sorted(meta, key=lambda x: x[0]):
    if m[2] == 0 and not m[1]:
        concat += files[m[0]]
print('expected cmPool:', len(concat), 'bytes')
print('cmPool byteEqual:', cmraw == concat)

# 5) 原始池比对
rawpool = d[rawPoolStart:]
rp = b''
for m in sorted(meta, key=lambda x: x[0]):
    if m[2] == 1:
        rp += files[m[0]]
print('rawPool byteEqual:', rawpool == rp if rp else '(empty ok)')
print('rawPool entries:', [m[0] for m in meta if m[2] == 1])
