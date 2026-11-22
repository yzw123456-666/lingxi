"""窗口模式修复: 边界匹配/搜索验证全部走循环索引; 压缩消息长度先记后回写"""
src = open('lingxi.c', encoding='utf-8', newline='').read()
src = src.replace('\r\n', '\n')

def rep(old, new):
    global src
    if new in src and old not in src:
        print('skip:', old[:44])
        return
    assert old in src, 'NOT FOUND: ' + old[:70]
    src = src.replace(old, new, 1)

# 1) 边界匹配延续: 循环索引
rep('    if (mLen > 0) {\n        if ((U32)b == H[mPos]) {',
    '    if (mLen > 0) {\n        if ((U32)b == HB(mPos)) {')

# 2) 搜索: 仅接受窗口内位置
rep('''    if (mLen == 0 && n >= (U32)MINLEN) {
        U32 pos = MT[key];
        if (pos && pos < n && pos >= (U32)MINLEN) {
            int ok = 1;
            for (int k = 1; k <= MINLEN; k++) {
                if (H[pos - (U32)k] != H[n - (U32)k]) { ok = 0; break; }
            }''',
    '''    if (mLen == 0 && n >= (U32)MINLEN) {
        U32 pos = MT[key];
        if (pos && pos < n && pos >= (U32)MINLEN && n - pos < HCAP) {
            int ok = 1;
            for (int k = 1; k <= MINLEN; k++) {
                if (HB(pos - (U32)k) != HB(n - (U32)k)) { ok = 0; break; }
            }''')

# 3) 压缩消息: 先记长度再回写头
rep('''    rc_flush();
    g_out = NULL;
    crc = CRC32_FINAL(crc);
    for (int k = 0; k < 4; k++) hdr[11 + k] = (U8)(crc >> (8 * k));
    _fseeki64(fo, 0, SEEK_SET);
    fwrite(hdr, 1, 15, fo);
    int werr = ferror(fo);
    long long outN = _ftelli64(fo);
    fclose(fo); fclose(fi);''',
    '''    rc_flush();
    g_out = NULL;
    crc = CRC32_FINAL(crc);
    for (int k = 0; k < 4; k++) hdr[11 + k] = (U8)(crc >> (8 * k));
    fflush(fo);
    long long outN = _ftelli64(fo);          /* 先记长度, 再回写头 */
    _fseeki64(fo, 0, SEEK_SET);
    fwrite(hdr, 1, 15, fo);
    int werr = ferror(fo);
    fclose(fo); fclose(fi);''')

open('lingxi.c', 'w', encoding='utf-8', newline='\n').write(src)
print('window fixes applied OK')
