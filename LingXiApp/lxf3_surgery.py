"""花括号匹配整块替换: PackFolder/ExtractFolder/ListManifest/DecompressJob/路由预检 → LXF3"""
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

src = open('Program.cs', encoding='utf-8', newline='').read()
src = src.replace('\r\n', '\n')

def brace_block(src, sig_start):
    """定位 sig_start 起的方法体 {...} (花括号配对), 返回 (start, end_exclusive)"""
    i = src.find(sig_start)
    assert i >= 0, 'sig not found: ' + sig_start[:60]
    ob = src.find('{', i)
    depth = 0
    j = ob
    while j < len(src):
        c = src[j]
        if c == '"':                       # 跳过字符串字面量
            j += 1
            while j < len(src) and src[j] != '"':
                if src[j] == '\\': j += 1
                j += 1
        elif c == "'":
            j += 1
            while j < len(src) and src[j] != "'":
                if src[j] == '\\': j += 1
                j += 1
        elif c == '{': depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0: return i, j + 1
        j += 1
    raise AssertionError('unbalanced braces: ' + sig_start[:60])

def replace_block(src, sig_start, new_text):
    s, e = brace_block(src, sig_start)
    return src[:s] + new_text + src[e:]

# ---------- 1) PackFolder → LXF3 ----------
PACK_NEW = '''    // ---------- 文件夹归档 LXF3: CM 池单流建模 + 原始池 + 清单 ----------
    // 关键优化: 所有"可建模"文件的原始字节按序拼成一个连续流, 整个文件夹只做
    // **一次**工厂初始化 + 一次建模 (消除 2348 个小文件 × 每文件重置的瓶颈)。
    // 已压缩数据 (zip/exe/webp...) 不进入 CM 池: 原样进原始池 (磁盘直拷)。
    // 结构: "LXF3" + u32 条目数 + 条目×[u16 路径长 + UTF-8 路径 + u8 isDir + u8 src
    //        + u64 origSize + u64 off + u64 len] + u64 cmLxSize + CM 池 LX1 流 + 原始池
    // src 0 = 数据在 CM 池解压结果 [off, off+len); src 1 = 原始池 [off, off+len)
    static void PackFolder(string dir, string outLxf)
    {
        var root = new DirectoryInfo(dir);
        var entries = new List<(string rel, bool isDir, long size, string full)>();
        CollectEntries(root, "", entries);
        entries.Sort((a, b) => string.CompareOrdinal(a.rel, b.rel));

        var tmpCmRaw = Path.Combine(Path.GetTempPath(), "lxapp_cmraw_" + Guid.NewGuid().ToString("N") + ".tmp");
        var tmpCmLx = Path.Combine(Path.GetTempPath(), "lxapp_cmlx_" + Guid.NewGuid().ToString("N") + ".lx");
        try
        {
            // 阶段 1: 决策 + 把"可建模"文件的原始字节按序写入 CM 源池 (0-70%)
            var meta = new List<(string rel, bool isDir, byte src, long origSize, long off, long len)>();
            long cmOff = 0, rawOff = 0;
            int idx = 0;
            using (var cmSrc = new FileStream(tmpCmRaw, FileMode.Create, FileAccess.Write, FileShare.None, 1 << 20))
            {
                foreach (var e in entries)
                {
                    progressPct = idx * 70 / Math.Max(1, entries.Count);
                    if (e.isDir)
                    { meta.Add((e.rel, true, 0, 0, 0, 0)); idx++; continue; }
                    bool stored;
                    if (e.size > LxfCmThreshold)
                        stored = IsStoredByExt(e.full, e.size) || SampleIncompressible(e.full);
                    else
                        stored = false;                     /* 小文件: 进 CM 池 (池化, 无重置开销) */
                    if (stored)
                    {
                        /* 原始池在组装阶段直接从原文件流式拷贝, 此处仅记录位置 */
                        meta.Add((e.rel, false, 1, e.size, rawOff, e.size));
                        rawOff += e.size;
                    }
                    else
                    {
                        meta.Add((e.rel, false, 0, e.size, cmOff, e.size));
                        cmOff += e.size;
                        using var fs = new FileStream(e.full, FileMode.Open, FileAccess.Read, FileShare.Read, 1 << 20);
                        fs.CopyTo(cmSrc);                   /* CM 源池按序拼接 */
                    }
                    idx++;
                }
            }

            // 阶段 2: CM 源池 → 引擎建模 → CM 池 LX1 流 (70-95%, 引擎回调驱动进度)
            lx_compress_file(tmpCmRaw, tmpCmLx, new StringBuilder(512), 512);

            // 阶段 3: 组装容器 = 清单 + CM 池 LX1 流 + 原始池 (95-100%)
            long cmLxSize = new FileInfo(tmpCmLx).Length;
            using (var outf = new FileStream(outLxf, FileMode.Create, FileAccess.Write, FileShare.None, 1 << 20))
            {
                outf.Write(Encoding.ASCII.GetBytes("LXF3"));
                outf.Write(BitConverter.GetBytes((uint)meta.Count));
                foreach (var m in meta)
                {
                    var pb = Encoding.UTF8.GetBytes(m.rel);
                    outf.Write(BitConverter.GetBytes((ushort)pb.Length));
                    outf.Write(pb);
                    outf.Write(new byte[] { (byte)(m.isDir ? 1 : 0), m.src });
                    outf.Write(BitConverter.GetBytes((ulong)m.origSize));
                    outf.Write(BitConverter.GetBytes((ulong)m.off));
                    outf.Write(BitConverter.GetBytes((ulong)m.len));
                }
                outf.Write(BitConverter.GetBytes((ulong)cmLxSize));
                using var cm = new FileStream(tmpCmLx, FileMode.Open, FileAccess.Read, FileShare.Read, 1 << 20);
                cm.CopyTo(outf);
                /* 原始池: 按条目顺序从原文件流式拷贝 */
                foreach (var m2 in meta)
                {
                    if (m2.isDir || m2.src != 1) continue;
                    using var rf = new FileStream(Path.Combine(root.FullName, m2.rel.Replace('/', Path.DirectorySeparatorChar)),
                        FileMode.Open, FileAccess.Read, FileShare.Read, 1 << 20);
                    rf.CopyTo(outf);
                }
            }
        }
        finally
        {
            TryDelete(tmpCmRaw); TryDelete(tmpCmLx);
        }
    }'''

src = replace_block(src, 'static void PackFolder(string dir, string outLxf)', PACK_NEW)

# ---------- 2) ExtractFolder → LXF3 (含 method 0/1) ----------
EXTRACT_NEW = '''    static (int files, int dirs) ExtractFolder(string lxf, string destDir)
    {
        using var f = new FileStream(lxf, FileMode.Open, FileAccess.Read, FileShare.Read, 1 << 20);
        var magic = new byte[4];
        f.ReadExactly(magic, 0, 4);
        var magicStr = Encoding.ASCII.GetString(magic);
        if (magicStr != "LXF3" && magicStr != "LXF2" && magicStr != "LXF1")
            throw new IOException("不是文件夹归档");
        bool hasMethod = magicStr != "LXF1";
        var cnt = new byte[4];
        f.ReadExactly(cnt, 0, 4);
        uint count = BitConverter.ToUInt32(cnt, 0);
        var meta = new List<(string rel, bool isDir, byte method, ulong psize)>();
        for (uint i = 0; i < count; i++)
        {
            var lb = new byte[2]; f.ReadExactly(lb, 0, 2);
            ushort pl = BitConverter.ToUInt16(lb, 0);
            var pb = new byte[pl]; f.ReadExactly(pb, 0, pl);
            var fl = new byte[hasMethod ? 2 : 1]; f.ReadExactly(fl, 0, fl.Length);
            var zb = new byte[8]; f.ReadExactly(zb, 0, 8);
            byte method = hasMethod ? fl[1] : (byte)0;
            meta.Add((Encoding.UTF8.GetString(pb), fl[0] == 1, method, BitConverter.ToUInt64(zb, 0)));
        }
        Directory.CreateDirectory(destDir);
        int files = 0, dirs = 0;
        long doneBytes = 0;
        long totalBytes = meta.Sum(m => (long)m.psize);
        string tmpLx = Path.Combine(Path.GetTempPath(), "lxapp_x_" + Guid.NewGuid().ToString("N") + ".lx");
        foreach (var (rel, isDir, method, psize) in meta)
        {
            var target = Path.Combine(destDir, SafeRel(rel).Replace('/', Path.DirectorySeparatorChar));
            if (isDir)
            {
                Directory.CreateDirectory(target); dirs++;
                continue;
            }
            Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            if (method == 1)
            {
                /* 条目载荷 = 完整 LX1 流: 落盘后交给引擎解压 */
                using (var lx = new FileStream(tmpLx, FileMode.Create, FileAccess.Write, FileShare.None, 1 << 20))
                {
                    var buf = new byte[1 << 16];
                    ulong left = psize;
                    while (left > 0)
                    {
                        int chunk = (int)Math.Min(left, (ulong)buf.Length);
                        f.ReadExactly(buf, 0, chunk);
                        lx.Write(buf, 0, chunk);
                        left -= (ulong)chunk;
                    }
                }
                var xmsg = new byte[1024];
                lx_decompress_file(tmpLx, target, xmsg, xmsg.Length);
                TryDelete(tmpLx);
                doneBytes += (long)psize;
                progressPct = (int)(doneBytes * 100 / Math.Max(1, totalBytes));
                files++;
                continue;
            }
            /* method 0: 原始存储, 流式拷贝 */
            using var fs = new FileStream(target, FileMode.Create, FileAccess.Write, FileShare.None, 1 << 20);
            var buf2 = new byte[1 << 16];
            ulong left2 = psize;
            while (left2 > 0)
            {
                int chunk = (int)Math.Min(left2, (ulong)buf2.Length);
                f.ReadExactly(buf2, 0, chunk);
                fs.Write(buf2, 0, chunk);
                left2 -= (ulong)chunk;
                doneBytes += chunk;
                progressPct = (int)(doneBytes * 100 / Math.Max(1, totalBytes));
            }
            files++;
        }
        TryDelete(tmpLx);
        return (files, dirs);
    }'''

src = replace_block(src, 'static (int files, int dirs) ExtractFolder(string lxf, string destDir)', EXTRACT_NEW)

# ---------- 3) ListManifest → LXF3 ----------
LIST_NEW = '''    static (uint count, List<(string rel, bool isDir, ulong size)>, long totalBytes) ListManifest(string lxf)
    {
        using var f = new FileStream(lxf, FileMode.Open, FileAccess.Read, FileShare.Read, 1 << 20);
        var magic = new byte[4];
        f.ReadExactly(magic, 0, 4);
        var magicStr = Encoding.ASCII.GetString(magic);
        if (magicStr != "LXF3" && magicStr != "LXF2" && magicStr != "LXF1")
            throw new IOException("不是文件夹归档");
        bool hasMethod = magicStr != "LXF1";
        var cnt = new byte[4];
        f.ReadExactly(cnt, 0, 4);
        uint count = BitConverter.ToUInt32(cnt, 0);
        var meta = new List<(string rel, bool isDir, ulong size)>();
        long total = 0;
        for (uint i = 0; i < count; i++)
        {
            var lb = new byte[2]; f.ReadExactly(lb, 0, 2);
            ushort pl = BitConverter.ToUInt16(lb, 0);
            var pb = new byte[pl]; f.ReadExactly(pb, 0, pl);
            var fl = new byte[hasMethod ? 2 : 1]; f.ReadExactly(fl, 0, fl.Length);
            var zb = new byte[8]; f.ReadExactly(zb, 0, 8);
            var sz = BitConverter.ToUInt64(zb, 0);
            meta.Add((Encoding.UTF8.GetString(pb), fl[0] == 1, sz));
            if (fl[0] == 0) total += (long)sz;
        }
        return (count, meta, total);
    }'''

src = replace_block(src, 'static (uint count, List<(string rel, bool isDir, ulong size)>, long totalBytes) ListManifest(string lxf)', LIST_NEW)

# ---------- 4) DecompressJob → 魔数分发 ----------
DEC_NEW = '''    static (int rc, string msg, string saved, bool isFolder) DecompressJob(string lxPath, string destDir)
    {
        busy = true; progressPct = 0;
        try
        {
            var magic = new byte[4];
            using (var fs = File.OpenRead(lxPath))
                fs.ReadExactly(magic, 0, 4);
            var mg = Encoding.ASCII.GetString(magic);
            var stem = Path.GetFileNameWithoutExtension(lxPath);
            string baseDir = destDir ?? Path.GetDirectoryName(lxPath)!;
            if (mg == "LXF3" || mg == "LXF2" || mg == "LXF1")
            {
                string dest = UniqueDir(Path.Combine(baseDir, stem));
                var (files, dirs) = ExtractFolder(lxPath, dest);
                return (0, $"解压完成: {files} 个文件 / {dirs} 个文件夹　已保存到: {dest}", dest, true);
            }
            if (mg != "LX1" && mg != "LX0")
                return (1, "不是灵犀文件: 无法识别的头部", "", false);
            busy 恢复后建模解压单文件;
        }
        catch (Exception ex)
        {
            return (1, "失败: " + ex.Message, "", false);
        }
        finally
        {
            lx_shutdown(); busy = false; progressPct = 100;
        }
    }'''

DEC_NEW = DEC_NEW.replace('busy 恢复后建模解压单文件;', '''            var sb = new byte[1024];
            string tout = Path.Combine(Path.GetTempPath(), "lxapp_out_" + Guid.NewGuid().ToString("N") + ".tmp");
            int rc = lx_decompress_file(lxPath, tout, sb, sb.Length);
            var msg = MsgOf(sb);
            if (rc != 0) { TryDelete(tout); return (rc, msg, "", false); }
            string final = UniqueOut(Path.Combine(baseDir, stem));
            File.Move(tout, final, true);
            return (0, msg + "　已保存到: " + final, final, false);''')

src = replace_block(src, 'static (int rc, string msg, string saved, bool isFolder) DecompressJob(string lxPath, string destDir)', DEC_NEW)

open('Program.cs', 'w', encoding='utf-8', newline='\n').write(src)
print('LXF3 surgery applied OK')
