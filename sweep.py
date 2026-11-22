"""参数坐标下降扫描: LMX -> RATE_CAP -> APM_RATE, 以总压缩字节为目标"""
import subprocess, os, sys

ZIG = ['python', '-m', 'ziglang', 'cc', '-O2']
FILES = ['corpus/code_py.txt', 'corpus/zh_prose.txt', 'corpus/docs_en.txt']

def build_and_measure(lmx, cap, apm):
    exe = f'tmp_sweep/lx_{lmx}_{cap}_{apm}.exe'
    r = subprocess.run(ZIG + [f'-DLMX={lmx}', f'-DRATE_CAP={cap}',
                              f'-DAPM_RATE={apm}', 'lingxi.c', '-o', exe],
                       capture_output=True)
    if r.returncode != 0:
        print('BUILD FAIL', lmx, cap, apm); return 10**12
    total = 0
    for f in FILES:
        out = exe + '.lx'
        rr = subprocess.run([exe, 'c', f, out], capture_output=True)
        total += os.path.getsize(out)
        os.remove(out)
    return total

os.makedirs('tmp_sweep', exist_ok=True)
best = (12, 4, 7)
best_total = build_and_measure(*best)
print(f'start LMX={best[0]} CAP={best[1]} APM={best[2]}: {best_total}')

for lmx in [8, 10, 14]:
    t = build_and_measure(lmx, best[1], best[2])
    print(f'LMX={lmx:3d} CAP={best[1]} APM={best[2]}: {t}  {"<== better" if t < best_total else ""}')
    if t < best_total: best, best_total = (lmx, best[1], best[2]), t
print('best after LMX:', best, best_total)

for cap in [3, 5]:
    t = build_and_measure(best[0], cap, best[2])
    print(f'LMX={best[0]} CAP={cap} APM={best[2]}: {t}  {"<== better" if t < best_total else ""}')
    if t < best_total: best, best_total = (best[0], cap, best[2]), t
print('best after CAP:', best, best_total)

for apm in [6, 8]:
    t = build_and_measure(best[0], best[1], apm)
    print(f'LMX={best[0]} CAP={best[1]} APM={apm}: {t}  {"<== better" if t < best_total else ""}')
    if t < best_total: best, best_total = (best[0], best[1], apm), t
print('FINAL:', best, best_total)
with open('best_params.txt', 'w') as f:
    f.write(f'{best[0]} {best[1]} {best[2]}\n')
