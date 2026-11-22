"""文件 API 流式化: 压缩(单遍直写文件) + 解压(边解码边落盘), 大文件内存恒定"""
src = open('lingxi.c', encoding='utf-8', newline='').read()
src = src.replace('\r\n', '\n')

def rep(old, new):
    global src
    if new in src and old not in src:
        print('skip:', old[:44])
        return
    assert old in src, 'NOT FOUND: ' + old[:70]
    src = src.replace(old, new, 1)

# ---- lx_compress_file 整体替换 ----
rep('''LXAPI int lx_compress_file(const char *inpath, const char *outpath,
                     char *msg, size_t msgn) {
    lx_shutdown();      /* 每个作业从出厂状态开始: 编解码状态必须严格一致 */
    ensure_init();
    clock_t t0 = clock();
    size_t inN;
    U8 *in = load_file2(inpath, &inN, msg, msgn);
    if (!in) return 1;
    reset_state();
    H = (U8 *)calloc(inN + 65536, 1);
    if (!H) { free(in); snprintf(msg, msgn, "内存不足"); return 1; }
    ctx_hashes();
    rc_init_enc();
    for (size_t k = 0; k < inN; k++) {
        process_byte(in[k], 0);
        if (lx_progress_cb && (k & 0xFFFF) == 0)
            lx_progress_cb((int)(k * 100 / (inN ? inN : 1)));
    }
    rc_flush();

    /* 文件头: "LX1" + 原始长度(8LE) + CRC32(4LE) */
    U8 hdr[15];
    memcpy(hdr, "LX1", 3);
    U64 sz64 = (U64)inN;
    for (int k = 0; k < 8; k++) hdr[3 + k] = (U8)(sz64 >> (8 * k));
    U32 crc = crc32buf(in, inN);
    for (int k = 0; k < 4; k++) hdr[11 + k] = (U8)(crc >> (8 * k));

    FILE *f = fopen(outpath, "wb");
    if (!f) {
        free(in); free(H); H = NULL;
        free(obuf); obuf = NULL; ocap = 0; olen = 0;
        snprintf(msg, msgn, "无法写出文件: %s", outpath);
        return 1;
    }
    fwrite(hdr, 1, 15, f);
    if (olen) fwrite(obuf, 1, olen, f);
    fclose(f);
    size_t outN = 15 + olen;
    free(in); free(H); H = NULL;
    free(obuf); obuf = NULL; ocap = 0; olen = 0;
    {
        double secs = (double)(clock() - t0) / CLOCKS_PER_SEC;
        double bpc = inN ? (double)outN * 8.0 / (double)inN : 0.0;
        snprintf(msg, msgn, "压缩完成: %zu -> %zu 字节 (%.2f%%, %.4f bpc), 用时 %.2fs",
                 inN, outN, inN ? outN * 100.0 / inN : 0.0, bpc, secs);
    }
    return 0;
}''',
    '''LXAPI int lx_compress_file(const char *inpath, const char *outpath,
                     char *msg, size_t msgn) {
    lx_shutdown();      /* 每个作业从出厂状态开始: 编解码状态必须严格一致 */
    ensure_init();
    if (!g_eng_init) { snprintf(msg, msgn, "内存不足: 模型初始化失败"); return 1; }
    clock_t t0 = clock();

    FILE *fi = fopen(inpath, "rb");
    if (!fi) { snprintf(msg, msgn, "无法打开文件: %s", inpath); return 1; }
    _fseeki64(fi, 0, SEEK_END);
    long long fsz = _ftelli64(fi);
    _fseeki64(fi, 0, SEEK_SET);
    if (fsz < 0) { fclose(fi); snprintf(msg, msgn, "无法获取文件大小"); return 1; }
    U64 inN = (U64)fsz;

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
    _fseeki64(fo, 0, SEEK_SET);
    fwrite(hdr, 1, 15, fo);
    int werr = ferror(fo);
    long long outN = _ftelli64(fo);
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
}''')

# ---- lx_decompress_file 整体替换 ----
rep('''LXAPI int lx_decompress_file(const char *inpath, const char *outpath,
                       char *msg, size_t msgn) {
    lx_shutdown();      /* 每个作业从出厂状态开始(与编码端一致) */
    ensure_init();
    clock_t t0 = clock();
    size_t inN;
    U8 *in = load_file2(inpath, &inN, msg, msgn);
    if (!in) return 1;
    if (inN < 15 || memcmp(in, "LX1", 3) != 0) {
        free(in); snprintf(msg, msgn, "不是灵犀文件(头错误): %s", inpath);
        return 1;
    }
    U64 sz64 = 0;
    for (int k = 0; k < 8; k++) sz64 |= (U64)in[3 + k] << (8 * k);
    U32 want = 0;
    for (int k = 0; k < 4; k++) want |= (U32)in[11 + k] << (8 * k);
    size_t N = (size_t)sz64;

    U8 *out = (U8 *)malloc(N + 1);
    H = (U8 *)calloc(N + 65536, 1);
    if (!out || !H) { free(in); free(out); free(H); H = NULL;
                      snprintf(msg, msgn, "内存不足"); return 1; }
    reset_state();
    ctx_hashes();
    ibuf = in + 15; ipos = 0; isize = inN - 15;
    rc_init_dec();
    for (size_t k = 0; k < N; k++) {
        out[k] = (U8)process_byte(-1, 1);
        if (lx_progress_cb && (k & 0xFFFF) == 0)
            lx_progress_cb((int)(k * 100 / (N ? N : 1)));
    }
    U32 got = crc32buf(out, N);
    free(in); free(H); H = NULL;
    if (got != want) {
        free(out);
        snprintf(msg, msgn, "校验失败! 数据损坏 (CRC %08x != %08x)", got, want);
        return 1;
    }
    int rc = save_file2(outpath, out, N, msg, msgn);
    free(out);
    if (rc == 0) {
        double secs = (double)(clock() - t0) / CLOCKS_PER_SEC;
        snprintf(msg, msgn, "解压完成: %zu 字节, CRC 校验通过, 用时 %.2fs", N, secs);
    }
    return rc;
}''', 'PLACEHOLDER_DECOMPRESS')

src = src.replace('PLACEHOLDER_DECOMPRESS', '''LXAPI int lx_decompress_file(const char *inpath, const char *outpath,
                       char *msg, size_t msgn) {
    lx_shutdown();      /* 每个作业从出厂状态开始(与编码端一致) */
    ensure_init();
    if (!g_eng_init) { snprintf(msg, msgn, "内存不足: 模型初始化失败"); return 1; }
    clock_t t0 = clock();

    FILE *fi = fopen(inpath, "rb");
    if (!fi) { snprintf(msg, msgn, "无法打开文件: %s", inpath); return 1; }
    U8 hdr[15];
    if (fread(hdr, 1, 15, fi) < 15 || memcmp(hdr, "LX1", 3) != 0) {
        fclose(fi);
        snprintf(msg, msgn, "不是灵犀文件(头错误): %s", inpath);
        return 1;
    }
    U64 sz64 = 0;
    for (int k = 0; k < 8; k++) sz64 |= (U64)hdr[3 + k] << (8 * k);
    U32 want = 0;
    for (int k = 0; k < 4; k++) want |= (U32)hdr[11 + k] << (8 * k);

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
}''')
open('lingxi.c', 'w', encoding='utf-8', newline='\n').write(src)
print('streaming file APIs applied OK')
