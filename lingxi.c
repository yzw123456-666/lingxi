/*
 * 灵犀 (LingXi) v0.1 —— 上下文混合无损压缩器
 * ============================================================
 * 设计哲学: 压缩即预测 (Shannon 1948: 最优压缩器 = 最优预测器)
 *
 * 架构(全部在线学习, 无预训练, 单遍压缩):
 *   [感知层] 十一路异构预测器
 *     - 多尺度哈希情景记忆: 0阶/1阶/2阶/3阶/4阶/6阶/8阶字节上下文
 *     - 稀疏跳跃上下文: (c3,c1) (c4,c2) 跳过相邻字节, 抗噪声
 *     - 词法感知器: 当前英文单词哈希 (跨词边界的长记忆)
 *     - 长程匹配器: 6字节哈希定位历史同文, 匹配越长预测越自信
 *     - 运行模型: 连续重复字节
 *   [决策层] 学习型混合器: 以 c0(已编码位前缀) 索引 256 组权重,
 *     逻辑斯蒂混合 11 路预测, 在线梯度下降
 *   [精修层] 双级自适应概率图 (APM/SSE): 按 (预测,上下文) 细化
 *   [写码层] 12位区间编码器 (LZMA式进位传播, 渐进溢出安全)
 *
 * 用法: lingxi c <in> <out>   压缩
 *       lingxi d <in> <out>   解压
 */
#ifndef LX_WASM
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>
#else
#include <stddef.h>
/* freestanding: 本文件尾部提供最小运行时实现 */
void *memcpy(void *d, const void *s, size_t n);
void *memset(void *d, int c, size_t n);
int memcmp(const void *a, const void *b, size_t n);
void *malloc(size_t n);
void *calloc(size_t a, size_t b);
void free(void *p);
void *realloc(void *p, size_t n);
void exit(int);
extern unsigned char __heap_base[];
static size_t arena_off = 0;
#define fprintf(...) ((void)0)
#define stderr ((void *)0)
#endif

#ifdef _WIN32
#define LXAPI __attribute__((dllexport))
#else
#define LXAPI
#endif

typedef unsigned char  U8;
typedef unsigned short U16;
typedef unsigned int   U32;
typedef unsigned long long U64;

#define TOP (1u << 24)          /* 区间编码器归一化阈值 */

#ifndef LMX
#define LMX 1                   /* 混合器学习率(定点, 经全语料坐标下降调优) */
#endif
#ifndef LSHIFT
#define LSHIFT 12               /* 权重更新右移位数(与LMX共同决定步长) */
#endif
#ifndef RATE_CAP
#define RATE_CAP 3              /* 计数器最终自适应步长 1/2^RATE_CAP */
#endif
#ifndef APM_RATE
#define APM_RATE 6              /* APM 自适应步长 1/2^APM_RATE */
#endif
#ifndef MINLEN
#define MINLEN 6                /* 长程匹配器最小可信匹配 */
#endif
#ifndef MBASE
#define MBASE 1344              /* 匹配置信度基净 */
#endif
#ifndef MSTEP
#define MSTEP 16                /* 每延长一字节增加的置信度 */
#endif

#define NIN 17                  /* 混合器输入数 */
#define PHI 0x9E3779B1u         /* 黄金比例哈希常数 */

/* ================= 概率域变换 (stretch/squash 互逆) ================= */
#include "tables_gen.h"         /* 固化概率表: 跨编译器/跨目标逐位一致 */
static void init_tables(void) { }
#define SQ(s) squash_t[(s) + 2048]
#define SQ(s) squash_t[(s) + 2048]

static U32 fold(U32 h, U32 c) { return (h + c + 1u) * PHI; }

/* ================= 计数器表 (16位概率 + 8位置信度) ================= */
/* Cell 交错布局: p/n 同一缓存行, 每模型每比特 1 次缓存未命中。
   哈希表为 2 路关联: 每桶 2 个候选 + 8 位校验码, 校验不符则
   确定性地替换置信度较低者 (编码/解码两侧严格一致)。 */
typedef struct { U16 p; U8 n; U8 chk; } Cell;
typedef struct { Cell *c; U32 mask; int direct; } Tbl;
static Tbl T[14];
static const U32 TSZ[14] = { 256, 1u<<16, 1u<<18, 1u<<21, 1u<<21,
                             1u<<22, 1u<<22, 1u<<20, 1u<<20, 1u<<20,
                             1u<<20, 1u<<18, 1u<<18, 1u<<18 };
/*                     o0     o1       o2       o3       o4
                       o6     o8       sp1      sp2      word     */

static U8 RATE[64];
static void init_rate(void) {
    for (int n = 0; n < 64; n++) {
        int r;
        if      (n == 0) r = 1;   /* 第一次见到: 大胆跳变 */
        else if (n == 1) r = 2;
        else if (n <= 3) r = 3;
        else             r = RATE_CAP;
        RATE[n] = (U8)r;
    }
}

/* 哈希表取单元: 2 路关联 + 校验码; 直接表按位取用 */
static inline Cell *tget(int ti, U32 h, U32 dkey) {
    Tbl *t = &T[ti];
    if (t->direct) return &t->c[dkey & t->mask];
    U32 b = (h >> 8) & (t->mask >> 1);
    Cell *c0 = &t->c[b * 2], *c1 = c0 + 1;
    U32 chk = h & 0xFF;
    if (c0->chk == chk) return c0;
    if (c1->chk == chk) return c1;
    Cell *v = (c0->n < c1->n) ? c0 : c1;
    v->p = 32768; v->n = 0; v->chk = (U8)chk;
    return v;
}

/* ================= 学习型混合器 (c0 索引 256 组权重) ================= */
static int Wmix[3][256][NIN];   /* 权重组: [0]=常规 [1]=短匹配 [2]=长匹配(>=16) */

/* ================= 双级概率精修 APM + 学习式终混 ================= */
static U16 A3[65536 * 33];      /* 上下文: hash(c2,c0) */
static int W3[256][5];          /* 终混: [st(pr), st(a1), st(a2), st(a3), 恒定] */
static U16 A1[256 * 33];        /* 上下文: c0 */
static U16 A2[65536 * 33];      /* 上下文: hash(c1,c0) */

/* ================= 长程匹配器 ================= */
static U32 *MT;                 /* 上下文哈希 -> 下一次出现的位置 */
#define MTBITS 22
#define MTMASK ((1u << MTBITS) - 1u)

/* ================= 全局状态 ================= */
static U8  *H;                  /* 历史窗口 (循环缓冲) */
static size_t HCAP = 0;         /* 窗口容量 = min(文件+64K, HISTWIN) */
#ifndef HISTWIN
#define HISTWIN (1024u * 1024 * 1024)   /* 1GB 窗口: 更远的重复放弃精确匹配 */
#endif
#define HB(i) H[(i) % HCAP]
static U32  n;                  /* 已处理字节数(含当前正在编码者待写) */
static U8   c1, c2, c3, c4, c5, c6, c7, c8, c9, c10, c11, c12;
static U32  H2v, H3v, H4v, H6v, H8v, H12v, SP1v, SP2v, wordH, prevWordH;
static U8   runB; static U32 runC;
static U32  mPos; static int mLen;
static U8  *indT;               /* 间接上下文: 4阶上下文 -> 其历史后继字节 */
static U32  lineStart, prevLineStart;

static void ctx_hashes(void) {
    H2v  = fold(fold(0, c2), c1);
    H3v  = fold(H2v, c3);
    H4v  = fold(H3v, c4);
    H6v  = fold(fold(H4v, c5), c6);
    H8v  = fold(fold(H6v, c7), c8);
    H12v = fold(fold(fold(fold(H8v, c9), c10), c11), c12);
    SP1v = fold(fold(0, c3), c1);
    SP2v = fold(fold(0, c4), c2);
}

/* ================= 区间编码器 (LZMA式进位传播) ================= */
static U64 rc_low;              /* 64位低位累计(高位为待进位传播) */
static U32 rc_range;
static U8  rc_cache;
static U64 rc_cacheSize;
static U8  *obuf; static size_t olen, ocap;
static const U8 *ibuf; static size_t ipos, isize;

#ifndef LX_WASM
static FILE *g_out;             /* 流式输出文件; NULL = 内存模式 */
static FILE *g_in;              /* 流式输入文件 (解压) */
#endif
static int g_oom = 0;           /* 内存不足标记: 置位后优雅失败, 绝不闪退 */

static void putbyte(U8 b) {
#ifndef LX_WASM
    if (g_out) { fputc(b, g_out); return; }       /* 流式: 直接写文件 */
#endif
    if (olen == ocap) {
        ocap = ocap ? ocap * 2 : (1u << 20);
        U8 *nb = (U8 *)realloc(obuf, ocap);
        if (!nb) { g_oom = 1; return; }           /* 内存不足: 标记并丢弃, 不闪退 */
    }
    obuf[olen++] = b;
}
static U8 getbyte(void) {
#ifndef LX_WASM
    if (g_in) { int c = fgetc(g_in); return c == EOF ? 0 : (U8)c; }
#endif
    return ipos < isize ? ibuf[ipos++] : 0;
}

static void shift_low(void) {
    if ((U32)rc_low < 0xFF000000u || (rc_low >> 32) != 0) {
        U8 temp = rc_cache;
        do {
            putbyte((U8)(temp + (U8)(rc_low >> 32)));
            temp = 0xFF;
        } while (--rc_cacheSize != 0);
        rc_cache = (U8)(rc_low >> 24);
    }
    rc_cacheSize++;
    rc_low = (U32)rc_low << 8;
}

static void rc_init_enc(void) {
    rc_low = 0; rc_range = 0xFFFFFFFFu;
    rc_cache = 0; rc_cacheSize = 1; olen = 0;
}
static void rc_flush(void) { for (int k = 0; k < 5; k++) shift_low(); }

static U32 rc_code;             /* 解码器专用区间码 */

static void rc_init_dec(void) {
    rc_range = 0xFFFFFFFFu;
    rc_code = 0;
    for (int k = 0; k < 5; k++) rc_code = (rc_code << 8) | getbyte();
}

/* p = P(bit==1), 12位定值域 [2,4094] */
static inline void enc_bit(int bit, int p) {
    U32 bound0 = (rc_range >> 12) * (U32)(4096 - p);   /* bit==0 区间宽 */
    if (!bit) {
        rc_range = bound0;
    } else {
        rc_low  += bound0;
        rc_range -= bound0;
    }
    while (rc_range < TOP) { shift_low(); rc_range <<= 8; }
}
static inline int dec_bit(int p) {
    U32 bound0 = (rc_range >> 12) * (U32)(4096 - p);
    int bit = (rc_code >= bound0);
    if (bit) { rc_code -= bound0; rc_range -= bound0; }
    else     { rc_range = bound0; }
    while (rc_range < TOP) {
        rc_range <<= 8;
        rc_code = (rc_code << 8) | getbyte();
    }
    return bit;
}

/* ================= 核心: 单字节 处理(编码/解码共用同一路径) ================= */
static int process_byte(int C, int decoding) {
    int  x[NIN];
    Cell *CP[14];
    int  y = 0;

    for (int i = 0; i < 8; i++) {
        int c0 = (1 << i) | y;   /* 含前导1的部分字节 */

        /* ---- 感知层: 11 路预测器 ---- */
        x[0] = 256;                                            /* 偏置 */
        CP[0] = tget(0, 0, (U32)c0);
        x[1] = stretch_t[CP[0]->p >> 4];                       /* o0 */
        CP[1] = tget(1, 0, ((U32)c1 << 8) | (U32)c0);
        x[2] = stretch_t[CP[1]->p >> 4];                       /* o1 */
        CP[2] = tget(2, fold(H2v,  (U32)c0), 0);
        x[3] = stretch_t[CP[2]->p >> 4];                       /* o2 */
        CP[3] = tget(3, fold(H3v,  (U32)c0), 0);
        x[4] = stretch_t[CP[3]->p >> 4];                       /* o3 */
        CP[4] = tget(4, fold(H4v,  (U32)c0), 0);
        x[5] = stretch_t[CP[4]->p >> 4];                       /* o4 */
        CP[5] = tget(5, fold(H6v,  (U32)c0), 0);
        x[6] = stretch_t[CP[5]->p >> 4];                       /* o6 */
        CP[6] = tget(6, fold(H8v,  (U32)c0), 0);
        x[7] = stretch_t[CP[6]->p >> 4];                       /* o8 */
        CP[7] = tget(7, fold(SP1v, (U32)c0), 0);
        x[8] = stretch_t[CP[7]->p >> 4];                       /* sp1 */
        CP[8] = tget(8, fold(SP2v, (U32)c0), 0);
        x[9] = stretch_t[CP[8]->p >> 4];                       /* sp2 */
        CP[9] = tget(9, fold(wordH, (U32)c0), 0);
        x[10] = stretch_t[CP[9]->p >> 4];                      /* word */
        CP[10] = tget(10, fold(H12v, (U32)c0), 0);
        x[13] = stretch_t[CP[10]->p >> 4];                     /* o12 */
        CP[11] = tget(11, fold(fold(prevWordH, wordH), (U32)c0), 0);
        x[14] = stretch_t[CP[11]->p >> 4];                     /* wordpair */
        CP[12] = tget(12, fold((U32)indT[H4v & 0xFFFFFu], (U32)c0), 0);
        x[15] = stretch_t[CP[12]->p >> 4];                     /* indirect */
        {   /* 行列模型: 上一行同列字节 */
            U32 col = n - lineStart;
            U32 above = 0;
            if (prevLineStart != lineStart && col < 240 &&
                prevLineStart + col + 1 < lineStart)
                above = HB(prevLineStart + col);
            CP[13] = tget(13, fold(fold(0, above), (U32)c0), 0);
        }
        x[16] = stretch_t[CP[13]->p >> 4];                     /* line */

        int mst = 0;                                 /* 长程匹配 */
        if (mLen > 0) {
            U32 pb = HB(mPos);
            if (((pb | 256u) >> (8 - i)) == (U32)c0) {
                int pb1 = (int)((pb >> (7 - i)) & 1u);
                int s = MBASE + (mLen > 31 ? 31 : mLen) * MSTEP;
                if (s > 2047) s = 2047;
                mst = pb1 ? s : -s;
            } else {
                mLen = 0;                            /* 匹配在位级断裂 */
            }
        }
        x[11] = mst;

        int rst = 0;                                 /* 运行模型 */
        if (runC >= 2) {
            int rb1 = (int)((runB >> (7 - i)) & 1u);
            int s = 512 + (int)(runC > 12 ? 12 : runC) * 128;
            if (s > 2047) s = 2047;
            rst = rb1 ? s : -s;
        }
        x[12] = rst;

        /* ---- 决策层: 逻辑斯蒂混合 (权重组按匹配状态选择) ---- */
        int sel = mLen >= 16 ? 2 : (mLen > 0 ? 1 : 0);
        int *w = Wmix[sel][c0];
        long long sum = 0;
        for (int j = 0; j < NIN; j++) sum += (long long)w[j] * x[j];
        int st_m = (int)(sum >> 16);
        if (st_m >  2047) st_m =  2047;
        if (st_m < -2047) st_m = -2047;
        int pr = SQ(st_m);

        /* ---- 精修层: 双级 APM ---- */
        int bkt = ((st_m + 2047) * 33) >> 12;
        U32 a2i = (fold((U32)c1, (U32)c0) & 0xFFFFu) * 33u + (U32)bkt;
        U32 a3i = (fold((U32)c2, (U32)c0) & 0xFFFFu) * 33u + (U32)bkt;
        int a1 = A1[(U32)c0 * 33u + (U32)bkt] >> 4;
        int a2 = A2[a2i] >> 4;
        int a3 = A3[a3i] >> 4;
        int yF[5] = { st_m, stretch_t[a1], stretch_t[a2], stretch_t[a3], 256 };
        long long sF = 0;
        for (int j = 0; j < 5; j++) sF += (long long)W3[c0][j] * yF[j];
        int stF = (int)(sF >> 16);
        if (stF >  2047) stF =  2047;
        if (stF < -2047) stF = -2047;
        int p12 = SQ(stF);
        if (p12 < 2) p12 = 2;
        if (p12 > 4094) p12 = 4094;

        /* ---- 写码层 ---- */
        int bit;
        if (!decoding) {
            bit = (C >> (7 - i)) & 1;
            enc_bit(bit, p12);
        } else {
            bit = dec_bit(p12);
        }

        /* ---- 在线学习(编码器与解码器同步更新) ---- */
        int err = ((bit << 12) - pr) * LMX;
        for (int j = 0; j < NIN; j++) {
            w[j] += (x[j] * err) >> LSHIFT;
            if (w[j] >  (1 << 20)) w[j] =  (1 << 20);
            if (w[j] < -(1 << 20)) w[j] = -(1 << 20);
        }
        U32 tgt = bit ? 65535u : 0u;
        for (int m = 0; m < 14; m++) {
            Cell *cc = CP[m];
            cc->p = (U16)(cc->p + (int)((tgt - (U32)cc->p) >> RATE[cc->n]));
            if (cc->n < 63) ++cc->n;
        }
        {
            U16 *q = &A1[(U32)c0 * 33u + (U32)bkt];
            *q = (U16)(*q + (int)((tgt - (U32)*q) >> APM_RATE));
            q = &A2[a2i];
            *q = (U16)(*q + (int)((tgt - (U32)*q) >> APM_RATE));
            q = &A3[a3i];
            *q = (U16)(*q + (int)((tgt - (U32)*q) >> APM_RATE));
        }
        {
            int errF = ((bit << 12) - p12) * LMX;
            for (int j = 0; j < 5; j++) {
                W3[c0][j] += (yF[j] * errF) >> LSHIFT;
                if (W3[c0][j] >  (1 << 20)) W3[c0][j] =  (1 << 20);
                if (W3[c0][j] < -(1 << 20)) W3[c0][j] = -(1 << 20);
            }
        }
        y = (y << 1) | bit;
    }

    /* ---- 字节边界: 状态演化 ---- */
    int b = y;
    HB(n) = (U8)b;
    indT[H4v & 0xFFFFFu] = (U8)b;   /* 记录该上下文的历史后继 */

    /* 匹配键: 当前字节前 6 字节 */
    U32 key = 0;
    int ks = (int)n - MINLEN; if (ks < 0) ks = 0;
    for (int k = ks; k < (int)n; k++) key = fold(key, HB(k));
    key &= MTMASK;

    if (mLen > 0) {
        if ((U32)b == HB(mPos)) {
            mPos++; mLen++;
            if (mLen > 60000) mLen = 60000;
        } else {
            mLen = 0;
        }
    }
    if (mLen == 0 && n >= (U32)MINLEN) {
        U32 pos = MT[key];
        if (pos && pos < n && pos >= (U32)MINLEN && n - pos < HCAP) {
            int ok = 1;
            for (int k = 1; k <= MINLEN; k++) {
                if (HB(pos - (U32)k) != HB(n - (U32)k)) { ok = 0; break; }
            }
            if (ok) { mPos = pos; mLen = MINLEN; }
        }
    }
    if (mLen > 0 && n - mPos >= HCAP) mLen = 0;   /* 超出窗口: 放弃长匹配 */
    MT[key] = n;

    n++;
    if (b == '\n') { prevLineStart = lineStart; lineStart = n; }
    c12 = c11; c11 = c10; c10 = c9; c9 = c8;
    c8 = c7; c7 = c6; c6 = c5; c5 = c4;
    c4 = c3; c3 = c2; c2 = c1; c1 = (U8)b;
    ctx_hashes();

    int alnum = ((b | 0x20) >= 'a' && (b | 0x20) <= 'z') || (b >= '0' && b <= '9');
    if (alnum) wordH = fold(wordH, (U32)(b | 0x20));
    else { if (wordH) prevWordH = wordH; wordH = 0; }

    if ((U8)b == runB) runC++;
    else { runB = (U8)b; runC = 1; }

    return b;
}

static void alloc_all(void) {
    g_oom = 0;
    for (int m = 0; m < 14; m++) {
        T[m].c = (Cell *)calloc(TSZ[m], sizeof(Cell));
        T[m].mask = TSZ[m] - 1;
        T[m].direct = (m < 2);      /* o0/o1 直接索引, 其余 2 路哈希 */
        if (!T[m].c) { g_oom = 1; return; }
    }
    for (int s = 0; s < 3; s++)
        for (int c = 0; c < 256; c++)
            for (int j = 0; j < NIN; j++)
                Wmix[s][c][j] = 65536 / NIN;      /* 初始权重: 各路均分 */
    for (int c = 0; c < 256; c++)
        for (int j = 0; j < 5; j++)
            W3[c][j] = 65536 / 5;                 /* 终混均分 */
    for (size_t k = 0; k < sizeof(A3) / sizeof(U16); k++) A3[k] = 32768;
    for (size_t k = 0; k < sizeof(A1) / sizeof(U16); k++) A1[k] = 32768;
    for (size_t k = 0; k < sizeof(A2) / sizeof(U16); k++) A2[k] = 32768;
    MT = (U32 *)calloc(1u << MTBITS, sizeof(U32));
    indT = (U8 *)calloc(1u << 20, 1);
    if (!MT || !indT) { g_oom = 1; return; }
}

/* ================= CRC32 ================= */
static U32 crc_tab[256];
static void crc_init(void) {
    for (U32 i = 0; i < 256; i++) {
        U32 c = i;
        for (int k = 0; k < 8; k++)
            c = (c & 1) ? (0xEDB88320u ^ (c >> 1)) : (c >> 1);
        crc_tab[i] = c;
    }
}
static U32 crc32buf(const U8 *b, size_t len) {
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
#define CRC32_FINAL(c) ((c) ^ 0xFFFFFFFFu)

#ifndef LX_WASM
/* ================= 文件工具(非致命版) ================= */
static U8 *load_file2(const char *path, size_t *outN, char *msg, size_t msgn) {
    FILE *f = fopen(path, "rb");
    if (!f) { snprintf(msg, msgn, "无法打开文件: %s", path); return NULL; }
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    U8 *buf = (U8 *)malloc((size_t)sz + 1);
    if (!buf) { fclose(f); snprintf(msg, msgn, "内存不足"); return NULL; }
    if (sz > 0 && fread(buf, 1, (size_t)sz, f) != (size_t)sz) {
        fclose(f); free(buf); snprintf(msg, msgn, "读取失败: %s", path); return NULL;
    }
    fclose(f);
    *outN = (size_t)sz;
    return buf;
}
static int save_file2(const char *path, const U8 *b, size_t len, char *msg, size_t msgn) {
    FILE *f = fopen(path, "wb");
    if (!f) { snprintf(msg, msgn, "无法写出文件: %s", path); return 1; }
    if (len && fwrite(b, 1, len, f) != len) {
        fclose(f); snprintf(msg, msgn, "写入失败: %s", path); return 1;
    }
    fclose(f);
    return 0;
}
#endif /* !LX_WASM */

/* ================= 主流程 ================= */
static void reset_state(void) {
    c1 = c2 = c3 = c4 = c5 = c6 = c7 = c8 = c9 = c10 = c11 = c12 = 0;
    H2v = H3v = H4v = H6v = H8v = H12v = SP1v = SP2v = wordH = prevWordH = 0;
    runB = 0; runC = 0; mPos = 0; mLen = 0; n = 0;
    lineStart = prevLineStart = 0;
}

/* ---------- 引擎生命周期 (供常驻 GUI 低占用使用) ---------- */
static int g_eng_init = 0;
static void ensure_init(void)
{
    if (g_eng_init) return;
    init_tables(); init_rate(); crc_init();
    alloc_all();
    if (!g_oom) g_eng_init = 1;
}
LXAPI void lx_shutdown(void) {           /* 释放全部大表, 进程回到 ~1MB 常驻 */
    if (!g_eng_init) return;
    for (int m = 0; m < 14; m++) { free(T[m].c); T[m].c = NULL; }
    free(MT); free(indT); MT = NULL; indT = NULL;
    free(H); H = NULL; HCAP = 0;
    g_eng_init = 0;
#ifdef LX_WASM
    arena_off = 0;              /* 整个 arena 归零, 下个作业重新分配 */
#endif
}
void (*lx_progress_cb)(int percent) = NULL;   /* 0..100, 可为 NULL */

/* 供宿主程序注册进度回调 (比导出变量更稳妥) */
#ifdef LX_DLL
LXAPI void lx_set_progress(void (*cb)(int)) { lx_progress_cb = cb; }
#endif

/* ---------- 文件到文件 API: 成功返回 0, 失败返回 1, msg 带中文详情 ---------- */
#ifndef LX_WASM
#ifndef STORE_THRESHOLD
#define STORE_THRESHOLD (4ull * 1024 * 1024)    /* 超过 4MB 的文件做存储采样决策 */
#endif
#ifndef SAMPLE_SIZE
#define SAMPLE_SIZE (256u * 1024)
#endif
#ifndef STORE_RATIO
#define STORE_RATIO 0.90                        /* 采样压缩率劣于此 → 存储模式 */
#endif

/* 已压缩数据 (zip/exe/图片) 跳过建模: 直接写 LX0 存储容器 */
static int write_stored(const char *inpath, const char *outpath,
                        U64 inN, U32 crc, char *msg, size_t msgn)
{
    FILE *fi = fopen(inpath, "rb");
    if (!fi) { snprintf(msg, msgn, "无法打开文件: %s", inpath); return 1; }
    FILE *fo = fopen(outpath, "wb");
    if (!fo) { fclose(fi); snprintf(msg, msgn, "无法写出文件: %s", outpath); return 1; }
    U8 hdr[15];
    memcpy(hdr, "LX0", 3);
    for (int k = 0; k < 8; k++) hdr[3 + k] = (U8)(inN >> (8 * k));
    for (int k = 0; k < 4; k++) hdr[11 + k] = (U8)(crc >> (8 * k));
    fwrite(hdr, 1, 15, fo);
    U8 buf[1 << 16];
    U64 left = inN;
    while (left > 0)
    {
        size_t chunk = (size_t)((left > (U64)sizeof(buf)) ? sizeof(buf) : (size_t)left);
        size_t got = fread(buf, 1, chunk, fi);
        if (got == 0) break;
        fwrite(buf, 1, got, fo);
        left -= got;
    }
    int werr = ferror(fo);
    fclose(fo); fclose(fi);
    if (left != 0 || werr) { remove(outpath); snprintf(msg, msgn, "读写失败"); return 1; }
    return 0;
}

/* 采样决策: 只压缩前 256KB 采样片段, 压缩率劣于阈值 → 判定为已压缩数据 */
LXAPI int lx_compress_file(const char *inpath, const char *outpath,
                     char *msg, size_t msgn);
static int looks_incompressible(const char *inpath, U64 inN)
{
    U64 sampleN = inN < SAMPLE_SIZE ? inN : SAMPLE_SIZE;
    char sOut[1030], cOut[1030], smsg[256];
    snprintf(sOut, sizeof(sOut), "%s.lxs.tmp", inpath);
    snprintf(cOut, sizeof(cOut), "%s.lxc.tmp", inpath);

    FILE *sf = fopen(inpath, "rb");
    if (!sf) return 0;
    FILE *df = fopen(sOut, "wb");
    if (!df) { fclose(sf); return 0; }
    U8 buf[1 << 16];
    U64 left = sampleN;
    while (left > 0)
    {
        size_t chunk = (size_t)((left > (U64)sizeof(buf)) ? sizeof(buf) : (size_t)left);
        size_t got = fread(buf, 1, chunk, sf);
        if (got == 0) break;
        fwrite(buf, 1, got, df);
        left -= got;
    }
    fclose(sf); fclose(df);
    if (left != 0) { remove(sOut); return 0; }

    int rc = lx_compress_file(sOut, cOut, smsg, sizeof(smsg));
    int verdict = 0;
    if (rc == 0)
    {
        FILE *cf = fopen(cOut, "rb");
        if (cf)
        {
            _fseeki64(cf, 0, SEEK_END);
            long long csz = _ftelli64(cf);
            fclose(cf);
            if ((double)csz / (double)sampleN > (double)STORE_RATIO) verdict = 1;
        }
    }
    remove(sOut); remove(cOut);
    return verdict;
}

LXAPI int lx_compress_file(const char *inpath, const char *outpath,
                     char *msg, size_t msgn) {
    lx_shutdown();      /* 出厂状态 (采样决策内部自管引擎生命周期) */
    clock_t t0 = clock();

    FILE *fi = fopen(inpath, "rb");
    if (!fi) { snprintf(msg, msgn, "无法打开文件: %s", inpath); return 1; }
    _fseeki64(fi, 0, SEEK_END);
    long long fsz = _ftelli64(fi);
    _fseeki64(fi, 0, SEEK_SET);
    if (fsz < 0) { fclose(fi); snprintf(msg, msgn, "无法获取文件大小"); return 1; }
    U64 inN = (U64)fsz;

    /* 大文件采样决策: 已压缩数据 (zip/exe/图片) 走存储, 秒级完成 */
    if (inN > STORE_THRESHOLD && looks_incompressible(inpath, inN))
    {
        fclose(fi);
        U32 crc0 = 0xFFFFFFFFu;
        fi = fopen(inpath, "rb");
        if (!fi) { snprintf(msg, msgn, "无法打开文件: %s", inpath); return 1; }
        U8 buf[1 << 16];
        U64 left = inN;
        while (left > 0)
        {
            size_t chunk = (size_t)((left > (U64)sizeof(buf)) ? sizeof(buf) : (size_t)left);
            size_t got = fread(buf, 1, chunk, fi);
            if (got == 0) break;
            crc0 = crc32_update(crc0, buf, got);
            left -= got;
        }
        crc0 = CRC32_FINAL(crc0);
        fclose(fi);
        int rc = write_stored(inpath, outpath, inN, crc0, msg, msgn);
        if (rc == 0)
            snprintf(msg, msgn, "压缩完成 (已压缩数据, 存储模式): %lld 字节",
                     (long long)inN);
        return rc;
    }

    /* 采样决策的内部压缩会训练引擎, 此处强制恢复出厂状态供正式压缩 */
    lx_shutdown(); ensure_init();
    if (!g_eng_init) { snprintf(msg, msgn, "内存不足: 模型初始化失败"); return 1; }

    /* 历史窗口: 小文件精确容量, 大文件封顶 1GB (循环缓冲) */
    HCAP = (inN + 65536u > (U64)HISTWIN) ? (size_t)HISTWIN : (size_t)(inN + 65536u);
    size_t need = HCAP;
    H = (U8 *)calloc(HCAP, 1);
    if (!H) {
        fclose(fi); HCAP = 0;
        snprintf(msg, msgn, "内存不足: 需要约 %.1f GB 可用内存 (历史窗口)", (double)need / 1073741824.0);
        return 1;
    }
    FILE *fo = fopen(outpath, "wb");
    if (!fo) {
        fclose(fi); free(H); H = NULL; HCAP = 0;
        snprintf(msg, msgn, "无法写出文件: %s", outpath);
        return 1;
    }

    reset_state(); ctx_hashes();
    g_out = fo;
    rc_init_enc();
    U8 hdr[15];
    memcpy(hdr, "LX1", 3);
    U64 sz64 = inN;
    for (int k = 0; k < 8; k++) hdr[3 + k] = (U8)(sz64 >> (8 * k));
    memset(hdr + 11, 0, 4);                 /* CRC 占位, 压缩结束后回写 */
    fwrite(hdr, 1, 15, fo);

    U32 crc = 0xFFFFFFFFu;
    U64 done = 0;
    unsigned lastMB = 0;
    int rderr = 0;
    U8 *tmp = (U8 *)malloc(1u << 20);
    if (!tmp) {
        g_out = NULL; fclose(fo); fclose(fi);
        free(H); H = NULL; HCAP = 0;
        snprintf(msg, msgn, "内存不足"); return 1;
    }
    while (done < inN)
    {
        size_t chunk = (size_t)((inN - done > (U64)(1u << 20)) ? (1u << 20) : (U64)(inN - done));
        size_t got = fread(tmp, 1, chunk, fi);
        if (got == 0) { rderr = 1; break; }
        crc = crc32_update(crc, tmp, got);
        for (size_t k = 0; k < got; k++) process_byte(tmp[k], 0);
        done += got;
        unsigned mb = (unsigned)(done >> 20);
        if (mb != lastMB) { lastMB = mb; if (lx_progress_cb) lx_progress_cb((int)(done * 90 / (inN ? inN : 1))); }
    }
    free(tmp);
    if (rderr)
    {
        g_out = NULL; fclose(fo); fclose(fi);
        free(H); H = NULL; HCAP = 0;
        snprintf(msg, msgn, "读取输入文件失败");
        return 1;
    }
    rc_flush();
    g_out = NULL;
    crc = CRC32_FINAL(crc);
    for (int k = 0; k < 4; k++) hdr[11 + k] = (U8)(crc >> (8 * k));
    fflush(fo);
    long long outN = _ftelli64(fo);          /* 先记长度, 再回写头 */
    _fseeki64(fo, 0, SEEK_SET);
    fwrite(hdr, 1, 15, fo);
    int werr = ferror(fo);
    fclose(fo); fclose(fi);
    free(H); H = NULL; HCAP = 0;
    if (werr) { snprintf(msg, msgn, "写出失败: 磁盘空间不足?"); return 1; }
    {
        double secs = (double)(clock() - t0) / CLOCKS_PER_SEC;
        double bpc = inN ? (double)outN * 8.0 / (double)inN : 0.0;
        snprintf(msg, msgn, "压缩完成: %lld -> %lld 字节 (%.2f%%, %.4f bpc), 用时 %.2fs",
                 (long long)inN, outN, inN ? outN * 100.0 / (double)inN : 0.0, bpc, secs);
    }
    return 0;
}

LXAPI int lx_decompress_file(const char *inpath, const char *outpath,
                       char *msg, size_t msgn) {
    lx_shutdown();      /* 每个作业从出厂状态开始(与编码端一致) */
    ensure_init();
    if (!g_eng_init) { snprintf(msg, msgn, "内存不足: 模型初始化失败"); return 1; }
    clock_t t0 = clock();

    FILE *fi = fopen(inpath, "rb");
    if (!fi) { snprintf(msg, msgn, "无法打开文件: %s", inpath); return 1; }
    U8 hdr[15];
    if (fread(hdr, 1, 15, fi) < 15 || (memcmp(hdr, "LX1", 3) != 0 && memcmp(hdr, "LX0", 3) != 0)) {
        fclose(fi);
        snprintf(msg, msgn, "不是灵犀文件(头错误): %s", inpath);
        return 1;
    }
    U64 sz64 = 0;
    for (int k = 0; k < 8; k++) sz64 |= (U64)hdr[3 + k] << (8 * k);
    U32 want = 0;
    for (int k = 0; k < 4; k++) want |= (U32)hdr[11 + k] << (8 * k);
    if (memcmp(hdr, "LX0", 3) == 0)
    {
        /* 存储容器: 流式拷贝 + CRC 校验 */
        FILE *fo = fopen(outpath, "wb");
        if (!fo) { fclose(fi); snprintf(msg, msgn, "无法写出文件: %s", outpath); return 1; }
        U32 crc = 0xFFFFFFFFu;
        U8 buf[1 << 16];
        U64 left = sz64;
        while (left > 0)
        {
            size_t chunk = (size_t)((left > (U64)sizeof(buf)) ? sizeof(buf) : (size_t)left);
            size_t got = fread(buf, 1, chunk, fi);
            if (got == 0) break;
            crc = crc32_update(crc, buf, got);
            fwrite(buf, 1, got, fo);
            left -= got;
        }
        crc = CRC32_FINAL(crc);
        int werr = ferror(fo);
        fclose(fo); fclose(fi);
        free(H); H = NULL; HCAP = 0;
        if (left != 0 || crc != want) {
            remove(outpath);
            snprintf(msg, msgn, "校验失败! 数据损坏");
            return 1;
        }
        if (werr) { snprintf(msg, msgn, "写出失败: 磁盘空间不足?"); return 1; }
        snprintf(msg, msgn, "解压完成 (存储模式): %llu 字节, CRC 校验通过",
                 (unsigned long long)sz64);
        return 0;
    }

    HCAP = (sz64 + 65536u > (U64)HISTWIN) ? (size_t)HISTWIN : (size_t)(sz64 + 65536u);
    size_t need = HCAP;
    H = (U8 *)calloc(HCAP, 1);
    if (!H) {
        fclose(fi); HCAP = 0;
        snprintf(msg, msgn, "内存不足: 需要 %.1f GB 可用内存 (历史窗口)", (double)need / 1073741824.0);
        return 1;
    }
    FILE *fo = fopen(outpath, "wb");
    if (!fo) {
        fclose(fi); free(H); H = NULL; HCAP = 0;
        snprintf(msg, msgn, "无法写出文件: %s", outpath);
        return 1;
    }

    g_in = fi;
    _fseeki64(fi, 15, SEEK_SET);
    reset_state(); ctx_hashes();
    rc_init_dec();
    U32 crc = 0xFFFFFFFFu;
    U64 done = 0;
    U8 ob[65536]; int oi = 0;
    while (done < sz64)
    {
        U8 b = (U8)process_byte(-1, 1);
        HB(done) = b;
        crc = crc32_update(crc, &b, 1);
        ob[oi++] = b;
        if (oi == (int)sizeof(ob)) { fwrite(ob, 1, (size_t)oi, fo); oi = 0; }
        done++;
        if ((done & 0xFFFFF) == 0 && lx_progress_cb)
            lx_progress_cb((int)(done * 100 / (sz64 ? sz64 : 1)));
    }
    if (oi) fwrite(ob, 1, (size_t)oi, fo);
    g_in = NULL;
    crc = CRC32_FINAL(crc);
    int werr = ferror(fo);
    fclose(fo); fclose(fi);
    free(H); H = NULL; HCAP = 0;
    if (crc != want) {
        remove(outpath);
        snprintf(msg, msgn, "校验失败! 数据损坏 (CRC %08x != %08x)", crc, want);
        return 1;
    }
    if (werr) { snprintf(msg, msgn, "写出失败: 磁盘空间不足?"); return 1; }
    {
        double secs = (double)(clock() - t0) / CLOCKS_PER_SEC;
        snprintf(msg, msgn, "解压完成: %llu 字节, CRC 校验通过, 用时 %.2fs",
                 (unsigned long long)sz64, secs);
    }
    return 0;
}

#endif /* !LX_WASM */

#ifdef LX_WASM
/* ================= WASM 内存作业 API (供浏览器调用) ================= */
static unsigned int g_n_total;          /* 压缩: 待编码总字节数 */
static U8  *d_out;                      /* 解压: 输出缓冲 */
static unsigned int d_total, d_pos, d_want;
static U8  *out_g; static unsigned int out_len_g;

__attribute__((export_name("lx_ver")))
int lx_ver(void) { return 202; }

/* JS 先分配输入缓冲并写入文件字节, 再调 c/d_start。
   input_end = 输入块结束: 解压重置保留输入(其后的上一作业内存全部回收);
   压缩不经输入缓冲(直写 H), 完整归零重置 */
static size_t input_end = 0;
__attribute__((export_name("lx_in_alloc")))
unsigned char *lx_in_alloc(unsigned int n) {
    unsigned char *p = (unsigned char *)calloc(1u, n);
    input_end = arena_off;
    return p;
}

__attribute__((export_name("lx_c_start")))
int lx_c_start(unsigned int n) {
    arena_off = 0; g_eng_init = 0;          /* 完整出厂重置 */
    ensure_init();
    H = (U8 *)calloc((size_t)n + 65536u, 1u);
    if (!H) return -1;
    g_n_total = n;
    reset_state(); ctx_hashes(); rc_init_enc();
    return 0;
}

__attribute__((export_name("lx_c_hbuf")))
unsigned char *lx_c_hbuf(void) { return H; }

/* 区间 [from,to) 的字节已在 H 中, 逐字节编码; 返回推进到的位置 */
__attribute__((export_name("lx_c_run")))
int lx_c_run(unsigned int from, unsigned int to) {
    if (to > g_n_total) to = g_n_total;
    for (unsigned int k = from; k < to; k++) process_byte(H[k], 0);
    return (int)to;
}

__attribute__((export_name("lx_c_end")))
int lx_c_end(void) {
    rc_flush();
    U8 hdr[15];
    memcpy(hdr, "LX1", 3);
    U64 sz64 = (U64)g_n_total;
    for (int k = 0; k < 8; k++) hdr[3 + k] = (U8)(sz64 >> (8 * k));
    U32 crc = crc32buf(H, g_n_total);
    for (int k = 0; k < 4; k++) hdr[11 + k] = (U8)(crc >> (8 * k));
    unsigned int total = 15u + (unsigned int)olen;
    U8 *out = (U8 *)malloc(total);
    if (!out) return -2;
    memcpy(out, hdr, 15);
    if (olen) memcpy(out + 15, obuf, olen);
    out_g = out; out_len_g = total;
    free(H); H = NULL; free(obuf); obuf = NULL; ocap = 0; olen = 0;
    return (int)total;
}

/* 输入 = 完整 .lx 字节 (JS 已写入 in 缓冲并传指针) */
__attribute__((export_name("lx_d_start")))
int lx_d_start(unsigned char *in, unsigned int inN) {
    arena_off = input_end; g_eng_init = 0;  /* 重置时保留输入缓冲 */
    ensure_init();
    if (inN < 15 || memcmp(in, "LX1", 3) != 0) return -1;
    U64 sz64 = 0;
    for (int k = 0; k < 8; k++) sz64 |= (U64)in[3 + k] << (8 * k);
    U32 want = 0;
    for (int k = 0; k < 4; k++) want |= (U32)in[11 + k] << (8 * k);
    if (sz64 > 3ull * 1024 * 1024 * 1024) return -4;   /* wasm 32位地址空间实际边界 */
    size_t N = (size_t)sz64;
    d_out = (U8 *)malloc(N + 1);
    H = (U8 *)calloc(N + 65536u, 1u);
    if (!d_out || !H) return -2;
    reset_state(); ctx_hashes();
    ibuf = in + 15; ipos = 0; isize = inN - 15;
    rc_init_dec();
    d_total = (unsigned int)N; d_pos = 0; d_want = want;
    return (int)N;
}

__attribute__((export_name("lx_d_run")))
int lx_d_run(unsigned int to) {
    if (to > d_total) to = d_total;
    for (unsigned int k = d_pos; k < to; k++) d_out[k] = (U8)process_byte(-1, 1);
    d_pos = to;
    return (int)to;
}

__attribute__((export_name("lx_d_end")))
int lx_d_end(void) {
    U32 got = crc32buf(d_out, d_total);
    free(H); H = NULL;
    if (got != d_want) return -3;
    out_g = d_out; out_len_g = d_total;
    return (int)d_total;
}

__attribute__((export_name("lx_out_ptr")))
unsigned char *lx_out_ptr(void) { return out_g; }
__attribute__((export_name("lx_out_len")))
unsigned int lx_out_len(void) { return out_len_g; }
#endif /* LX_WASM */

#ifdef LX_DLL
#define LX_NO_MAIN
#define LX_WASM_GUARD_UNUSED
#endif

#ifndef LX_NO_MAIN
int main(int argc, char **argv) {
    if (argc < 4 || (argv[1][0] != 'c' && argv[1][0] != 'd')) {
        fprintf(stderr, "用法: %s c|d <输入> <输出>\n", argv[0]);
        return 2;
    }
    char msg[512];
    int rc = (argv[1][0] == 'c')
        ? lx_compress_file(argv[2], argv[3], msg, sizeof(msg))
        : lx_decompress_file(argv[2], argv[3], msg, sizeof(msg));
    fprintf(stderr, "灵犀: %s\n", msg);
    return rc;
}
#endif

#ifdef LX_WASM
/* ---------- freestanding 最小运行时: arena 分配器 + 内存原语 ---------- */
static void *arena_alloc(size_t n) {
    size_t total = (n + 15u) & ~(size_t)15u;       /* 16字节对齐 + 尺寸头 */
    total += 16u;
    unsigned char *base = (unsigned char *)&__heap_base;
    size_t cur = (size_t)__builtin_wasm_memory_size(0) << 16;
    size_t end = (size_t)base + arena_off + total;
    if (end > cur) {
        size_t pages = ((end - cur) + 65535u) >> 16;
        if (__builtin_wasm_memory_grow(0, (int)pages) < 0) return 0;
    }
    unsigned char *p = base + arena_off;
    arena_off += total;
    ((size_t *)p)[0] = n;                          /* 头: 有效负载尺寸 */
    return p + 16;
}
void *malloc(size_t n) { return arena_alloc(n); }
void *calloc(size_t a, size_t b) {
    size_t n = a * b;
    unsigned char *p = (unsigned char *)arena_alloc(n);
    if (p) { size_t head = ((size_t *)p)[-1]; (void)head;
             for (size_t i = 0; i < n; i++) p[i] = 0; }
    return p;
}
void free(void *p) { (void)p; }                    /* arena: 作业结束整体重置 */
void *realloc(void *p, size_t n) {
    void *q = malloc(n);
    if (q && p) {
        size_t old = ((size_t *)p)[-1];
        memcpy(q, p, old < n ? old : n);
    }
    return q;
}
void *memcpy(void *d, const void *s, size_t n) {
    unsigned char *dd = (unsigned char *)d;
    const unsigned char *ss = (const unsigned char *)s;
    for (size_t i = 0; i < n; i++) dd[i] = ss[i];
    return d;
}
void *memset(void *d, int c, size_t n) {
    unsigned char *dd = (unsigned char *)d;
    for (size_t i = 0; i < n; i++) dd[i] = (unsigned char)c;
    return d;
}
int memcmp(const void *a, const void *b, size_t n) {
    const unsigned char *aa = (const unsigned char *)a, *bb = (const unsigned char *)b;
    for (size_t i = 0; i < n; i++) { if (aa[i] != bb[i]) return aa[i] < bb[i] ? -1 : 1; }
    return 0;
}
#endif
