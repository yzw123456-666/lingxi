"""生成固化概率表 (跨平台逐位一致: CLI/WASM/任何编译器同一张表)
公式与运行时版本完全一致: C 的 (int) 截断语义"""
import math

stretch = []
for p in range(4096):
    x = (p + 0.5) / 4096.0
    s = 256.0 * math.log(x / (1.0 - x))
    if s > 2047.0: s = 2047.0
    if s < -2047.0: s = -2047.0
    stretch.append(int(s))          # C 语义: 向零截断

squash = []
for i in range(4096):
    s = i - 2048
    x = 1.0 / (1.0 + math.exp(-s / 256.0))
    p = int(x * 4096.0)
    if p < 1: p = 1
    if p > 4095: p = 4095
    squash.append(p)

with open('tables_gen.h', 'w', encoding='utf-8', newline='\n') as f:
    f.write('/* 固化概率表 (gen_tables.py 生成, 跨编译器/跨目标逐位一致) */\n')
    f.write('static const int stretch_t[4096] = {')
    for i in range(0, 4096, 8):
        f.write('\n  ' + ','.join(str(v) for v in stretch[i:i+8]) + ',')
    f.write('\n};\n')
    f.write('static const short squash_t[4096] = {')
    for i in range(0, 4096, 16):
        f.write('\n  ' + ','.join(str(v) for v in squash[i:i+16]) + ',')
    f.write('\n};\n')
print('tables_gen.h written:',
      'stretch range', min(stretch), '..', max(stretch),
      '| squash range', min(squash), '..', max(squash))
