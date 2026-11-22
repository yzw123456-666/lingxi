"""检查压缩输出文件 (干净路径)"""
import os, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
out = os.path.join('D:\\', '下载文件', '2026-11-22日以前.lx')
print('目标:', out)
print('存在:', os.path.exists(out))
if os.path.exists(out):
    print(f'大小: {os.path.getsize(out):,} bytes = {os.path.getsize(out)/1073741824:.2f} GB')
