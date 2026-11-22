"""实战测试: 通过运行中的应用打包并压缩 D:\\下载文件\\2026-11-22日以前 (7.67GB)"""
import os, json, sys, time, urllib.request
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

port = open(os.path.join(os.environ['TMP'], 'lxapp_port.txt')).read().strip()
base = f'http://127.0.0.1:{port}'
FOLDER = 'D:\\下载文件\\2026-11-22日以前'
TMP_LXF = 'D:\\下载文件\\_pack_tmp.lxf'
OUT_LX = 'D:\\下载文件\\2026-11-22日以前.lx'

print('目标:', FOLDER)
print('exists:', os.path.isdir(FOLDER))

# 1) 打包 (应用 PackFolder 流式写盘)
print('打包中…')
req = urllib.request.Request(base + '/api/packdir',
    data=json.dumps({'path': FOLDER}).encode(), method='POST')
req.add_header('Content-Type', 'application/json')
r = urllib.request.urlopen(req, timeout=1800)
total = 0
with open(TMP_LXF, 'wb') as f:
    while True:
        chunk = r.read(1 << 20)
        if not chunk:
            break
        f.write(chunk)
        total += len(chunk)
print('LXF:', f'{total:,}', 'bytes =', round(total / 1073741824, 2), 'GB')

# 2) 后台压缩 (detached CLI, 流式窗口引擎)
cmd = f'start "" /b "{os.path.abspath("lingxi.exe")}" c "{TMP_LXF}" "{OUT_LX}"'
os.system(cmd)
print('压缩已在后台启动 →', OUT_LX)
print('进度查看: 观察输出文件大小增长, 或 %TEMP%\\lxapp_port.txt 同目录无进度文件')
