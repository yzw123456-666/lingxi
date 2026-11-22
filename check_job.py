"""监控后台压缩进程: CPU 增量 + 输出文件增长"""
import ctypes, os, sys, time
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
k32 = ctypes.windll.kernel32

class FT(ctypes.Structure):
    _fields_ = [('lo', ctypes.c_ulong), ('hi', ctypes.c_ulong)]

def q(f):
    return (f.hi << 32) | f.lo

def cpu(pid):
    h = k32.OpenProcess(0x1F0FFF, False, pid)
    if not h:
        return -1
    c, e, k, u = FT(), FT(), FT(), FT()
    k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(k), ctypes.byref(u))
    k32.CloseHandle(h)
    return q(k) / 1e7 + q(u) / 1e7

out = 'D:\\下载文件\\2026-11-22日以前.lx'
pid = 14704
sz1 = os.path.getsize(out) if os.path.exists(out) else -1
c1 = cpu(pid)
time.sleep(20)
sz2 = os.path.getsize(out) if os.path.exists(out) else -1
c2 = cpu(pid)
print(f'output: {sz1:,} -> {sz2:,} bytes (delta {sz2 - sz1:,})')
print(f'cpu: {c1:.1f}s -> {c2:.1f}s (delta {c2 - c1:.1f}s / 20s wall)')
print('alive:', c2 > 0)
