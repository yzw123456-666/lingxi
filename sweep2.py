"""终扫: 在新架构上协调下降 (MINLEN/APM/RATE_CAP/LMX/MBASE)"""
import subprocess, os, sys

ZIG = ['python', '-m', 'ziglang', 'cc', '-O2']
FILES = ['corpus/code_py.txt', 'corpus/docs_en.txt', 'corpus/zh_dict.txt',
         'corpus/zh_prose.txt', 'corpus/logs.json', 'corpus/sample.dll']

def bm(extra):
    exe = 'tmp_exp/sw.exe'
    r = subprocess.run(ZIG + extra + ['lingxi.c', '-o', exe], capture_output=True)
    if r.returncode != 0:
        print('BUILD FAIL', r.stderr.decode(errors='replace')[-500:]); return 10**12
    tot = 0
    for f in FILES:
        subprocess.run([exe, 'c', f, exe + '.lx'], capture_output=True)
        tot += os.path.getsize(exe + '.lx'); os.remove(exe + '.lx')
    return tot

base = ['-DLMX=1', '-DRATE_CAP=2', '-DAPM_RATE=5']
best_cfg, best = list(base), 2_498_736
print(f'baseline: {best:,}')

for name, extra in [
    ('MINLEN=5',  ['-DMINLEN=5']),
    ('MINLEN=7',  ['-DMINLEN=7']),
    ('APM=4',     ['-DAPM_RATE=4']),
    ('APM=6',     ['-DAPM_RATE=6']),
    ('CAP=1',     ['-DRATE_CAP=1']),
    ('CAP=3',     ['-DRATE_CAP=3']),
    ('LMX=2',     ['-DLMX=2']),
    ('MBASE=1344',['-DMBASE=1344']),
    ('MBASE=1472',['-DMBASE=1472']),
    ('MSTEP=16',  ['-DMSTEP=16']),
    ('MSTEP=24',  ['-DMSTEP=24']),
]:
    t = bm(best_cfg + extra)
    mark = ''
    if t < best:
        best = t; best_cfg += extra; mark = ' <== KEEP'
    print(f'{name:12s}: {t:,}{mark}')
    sys.stdout.flush()

print('FINAL CFG:', ' '.join(best_cfg), best)
open('best_params2.txt', 'w').write(' '.join(best_cfg) + '\n')
