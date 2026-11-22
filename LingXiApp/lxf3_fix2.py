"""修正 ExtractFolder/ListManifest 为 LXF3 池语义 (花括号匹配整块替换)"""
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

src = open('Program.cs', encoding='utf-8', newline='').read()
src = src.replace('\r\n', '\n')

def brace_block(src, sig_start):
    i = src.find(sig_start)
    assert i >= 0, 'sig not found: ' + sig_start[:60]
    ob = src.find('{', i)
    depth = 0
    j = ob
    while j < len(src):
        c = src[j]
        if c == '"':
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
    raise AssertionError('unbalanced: ' + sig_start[:60])

def replace_block(src, sig_start, new_text):
    s, e = brace_block(src, sig_start)
    return src[:s] + new_text + src[e:]

# ---------- ExtractFolder: LXF3 池语义 ----------
EXTRACT_NEW = '''    static (int files, int dirs) ExtractFolder(string lxf, string destDir)
    {
        using var f = new FileStream(lxf, FileMode.Open, FileAccess.Read, FileShare.Read, 1 << 20);
        var magic = new byte[4];
        f.ReadExactly(magic, 0, 4);
        var magicStr = Encoding.ASCII.GetString(magic);
        if (magicStr != "LXF3" && magicStr != "LXF2" && magicStr != "LXF1")
            throw new IOException("不是文件夹归档");
        bool pooled = magicStr == "LXF3";
        bool hasMethod = magicStr != "LXF1";
        var cnt = new byte[4];
        f.ReadExactly(cnt, 0, 4);
        uint count = BitConverter.ToUInt32(cnt, 0);
        var meta = new List<(string rel, bool isDir, byte src, ulong off, ulong len)>();
        for (uint i = 0; i < count; i++)
        {
            var lb = new byte[2]; f.ReadExactly(lb, 0, 2);
            ushort pl = BitConverter.ToUInt16(lb, 0);
            var pb = new byte[pl]; f.ReadExactly(pb, 0, pl);
            var fl = new byte[hasMethod ? 2 : 1]; f.ReadExactly(fl, 0, fl.Length);
            var ob = new byte[8]; f.ReadExactly(ob, 0, 8);
            var lb2 = new byte[8]; f.ReadExactly(lb2, 0, 8);
            var lb3 = new byte[8]; f.ReadExactly(lb3, 0, 8);
            ulong off = pooled ? BitConverter.ToUInt64(ob, 0) : 0;
            ulong len = BitConverter.ToUInt64(lb3, 0);
            meta.Add((Encoding.UTF8.GetString(pb), fl[0] == 1, fl.Length > 1 ? fl[1] : (byte)0, off, len));
        }

        /* LXF3: 先解压 CM 池 (LX1 流区域 → 临时文件 → 引擎解压 → 连续原始字节) */
        string cmRawFile = null;
        long rawPoolStart = 0;
        if (pooled)
        {
            var szB = new byte[8]; f.ReadExactly(szB, 0, 8);
            ulong cmLxSize = BitConverter.ToUInt64(szB, 0);
            cmRawFile = Path.Combine(Path.GetTempPath(), "lxapp_cmraw_" + Guid.NewGuid().ToString("N") + ".tmp");
            using (var lx = new FileStream(cmRawFile, FileMode.Create, FileAccess.Write, FileShare.None, 1 << 20))
            {
                var buf = new byte[1 << 16];
                ulong left = cmLxSize;
                while (left > 0)
                {
                    int chunk = (int)Math.Min(left, (ulong)buf.Length);
                    f.ReadExactly(buf, 0, chunk);
                    lx.Write(buf, 0, chunk);
                    left -= (ulong)chunk;
                }
            }
            rawPoolStart = f.Position;
            var xmsg = new byte[1024];
            lx_decompress_file(cmRawFile, cmRawFile + ".raw", xmsg, xmsg.Length);
        }

        Directory.CreateDirectory(destDir);
        int files = 0, dirs = 0;
        long doneBytes = 0;
        long totalBytes = meta.Sum(m => (long)m.len);
        using var cmRaw = cmRawFile != null ? new FileStream(cmRawFile + ".raw", FileMode.Open, FileAccess.Read, FileShare.Read, 1 << 20) : null;
        foreach (var (rel, isDir, src, off, len) in meta)
        {
            var target = Path.Combine(destDir, SafeRel(rel).Replace('/', Path.DirectorySeparatorChar));
            if (isDir)
            {
                Directory.CreateDirectory(target); dirs++;
                continue;
            }
            Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            FileStream dataSrc;
            if (src == 0)
            {
                if (cmRaw == null) throw new IOException("CM 池缺失");
                cmRaw.Position = off;
                dataSrc = cmRaw;
            }
            else
            {
                f.Position = rawPoolStart + off;
                dataSrc = f;
            }
            using var fs = new FileStream(target, FileMode.Create, FileAccess.Write, FileShare.None, 1 << 20);
            var buf = new byte[1 << 16];
            ulong left = (ulong)len;
            while (left > 0)
            {
                int chunk = (int)Math.Min(left, (ulong)buf.Length);
                int k = dataSrc.Read(buf, 0, chunk);
                if (k <= 0) throw new IOException("数据池提前结束");
                fs.Write(buf, 0, k);
                left -= (ulong)k;
                doneBytes += k;
                progressPct = (int)(doneBytes * 100 / Math.Max(1, totalBytes));
            }
            files++;
        }
        cmRaw?.Dispose();
        if (cmRawFile != null) { TryDelete(cmRawFile); TryDelete(cmRawFile + ".raw"); }
        return (files, dirs);
    }'''

src = replace_block(src, 'static (int files, int dirs) ExtractFolder(string lxf, string destDir)', EXTRACT_NEW)

# ---------- ListManifest: LXF3 字段 ----------
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
            var ob = new byte[8]; f.ReadExactly(ob, 0, 8);
            var lb2 = new byte[8]; f.ReadExactly(lb2, 0, 8);
            var lb3 = new byte[8]; f.ReadExactly(lb3, 0, 8);
            var sz = BitConverter.ToUInt64(lb3, 0);
            meta.Add((Encoding.UTF8.GetString(pb), fl[0] == 1, sz));
            if (fl[0] == 0) total += (long)sz;
        }
        return (count, meta, total);
    }'''

src = replace_block(src, 'static (uint count, List<(string rel, bool isDir, ulong size)>, long totalBytes) ListManifest(string lxf)', LIST_NEW)

open('Program.cs', 'w', encoding='utf-8', newline='\n').write(src)
print('ExtractFolder/ListManifest LXF3 semantics applied OK')
