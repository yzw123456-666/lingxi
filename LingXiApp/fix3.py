"""补丁3: /api/decompress LXF 归档自动解压到下载文件夹"""
src = open('Program.cs', encoding='utf-8', newline='').read()
src = src.replace('\r\n', '\n')

def rep(old, new):
    global src
    if new in src and old not in src:
        print('skip:', old[:44])
        return
    assert old in src, 'NOT FOUND: ' + old[:70]
    src = src.replace(old, new, 1)

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
print('patch3 applied OK')
