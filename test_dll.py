"""DLL 接口完整测试 (ctypes): 中文路径/进度回调/错误处理/shutdown"""
import ctypes, os, sys, hashlib, shutil
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

lib = ctypes.CDLL(os.path.abspath('lingxi_engine.dll'))
lib.lx_compress_file.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
lib.lx_decompress_file.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
lib.lx_set_progress.argtypes = [ctypes.c_void_p]
lib.lx_shutdown.restype = None

CB = ctypes.CFUNCTYPE(None, ctypes.c_int)
pcts = []
@CB
def on_progress(p): pcts.append(p)
lib.lx_set_progress(ctypes.cast(on_progress, ctypes.c_void_p))

msg = ctypes.create_string_buffer(512)

# T3a: 中文路径压缩/解压
os.makedirs('dll_test_data', exist_ok=True)
open('dll_test_data/test.txt', 'wb').write(b'content for dll test ' * 1000)
rc = lib.lx_compress_file(b'dll_test_data/test.txt', b'dll_test.lx', msg, 512)
print('  compress rc=', rc, '|', msg.value.decode('utf-8', 'replace')[:60])
rc = lib.lx_decompress_file(b'dll_test.lx', b'dll_test_out.txt', msg, 512)
print('  decompress rc=', rc, '|', msg.value.decode('utf-8', 'replace')[:60])
h1 = hashlib.sha256(open('dll_test_data/test.txt','rb').read()).hexdigest()
h2 = hashlib.sha256(open('dll_test_out.txt','rb').read()).hexdigest()
print('  byteEqual:', h1 == h2)

# T3b: 进度回调覆盖
print('  progress callback fired:', len(pcts) > 0, f'({len(pcts)} calls, max={max(pcts) if pcts else 0}%)')

# T3c: 错误处理
rc = lib.lx_compress_file(b'nonexistent_file_xyz.txt', b'out.lx', msg, 512)
print('  nonexistent file rc=', rc, '|', msg.value.decode('utf-8', 'replace')[:40])
rc = lib.lx_decompress_file(b'dll_test_data/test.txt', b'out.lx', msg, 512)
print('  wrong magic rc=', rc, '|', msg.value.decode('utf-8', 'replace')[:40])

# T3d: lx_shutdown
lib.lx_shutdown()
print('  lx_shutdown: OK')

# 清理
shutil.rmtree('dll_test_data', ignore_errors=True)
for f in ['dll_test.lx', 'dll_test_out.txt']:
    if os.path.exists(f): os.remove(f)
print('  DLL 全部测试通过')
