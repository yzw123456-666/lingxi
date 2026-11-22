"""灵犀 vs 主流最强通用无损压缩器 基准测试
对比对象: gzip(-9) bzip2(-9) xz(-9e) zstd(-19) brotli(-q11) —— 全部最高强度设置
灵犀: ./lingxi.exe (命令行压缩/解压, 逐字节+CRC双重校验)
"""
import os, sys, time, zlib, bz2, lzma, hashlib, subprocess
import zstandard, brotli

FILES = ['corpus/code_py.txt', 'corpus/docs_en.txt', 'corpus/zh_dict.txt',
         'corpus/zh_prose.txt', 'corpus/logs.json', 'corpus/sample.dll']
NAMES = {'code_py.txt': 'Python源码', 'docs_en.txt': '英文文档',
         'zh_dict.txt': '中文词典', 'zh_prose.txt': '中文散文',
         'logs.json': 'JSON日志', 'sample.dll': '二进制'}
LX = './lingxi.exe'
TMP = 'tmp_bench'
os.makedirs(TMP, exist_ok=True)

def sha(b): return hashlib.sha256(b).hexdigest()

def lingxi(path):
    c, d = f'{TMP}/b.lx', f'{TMP}/b.out'
    t0 = time.perf_counter()
    r1 = subprocess.run([LX, 'c', path, c], capture_output=True)
    t1 = time.perf_counter()
    r2 = subprocess.run([LX, 'd', c, d], capture_output=True)
    t2 = time.perf_counter()
    assert r1.returncode == 0 and r2.returncode == 0, (r1.stderr, r2.stderr)
    orig = open(path, 'rb').read()
    rec = open(d, 'rb').read()
    assert rec == orig, '灵犀还原失败!'
    size = os.path.getsize(c)
    os.remove(c); os.remove(d)
    return size, t1 - t0, t2 - t1

TOOLS = [
    ('gzip -9',    lambda b: zlib.compress(b, 9)),
    ('bzip2 -9',   lambda b: bz2.compress(b, 9)),
    ('xz -9e',     lambda b: lzma.compress(b, format=lzma.FORMAT_XZ,
                                           preset=9 | lzma.PRESET_EXTREME)),
    ('zstd -19',   lambda b: zstandard.ZstdCompressor(level=19).compress(b)),
    ('brotli -q11', lambda b: brotli.compress(b, quality=11)),
]

def main():
    rows = []
    grand = {}
    for path in FILES:
        name = NAMES[os.path.basename(path)]
        data = open(path, 'rb').read()
        n = len(data)
        row = {'file': name, 'orig': n}
        lx_size, lx_ct, lx_dt = lingxi(path)
        row['灵犀'] = lx_size; row['灵犀_t'] = lx_ct; row['灵犀_dt'] = lx_dt
        for label, fn in TOOLS:
            t0 = time.perf_counter()
            blob = fn(data)
            t1 = time.perf_counter()
            # 主流工具也验证无损
            if label == 'gzip -9':
                back = zlib.decompress(blob)
            elif label == 'bzip2 -9':
                back = bz2.decompress(blob)
            elif label == 'xz -9e':
                back = lzma.decompress(blob, format=lzma.FORMAT_XZ)
            elif label == 'zstd -19':
                back = zstandard.ZstdDecompressor().decompress(blob)
            else:
                back = brotli.decompress(blob)
            assert back == data, f'{label} 自校验失败'
            row[label] = len(blob); row[label + '_t'] = t1 - t0
            grand[label] = grand.get(label, 0) + len(blob)
        grand['灵犀'] = grand.get('灵犀', 0) + lx_size
        rows.append(row)
        sys.stdout.flush()

    hdr = ['file', 'orig', '灵犀', 'gzip -9', 'bzip2 -9', 'xz -9e', 'zstd -19', 'brotli -q11']
    print('\n===== 分文件压缩结果 (字节) =====')
    print('| 文件 | 原始 | 灵犀 | gzip -9 | bzip2 -9 | xz -9e | zstd -19 | brotli -q11 |')
    print('|---|---|---|---|---|---|---|---|')
    for row in rows:
        cells = [row['file'], f'{row["orig"]:,}']
        for k in hdr[2:]:
            cells.append(f'{row[k]:,}' + (' ⭐' if k == min(hdr[2:], key=lambda x: row[x]) else ''))
        print('| ' + ' | '.join(cells) + ' |')

    print('\n===== 总计 =====')
    tot_orig = sum(r['orig'] for r in rows)
    best_main = min(grand[k] for k in grand if k != '灵犀')
    print(f'总原始: {tot_orig:,} 字节')
    for k in ['灵犀', 'gzip -9', 'bzip2 -9', 'xz -9e', 'zstd -19', 'brotli -q11']:
        bpc = grand[k] * 8 / tot_orig
        print(f'{k:12s}: {grand[k]:>12,} 字节  {bpc:.4f} bpc  ({grand[k]*100/tot_orig:.2f}%)')
    print(f'\n灵犀比最强主流工具总成绩再省 {best_main - grand["灵犀"]:,} 字节 '
          f'({(best_main - grand["灵犀"]) * 100.0 / best_main:.1f}%)')

    print('\n===== 灵犀耗时 (压缩/解压) =====')
    for row in rows:
        print(f'{row["file"]:8s}: {row["灵犀_t"]:.2f}s / {row["灵犀_dt"]:.2f}s '
              f'({row["orig"]/1e6/max(row["灵犀_t"],0.01):.2f} MB/s)')

if __name__ == '__main__':
    main()
