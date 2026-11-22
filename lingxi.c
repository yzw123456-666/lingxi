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
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>

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
#define RATE_CAP 2              /* 计数器最终自适应步长 1/2^RATE_CAP */
#endif
#ifndef APM_RATE
#define APM_RATE 5              /* APM 自适应步长 1/2^APM_RATE */
#endif
#define MINLEN 6                /* 长程匹配器最小可信匹配 */

#define NIN 13                  /* 混合器输入数 */
#define PHI 0x9E3779B1u         /* 黄金比例哈希常数 */

/* ================= 概率域变换 (stretch/squash 互逆) ================= */
static int  stretch_t[4096];    /* p(12bit) -> st(-2047..2047) 对数几率域 */
static short squash_t[4096];    /* st(-2047..2047) -> p(1..4095) */

static void init_tables(void) {
    for (int p = 0; p < 4096; p++) {
        double x = (p + 0.5) / 4096.0;
        double s = 256.0 * log(x / (1.0 - x));
        if (s >  2047.0) s =  2047.0;
        if (s < -2047.0) s = -2047.0;
        stretch_t[p] = (int)s;
    }
    for (int i = 0; i < 4096; i++) {
        int s = i - 2048;
        double x = 1.0 / (1.0 + exp(-(double)s / 256.0));
        int p = (int)(x * 4096.0);
        if (p < 1) p = 1;
        if (p > 4095) p = 4095;
        squash_t[i] = (short)p;
    }
}
#define SQ(s) squash_t[(s) + 2048]

static U32 fold(U32 h, U32 c) { return (h + c + 1u) * PHI; }

/* ================= 计数器表 (16位概率 + 8位置信度) ================= */
typedef struct { U16 *p; U8 *n; U32 mask; } Tbl;
static Tbl T[10];
static const U32 TSZ[10] = { 256, 1u<<16, 1u<<17, 1u<<20, 1u<<20,
                             1u<<21, 1u<<21, 1u<<19, 1u<<19, 1u<<19 };
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

static inline int stget(int ti, U32 k) {
    Tbl *t = &T[ti];
    k &= t->mask;
    return stretch_t[t->p[k] >> 4];
}

/* ================= 学习型混合器 (c0 索引 256 组权重) ================= */
static int (*Wmix)[NIN];

/* ================= 双级概率精修 APM ================= */
static U16 A1[256 * 33];        /* 上下文: c0 */
static U16 A2[65536 * 33];      /* 上下文: hash(c1,c0) */

/* ================= 长程匹配器 ================= */
static U32 *MT;                 /* 上下文哈希 -> 下一次出现的位置 */
#define MTBITS 22
#define MTMASK ((1u << MTBITS) - 1u)

/* ================= 全局状态 ================= */
static U8  *H;                  /* 全历史缓冲 */
static U32  n;                  /* 已处理字节数(含当前正在编码者待写) */
static U8   c1, c2, c3, c4, c5, c6, c7, c8;
static U32  H2v, H3v, H4v, H6v, H8v, SP1v, SP2v, wordH;
static U8   runB; static U32 runC;
static U32  mPos; static int mLen;

static void ctx_hashes(void) {
    H2v  = fold(fold(0, c2), c1);
    H3v  = fold(H2v, c3);
    H4v  = fold(H3v, c4);
    H6v  = fold(fold(H4v, c5), c6);
    H8v  = fold(fold(H6v, c7), c8);
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

static void putbyte(U8 b) {
    if (olen == ocap) {
        ocap = ocap ? ocap * 2 : (1u << 20);
        obuf = (U8 *)realloc(obuf, ocap);
        if (!obuf) { fprintf(stderr, "out of memory\n"); exit(1); }
    }
    obuf[olen++] = b;
}
static U8 getbyte(void) { return ipos < isize ? ibuf[ipos++] : 0; }

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
    U32  K[10];
    int  y = 0;

    for (int i = 0; i < 8; i++) {
        int c0 = (1 << i) | y;   /* 含前导1的部分字节 */

        /* ---- 感知层: 11 路预测器 ---- */
        x[0] = 256;                                  /* 偏置 */
        K[0] = (U32)c0;                 x[1]  = stget(0, K[0]);   /* o0 */
        K[1] = ((U32)c1 << 8) | (U32)c0; x[2] = stget(1, K[1]);   /* o1 */
        K[2] = fold(H2v,  (U32)c0) >> 15; x[3] = stget(2, K[2]);  /* o2 */
        K[3] = fold(H3v,  (U32)c0) >> 12; x[4] = stget(3, K[3]);  /* o3 */
        K[4] = fold(H4v,  (U32)c0) >> 12; x[5] = stget(4, K[4]);  /* o4 */
        K[5] = fold(H6v,  (U32)c0) >> 11; x[6] = stget(5, K[5]);  /* o6 */
        K[6] = fold(H8v,  (U32)c0) >> 11; x[7] = stget(6, K[6]);  /* o8 */
        K[7] = fold(SP1v, (U32)c0) >> 13; x[8] = stget(7, K[7]);  /* sp1 */
        K[8] = fold(SP2v, (U32)c0) >> 13; x[9] = stget(8, K[8]);  /* sp2 */
        K[9] = fold(wordH,(U32)c0) >> 13; x[10]= stget(9, K[9]);  /* word */

        int mst = 0;                                 /* 长程匹配 */
        if (mLen > 0) {
            U32 pb = H[mPos];
            if (((pb | 256u) >> (8 - i)) == (U32)c0) {
                int pb1 = (int)((pb >> (7 - i)) & 1u);
                int s = 1408 + (mLen > 31 ? 31 : mLen) * 20;
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

        /* ---- 决策层: 逻辑斯蒂混合 ---- */
        int *w = Wmix[c0];
        long long sum = 0;
        for (int j = 0; j < NIN; j++) sum += (long long)w[j] * x[j];
        int st_m = (int)(sum >> 16);
        if (st_m >  2047) st_m =  2047;
        if (st_m < -2047) st_m = -2047;
        int pr = SQ(st_m);

        /* ---- 精修层: 双级 APM ---- */
        int bkt = ((st_m + 2047) * 33) >> 12;
        U32 a2i = (fold((U32)c1, (U32)c0) & 0xFFFFu) * 33u + (U32)bkt;
        int a1 = A1[(U32)c0 * 33u + (U32)bkt] >> 4;
        int a2 = A2[a2i] >> 4;
        int p12 = (pr * 2 + a1 * 2 + a2 * 4) >> 3;
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
        for (int m = 0; m < 10; m++) {
            Tbl *t = &T[m];
            U16 *pp = &t->p[K[m]];
            U8  *nn = &t->n[K[m]];
            *pp = (U16)(*pp + (int)((tgt - (U32)*pp) >> RATE[*nn]));
            if (*nn < 63) ++*nn;
        }
        {
            U16 *q = &A1[(U32)c0 * 33u + (U32)bkt];
            *q = (U16)(*q + (int)((tgt - (U32)*q) >> APM_RATE));
            q = &A2[a2i];
            *q = (U16)(*q + (int)((tgt - (U32)*q) >> APM_RATE));
        }
        y = (y << 1) | bit;
    }

    /* ---- 字节边界: 状态演化 ---- */
    int b = y;
    H[n] = (U8)b;

    /* 匹配键: 当前字节前 6 字节 */
    U32 key = 0;
    int ks = (int)n - MINLEN; if (ks < 0) ks = 0;
    for (int k = ks; k < (int)n; k++) key = fold(key, H[k]);
    key &= MTMASK;

    if (mLen > 0) {
        if ((U32)b == H[mPos]) {
            mPos++; mLen++;
            if (mLen > 60000) mLen = 60000;
        } else {
            mLen = 0;
        }
    }
    if (mLen == 0 && n >= (U32)MINLEN) {
        U32 pos = MT[key];
        if (pos && pos < n && pos >= (U32)MINLEN) {
            int ok = 1;
            for (int k = 1; k <= MINLEN; k++) {
                if (H[pos - (U32)k] != H[n - (U32)k]) { ok = 0; break; }
            }
            if (ok) { mPos = pos; mLen = MINLEN; }
        }
    }
    MT[key] = n;

    n++;
    c8 = c7; c7 = c6; c6 = c5; c5 = c4;
    c4 = c3; c3 = c2; c2 = c1; c1 = (U8)b;
    ctx_hashes();

    int alnum = ((b | 0x20) >= 'a' && (b | 0x20) <= 'z') || (b >= '0' && b <= '9');
    if (alnum) wordH = fold(wordH, (U32)(b | 0x20));
    else       wordH = 0;

    if ((U8)b == runB) runC++;
    else { runB = (U8)b; runC = 1; }

    return b;
}

static void alloc_all(void) {
    for (int m = 0; m < 10; m++) {
        T[m].p = (U16 *)calloc(TSZ[m], sizeof(U16));
        T[m].n = (U8  *)calloc(TSZ[m], sizeof(U8));
        T[m].mask = TSZ[m] - 1;
        if (!T[m].p || !T[m].n) { fprintf(stderr, "out of memory\n"); exit(1); }
    }
    Wmix = (int (*)[NIN])malloc(256 * sizeof(*Wmix));
    for (int c = 0; c < 256; c++)
        for (int j = 0; j < NIN; j++)
            Wmix[c][j] = 65536 / NIN;      /* 初始权重: 各路均分 */
    for (size_t k = 0; k < sizeof(A1) / sizeof(U16); k++) A1[k] = 32768;
    for (size_t k = 0; k < sizeof(A2) / sizeof(U16); k++) A2[k] = 32768;
    MT = (U32 *)calloc(1u << MTBITS, sizeof(U32));
    if (!MT) { fprintf(stderr, "out of memory\n"); exit(1); }
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

/* ================= 文件工具 ================= */
static U8 *load_file(const char *path, size_t *outN) {
    FILE *f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "cannot open %s\n", path); exit(1); }
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    U8 *buf = (U8 *)malloc((size_t)sz + 1);
    if (!buf) { fprintf(stderr, "out of memory\n"); exit(1); }
    if (sz > 0 && fread(buf, 1, (size_t)sz, f) != (size_t)sz) {
        fprintf(stderr, "read error %s\n", path); exit(1);
    }
    fclose(f);
    *outN = (size_t)sz;
    return buf;
}
static void save_file(const char *path, const U8 *b, size_t len) {
    FILE *f = fopen(path, "wb");
    if (!f) { fprintf(stderr, "cannot write %s\n", path); exit(1); }
    if (len && fwrite(b, 1, len, f) != len) { fprintf(stderr, "write error\n"); exit(1); }
    fclose(f);
}

/* ================= 主流程 ================= */
static void reset_state(void) {
    c1 = c2 = c3 = c4 = c5 = c6 = c7 = c8 = 0;
    H2v = H3v = H4v = H6v = H8v = SP1v = SP2v = wordH = 0;
    runB = 0; runC = 0; mPos = 0; mLen = 0; n = 0;
}

int main(int argc, char **argv) {
    if (argc < 4 || (argv[1][0] != 'c' && argv[1][0] != 'd')) {
        fprintf(stderr, "用法: %s c|d <输入> <输出>\n", argv[0]);
        return 2;
    }
    int decode = (argv[1][0] == 'd');
    init_tables();
    init_rate();
    crc_init();
    alloc_all();

    clock_t t0 = clock();
    size_t inN;
    U8 *in = load_file(argv[2], &inN);

    if (!decode) {
        reset_state();
        H = (U8 *)calloc(inN + 65536, 1);
        if (!H) { fprintf(stderr, "out of memory\n"); return 1; }
        ctx_hashes();
        rc_init_enc();
        for (size_t k = 0; k < inN; k++) process_byte(in[k], 0);
        rc_flush();

        /* 文件头: "LX1" + 原始长度(8LE) + CRC32(4LE) */
        U8 hdr[15];
        memcpy(hdr, "LX1", 3);
        U64 sz64 = (U64)inN;
        for (int k = 0; k < 8; k++) hdr[3 + k] = (U8)(sz64 >> (8 * k));
        U32 crc = crc32buf(in, inN);
        for (int k = 0; k < 4; k++) hdr[11 + k] = (U8)(crc >> (8 * k));

        FILE *f = fopen(argv[3], "wb");
        if (!f) { fprintf(stderr, "cannot write %s\n", argv[3]); return 1; }
        fwrite(hdr, 1, 15, f);
        if (olen) fwrite(obuf, 1, olen, f);
        fclose(f);

        double secs = (double)(clock() - t0) / CLOCKS_PER_SEC;
        double bpc = inN ? (double)(15 + olen) * 8.0 / (double)inN : 0.0;
        fprintf(stderr, "灵犀: %zu -> %zu 字节 (%.4f bpc) 压缩用时 %.2fs\n",
                inN, 15 + olen, bpc, secs);
    } else {
        if (inN < 15 || memcmp(in, "LX1", 3) != 0) {
            fprintf(stderr, "不是灵犀文件(头错误)\n");
            return 1;
        }
        U64 sz64 = 0;
        for (int k = 0; k < 8; k++) sz64 |= (U64)in[3 + k] << (8 * k);
        U32 want = 0;
        for (int k = 0; k < 4; k++) want |= (U32)in[11 + k] << (8 * k);
        size_t N = (size_t)sz64;

        U8 *out = (U8 *)malloc(N + 1);
        H = (U8 *)calloc(N + 65536, 1);
        if (!out || !H) { fprintf(stderr, "out of memory\n"); return 1; }
        reset_state();
        ctx_hashes();
        ibuf = in + 15; ipos = 0; isize = inN - 15;
        rc_init_dec();
        for (size_t k = 0; k < N; k++) out[k] = (U8)process_byte(-1, 1);

        U32 got = crc32buf(out, N);
        if (got != want) {
            fprintf(stderr, "校验失败! CRC %08x != %08x\n", got, want);
            return 1;
        }
        save_file(argv[3], out, N);
        double secs = (double)(clock() - t0) / CLOCKS_PER_SEC;
        fprintf(stderr, "灵犀: 解压 %zu 字节, CRC 校验通过, 用时 %.2fs\n", N, secs);
    }
    return 0;
}
