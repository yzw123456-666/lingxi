"""验证 7.67GB 文件夹归档的解压还原 (修正路径基准)"""
import os, sys, json, urllib.request, hashlib
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
port = open(os.path.join(os.environ['TMP'], 'lxapp_port.txt')).read().strip()
base = f'http://127.0.0.1:{port}'

lx = os.path.join('D:' + os.sep, '下载文件', '2026-11-22日以前.lx')
print('压缩产物:', f'{os.path.getsize(lx)/1073741824:.2f} GB')

r = json.loads(urllib.request.urlopen(urllib.request.Request(
    base + '/api/decompress', data=json.dumps({'path': lx}).encode(), method='POST'),
    timeout=3600).read().decode('utf-8'))
print('解压:', r.get('msg', ''))
dest = r.get('dest', '')
src = os.path.join('D:' + os.sep, '下载文件', '2026-11-22日以前')

ok = fail = 0
for r2, ds, fs in os.walk(dest):
    rel_dir = os.path.relpath(r2, dest)          # 相对于解压根目录
    for f in fs:
        new_p = os.path.join(r2, f)
        orig_p = os.path.join(src, rel_dir, f)   # 对应的原始文件
        try:
            h1 = hashlib.sha256(open(orig_p, 'rb').read()).hexdigest()
            h2 = hashlib.sha256(open(new_p, 'rb').read()).hexdigest()
            if h1 == h2: ok += 1
            else:
                fail += 1
                if fail <= 3: print('MISMATCH:', new_p)
        except FileNotFoundError:
            fail += 1
            if fail <= 3: print('NOT FOUND in src:', orig_p)
        except Exception as e:
            fail += 1
            print('ERROR:', e)

print(f'校验结果: {ok} 个文件逐字节一致, {fail} 个不一致')
