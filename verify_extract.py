"""验证 Downloads 里的解包结果"""
import os, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
dest = r'C:\Users\ytzsy\Downloads\folder_test'
print('dest exists:', os.path.isdir(dest))
if os.path.isdir(dest):
    for r, ds, fs in os.walk(dest):
        rel = os.path.relpath(r, dest)
        print(' dir:', rel, '| subdirs:', ds, '| files:', fs)
    for f in ['a.txt', os.path.join('sub', 'b.bin')]:
        p = os.path.join(dest, f)
        print(f, '->', os.path.getsize(p), 'bytes' if os.path.exists(p) else 'MISSING')
