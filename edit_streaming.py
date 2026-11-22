"""引擎流式化重构: 循环窗口历史 + 文件API流式 + 移除 exit(1) (幂等)"""
src = open('lingxi.c', encoding='utf-8', newline='').read()
src = src.replace('\r\n', '\n')

def rep(old, new):
    global src
    if new in src and old not in src:
        print('skip:', old[:44])
        return
    assert old in src, 'NOT FOUND: ' + old[:70]
    src = src.replace(old, new, 1)

# 1) 历史窗口声明 (H 处)
rep('static U8  *H;                  /* 全历史缓冲 */',
    '''static U8  *H;                  /* 历史窗口 (循环缓冲) */
static size_t HCAP = 0;         /* 窗口容量 = min(文件+64K, HISTWIN) */
#ifndef HISTWIN
#define HISTWIN (1024u * 1024 * 1024)   /* 1GB 窗口: 更远的重复放弃精确匹配 */
#endif
#define HB(i) H[(i) % HCAP]''')

# 2) putbyte: 流式分支 + OOM 不再 exit
rep('''static void putbyte(U8 b) {
    if (olen == ocap) {
        ocap = ocap ? ocap * 2 : (1u << 20);
        obuf = (U8 *)realloc(obuf, ocap);
        if (!obuf) { fprintf(stderr, "out of memory\\n"); exit(1); }
    }
    obuf[olen++] = b;
}''',
    '''static void putbyte(U8 b) {
#ifndef LX_WASM
    if (g_out) { fputc(b, g_out); return; }       /* 流式: 直接写文件 */
#endif
    if (olen == ocap) {
        ocap = ocap ? ocap * 2 : (1u << 20);
        U8 *nb = (U8 *)realloc(obuf, ocap);
        if (!nb) { g_oom = 1; return; }           /* 内存不足: 标记并丢弃, 不闪退 */
    }
    obuf[olen++] = b;
}''')

# 3) getbyte: 流式输入分支
rep('static U8 getbyte(void) { return ipos < isize ? ibuf[ipos++] : 0; }',
    '''static U8 getbyte(void) {
#ifndef LX_WASM
    if (g_in) { int c = fgetc(g_in); return c == EOF ? 0 : (U8)c; }
#endif
    return ipos < isize ? ibuf[ipos++] : 0;
}''')

# 4) g_out/g_in/g_oom 声明 (putbyte 之前)
rep('''static void putbyte(U8 b) {''',
    '''#ifndef LX_WASM
static FILE *g_out;             /* 流式输出文件; NULL = 内存模式 */
static FILE *g_in;              /* 流式输入文件 (解压) */
#endif
static int g_oom = 0;           /* 内存不足标记: 置位后优雅失败, 绝不闪退 */

static void putbyte(U8 b) {''')

# 5) alloc_all: 失败标记而非 exit
rep('''    for (int m = 0; m < 14; m++) {
        T[m].c = (Cell *)calloc(TSZ[m], sizeof(Cell));
        T[m].mask = TSZ[m] - 1;
        T[m].direct = (m < 2);      /* o0/o1 直接索引, 其余 2 路哈希 */
        if (!T[m].c) { fprintf(stderr, "out of memory\\n"); exit(1); }
    }''',
    '''    g_oom = 0;
    for (int m = 0; m < 14; m++) {
        T[m].c = (Cell *)calloc(TSZ[m], sizeof(Cell));
        T[m].mask = TSZ[m] - 1;
        T[m].direct = (m < 2);      /* o0/o1 直接索引, 其余 2 路哈希 */
        if (!T[m].c) { g_oom = 1; return; }
    }''')
rep('''    MT = (U32 *)calloc(1u << MTBITS, sizeof(U32));
    indT = (U8 *)calloc(1u << 20, 1);
    if (!MT || !indT) { fprintf(stderr, "out of memory\\n"); exit(1); }''',
    '''    MT = (U32 *)calloc(1u << MTBITS, sizeof(U32));
    indT = (U8 *)calloc(1u << 20, 1);
    if (!MT || !indT) { g_oom = 1; return; }''')

# 6) ensure_init: OOM 时不进入就绪态
rep('''static void ensure_init(void) {
    if (g_eng_init) return;
    init_tables(); init_rate(); crc_init(); alloc_all();
    g_eng_init = 1;
}''',
    '''static void ensure_init(void)
{
    if (g_eng_init) return;
    init_tables(); init_rate(); crc_init();
    alloc_all();
    if (!g_oom) g_eng_init = 1;
}''')

# 7) CRC 增量助手 (crc32buf 之后)
rep('''static U32 crc32buf(const U8 *b, size_t len) {
    U32 c = 0xFFFFFFFFu;
    for (size_t k = 0; k < len; k++)
        c = crc_tab[(c ^ b[k]) & 0xFFu] ^ (c >> 8);
    return c ^ 0xFFFFFFFFu;
}''',
    '''static U32 crc32buf(const U8 *b, size_t len) {
    U32 c = 0xFFFFFFFFu;
    for (size_t k = 0; k < len; k++)
        c = crc_tab[(c ^ b[k]) & 0xFFu] ^ (c >> 8);
    return c ^ 0xFFFFFFFFu;
}
static U32 crc32_update(U32 crc, const U8 *b, size_t len) {
    for (size_t k = 0; k < len; k++)
        crc = crc_tab[(crc ^ b[k]) & 0xFFu] ^ (crc >> 8);
    return crc;
}
#define CRC32_FINAL(c) ((c) ^ 0xFFFFFFFFu)''')

# 8) process_byte: 循环窗口索引 + 远距匹配保护
rep('    int b = y;\n    H[n] = (U8)b;',
    '    int b = y;\n    HB(n) = (U8)b;')
rep('''        int mst = 0;                                 /* 长程匹配 */
        if (mLen > 0) {
            U32 pb = H[mPos];''',
    '''        int mst = 0;                                 /* 长程匹配 */
        if (mLen > 0) {
            U32 pb = HB(mPos);''')
rep('''    MT[key] = n;

    n++;''',
    '''    if (mLen > 0 && n - mPos >= HCAP) mLen = 0;   /* 超出窗口: 放弃长匹配 */
    MT[key] = n;

    n++;''')

# 9) lx_shutdown: 窗口归零
rep('''    free(MT); free(indT); MT = NULL; indT = NULL;
    g_eng_init = 0;
#ifdef LX_WASM
    arena_off = 0;              /* 整个 arena 归零, 下个作业重新分配 */
#endif
}''',
    '''    free(MT); free(indT); MT = NULL; indT = NULL;
    free(H); H = NULL; HCAP = 0;
    g_eng_init = 0;
#ifdef LX_WASM
    arena_off = 0;              /* 整个 arena 归零, 下个作业重新分配 */
#endif
}''')

open('lingxi.c', 'w', encoding='utf-8', newline='\n').write(src)
print('streaming/window core applied OK')
