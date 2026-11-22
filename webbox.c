/*
 * 灵犀压缩盒 (LingXi Box) —— 单文件本地服务
 * ============================================================
 * 架构: 可移植 C 本地 HTTP 服务(只监听 127.0.0.1) + 浏览器界面。
 *       非 Win32 原生 GUI, 无框架无运行时; 界面内嵌于本文件,
 *       与 lingxi.c 引擎一体编译出一个 exe, 即软件全部。
 *
 * 功能: 压缩 / 解压 / 预览 .lx 文件; 开机自启动(启动文件夹);
 *       后台常驻: 空闲约 3MB 内存(每次任务后释放引擎大表)。
 *
 * 编译: python -m ziglang cc -O2 -DLX_NO_MAIN webbox.c lingxi.c
 *         -o LingXiBox.exe -lws2_32 -mwindows
 * 用法: 双击运行(自动打开浏览器); --no-browser 静默后台;
 *       --port N 指定端口; --selftest 自检(写 selftest_report.txt)。
 */
#ifdef _WIN32
  #define WIN32_LEAN_AND_MEAN
  #include <winsock2.h>
  #include <ws2tcpip.h>
  #include <windows.h>
  typedef SOCKET sock_t;
  #define CLOSESOCK closesocket
#else
  #include <sys/socket.h>
  #include <netinet/in.h>
  #include <unistd.h>
  #include <arpa/inet.h>
  #include <pthread.h>
  typedef int sock_t;
  #define CLOSESOCK close
  #define INVALID_SOCKET (-1)
#endif
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <stdint.h>

/* ---------- 引擎接口 (lingxi.c, -DLX_NO_MAIN) ---------- */
extern int  lx_compress_file(const char *in, const char *out, char *msg, size_t n);
extern int  lx_decompress_file(const char *in, const char *out, char *msg, size_t n);
extern void lx_shutdown(void);
extern void (*lx_progress_cb)(int percent);

#define VER       "v0.2"
#define DEF_PORT  7788
#define MAXHDR    32768
#define MAXBODY   (1024LL * 1024 * 1024)      /* 请求体上限 1GB */

static volatile int g_busy = 0;
static volatile int g_progress = 0;
static int          g_port = DEF_PORT;
static char         g_exepath[1024];

static void set_progress(int pct) { g_progress = pct; }

/* ---------- 小工具 ---------- */
static void url_decode(char *s) {
    char *o = s;
    for (; *s; s++) {
        if (*s == '%' && isxdigit((unsigned char)s[1]) && isxdigit((unsigned char)s[2])) {
            char h[3] = { s[1], s[2], 0 };
            *o++ = (char)strtol(h, NULL, 16); s += 2;
        } else if (*s == '+') *o++ = ' ';
        else *o++ = *s;
    }
    *o = 0;
}
static void pct_encode(const char *s, char *d, size_t dn) {
    size_t j = 0;
    for (size_t i = 0; s[i] && j + 5 < dn; i++) {
        unsigned char c = (unsigned char)s[i];
        if (isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~') d[j++] = (char)c;
        else j += (size_t)snprintf(d + j, dn - j, "%%%02X", c);
    }
    d[j] = 0;
}
static void json_escape(const char *s, char *d, size_t dn) {
    size_t j = 0;
    for (size_t i = 0; s[i] && j + 8 < dn; i++) {
        unsigned char c = (unsigned char)s[i];
        if (c == '"' || c == '\\') { d[j++] = '\\'; d[j++] = (char)c; }
        else if (c == '\n') { d[j++] = '\\'; d[j++] = 'n'; }
        else if (c == '\r') { d[j++] = '\\'; d[j++] = 'r'; }
        else if (c == '\t') { d[j++] = '\\'; d[j++] = 't'; }
        else if (c < 0x20) j += (size_t)snprintf(d + j, dn - j, "\\u%04x", c);
        else d[j++] = (char)c;
    }
    d[j] = 0;
}
static void tmp_path(char *out, size_t n, const char *tag) {
#ifdef _WIN32
    char td[300];
    GetTempPathA(280, td);
    snprintf(out, n, "%slxbox_%s_%lu.tmp", td, tag, (unsigned long)GetCurrentThreadId());
#else
    snprintf(out, n, "/tmp/lxbox_%s_%d.tmp", tag, (int)getpid());
#endif
}
static int read_file_all(const char *path, char **out, size_t *outn, size_t cap) {
    FILE *f = fopen(path, "rb");
    if (!f) return 1;
    fseek(f, 0, SEEK_END); long sz = ftell(f); fseek(f, 0, SEEK_SET);
    if (sz < 0 || (size_t)sz > cap) { fclose(f); return 2; }
    char *b = (char *)malloc((size_t)sz + 1);
    if (!b) { fclose(f); return 1; }
    if (sz && fread(b, 1, (size_t)sz, f) != (size_t)sz) { fclose(f); free(b); return 1; }
    fclose(f);
    b[sz] = 0;
    *out = b; *outn = (size_t)sz;
    return 0;
}

/* ---------- 开机自启动 (启动文件夹, 不动注册表) ---------- */
#ifdef _WIN32
static int autostart_path(char *p, size_t n) {
    char st[280];
    if (!GetEnvironmentVariableA("APPDATA", st, sizeof(st))) return 1;
    snprintf(p, n, "%s\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\LingXiBox.bat", st);
    return 0;
}
static int autostart_enabled(void) {
    char p[520];
    if (autostart_path(p, sizeof(p))) return 0;
    FILE *f = fopen(p, "rb");
    if (f) { fclose(f); return 1; }
    return 0;
}
static int autostart_set(int on, char *msg, size_t msgn) {
    char p[520];
    if (autostart_path(p, sizeof(p))) { snprintf(msg, msgn, "无法定位启动文件夹"); return 1; }
    if (on) {
        FILE *f = fopen(p, "wb");
        if (!f) { snprintf(msg, msgn, "无法写入: %s", p); return 1; }
        fprintf(f, "@echo off\r\nstart \"\" \"%s\" --no-browser\r\n", g_exepath);
        fclose(f);
        snprintf(msg, msgn, "开机自启动已开启");
    } else {
        remove(p);
        snprintf(msg, msgn, "开机自启动已关闭");
    }
    return 0;
}
#endif

/* ---------- HTTP 发送 ---------- */
static int send_all(sock_t s, const char *b, size_t n) {
    size_t off = 0;
    while (off < n) {
        int k = send(s, b + off, (int)((n - off > (size_t)(1u << 20)) ? (1u << 20) : (int)(n - off)), 0);
        if (k <= 0) return 1;
        off += (size_t)k;
    }
    return 0;
}
static void send_http(sock_t s, const char *ctype,
                      const char *extra, const char *body, size_t bn) {
    char hdr[1024];
    int h = snprintf(hdr, sizeof(hdr),
        "HTTP/1.1 200 OK\r\nContent-Type: %s\r\nContent-Length: %zu\r\n"
        "Cache-Control: no-store\r\n%s\r\n", ctype, bn, extra ? extra : "");
    send_all(s, hdr, (size_t)h);
    if (bn) send_all(s, body, bn);
}
static void send_json(sock_t s, const char *js) {
    send_http(s, "application/json; charset=utf-8", "Connection: close\r\n",
              js, strlen(js));
}
static void send_err(sock_t s, int code, const char *msg) {
    char js[512], em[512];
    json_escape(msg, em, sizeof(em));
    snprintf(js, sizeof(js), "{\"ok\":0,\"err\":1,\"msg\":\"%s\"}", em);
    char hdr[256];
    int h = snprintf(hdr, sizeof(hdr),
        "HTTP/1.1 %d Error\r\nContent-Type: application/json\r\n"
        "Content-Length: %zu\r\nConnection: close\r\n\r\n", code, strlen(js));
    send_all(s, hdr, (size_t)h);
    send_all(s, js, strlen(js));
}
static void send_file(sock_t s, const char *ctype, const char *extra, const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) { send_err(s, 500, "temp file lost"); return; }
    fseek(f, 0, SEEK_END); long sz = ftell(f); fseek(f, 0, SEEK_SET); fseek(f, 0, SEEK_SET);
    char hdr[1024];
    int h = snprintf(hdr, sizeof(hdr),
        "HTTP/1.1 200 OK\r\nContent-Type: %s\r\nContent-Length: %ld\r\n"
        "Cache-Control: no-store\r\n%s\r\n", ctype, sz, extra ? extra : "");
    send_all(s, hdr, (size_t)h);
    char *buf = (char *)malloc(1u << 20);
    size_t r;
    while ((r = fread(buf, 1, 1u << 20, f)) > 0)
        if (send_all(s, buf, r)) break;
    free(buf);
    fclose(f);
}

/* ---------- 预览 ---------- */
static int utf8_ok(const unsigned char *s, size_t n) {
    size_t i = 0, bad = 0;
    while (i < n) {
        unsigned char c = s[i];
        size_t need = 0;
        if (c < 0x80) need = 0;
        else if ((c & 0xE0) == 0xC0) need = 1;
        else if ((c & 0xF0) == 0xE0) need = 2;
        else if ((c & 0xF8) == 0xF0) need = 3;
        else { bad++; i++; continue; }
        int ok = 1;
        for (size_t k = 1; k <= need; k++) {
            if (i + k >= n || (s[i + k] & 0xC0) != 0x80) { ok = 0; break; }
        }
        if (!ok) bad++;
        i += need + 1;
    }
    return bad * 1000 / (n ? n : 1) < 20;   /* 坏字节率 <2% 视为文本 */
}
static void hexdump(const unsigned char *b, size_t n, char *d, size_t dn) {
    size_t j = 0;
    for (size_t i = 0; i < n && j + 80 < dn; i += 16) {
        j += (size_t)snprintf(d + j, dn - j, "%04zx  ", i);
        for (int k = 0; k < 16; k++) {
            if (i + (size_t)k < n)
                j += (size_t)snprintf(d + j, dn - j, "%02x ", b[i + (size_t)k]);
            else j += (size_t)snprintf(d + j, dn - j, "   ");
            if (k == 7) d[j++] = ' ';
        }
        j += (size_t)snprintf(d + j, dn - j, "|");
        for (int k = 0; k < 16 && i + (size_t)k < n; k++) {
            unsigned char c = b[i + (size_t)k];
            d[j++] = (c >= 0x20 && c < 0x7f) ? (char)c : '.';
        }
        d[j++] = '\n';
    }
    d[j] = 0;
}

/* ---------- 任务端点 ---------- */
static void respond_job(sock_t s, const char *inpath, const char *outpath,
                        const char *msg, int rc) {
    if (rc != 0) {
        char em[512];
        json_escape(msg, em, sizeof(em));
        char js[600];
        snprintf(js, sizeof(js), "{\"ok\":0,\"msg\":\"%s\"}", em);
        send_json(s, js);
        return;
    }
    char em[512], pm[1024];
    json_escape(msg, em, sizeof(em));
    pct_encode(msg, pm, sizeof(pm));
    char extra[1200];
    snprintf(extra, sizeof(extra),
             "X-Lx-Ok: 1\r\nX-Lx-Msg: %s\r\nX-Lx-Msg-Enc: %s\r\nConnection: close\r\n",
             em, pm);
    send_file(s, "application/octet-stream", extra, outpath);
}

static void handle_compress(sock_t s, const char *bodyfile, int decompress) {
    char msg[512] = "";
    char tout[1030];
    tmp_path(tout, sizeof(tout), decompress ? "d_out" : "c_out");
    remove(tout);
    if (decompress) {   /* 先验头, 提前给出友好错误 */
        char *b; size_t bn;
        if (read_file_all(bodyfile, &b, &bn, 16) == 0) {
            int bad = (bn < 15 || memcmp(b, "LX1", 3) != 0);
            free(b);
            if (bad) { send_err(s, 400, "不是灵犀文件: 缺少 LX1 头"); return; }
        }
    }
    g_busy = 1; g_progress = 0;
    int rc = decompress
        ? lx_decompress_file(bodyfile, tout, msg, sizeof(msg))
        : lx_compress_file(bodyfile, tout, msg, sizeof(msg));
    g_busy = 0; g_progress = 100;
    lx_shutdown();                       /* 任务后释放大表, 常驻回到 ~3MB */
    respond_job(s, bodyfile, tout, msg, rc);
    remove(tout);
}

static void handle_preview(sock_t s, const char *bodyfile) {
    char msg[512] = "";
    char tout[1030];
    tmp_path(tout, sizeof(tout), "p_out");
    remove(tout);
    g_busy = 1; g_progress = 0;
    int rc = lx_decompress_file(bodyfile, tout, msg, sizeof(msg));
    g_busy = 0; g_progress = 100;
    lx_shutdown();
    if (rc != 0) {
        char em[512];
        json_escape(msg, em, sizeof(em));
        char js[600];
        snprintf(js, sizeof(js), "{\"ok\":0,\"msg\":\"%s\"}", em);
        send_json(s, js);
        remove(tout);
        return;
    }
    char *b = NULL; size_t bn = 0;
    if (read_file_all(tout, &b, &bn, 1u << 20)) {
        send_err(s, 500, "read back fail");
        remove(tout);
        return;
    }
    remove(tout);
    size_t cap = bn > (1u << 19) ? (1u << 19) : bn;
    int is_text = cap ? utf8_ok((const unsigned char *)b, cap) : 0;
    char *esc = (char *)malloc((is_text ? cap * 6 : cap * 4) + 128);
    char *js = (char *)malloc((is_text ? cap * 6 : cap * 4) + 512);
    if (!esc || !js) { free(b); free(esc); free(js); send_err(s, 500, "oom"); return; }
    char label[128];
    if (is_text) {
        size_t show = cap;
        json_escape(b, esc, show * 6 + 64);
        snprintf(label, sizeof(label), "文本 (UTF-8 有效)");
        snprintf(js, (size_t)(cap * 6) + 500,
            "{\"ok\":1,\"origSize\":%zu,\"kind\":\"text\",\"label\":\"%s\","
            "\"msg\":\"%s\",\"content\":\"%s\"}", bn, label, msg, esc);
    } else {
        hexdump((const unsigned char *)b, cap > 512 ? 512 : cap, esc, cap * 4 + 64);
        snprintf(label, sizeof(label), "二进制 (十六进制转储, 前 512 字节)");
        snprintf(js, (size_t)(cap * 4) + 500,
            "{\"ok\":1,\"origSize\":%zu,\"kind\":\"hex\",\"label\":\"%s\","
            "\"msg\":\"%s\",\"content\":\"%s\"}", bn, label, msg, esc);
    }
    send_json(s, js);
    free(b); free(esc); free(js);
}

/* ---------- 路由 ---------- */
extern const char INDEX_HTML[];

static void route(sock_t s, const char *method, char *path,
                  const char *bodyfile, size_t bodyn) {
    (void)bodyn;
    char *q = strchr(path, '?');
    if (q) *q = 0;

    if (!strcmp(path, "/") && !strcmp(method, "GET")) {
        send_http(s, "text/html; charset=utf-8", "Connection: close\r\n",
                  INDEX_HTML, strlen(INDEX_HTML));
        return;
    }
    if (!strcmp(path, "/api/status") && !strcmp(method, "GET")) {
        char js[256];
#ifdef _WIN32
        int au = autostart_enabled();
#else
        int au = 0;
#endif
        snprintf(js, sizeof(js),
            "{\"ok\":1,\"busy\":%d,\"progress\":%d,\"port\":%d,\"autostart\":%d,"
            "\"version\":\"%s\"}", g_busy, g_progress, g_port, au, VER);
        send_json(s, js);
        return;
    }
#ifdef _WIN32
    if (!strcmp(path, "/api/autostart") && !strcmp(method, "POST")) {
        int on = 0;
        {   /* 读取第一个字节: '1' 开 / '0' 关 */
            char *b; size_t bn;
            if (read_file_all(bodyfile, &b, &bn, 64) == 0) {
                on = (bn > 0 && (b[0] == '1' || b[0] == 't'));
                free(b);
            }
        }
        char msg[512];
        autostart_set(on, msg, sizeof(msg));
        char es[256];
        json_escape(msg, es, sizeof(es));
        char js[400];
        snprintf(js, sizeof(js), "{\"ok\":1,\"msg\":\"%s\",\"enabled\":%d}",
                 es, autostart_enabled());
        send_json(s, js);
        return;
    }
#endif
    if (!strcmp(path, "/api/quit") && !strcmp(method, "POST")) {
        send_json(s, "{\"ok\":1,\"msg\":\"bye\"}");
        exit(0);
    }
    if (!strcmp(path, "/api/compress") && !strcmp(method, "POST")) {
        if (g_busy) { send_err(s, 409, "已有任务进行中"); return; }
        handle_compress(s, bodyfile, 0);
        return;
    }
    if (!strcmp(path, "/api/decompress") && !strcmp(method, "POST")) {
        if (g_busy) { send_err(s, 409, "已有任务进行中"); return; }
        handle_compress(s, bodyfile, 1);
        return;
    }
    if (!strcmp(path, "/api/preview") && !strcmp(method, "POST")) {
        if (g_busy) { send_err(s, 409, "已有任务进行中"); return; }
        handle_preview(s, bodyfile);
        return;
    }
    send_err(s, 404, "no route");
}

/* ---------- 连接线程 ---------- */
#ifdef _WIN32
static DWORD WINAPI client_thread(LPVOID pv)
#else
static void *client_thread(void *pv)
#endif
{
    sock_t s = (sock_t)(intptr_t)pv;
    char *hdr = (char *)malloc(MAXHDR + 1);
    size_t hlen = 0;
    char method[8] = "", path[2048] = "";
    long long clen = -1;
    int got = 0;

#ifdef _WIN32
    DWORD tmo = 15000;
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, (const char *)&tmo, sizeof(tmo));
#endif

    /* 1) 收头部 */
    while (hlen < MAXHDR) {
        char chunk[4096];
        int k = recv(s, chunk, sizeof(chunk), 0);
        if (k <= 0) goto out;
        for (int i = 0; i < k; i++) {
            hdr[hlen++] = chunk[i];
            if (hlen >= 4 && !memcmp(hdr + hlen - 4, "\r\n\r\n", 4)) { got = 1; break; }
        }
        if (got) break;
    }
    if (!got) { send_err(s, 431, "header too big"); goto out; }
    hdr[hlen] = 0;
    {
        char *line_end = strstr(hdr, "\r\n");
        if (!line_end) goto out;
        *line_end = 0;
        sscanf(hdr, "%7s %2047s", method, path);
        char *p = hdr;
        while ((p = strstr(p, "\r\n")) != NULL) {
            p += 2;
            if (!_strnicmp(p, "Content-Length:", 15)) clen = strtoll(p + 15, NULL, 10);
        }
    }
    if (clen > MAXBODY) { send_err(s, 413, "body too big (max 1GB)"); goto out; }

    /* 2) 请求体 -> 临时文件(流式) */
    char bodyfile[1030];
    int have_body = clen > 0;
    if (have_body) {
        tmp_path(bodyfile, sizeof(bodyfile), "body");
        FILE *f = fopen(bodyfile, "wb");
        if (!f) { send_err(s, 500, "io error"); goto out; }
        char *p = strstr(hdr, "\r\n\r\n");
        p += 4;
        size_t already = hlen - (size_t)(p - hdr);
        if (already > (size_t)clen) already = (size_t)clen;
        if (already) fwrite(p, 1, already, f);
        long long left = clen - (long long)already;
        char *chunk = (char *)malloc(1u << 16);
        while (left > 0) {
            int k = recv(s, chunk, (int)((left < (long long)(1u << 16))
                                         ? (size_t)left : (1u << 16)), 0);
            if (k <= 0) { free(chunk); fclose(f); remove(bodyfile);
                          send_err(s, 400, "body truncated"); goto out; }
            fwrite(chunk, 1, (size_t)k, f);
            left -= k;
        }
        free(chunk);
        fclose(f);
    } else bodyfile[0] = 0;

    /* 3) 路由 */
    url_decode(path);
    route(s, method, path, have_body ? bodyfile : "", (size_t)(have_body ? clen : 0));
    if (have_body) remove(bodyfile);
out:
    free(hdr);
    CLOSESOCK(s);
#ifdef _WIN32
    return 0;
#else
    return NULL;
#endif
}

/* ---------- 自检 ---------- */
static int selftest(void) {
    FILE *rep = fopen("selftest_report.txt", "w");
    fprintf(rep, "灵犀压缩盒自检 %s\n引擎: ", VER);
    fflush(rep);
    int fails = 0;
    {   /* 引擎往返 */
        FILE *f = fopen("selftest_src.bin", "wb");
        for (int i = 0; i < 300000; i++) {
            const char *s2 = "the quick brown fox 灵犀压缩盒 jumps over 12345\n";
            fwrite(s2, 1, strlen(s2), f);
        }
        fclose(f);
        char msg[512];
        int rc1 = lx_compress_file("selftest_src.bin", "selftest_c.lx", msg, sizeof(msg));
        fprintf(rep, "压缩 rc=%d [%s] ", rc1, msg);
        fflush(rep);
        int rc2 = lx_decompress_file("selftest_c.lx", "selftest_out.bin", msg, sizeof(msg));
        fprintf(rep, "解压 rc=%d [%s] ", rc2, msg);
        fflush(rep);
        char *a, *b; size_t an, bn;
        read_file_all("selftest_src.bin", &a, &an, 1u << 30);
        read_file_all("selftest_out.bin", &b, &bn, 1u << 30);
        int same = (an == bn) && (an == 0 || memcmp(a, b, an) == 0);
        fprintf(rep, "逐字节一致: %s\n", same ? "PASS" : "FAIL");
        fflush(rep);
        if (!same || rc1 || rc2) fails++;
        free(a); free(b);
        remove("selftest_src.bin"); remove("selftest_c.lx"); remove("selftest_out.bin");
        lx_shutdown();
    }
#ifdef _WIN32
    {   /* 自启动 写入/删除 */
        char m1[512], m2[512];
        int r1 = autostart_set(1, m1, sizeof(m1));
        int en1 = autostart_enabled();
        int r2 = autostart_set(0, m2, sizeof(m2));
        int en2 = autostart_enabled();
        int ok = (r1 == 0 && en1 && r2 == 0 && !en2);
        fprintf(rep, "自启动 写/删: %s\n", ok ? "PASS" : "FAIL");
        fflush(rep);
        if (!ok) fails++;
    }
#endif
    fprintf(rep, "RESULT: %s\n", fails ? "FAIL" : "ALL PASS");
    fclose(rep);
    return fails;
}

static int app_main(int argc, char **argv) {
    int no_browser = 0, do_selftest = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--no-browser")) no_browser = 1;
        else if (!strcmp(argv[i], "--port") && i + 1 < argc) g_port = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--selftest")) do_selftest = 1;
    }
#ifdef _WIN32
    GetModuleFileNameA(NULL, g_exepath, (DWORD)sizeof(g_exepath));
#else
    ssize_t rl = readlink("/proc/self/exe", g_exepath, sizeof(g_exepath) - 1);
    if (rl > 0) g_exepath[rl] = 0;
#endif
    if (do_selftest) return selftest();
    lx_progress_cb = set_progress;

#ifdef _WIN32
    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) return 1;
#endif
    sock_t srv = socket(AF_INET, SOCK_STREAM, 0);
    int one = 1;
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, (const char *)&one, sizeof(one));
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);   /* 只监听本机 */
    int bound = 0;
    for (int p = g_port; p < g_port + 20; p++) {
        addr.sin_port = htons((unsigned short)p);
        if (bind(srv, (struct sockaddr *)&addr, sizeof(addr)) == 0) { g_port = p; bound = 1; break; }
    }
    if (!bound) return 2;
    listen(srv, 8);

    if (!no_browser) {
#ifdef _WIN32
        char cmd[1200];
        snprintf(cmd, sizeof(cmd), "start \"\" http://127.0.0.1:%d", g_port);
        system(cmd);
#else
        char cmd[1200];
        snprintf(cmd, sizeof(cmd), "xdg-open http://127.0.0.1:%d >/dev/null 2>&1 &", g_port);
        system(cmd);
#endif
    }

    for (;;) {
        sock_t cs = accept(srv, NULL, NULL);
        if (cs == INVALID_SOCKET) continue;
#ifdef _WIN32
        DWORD tid;
        HANDLE h = CreateThread(NULL, 0, client_thread, (LPVOID)(intptr_t)cs, 0, &tid);
        if (h) CloseHandle(h);
        else CLOSESOCK(cs);
#else
        pthread_t tid;
        if (pthread_create(&tid, NULL, client_thread, (void *)(intptr_t)cs) == 0)
            pthread_detach(tid);
        else CLOSESOCK(cs);
#endif
    }
}

/* ---------- 内嵌页面 ---------- */
#include "webbox_html.h"

/* ---------- 入口: 窗口子系统(无控制台窗口, 纯后台) ---------- */
#ifdef _WIN32
int WINAPI WinMain(HINSTANCE hI, HINSTANCE hP, LPSTR cl, int show) {
    (void)hI; (void)hP; (void)cl; (void)show;
    return app_main(__argc, __argv);
}
#else
int main(int argc, char **argv) { return app_main(argc, argv); }
#endif
