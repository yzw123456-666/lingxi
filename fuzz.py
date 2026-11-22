"""灵犀无损性模糊测试: 尺寸扫描 + 恶性数据, 全部必须逐字节还原"""
import os, subprocess, hashlib, random, sys

TMP = 'tmp_fuzz'
os.makedirs(TMP, exist_ok=True)

def roundtrip(name, data):
    src, cmp_, rec = f'{TMP}/f.bin', f'{TMP}/f.lx', f'{TMP}/f.out'
    open(src, 'wb').write(data)
    r1 = subprocess.run(['./lingxi.exe', 'c', src, cmp_], capture_output=True)
    r2 = subprocess.run(['./lingxi.exe', 'd', cmp_, rec], capture_output=True)
    got = open(rec, 'rb').read() if os.path.exists(rec) else None
    ok = (r1.returncode == 0 and r2.returncode == 0 and got == data)
    os.remove(src); os.remove(cmp_)
    if os.path.exists(rec): os.remove(rec)
    if not ok:
        print(f'FAIL {name}: rc1={r1.returncode} rc2={r2.returncode} '
              f'match={got == data if got is not None else "no-output"}')
        print(' stderr1:', r1.stderr.decode(errors='replace')[:200])
        print(' stderr2:', r2.stderr.decode(errors='replace')[:200])
        return False
    return True

rng = random.Random(42)
fails = 0

# 1) 所有尺寸 0..300 的随机数据
for n in range(0, 301):
    if not roundtrip(f'rand-{n}', bytes(rng.randrange(256) for _ in range(n))):
        fails += 1
print('尺寸扫描 0..300: done')

# 2) 随机大尺寸
for n in [1024, 4097, 65536, 300001, 1 << 20]:
    if not roundtrip(f'rand-{n}', os.urandom(n)):
        fails += 1
print('随机大块: done')

# 3) 恶性数据
nasty = {
    'all-zero-2m': bytes(2 << 20),
    'all-ff-2m': b'\xff' * (2 << 20),
    'alt-2m': (b'\x00\xff' * (1 << 20)),
    'one-byte-repeat': b'Q' * 300000,
    'bytes-0-255-x1500': bytes(range(256)) * 1500,
    'low-entropy': b'abracadabra abracadabra ' * 40000,
    'utf8-ish': ('混合文本 mixed 汉字 āčģé ©ü 漢字テスト ' * 30000).encode('utf-8'),
}
for name, data in nasty.items():
    if not roundtrip(name, data):
        fails += 1
print('恶性数据: done')

print('FUZZ PASS' if fails == 0 else f'FUZZ FAILED: {fails}')
sys.exit(1 if fails else 0)
