"""生成应用图标 app.ico (32x32 BMP 格式 ICO, 靛蓝-青色渐变圆角方块)"""
import struct

W = H = 32

def inside(x, y):
    r = 7
    if r <= x <= W - 1 - r or r <= y <= H - 1 - r:
        return True
    cx = r if x < r else (W - 1 - r if x > W - 1 - r else x)
    cy = r if y < r else (H - 1 - r if y > H - 1 - r else y)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r

def pixel(x, y):
    t = (x + y) / (2.0 * (W - 1))
    r = int(124 + (76 - 124) * t)
    g = int(131 + (201 - 131) * t)
    b = int(255 + (240 - 255) * t)
    return r, g, b, 255

# 像素数据: 自底向上 BGRA
px = bytearray()
for y in range(H - 1, -1, -1):
    for x in range(W):
        if inside(x, y):
            r, g, b, a = pixel(x, y)
            px += struct.pack('<BBBB', b, g, r, a)
        else:
            px += struct.pack('<BBBB', 0, 0, 0, 0)
# AND 掩码 (全 0 = 使用 alpha)
mask = bytearray(H * ((W // 8 + 3) // 4 * 4) // 8 * 8 + (H * 4))
mask = bytearray((W // 32 + 1) * 4 * H)

bih = struct.pack('<IiiHHIIiiII', 40, W, H * 2, 1, 32, 0, len(px) + len(mask), 0, 0, 0, 0)
img = bih + bytes(px) + bytes(mask)
entry = struct.pack('<BBBBHHII', W, H, 0, 0, 1, 32, len(img), 6 + 16)
ico = struct.pack('<HHH', 0, 1, 1) + entry + img
open('app.ico', 'wb').write(ico)
print('app.ico:', len(ico), 'bytes')
