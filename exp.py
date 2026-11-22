"""优化实验框架: 编译当前 lingxi.c -> 全语料压缩 -> 分文件+总计报告
用法: python -I exp.py [标签] [-DLMX=1 ...]
"""
import subprocess, os, sys, time

ZIG = ['python', '-m', 'ziglang', 'cc', '-O2']
FILES = ['corpus/code_py.txt', 'corpus/docs_en.txt', 'corpus/zh_dict.txt',
         'corpus/zh_prose.txt', 'corpus/logs.json', 'corpus/sample.dll']
SHORT = ['code', 'docs', 'dict', 'prose', 'logs', 'dll']
BASELINE = 2808150  # v0.1 调优后基线

def main():
    tag = 'exp'
    extra = []
    args = sys.argv[1:]
    if args and not args[0].startswith('-D'):
        tag = args[0]; args = args[1:]
    extra = args
    exe = f'tmp_exp/{tag}.exe'
    os.makedirs('tmp_exp', exist_ok=True)
    r = subprocess.run(ZIG + extra + ['lingxi.c', '-o', exe], capture_output=True)
    if r.returncode != 0:
        print('BUILD FAIL'); print(r.stderr.decode(errors='replace')[:2000]); sys.exit(1)
    total, per, times = 0, {}, {}
    for f, s in zip(FILES, SHORT):
        t0 = time.perf_counter()
        rr = subprocess.run([exe, 'c', f, f'tmp_exp/{s}.lx'], capture_output=True)
        times[s] = time.perf_counter() - t0
        per[s] = os.path.getsize(f'tmp_exp/{s}.lx')
        # 快速无损抽检(两个小文件)
        if s in ('prose', 'dll'):
            rd = subprocess.run([exe, 'd', f'tmp_exp/{s}.lx', f'tmp_exp/{s}.out'],
                                capture_output=True)
            assert rd.returncode == 0, f'解压失败 {s}'
            assert open(f'tmp_exp/{s}.out','rb').read() == open(f,'rb').read(), f'还原不一致 {s}'
        total += per[s]
    delta = total - BASELINE
    speeds = ' '.join(f'{s}:{v:.1f}s' for s, v in times.items() if v > 0.05)
    print(f'[{tag}] 总计 {total:,} ({delta:+,} vs 基线, {delta*100/BASELINE:+.2f}%)')
    print('  ' + ' '.join(f'{s}:{v//1000}K' for s, v in per.items()))
    print(f'  耗时 {speeds}')
    with open('OPTLOG.csv', 'a', encoding='utf-8') as fh:
        fh.write(f'{tag},{total},{delta},{",".join(str(v) for v in per.values())}\n')

if __name__ == '__main__':
    main()
