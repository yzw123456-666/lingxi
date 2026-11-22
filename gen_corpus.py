"""生成测试语料: 代码 / 英文文档 / 中文词典 / 中文散文 / JSON日志 / 二进制"""
import os, glob, random, json, shutil

OUT = 'corpus'
os.makedirs(OUT, exist_ok=True)

# ---------- 1. 代码语料: Python 标准库 ----------
py_root = r'C:\Program Files\Python312\Lib'
candidates = []
for pat in ['*.py', 'json/*.py', 'asyncio/*.py', 'email/*.py', 'unittest/*.py',
            'logging/*.py', 'xml/etree/*.py', 'http/*.py', 'collections/*.py',
            'importlib/*.py', 'multiprocessing/*.py', 'urllib/*.py', 'wsgiref/*.py',
            'tomllib/*.py', 'ctypes/*.py']:
    candidates += glob.glob(os.path.join(py_root, pat))
picked, total = [], 0
for f in sorted(candidates, key=lambda p: -os.path.getsize(p)):
    sz = os.path.getsize(f)
    if 3000 <= sz <= 80000:
        picked.append(f); total += sz
    if total > 900_000: break
parts = []
for f in picked:
    try:
        src = open(f, encoding='utf-8', errors='replace').read()
    except Exception:
        continue
    parts.append(f'# ===== {os.path.basename(f)} =====\n' + src)
open(f'{OUT}/code_py.txt', 'w', encoding='utf-8', newline='\n').write(
    '\n\n'.join(parts))
print('code_py.txt', os.path.getsize(f'{OUT}/code_py.txt'))

# ---------- 2. 英文文档语料: 已安装包的 rst/md/txt ----------
roots = [r'C:\Program Files\Python312\Lib\site-packages',
         r'C:\Program Files\Git\mingw64\share\doc',
         r'C:\Program Files\Git\etc',
         r'C:\Program Files\Python312']
docs, total = [], 0
for root in roots:
    if not os.path.isdir(root): continue
    for path in glob.glob(root + '/**/*.rst', recursive=True) + \
                glob.glob(root + '/**/*.md', recursive=True) + \
                glob.glob(root + '/**/*.txt', recursive=True):
        sz = os.path.getsize(path)
        if 2000 <= sz <= 120000:
            docs.append(path); total += sz
        if total > 900_000: break
    if total > 900_000: break
parts = []
for f in docs[:80]:
    try:
        txt = open(f, encoding='utf-8', errors='replace').read()
    except Exception:
        continue
    parts.append(f'===== {os.path.basename(f)} =====\n' + txt)
open(f'{OUT}/docs_en.txt', 'w', encoding='utf-8', newline='\n').write(
    '\n\n'.join(parts))
print('docs_en.txt', os.path.getsize(f'{OUT}/docs_en.txt'))

# ---------- 3. 中文词典语料: jieba 内置词典 (真实中文词频数据) ----------
import shutil
d = os.path.expanduser(
    r'~\AppData\Roaming\Python\Python312\site-packages\jieba\dict.txt')
assert os.path.exists(d), f'jieba dict not found: {d}'
shutil.copy(d, f'{OUT}/zh_dict.txt')
print('zh_dict.txt', os.path.getsize(f'{OUT}/zh_dict.txt'))

# ---------- 4. 中文散文语料 (手写整理的经典节选 + 自撰散文) ----------
zh = open('zh_prose.txt', encoding='utf-8').read()
open(f'{OUT}/zh_prose.txt', 'w', encoding='utf-8', newline='\n').write(zh)
print('zh_prose.txt', os.path.getsize(f'{OUT}/zh_prose.txt'))

# ---------- 5. 结构化 JSON 日志 (合成) ----------
rng = random.Random(2026)
services = ['auth-svc', 'order-svc', 'payment-svc', 'search-svc', 'notify-svc',
            'inventory-svc', 'gateway', 'user-svc', 'cart-svc', 'recommend-svc']
levels = ['INFO'] * 70 + ['WARN'] * 15 + ['ERROR'] * 8 + ['DEBUG'] * 7
msgs = ['user {uid} logged in from {ip}',
        'order {oid} created for user {uid}, amount={amt}, channel={ch}',
        'payment {pid} {st}, latency={lat}ms, gateway={ch}',
        'search query "{q}" returned {cnt} results in {lat}ms',
        'cache miss for key user:profile:{uid}, fallback to db',
        'inventory check sku={sku} qty={qty} warehouse={wh}',
        'notification email sent to user {uid}, template={tpl}',
        'request {rid} failed upstream {svc}: timeout after {lat}ms, retries={rt}',
        'session {sess} expired for user {uid}',
        'rate limit triggered for ip {ip}, window={lat}s']
chans = ['alipay', 'wechat', 'unionpay', 'visa', 'applepay']
status = ['success', 'success', 'success', 'pending', 'refunded', 'failed']
def ip():
    return f'{rng.choice([10,172,192])}.{rng.randrange(256)}.{rng.randrange(256)}.{rng.randrange(1,254)}'
lines = []
t = 1760000000.0
for i in range(60000):
    t += rng.expovariate(50)
    import datetime
    ts = datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat()
    uid = rng.randrange(100000)
    tpl = rng.choice(msgs)
    body = tpl.format(uid=uid, ip=ip(), oid=f'ORD{rng.randrange(10**9):09d}',
                      amt=round(rng.uniform(1, 5000), 2), ch=rng.choice(chans),
                      pid=f'PAY{rng.randrange(10**9):09d}', st=rng.choice(status),
                      lat=rng.randrange(1, 3000), q=rng.choice(
                          ['手机壳', 'bluetooth耳机', 'laptop stand', '数据线', 'usb hub',
                           'coffee', '键盘', 'monitor', 'ssd', '鼠标垫']),
                      cnt=rng.randrange(0, 500), sku=f'SKU{rng.randrange(10**6):06d}',
                      qty=rng.randrange(1, 99), wh=rng.choice(['CN-SH', 'CN-BJ', 'US-W', 'EU-DE']),
                      tpl=rng.choice(['order_confirm', 'otp', 'promo', 'receipt']),
                      rid=f'REQ{rng.randrange(10**10):010d}', svc=rng.choice(services),
                      rt=rng.randrange(0, 4), sess=f'S{rng.randrange(10**12):012d}')
    rec = {'ts': ts, 'level': rng.choice(levels), 'svc': rng.choice(services),
           'host': f'pod-{rng.randrange(48)}', 'trace': f'trace-{rng.randrange(10**12):012d}',
           'msg': body}
    lines.append(json.dumps(rec, ensure_ascii=False, separators=(',', ':')))
open(f'{OUT}/logs.json', 'w', encoding='utf-8', newline='\n').write('\n'.join(lines))
print('logs.json', os.path.getsize(f'{OUT}/logs.json'))

# ---------- 6. 二进制语料: 系统 DLL 只读拷贝 ----------
import ctypes
bin_src = None
for path in glob.glob(r'C:\Windows\System32\*.dll'):
    sz = os.path.getsize(path)
    if 400_000 <= sz <= 1_200_000:
        bin_src = path
        break
shutil.copyfile(bin_src, f'{OUT}/sample.dll')
print('sample.dll', os.path.getsize(f'{OUT}/sample.dll'), '<-', bin_src)
