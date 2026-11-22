"""等待后台压缩完成并输出结果"""
import os, sys, json, time, urllib.request
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
port = open(os.path.join(os.environ['TMP'], 'lxapp_port.txt')).read().strip()
base = f'http://127.0.0.1:{port}'
out = 'D:\\下载文件\\2026-11-22日以前.lx'

start = time.time()
while True:
    try:
        st = json.loads(urllib.request.urlopen(base + '/api/status', timeout=30).read().decode('utf-8'))
    except Exception as e:
        print('status error:', e, flush=True)
        time.sleep(10)
        continue
    el = (time.time() - start) / 60
    if not st['busy']:
        print(f'=== 任务完成! 总耗时约 {el:.0f} 分钟 ===', flush=True)
        try:
            res = open(os.path.join(os.environ['TMP'], 'compress_result.json'), encoding='utf-8').read()
            print('结果:', res[:500], flush=True)
        except OSError:
            print('(HTTP 结果文件不可读, 以文件系统为准)', flush=True)
        sz = os.path.getsize(out) if os.path.exists(out) else 0
        print(f'输出文件: {out} = {sz:,} bytes ({sz/1073741824:.2f} GB)', flush=True)
        break
    print(f'进度 {st["progress"]}% ...', flush=True)
    time.sleep(60)
    if el > 40:
        print('超时 40 分钟, 退出监控 (任务仍在后台)', flush=True)
        break
