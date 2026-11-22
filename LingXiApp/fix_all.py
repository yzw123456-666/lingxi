"""合并补丁: name 参数捕获 + RunPathJob LXF 分支 + /api/decompress LXF 自动解压"""
src = open('Program.cs', encoding='utf-8', newline='').read()
src = src.replace('\r\n', '\n')

def rep(old, new):
    global src
    if new in src and old not in src:
        print('skip:', old[:44])
        return
    assert old in src, 'NOT FOUND: ' + old[:70]
    src = src.replace(old, new, 1)

# 1) 路由捕获 name 参数
rep('''    async Task RouteAsync(NetworkStream s, string method, string path, string bodyFile, long bodyLen)
    {
        var q = path.IndexOf('?');
        if (q >= 0) path = path[..q];''',
    '''    async Task RouteAsync(NetworkStream s, string method, string path, string bodyFile, long bodyLen)
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
        }''')

# 2) RunPathJob: 解压 -> LXF 检测 -> 解包为文件夹
rep('''            string candidate = decompress
                ? Path.Combine(Path.GetDirectoryName(path)!, Path.GetFileNameWithoutExtension(path))
                : path + ".lx";
            string tout = UniqueOut(candidate);
            busy = true; progressPct = 0;
            var sb = new byte[1024];
            int rc = 0;
            try
            {
                rc = await Task.Run(() => decompress
                    ? lx_decompress_file(path, tout, sb, sb.Length)
                    : lx_compress_file(path, tout, sb, sb.Length));
            }
            finally
            {
                busy = false; progressPct = 100;
                lx_shutdown();
            }
            var msg = MsgOf(sb);
            PostJson(new { type = "done", ok = rc == 0, msg = msg, saved = rc == 0 ? tout : "" });''',
    '''            string candidate = decompress
                ? Path.Combine(Path.GetDirectoryName(path)!, Path.GetFileNameWithoutExtension(path))
                : path + ".lx";
            string tout = Path.Combine(Path.GetTempPath(), "lxapp_out_" + Guid.NewGuid().ToString("N") + ".tmp");
            busy = true; progressPct = 0;
            var sb = new byte[1024];
            int rc = 0;
            try
            {
                rc = await Task.Run(() => decompress
                    ? lx_decompress_file(path, tout, sb, sb.Length)
                    : lx_compress_file(path, tout, sb, sb.Length));
            }
            finally
            {
                busy = false; progressPct = 100;
                lx_shutdown();
            }
            var msg = MsgOf(sb);
            if (rc != 0)
            {
                File.Delete(tout);
                PostJson(new { type = "done", ok = false, msg = msg });
                return;
            }
            if (decompress && IsFolderArchive(tout))
            {
                string dest = UniqueDir(candidate);
                var (files, dirs) = ExtractFolder(tout, dest);
                File.Delete(tout);
                PostJson(new { type = "done", ok = true,
                    msg = $"解压完成: {files} 个文件 / {dirs} 个文件夹　已保存到: {dest}", saved = dest });
                return;
            }
            string final = UniqueOut(candidate);
            File.Move(tout, final, true);
            PostJson(new { type = "done", ok = true, msg = msg + "　已保存到: " + final, saved = final });''')

# 3) /api/decompress (拖拽模式): LXF 归档自动解压到下载文件夹
rep('''            if (rc != 0)
            {
                File.Delete(tout);
                await RespondAsync(s, "application/json; charset=utf-8", "Connection: close\\r\\n",
                    Encoding.UTF8.GetBytes($"{{\\"ok\\":0,\\"msg\\":\\"{JsonEsc(msg)}\\"}}"));
                return;
            }
            var em = JsonEsc(msg);''',
    '''            if (decompress && IsFolderArchive(tout))
            {
                string stem = Path.GetFileNameWithoutExtension(
                    string.IsNullOrEmpty(nameParam) ? "archive" : nameParam);
                string dest = UniqueDir(Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Downloads", stem));
                var (files, dirs) = await Task.Run(() => ExtractFolder(tout, dest));
                File.Delete(tout);
                await RespondAsync(s, "application/json; charset=utf-8", "Connection: close\\r\\n",
                    Encoding.UTF8.GetBytes(JsonSerializer.Serialize(new { ok = true, extracted = true,
                        msg = $"文件夹已解压: {files} 个文件 / {dirs} 个目录", dest })));
                return;
            }
            var em = JsonEsc(msg);''')

open('Program.cs', 'w', encoding='utf-8', newline='\n').write(src)
print('all folder-extract patches applied OK')
