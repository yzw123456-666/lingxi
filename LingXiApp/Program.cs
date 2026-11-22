using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using Microsoft.Win32;

namespace LingXiApp;

internal static class Program
{
    [STAThread]
    static void Main(string[] args)
    {
        ApplicationConfiguration.Initialize();
        Application.Run(new MainForm(args));
    }
}

public class MainForm : Form
{
    WebView2 wv = null!;
    NotifyIcon tray = null!;
    readonly ContextMenuStrip trayMenu = new();
    TcpListener listener = null!;
    int port;
    bool reallyExit, trayHintShown, minimizedStart;
    static volatile bool busy;
    static volatile int progressPct;
    static readonly SemaphoreSlim jobLock = new(1, 1);
    static string html = "";

    // ---------- 引擎 P/Invoke ----------
    const string Dll = "lingxi_engine.dll";
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl, CharSet = CharSet.Ansi)]
    static extern int lx_compress_file(string inp, string outp, byte[] msg, int msgn);
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl, CharSet = CharSet.Ansi)]
    static extern int lx_decompress_file(string inp, string outp, byte[] msg, int msgn);
    /* 引擎写回 UTF-8 文本; ANSI 编组的输入路径与 GBK 代码页一致 (中文路径可用) */
    static string MsgOf(byte[] buf)
    {
        int n = Array.IndexOf(buf, (byte)0);
        if (n < 0) n = buf.Length;
        return Encoding.UTF8.GetString(buf, 0, n);
    }
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl)]
    static extern void lx_shutdown();
    public delegate void ProgressCb(int pct);
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl)]
    static extern void lx_set_progress(ProgressCb cb);
    static readonly ProgressCb cbKeep = pct => progressPct = pct;   // 防 GC

    const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
    const string Version = "v0.3-app";

    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool SetDllDirectory(string path);

    /* 把内嵌的本机引擎 DLL 解压到本地并注册搜索目录 (单文件发布的关键) */
    static void ExtractEngineDll()
    {
        var dir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "LingXiApp");
        Directory.CreateDirectory(dir);
        var dllPath = Path.Combine(dir, "lingxi_engine.dll");
        using (var rs = typeof(MainForm).Assembly.GetManifestResourceStream("LingXiApp.lingxi_engine.dll"))
        using (var fs = new FileStream(dllPath, FileMode.Create, FileAccess.Write))
            rs!.CopyTo(fs);
        SetDllDirectory(dir);
    }

    public MainForm(string[] args)
    {
        minimizedStart = args.Contains("--minimized");
        Text = "灵犀压缩盒";
        Icon = new Icon(typeof(MainForm).Assembly.GetManifestResourceStream("LingXiApp.app.ico")!);
        StartPosition = FormStartPosition.CenterScreen;
        MinimumSize = new Size(760, 660);
        Size = new Size(940, 880);

        using (var s = typeof(MainForm).Assembly.GetManifestResourceStream("LingXiApp.app.html")!)
        using (var r = new StreamReader(s, Encoding.UTF8))
            html = r.ReadToEnd();

        ExtractEngineDll();
        lx_set_progress(cbKeep);
        StartServer();

        wv = new WebView2 { Dock = DockStyle.Fill };
        Controls.Add(wv);
        Shown += async (_, _) =>
        {
            if (minimizedStart)
            {
                Hide();
                tray.ShowBalloonTip(2500, "灵犀压缩盒", "灵犀压缩盒正在后台运行，双击托盘图标可打开。", ToolTipIcon.Info);
                trayHintShown = true;
            }
            await InitWebView();
        };

        // 托盘
        tray = new NotifyIcon
        {
            Icon = Icon,
            Text = "灵犀压缩盒",
            Visible = true
        };
        tray.DoubleClick += (_, _) => RestoreFromTray();
        trayMenu.Items.Add("打开", null, (_, _) => RestoreFromTray());
        var autoItem = new ToolStripMenuItem("开机自启动") { CheckOnClick = true, Checked = AutostartOn() };
        autoItem.CheckedChanged += (_, _) => AutostartSet(autoItem.Checked);
        trayMenu.Items.Add(autoItem);
        trayMenu.Items.Add("退出", null, (_, _) => ExitApp());
        tray.ContextMenuStrip = trayMenu;

        FormClosing += (_, e) =>
        {
            if (reallyExit) return;
            e.Cancel = true;
            Hide();
            if (!trayHintShown) { tray.ShowBalloonTip(2500, "灵犀压缩盒", "已缩小到系统托盘，双击图标可再次打开。", ToolTipIcon.Info); trayHintShown = true; }
        };
    }

    void RestoreFromTray()
    {
        Show(); WindowState = FormWindowState.Normal; Activate();
    }
    void ExitApp()
    {
        reallyExit = true;
        tray.Visible = false;
        Close();
        Application.Exit();
    }

    async Task InitWebView()
    {
        var dataDir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "LingXiApp", "WebView2");
        var env = await CoreWebView2Environment.CreateAsync(null, dataDir);
        await wv.EnsureCoreWebView2Async(env);
        wv.CoreWebView2.Settings.AreDefaultContextMenusEnabled = false;
        wv.CoreWebView2.WebMessageReceived += OnWebMessage;
        wv.CoreWebView2.Navigate($"http://127.0.0.1:{port}/");
    }

    void PostJson(object o)
    {
        if (wv?.CoreWebView2 == null) return;
        wv.CoreWebView2.PostWebMessageAsJson(JsonSerializer.Serialize(o));
    }

    static string? JsonBodyProp(string bodyFile, string prop)
    {
        if (!File.Exists(bodyFile)) return null;
        try
        {
            using var doc = JsonDocument.Parse(File.ReadAllText(bodyFile));
            var v = doc.RootElement.GetProperty(prop).GetString();
            return v;
        }
        catch { return null; }
    }

    static string UniqueOut(string candidate)
    {
        if (!File.Exists(candidate) && !Directory.Exists(candidate)) return candidate;
        var dir = Path.GetDirectoryName(candidate)!;
        var stem = Path.GetFileNameWithoutExtension(candidate);
        var ext = Path.GetExtension(candidate);
        for (int i = 1; ; i++)
        {
            var c = Path.Combine(dir, $"{stem} ({i}){ext}");
            if (!File.Exists(c) && !Directory.Exists(c)) return c;
        }
    }

    static string UniqueDir(string candidate)
    {
        if (!Directory.Exists(candidate)) return candidate;
        for (int i = 1; ; i++)
        {
            var c = candidate + $" ({i})";
            if (!Directory.Exists(c)) return c;
        }
    }

    // ---------- 文件夹归档 (LXF1 容器: 清单 + 顺序载荷, 交给引擎整体压缩) ----------
    // 格式: "LXF1" + u32 条目数 + N×[u16 路径长 + UTF-8 路径 + u8 isDir + u64 size] + 顺序文件载荷
    const string LxfMagic = "LXF1";

    static void CollectEntries(DirectoryInfo dir, string prefix,
        List<(string rel, bool isDir, long size, string full)> acc)
    {
        foreach (var d in dir.EnumerateDirectories())
        {
            var rel = prefix + d.Name + "/";
            acc.Add((rel, true, 0, d.FullName));
            CollectEntries(d, rel, acc);
        }
        foreach (var f in dir.EnumerateFiles())
            acc.Add((prefix + f.Name, false, f.Length, f.FullName));
    }

    const long LxfCmThreshold = 4L * 1024 * 1024;    /* > 4MB 的文件做采样决策 */
    const int LxfSampleSize = 256 * 1024;

    static readonly System.Collections.Generic.HashSet<string> StoredExts = new(
        new[] { ".zip", ".exe", ".webp", ".png", ".jpg", ".jpeg", ".gif", ".mp4", ".mkv",
                ".avi", ".mov", ".7z", ".rar", ".gz", ".bz2", ".xz", ".zst", ".mp3",
                ".flac", ".ogg", ".webm", ".iso", ".msi", ".apk", ".docx", ".xlsx", ".pptx" });

    static bool IsStoredByExt(string file, long size)
    {
        var ext = Path.GetExtension(file).ToLowerInvariant();
        return StoredExts.Contains(ext);
    }

    /* 采样决策: 压缩前 256KB; 压缩率 > 0.90 → 已压缩数据 → 存储而非建模 */
    static bool SampleIncompressible(string file)
    {
        var sample = Path.Combine(Path.GetTempPath(), "lxapp_smp_" + Guid.NewGuid().ToString("N") + ".bin");
        var comp = sample + ".lx";
        try
        {
            using (var s1 = File.OpenRead(file))
            using (var d1 = File.Create(sample))
            {
                var buf = new byte[1 << 16];
                int left = LxfSampleSize, k;
                while (left > 0 && (k = s1.Read(buf, 0, Math.Min(left, buf.Length))) > 0)
                { d1.Write(buf, 0, k); left -= k; }
            }
            var smsg = new byte[1024];
            var rc = lx_compress_file(sample, comp, smsg, smsg.Length);
            if (rc != 0) return false;
            long sampleLen = Math.Min(LxfSampleSize, new FileInfo(file).Length);
            return new FileInfo(comp).Length * 1.0 / sampleLen > 0.90;
        }
        catch { return false; }
        finally { TryDelete(sample); TryDelete(comp); }
    }

    /* 单文件完整建模压缩 → 返回 LX1 流字节 (LXF2 条目载荷, method 1) */
    static byte[] CompressToLxBytes(string file)
    {
        var tmp = Path.Combine(Path.GetTempPath(), "lxapp_e_" + Guid.NewGuid().ToString("N") + ".lx");
        try
        {
            var sb = new byte[1024];
            var cmsg = new byte[1024];
            lx_compress_file(file, tmp, cmsg, cmsg.Length);
            return File.ReadAllBytes(tmp);
        }
        finally { TryDelete(tmp); }
    }

    // ---------- 文件夹归档 LXF2: 容器不整体压缩, 逐文件方法决策 ----------
    // method 0 = 原样存储 (已压缩数据, 磁盘直拷); method 1 = 逐文件 LX1 流 (完整建模)
    // 格式: "LXF2" + u32 条目数 + N×[u16 路径长 + UTF-8 路径 + u8 isDir + u8 method + u64 payloadSize] + N 个载荷
        // ---------- 文件夹归档 LXF3: CM 池单流建模 + 原始池 + 清单 ----------
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
            var pmsg = new byte[1024];
            lx_compress_file(tmpCmRaw, tmpCmLx, pmsg, pmsg.Length);

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
    }

    static string SafeRel(string p)
    {
        var n = p.Replace('\\', '/').TrimEnd('/');
        if (n.Length == 0) throw new IOException("归档包含空路径");
        if (n.Contains("..") || n.StartsWith('/') || n.Contains(':'))
            throw new IOException("归档包含非法路径: " + p);
        return n;
    }

    static bool IsFolderArchive(string file)
    {
        using var f = File.OpenRead(file);
        var m = new byte[4];
        if (f.Read(m, 0, 4) < 4) return false;
        var s = Encoding.ASCII.GetString(m);
        return s == "LXF3" || s == "LXF2" || s == "LXF1";
    }

            static (int files, int dirs) ExtractFolder(string lxf, string destDir)
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
        var meta = new List<(string rel, bool isDir, byte src, long off, long len)>();
        for (uint i = 0; i < count; i++)
        {
            var lb = new byte[2]; f.ReadExactly(lb, 0, 2);
            ushort pl = BitConverter.ToUInt16(lb, 0);
            var pb = new byte[pl]; f.ReadExactly(pb, 0, pl);
            var fl = new byte[hasMethod ? 2 : 1]; f.ReadExactly(fl, 0, fl.Length);
            var ob = new byte[8]; f.ReadExactly(ob, 0, 8);
            var lb2 = new byte[8]; f.ReadExactly(lb2, 0, 8);
            var lb3 = new byte[8]; f.ReadExactly(lb3, 0, 8);
            long off = pooled ? (long)BitConverter.ToUInt64(lb2, 0) : 0;
            long len = (long)BitConverter.ToUInt64(lb3, 0);
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
            try
            {
                var dbg = "cmLxSize=" + cmLxSize + " rawPoolStart=" + rawPoolStart
                    + " containerLen=" + new FileInfo(lxf).Length
                    + " rawSize=" + new FileInfo(cmRawFile + ".raw").Length + "\n"
                    + string.Join("\n", meta.Select(m =>
                        "  " + m.rel + " src=" + m.src + " off=" + m.off + " len=" + m.len));
                File.WriteAllText(Path.Combine(Path.GetTempPath(), "lx_debug.txt"), dbg);
            }
            catch { }
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
    }

            static (uint count, List<(string rel, bool isDir, ulong size)>, long totalBytes) ListManifest(string lxf)
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
    }

    private void OnWebMessage(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        try
        {
            using var doc = JsonDocument.Parse(e.WebMessageAsJson);
            var root = doc.RootElement;
            string cmd = root.TryGetProperty("cmd", out var cv) ? cv.GetString() ?? "" : "";
            switch (cmd)
            {
                case "pick":
                {
                    using var dlg = new OpenFileDialog
                    {
                        Title = "选择要压缩 / 解压的文件",
                        Filter = "所有文件 (*.*)|*.*|灵犀压缩文件 (*.lx)|*.lx"
                    };
                    if (dlg.ShowDialog(this) == DialogResult.OK)
                    {
                        var fi = new FileInfo(dlg.FileName);
                        PostJson(new { type = "picked", ok = true, name = fi.Name, size = fi.Length, path = dlg.FileName });
                    }
                    else PostJson(new { type = "picked", ok = false });
                    break;
                }
                case "run":
                {
                    string op = root.GetProperty("op").GetString() ?? "";
                    string path = root.GetProperty("path").GetString() ?? "";
                    RunPathJob(op, path);
                    break;
                }
                case "pickdir":
                {
                    using var dlg = new FolderBrowserDialog
                    {
                        Description = "选择要压缩的文件夹",
                        ShowNewFolderButton = false
                    };
                    if (dlg.ShowDialog(this) == DialogResult.OK)
                    {
                        long bytes = 0; int files = 0;
                        foreach (var f in new DirectoryInfo(dlg.SelectedPath).EnumerateFiles("*", SearchOption.AllDirectories))
                        { bytes += f.Length; files++; }
                        PostJson(new { type = "picked-dir", ok = true, name = Path.GetFileName(
                            dlg.SelectedPath.TrimEnd(Path.DirectorySeparatorChar)), path = dlg.SelectedPath,
                            files, bytes });
                    }
                    else PostJson(new { type = "picked-dir", ok = false });
                    break;
                }
                case "rundir":
                {
                    string path = root.GetProperty("path").GetString() ?? "";
                    RunDirJob(path);
                    break;
                }
                case "preview":
                {
                    string path = root.GetProperty("path").GetString() ?? "";
                    PreviewPathJob(path);
                    break;
                }
            }
        }
        catch (Exception ex)
        {
            PostJson(new { type = "done", ok = false, msg = "宿主错误: " + ex.Message });
        }
    }

    // ---------- 共享作业实现 (UI postMessage 与 HTTP 端点走同一条路径, 可自动化测试) ----------
    static (int rc, string msg, string saved, bool isFolder) CompressJob(string srcPath, string destDir)
    {
        busy = true; progressPct = 0;
        var sb = new byte[1024];
        string tmpLxf = null;
        try
        {
            bool isFolder = Directory.Exists(srcPath) && !File.Exists(srcPath);
            string baseName = Path.GetFileName(srcPath.TrimEnd(Path.DirectorySeparatorChar));
            string outLx = UniqueOut(Path.Combine(
                destDir ?? Path.GetDirectoryName(srcPath)!, baseName) + ".lx");
            if (isFolder)
            {
                /* LXF3 容器: 逐文件决策, 已压缩数据存储直拷, 可压缩数据池化单流建模
                   容器本身不再整体压缩 (避免对已压缩数据做 2 次无效建模) */
                PackFolder(srcPath, outLx);
                var nFiles = new DirectoryInfo(srcPath)
                    .EnumerateFiles("*", SearchOption.AllDirectories).Count();
                return (0, $"文件夹压缩完成 ({nFiles} 个文件)　已保存到: {outLx}", outLx, false);
            }
            var rc = lx_compress_file(srcPath, outLx, sb, sb.Length);
            if (rc != 0) return (rc, MsgOf(sb), "", false);
            return (0, MsgOf(sb) + "　已保存到: " + outLx, outLx, false);
        }
        catch (Exception ex)
        {
            return (1, "失败: " + ex.Message, "", false);
        }
        finally
        {
            if (tmpLxf != null) TryDelete(tmpLxf);
            lx_shutdown(); busy = false; progressPct = 100;
        }
    }

        static (int rc, string msg, string saved, bool isFolder) DecompressJob(string lxPath, string destDir)
    {
        busy = true; progressPct = 0;
        try
        {
            var magic = new byte[4];
            using (var fs = File.OpenRead(lxPath))
                fs.ReadExactly(magic, 0, 4);
            var mg = Encoding.ASCII.GetString(magic, 0, 3);
            var stem = Path.GetFileNameWithoutExtension(lxPath);
            string baseDir = destDir ?? Path.GetDirectoryName(lxPath)!;
            if (mg == "LXF")
            {
                string dest = UniqueDir(Path.Combine(baseDir, stem));
                var (files, dirs) = ExtractFolder(lxPath, dest);
                return (0, $"解压完成: {files} 个文件 / {dirs} 个文件夹　已保存到: {dest}", dest, true);
            }
            if (mg != "LX1" && mg != "LX0")
                return (1, "不是灵犀文件: 无法识别的头部", "", false);
                        var sb = new byte[1024];
            string tout = Path.Combine(Path.GetTempPath(), "lxapp_out_" + Guid.NewGuid().ToString("N") + ".tmp");
            int rc = lx_decompress_file(lxPath, tout, sb, sb.Length);
            var msg = MsgOf(sb);
            if (rc != 0) { TryDelete(tout); return (rc, msg, "", false); }
            string final = UniqueOut(Path.Combine(baseDir, stem));
            File.Move(tout, final, true);
            return (0, msg + "　已保存到: " + final, final, false);
        }
        catch (Exception ex)
        {
            return (1, "失败: " + ex.Message, "", false);
        }
        finally
        {
            lx_shutdown(); busy = false; progressPct = 100;
        }
    }

    private async void RunDirJob(string path)
    {
        if (busy) { PostJson(new { type = "done", ok = false, msg = "已有任务进行中" }); return; }
        if (!Directory.Exists(path)) { PostJson(new { type = "done", ok = false, msg = "文件夹不存在" }); return; }
        var r = await Task.Run(() => CompressJob(path, null));
        PostJson(new { type = "done", ok = r.rc == 0, msg = r.msg, saved = r.saved });
    }

    private async void RunPathJob(string op, string path)
    {
        if (busy) { PostJson(new { type = "done", ok = false, msg = "已有任务进行中" }); return; }
        if (op != "compress" && op != "decompress")
        { PostJson(new { type = "done", ok = false, msg = "未知操作" }); return; }
        if (!File.Exists(path) && !Directory.Exists(path))
        { PostJson(new { type = "done", ok = false, msg = "路径不存在: " + path }); return; }
        var r = await Task.Run(() => op == "decompress"
            ? DecompressJob(path, null)
            : CompressJob(path, null));
        PostJson(new { type = "done", ok = r.rc == 0, msg = r.msg, saved = r.saved, isFolder = r.isFolder });
    }

    private async void PreviewPathJob(string path)
    {
        try
        {
            if (busy) { PostJson(new { type = "preview", ok = false, msg = "已有任务进行中" }); return; }
            if (!File.Exists(path)) { PostJson(new { type = "preview", ok = false, msg = "文件不存在" }); return; }
            var head = new byte[15];
            using (var fs = File.OpenRead(path))
                if (await fs.ReadAsync(head) < 15 || Encoding.ASCII.GetString(head, 0, 3) != "LX1")
                {
                    PostJson(new { type = "preview", ok = false, msg = "不是灵犀文件: 缺少 LX1 头" });
                    return;
                }
            string tout = Path.Combine(Path.GetTempPath(), "lxapp_pv_" + Guid.NewGuid().ToString("N") + ".tmp");
            busy = true; progressPct = 0;
            var sb = new byte[1024];
            int rc = 0;
            try
            {
                rc = await Task.Run(() => lx_decompress_file(path, tout, sb, sb.Length));
            }
            finally
            {
                busy = false; progressPct = 100;
                lx_shutdown();
            }
            var msg = MsgOf(sb);
            try
            {
                if (rc != 0) { PostJson(new { type = "preview", ok = false, msg }); return; }
                var (origSize, kind, label, content) = BuildPreview(tout, msg);
                PostJson(new { type = "preview", ok = true, origSize, kind, label, msg, content });
            }
            finally { File.Delete(tout); }
        }
        catch (Exception ex)
        {
            PostJson(new { type = "preview", ok = false, msg = ex.Message });
        }
    }

    // ---------- 开机自启动 (注册表 HKCU Run) ----------
    static bool AutostartOn() =>
        Registry.CurrentUser.OpenSubKey(RunKey)?.GetValue("LingXiBox") != null;
    static void AutostartSet(bool on)
    {
        using var k = Registry.CurrentUser.OpenSubKey(RunKey, true)!;
        if (on) k.SetValue("LingXiBox", $"\"{Application.ExecutablePath}\" --minimized");
        else k.DeleteValue("LingXiBox", false);
    }

    // ---------- 内置 HTTP 服务 (TcpListener, 只监听本机, 端口自动分配) ----------
    void StartServer()
    {
        listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        port = ((IPEndPoint)listener.LocalEndpoint).Port;
        File.WriteAllText(Path.Combine(Path.GetTempPath(), "lxapp_port.txt"), port.ToString());
        _ = Task.Run(AcceptLoopAsync);
    }

    async Task AcceptLoopAsync()
    {
        while (true)
        {
            TcpClient client;
            try { client = await listener.AcceptTcpClientAsync(); }
            catch { return; }
            _ = Task.Run(() => HandleClientAsync(client));
        }
    }

    static async Task SendAllAsync(NetworkStream s, byte[] data)
    {
        await s.WriteAsync(data);
    }

    static async Task RespondAsync(NetworkStream s, string ctype, string extra, byte[] body)
    {
        var head = Encoding.UTF8.GetBytes(
            $"HTTP/1.1 200 OK\r\nContent-Type: {ctype}\r\nContent-Length: {body.Length}\r\nCache-Control: no-store\r\n{extra}\r\n");
        await s.WriteAsync(head);
        if (body.Length > 0) await s.WriteAsync(body);
    }

    static async Task RespondFileAsync(NetworkStream s, string ctype, string extra, string path)
    {
        await using var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read, 1 << 16, useAsync: true);
        var head = Encoding.UTF8.GetBytes(
            $"HTTP/1.1 200 OK\r\nContent-Type: {ctype}\r\nContent-Length: {fs.Length}\r\nCache-Control: no-store\r\n{extra}\r\n");
        await s.WriteAsync(head);
        var buf = new byte[1 << 16];
        int k;
        while ((k = await fs.ReadAsync(buf)) > 0)
            await s.WriteAsync(buf, 0, k);
    }

    static async Task RespondErrAsync(NetworkStream s, int code, string msg)
    {
        var body = Encoding.UTF8.GetBytes($"{{\"ok\":0,\"err\":1,\"msg\":\"{JsonEsc(msg)}\"}}");
        var head = Encoding.UTF8.GetBytes(
            $"HTTP/1.1 {code} Error\r\nContent-Type: application/json\r\nContent-Length: {body.Length}\r\nConnection: close\r\n\r\n");
        await s.WriteAsync(head);
        await s.WriteAsync(body);
    }

    static string DownloadsDir()
    {
        var d = Path.Combine(Environment.GetFolderPath(
            Environment.SpecialFolder.UserProfile), "Downloads");
        Directory.CreateDirectory(d);
        return d;
    }

    static void TryDelete(string path)
    {
        for (int i = 0; i < 5; i++)
        {
            try { if (File.Exists(path)) File.Delete(path); return; }
            catch (IOException) { Thread.Sleep(300); }
            catch (UnauthorizedAccessException) { Thread.Sleep(300); }
        }
    }

    static string JsonEsc(string s) => s
        .Replace("\\", "\\\\").Replace("\"", "\\\"")
        .Replace("\n", "\\n").Replace("\r", "\\r").Replace("\t", "\\t");

    static async Task<(string method, string path, string bodyFile, long bodyLen)> ReadRequestAsync(TcpClient client)
    {
        var s = client.GetStream();
        s.ReadTimeout = 15000;
        var raw = new MemoryStream();
        var buf = new byte[65536];
        long total = 0;
        int headerEnd = -1;
        int k;
        while ((k = await s.ReadAsync(buf)) > 0)
        {
            raw.Write(buf, 0, k); total += k;
            // 在原始字节中查找 \r\n\r\n (避免二进制请求体破坏 UTF-8 解码偏移)
            var b = raw.GetBuffer();
            for (long i = Math.Max(0, total - k - 3); i + 4 <= total; i++)
                if (b[i] == '\r' && b[i + 1] == '\n' && b[i + 2] == '\r' && b[i + 3] == '\n') { headerEnd = (int)i; break; }
            if (headerEnd >= 0) break;
            if (total > 32768) throw new IOException("header too big");
        }
        if (headerEnd < 0) throw new IOException("bad request");
        var headText = Encoding.UTF8.GetString(raw.GetBuffer(), 0, headerEnd);
        var line = headText.Split("\r\n")[0];
        var parts = line.Split(' ');
        if (parts.Length < 2) throw new IOException("bad request");
        long clen = 0;
        foreach (var h in headText.Split("\r\n"))
            if (h.StartsWith("Content-Length:", StringComparison.OrdinalIgnoreCase))
                clen = long.Parse(h.Substring(15).Trim());

        // 请求体 -> 临时文件 (流式; 头缓冲里可能已含部分请求体)
        string bodyFile = "";
        if (clen > 0)
        {
            bodyFile = Path.Combine(Path.GetTempPath(), "lxapp_body_" + Guid.NewGuid().ToString("N") + ".tmp");
            await using var f = new FileStream(bodyFile, FileMode.Create, FileAccess.Write, FileShare.None, 1 << 16, useAsync: true);
            int bodyStart = headerEnd + 4;
            long already = Math.Min(total - bodyStart, clen);
            if (already > 0) await f.WriteAsync(raw.GetBuffer().AsMemory(bodyStart, (int)already));
            long left = clen - already;
            while (left > 0)
            {
                k = await s.ReadAsync(buf);
                if (k <= 0) throw new IOException("body truncated");
                await f.WriteAsync(buf.AsMemory(0, k));
                left -= k;
            }
        }
        var decodedPath = Uri.UnescapeDataString(parts[1]);
        return (parts[0], decodedPath, bodyFile, clen);
    }

    async Task HandleClientAsync(TcpClient client)
    {
        try
        {
            var s = client.GetStream();
            var (method, path, bodyFile, bodyLen) = await ReadRequestAsync(client);
            try
            {
                await RouteAsync(s, method, path, bodyFile, bodyLen);
            }
            finally
            {
                if (bodyFile.Length > 0 && File.Exists(bodyFile)) TryDelete(bodyFile);
            }
        }
        catch (Exception ex)
        {
            try { await RespondErrAsync(client.GetStream(), 500, ex.Message); } catch { }
        }
        try { client.Close(); } catch { }
    }

    async Task RouteAsync(NetworkStream s, string method, string path, string bodyFile, long bodyLen)
    {
        string nameParam = "";
        var q = path.IndexOf('?');
        if (q >= 0)
        {
            var qs = path[(q + 1)..];
            path = path[..q];
            foreach (var kv in qs.Split('&'))
            {
                var eq = kv.IndexOf('=');
                if (eq > 0 && kv[..eq] == "name")
                    nameParam = Uri.UnescapeDataString(kv[(eq + 1)..]);
            }
        }

        if (method == "GET" && path == "/")
        {
            await RespondAsync(s, "text/html; charset=utf-8", "Connection: close\r\n", Encoding.UTF8.GetBytes(html));
            return;
        }
        if (method == "GET" && path == "/api/status")
        {
            await RespondAsync(s, "application/json; charset=utf-8", "Connection: close\r\n",
                Encoding.UTF8.GetBytes($"{{\"ok\":1,\"busy\":{(busy ? 1 : 0)},\"progress\":{progressPct},\"autostart\":{(AutostartOn() ? 1 : 0)},\"version\":\"{Version}\"}}"));
            return;
        }
        if (method == "POST" && path == "/api/autostart")
        {
            bool on = false;
            if (File.Exists(bodyFile))
                on = (await File.ReadAllTextAsync(bodyFile)).TrimStart().StartsWith("1");
            AutostartSet(on);
            await RespondAsync(s, "application/json; charset=utf-8", "Connection: close\r\n",
                Encoding.UTF8.GetBytes($"{{\"ok\":1,\"msg\":\"{(on ? "开机自启动已开启" : "开机自启动已关闭")}\",\"enabled\":{(AutostartOn() ? 1 : 0)}}}"));
            return;
        }
        if (method == "POST" && path == "/api/quit")
        {
            await RespondAsync(s, "application/json; charset=utf-8", "Connection: close\r\n",
                Encoding.UTF8.GetBytes("{\"ok\":1,\"msg\":\"bye\"}"));
            BeginInvoke(() => { reallyExit = true; tray.Visible = false; Close(); Application.Exit(); });
            return;
        }
        if (method == "POST" && path == "/api/compress")
        {
            if (busy) { await RespondErrAsync(s, 409, "已有任务进行中"); return; }
            var p1 = JsonBodyProp(bodyFile, "path");
            if (p1 == null || (!File.Exists(p1) && !Directory.Exists(p1)))
            { await RespondErrAsync(s, 400, "路径不存在"); return; }
            var r = await Task.Run(() => CompressJob(p1, null));
            await RespondAsync(s, "application/json; charset=utf-8", "Connection: close\r\n",
                Encoding.UTF8.GetBytes(JsonSerializer.Serialize(new { ok = r.rc == 0, msg = r.msg, saved = r.saved })));
            return;
        }
        if (method == "POST" && path == "/api/decompress")
        {
            if (busy) { await RespondErrAsync(s, 409, "已有任务进行中"); return; }
            var p1 = JsonBodyProp(bodyFile, "path");
            if (p1 == null || !File.Exists(p1)) { await RespondErrAsync(s, 400, "文件不存在"); return; }
            var r = await Task.Run(() => DecompressJob(p1, null));
            await RespondAsync(s, "application/json; charset=utf-8", "Connection: close\r\n",
                Encoding.UTF8.GetBytes(JsonSerializer.Serialize(new { ok = r.rc == 0, msg = r.msg,
                    saved = r.saved, isFolder = r.isFolder })));
            return;
        }
        if (method == "POST" && path == "/api/compress-body")
        {
            if (busy) { await RespondErrAsync(s, 409, "已有任务进行中"); return; }
            if (!File.Exists(bodyFile)) { await RespondErrAsync(s, 400, "缺少请求体"); return; }
            var r = await Task.Run(() => CompressJob(bodyFile, DownloadsDir()));
            await RespondAsync(s, "application/json; charset=utf-8", "Connection: close\r\n",
                Encoding.UTF8.GetBytes(JsonSerializer.Serialize(new { ok = r.rc == 0, msg = r.msg, saved = r.saved })));
            return;
        }
        if (method == "POST" && path == "/api/decompress-body")
        {
            if (busy) { await RespondErrAsync(s, 409, "已有任务进行中"); return; }
            if (!File.Exists(bodyFile)) { await RespondErrAsync(s, 400, "缺少请求体"); return; }
            var r = await Task.Run(() => DecompressJob(bodyFile, DownloadsDir()));
            await RespondAsync(s, "application/json; charset=utf-8", "Connection: close\r\n",
                Encoding.UTF8.GetBytes(JsonSerializer.Serialize(new { ok = r.rc == 0, msg = r.msg,
                    saved = r.saved, isFolder = r.isFolder })));
            return;
        }
        if (method == "POST" && path == "/api/preview")
        {
            if (busy) { await RespondErrAsync(s, 409, "已有任务进行中"); return; }
            if (!File.Exists(bodyFile)) { await RespondErrAsync(s, 400, "缺少请求体"); return; }
            string tout = Path.Combine(Path.GetTempPath(), "lxapp_pv_" + Guid.NewGuid().ToString("N") + ".tmp");
            busy = true; progressPct = 0;
            var msgBuf = new byte[1024];
            int rc = 0;
            try
            {
                await Task.Run(() => { rc = lx_decompress_file(bodyFile, tout, msgBuf, msgBuf.Length); });
            }
            finally
            {
                busy = false; progressPct = 100;
                lx_shutdown();
            }
            var msg = MsgOf(msgBuf);
            if (rc != 0)
            {
                TryDelete(tout);
                await RespondAsync(s, "application/json; charset=utf-8", "Connection: close\r\n",
                    Encoding.UTF8.GetBytes(JsonSerializer.Serialize(new { ok = false, msg })));
                return;
            }
            var (origSize, kind, label, content) = BuildPreview(tout, msg);
            TryDelete(tout);
            await RespondAsync(s, "application/json; charset=utf-8", "Connection: close\r\n",
                Encoding.UTF8.GetBytes(JsonSerializer.Serialize(new { ok = true, origSize, kind, label, msg, content })));
            return;
        }
        await RespondErrAsync(s, 404, "no route");
    }

    static (long origSize, string kind, string label, string content) BuildPreview(string tout, string msg)
    {
        var data = File.ReadAllBytes(tout);
        int cap = (int)Math.Min(data.Length, 1 << 19);
        bool isText = Utf8Ok(data, cap);
        string esc, label;
        if (isText)
        {
            var sub = Encoding.UTF8.GetString(data, 0, cap);
            if (sub.Length > 65536) sub = sub[..65536];
            esc = JsonEsc(sub);
            label = "文本 (UTF-8 有效)";
        }
        else
        {
            var dumpLen = Math.Min(cap, 512);
            var sb = new StringBuilder();
            for (int i = 0; i < dumpLen; i += 16)
            {
                sb.Append(i.ToString("x4")).Append("  ");
                for (int k = 0; k < 16; k++)
                {
                    sb.Append(i + k < dumpLen ? data[i + k].ToString("x2") + " " : "   ");
                    if (k == 7) sb.Append(' ');
                }
                sb.Append('|');
                for (int k = 0; k < 16 && i + k < dumpLen; k++)
                {
                    var c = data[i + k]; sb.Append(c >= 0x20 && c < 0x7f ? (char)c : '.');
                }
                sb.Append('\n');
            }
            esc = JsonEsc(sb.ToString());
            label = "二进制 (十六进制转储, 前 512 字节)";
        }
        return (data.Length, isText ? "text" : "hex", label, esc);
    }

    static bool Utf8Ok(byte[] data, int n)
    {
        long bad = 0;
        int i = 0;
        while (i < n)
        {
            int c = data[i], need;
            if (c < 0x80) need = 0;
            else if ((c & 0xE0) == 0xC0) need = 1;
            else if ((c & 0xF0) == 0xE0) need = 2;
            else if ((c & 0xF8) == 0xF0) need = 3;
            else { bad++; i++; continue; }
            bool ok = true;
            for (int k2 = 1; k2 <= need; k2++)
                if (i + k2 >= n || (data[i + k2] & 0xC0) != 0x80) { ok = false; break; }
            if (!ok) bad++;
            i += need + 1;
        }
        return bad * 1000 / (n == 0 ? 1 : n) < 20;
    }
}
