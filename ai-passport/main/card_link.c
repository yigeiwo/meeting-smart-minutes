// main/card_link.c —— 飞书胸卡 <-> 工作台 WebSocket/NDJSON 桥接客户端实现
//
// 传输方式 (方案 B): 复用 443 端口的 wss://，由 nginx 终止 TLS，因此无需在云安全组额外放行 5566。
//   URI 形如: wss://ai.shuoyunqi.online/ws/card?token=<设备令牌>
//   也兼容局域网直连裸 TCP 5566 的老路径（tx/rx 语义完全一致，仅承载方式不同）。
//
// 设计要点 (严格遵守项目"严禁模拟数据"铁律):
//   1. 只做真实的 WebSocket 连接/收发，任何失败都如实记录并真实重连；
//   2. 收到的纪要 / 失败原因原样透传给 UI，绝不伪造"已同步成功"；
//   3. 音频上行是真实 PCM 数据，缓冲不足时真实丢弃并计入 drops，不假装已送达。
#include "card_link.h"
#include "ble_prov.h"

#include "esp_log.h"
#include "esp_mac.h"
#include "esp_crt_bundle.h"
#include "esp_heap_caps.h"
#include "esp_websocket_client.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/stream_buffer.h"
#include "freertos/semphr.h"

#include "cJSON.h"

#include <string.h>
#include <stdio.h>
#include <stdarg.h>

static const char *TAG = "card_link";

// NDJSON 发送缓冲: 8KB 可容纳约 5 帧音频(单帧约 1.4KB base64)。
// 原为 16KB —— 实测启动后只剩 3KB 空闲堆, 而 wss 握手需要约 20KB, 只能从各处挤:
// 缓冲满时音频帧会被真实丢弃并计入 drops, 不假装已送达。
#define LINK_TX_SB_SIZE     8192
#define LINK_LINE_BUF       2048       // 接收行缓冲 (lcd_frame 等超大帧按前缀丢弃)
#define LINK_AUDIO_JSON     1600       // 单帧音频 JSON 上限 (1024B PCM -> ~1.4KB base64)
#define LINK_AUDIO_HEAD     48         // 音频帧 JSON 头部预留 (实际长度按写入量计算)
#define LINK_URI_BUF        320
#define LINK_HEARTBEAT_MS   15000
#define LINK_WS_TIMEOUT_MS  10000      // 网络超时
#define LINK_WS_RETRY_MS    3000       // 断线重连退避

// ---------------- 内部状态 ----------------
static TaskHandle_t             s_link_task = NULL;
static TaskHandle_t             s_tx_task = NULL;
static volatile bool            s_task_alive = false;

static esp_websocket_client_handle_t s_ws = NULL;
static char                     s_cur_uri[LINK_URI_BUF] = {0};
static volatile bool            s_connected = false;

static StreamBufferHandle_t     s_tx_sb = NULL;
static SemaphoreHandle_t        s_tx_mtx = NULL;
static SemaphoreHandle_t        s_sum_mtx = NULL;

static uint32_t s_tx_lines = 0;
static uint32_t s_rx_lines = 0;
static uint32_t s_audio_frames = 0;
static uint32_t s_audio_drops = 0;
static uint32_t s_seq = 0;

static card_link_summary_t s_summary = {0};
static char                s_last_err[CARD_LINK_ERR_LEN] = {0};

static card_link_event_cb_t s_ev_cb = NULL;
static void                *s_ev_user = NULL;

static void set_error(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void set_error(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(s_last_err, sizeof(s_last_err), fmt, ap);
    va_end(ap);
    ESP_LOGW(TAG, "链路错误: %s", s_last_err);
}

static void fire_event(card_link_event_t ev)
{
    if (s_ev_cb) s_ev_cb(ev, s_ev_user);
}

// ---------------- 发送侧 ----------------

// 单条 NDJSON 行原子写入发送缓冲 (先确认整行空间, 避免半行导致 JSON 破损)
static bool tx_line(const char *line, size_t len)
{
    if (!s_tx_sb || !s_tx_mtx || len == 0) return false;
    bool ok = false;
    if (xSemaphoreTake(s_tx_mtx, pdMS_TO_TICKS(100)) == pdTRUE) {
        if (!s_connected) {
            // 未连接: 不排队, 交给调用方按真实丢弃处理
            ok = false;
        } else if (xStreamBufferSpacesAvailable(s_tx_sb) >= len + 1) {
            ok = (xStreamBufferSend(s_tx_sb, line, len, 0) == len);
        }
        xSemaphoreGive(s_tx_mtx);
    }
    return ok;
}

static void tx_json_simple(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void tx_json_simple(const char *fmt, ...)
{
    char buf[192];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    // 绝不发送被截断的 JSON 行 (半截报文会让对端解析失败), 宁可如实丢弃
    if (n <= 0 || n >= (int)sizeof(buf) - 1) {
        ESP_LOGW(TAG, "上行消息过长或格式化失败, 已丢弃 (n=%d)", n);
        return;
    }
    buf[n] = '\n';
    if (!tx_line(buf, (size_t)n + 1)) {
        ESP_LOGW(TAG, "上行消息未送达 (链路未连接或缓冲满): %s", buf);
    } else {
        s_tx_lines++;
    }
}

void card_link_send_battery(int soc)
{
    tx_json_simple("{\"type\":\"battery\",\"soc\":%d}", soc);
}

void card_link_send_record_start(void)
{
    tx_json_simple("{\"type\":\"record_start\"}");
}

void card_link_send_record_stop(void)
{
    tx_json_simple("{\"type\":\"record_stop\"}");
}

void card_link_send_combo_menu(void)
{
    tx_json_simple("{\"type\":\"combo_menu\"}");
}

// 真实音频上行: 1024B PCM -> base64 -> 单行 NDJSON
static char s_audio_json[LINK_AUDIO_JSON];

static const char B64_TAB[] =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static size_t b64_encode(const uint8_t *in, size_t n, char *out)
{
    size_t o = 0;
    for (size_t i = 0; i < n; i += 3) {
        uint32_t v = (uint32_t)in[i] << 16;
        if (i + 1 < n) v |= (uint32_t)in[i + 1] << 8;
        if (i + 2 < n) v |= (uint32_t)in[i + 2];
        out[o++] = B64_TAB[(v >> 18) & 63];
        out[o++] = B64_TAB[(v >> 12) & 63];
        out[o++] = (i + 1 < n) ? B64_TAB[(v >> 6) & 63] : '=';
        out[o++] = (i + 2 < n) ? B64_TAB[v & 63] : '=';
    }
    out[o] = '\0';
    return o;
}

bool card_link_send_audio(const void *pcm, size_t bytes)
{
    if (!pcm || bytes == 0) return false;
    if (!s_connected || !s_tx_sb) {
        s_audio_drops++;
        return false;
    }

    // base64 长度 = 4 * ceil(n/3)
    size_t b64_len = ((bytes + 2) / 3) * 4;
    if (LINK_AUDIO_HEAD + b64_len + 3 > sizeof(s_audio_json)) {
        ESP_LOGW(TAG, "音频帧过大被丢弃: %u 字节", (unsigned)bytes);
        s_audio_drops++;
        return false;
    }

    // 头部长度按实际写入量计算, base64 紧随其后 (严禁留下未写入的缝隙,
    // 否则会把上一帧的残留字节混进 JSON, 造成报文破损)
    int head = snprintf(s_audio_json, LINK_AUDIO_HEAD,
                        "{\"type\":\"audio\",\"seq\":%u,\"pcm\":\"", (unsigned)++s_seq);
    if (head <= 0 || head >= LINK_AUDIO_HEAD) {
        s_audio_drops++;
        return false;
    }

    char *b64 = s_audio_json + head;
    b64_encode((const uint8_t *)pcm, bytes, b64);

    size_t pos = (size_t)head + b64_len;
    s_audio_json[pos++] = '"';
    s_audio_json[pos++] = '}';
    s_audio_json[pos++] = '\n';

    if (!tx_line(s_audio_json, pos)) {
        s_audio_drops++;
        return false;
    }
    s_audio_frames++;
    s_tx_lines++;
    return true;
}

// ---------------- 接收与解析 ----------------

static void item_to_text(const cJSON *it, char *out, size_t n)
{
    out[0] = '\0';
    if (!it) return;
    if (cJSON_IsString(it)) {
        snprintf(out, n, "%s", it->valuestring);
        return;
    }
    if (!cJSON_IsObject(it)) return;

    const cJSON *task = cJSON_GetObjectItem(it, "task");
    if (!task) task = cJSON_GetObjectItem(it, "title");
    if (!task) task = cJSON_GetObjectItem(it, "content");
    const cJSON *owner = cJSON_GetObjectItem(it, "owner");
    if (task && cJSON_IsString(task)) {
        if (owner && cJSON_IsString(owner) && owner->valuestring && owner->valuestring[0]) {
            snprintf(out, n, "%s (%s)", task->valuestring, owner->valuestring);
        } else {
            snprintf(out, n, "%s", task->valuestring);
        }
    }
}

static void fill_items(const cJSON *arr, char (*dst)[CARD_LINK_ITEM_LEN], uint8_t *cnt, uint8_t max)
{
    *cnt = 0;
    if (!cJSON_IsArray(arr)) return;
    const cJSON *it = NULL;
    cJSON_ArrayForEach(it, arr) {
        if (*cnt >= max) break;
        char tmp[CARD_LINK_ITEM_LEN];
        item_to_text(it, tmp, sizeof(tmp));
        if (tmp[0]) {
            snprintf(dst[*cnt], CARD_LINK_ITEM_LEN, "%s", tmp);
            (*cnt)++;
        }
    }
}

// 收到工作台真实纪要
static void on_summary_msg(const cJSON *root)
{
    card_link_summary_t tmp = {0};
    const cJSON *t = cJSON_GetObjectItem(root, "title");
    const cJSON *u = cJSON_GetObjectItem(root, "doc_url");
    if (t && cJSON_IsString(t)) snprintf(tmp.title, sizeof(tmp.title), "%s", t->valuestring);
    if (u && cJSON_IsString(u)) snprintf(tmp.doc_url, sizeof(tmp.doc_url), "%s", u->valuestring);
    if (!tmp.title[0]) snprintf(tmp.title, sizeof(tmp.title), "会议纪要");

    fill_items(cJSON_GetObjectItem(root, "todos"), tmp.todos, &tmp.todo_count, CARD_LINK_MAX_ITEMS);
    fill_items(cJSON_GetObjectItem(root, "decisions"), tmp.decisions, &tmp.decision_count, CARD_LINK_MAX_ITEMS);
    tmp.valid = true;
    tmp.recv_ms = (uint32_t)pdTICKS_TO_MS(xTaskGetTickCount());

    if (xSemaphoreTake(s_sum_mtx, pdMS_TO_TICKS(200)) == pdTRUE) {
        s_summary = tmp;
        xSemaphoreGive(s_sum_mtx);
    }
    s_last_err[0] = '\0';

    ESP_LOGI(TAG, "收到工作台真实纪要: 标题=%s, 待办=%u 项, 决议=%u 项, 云文档=%s",
             tmp.title, (unsigned)tmp.todo_count, (unsigned)tmp.decision_count,
             tmp.doc_url[0] ? "有" : "无");
    fire_event(CARD_LINK_EV_SUMMARY);
}

static void dispatch_line(const char *line)
{
    cJSON *root = cJSON_Parse(line);
    if (!root) {
        ESP_LOGW(TAG, "无法解析的行: %.80s", line);
        s_rx_lines++;
        return;
    }
    s_rx_lines++;

    const cJSON *ev = cJSON_GetObjectItem(root, "event");
    const cJSON *cmd = cJSON_GetObjectItem(root, "cmd");
    const cJSON *typ = cJSON_GetObjectItem(root, "type");
    const char *evs = (ev && cJSON_IsString(ev)) ? ev->valuestring : NULL;
    const char *cms = (cmd && cJSON_IsString(cmd)) ? cmd->valuestring : NULL;
    const char *tys = (typ && cJSON_IsString(typ)) ? typ->valuestring : NULL;

    if (evs && (!strcmp(evs, "meeting_processed") || !strcmp(evs, "summary")
                || !strcmp(evs, "summary_data"))) {
        on_summary_msg(root);
    } else if (evs && !strcmp(evs, "pipeline_result")) {
        const cJSON *ok = cJSON_GetObjectItem(root, "ok");
        if (ok && cJSON_IsBool(ok) && !cJSON_IsTrue(ok)) {
            const cJSON *m = cJSON_GetObjectItem(root, "msg");
            set_error("工作台提炼失败: %s", (m && cJSON_IsString(m)) ? m->valuestring : "未知原因");
            fire_event(CARD_LINK_EV_PIPELINE_FAILED);
        }
    } else if (evs && !strcmp(evs, "pong")) {
        // 心跳应答, 无需处理
    } else if (evs && !strcmp(evs, "hello_ack")) {
        // 工作台握手应答: 记录其对端协商的音频参数 (便于现场核对采样格式)
        const cJSON *aud = cJSON_GetObjectItem(root, "audio");
        int rate = 16000, ch = 1, sw = 2;
        if (cJSON_IsObject(aud)) {
            const cJSON *r = cJSON_GetObjectItem(aud, "sample_rate");
            const cJSON *c = cJSON_GetObjectItem(aud, "channels");
            const cJSON *w = cJSON_GetObjectItem(aud, "sample_width");
            if (cJSON_IsNumber(r)) rate = r->valueint;
            if (cJSON_IsNumber(c)) ch = c->valueint;
            if (cJSON_IsNumber(w)) sw = w->valueint;
        }
        ESP_LOGI(TAG, "工作台握手应答: 约定音频 %dHz / %d 声道 / %d 字节采样", rate, ch, sw);
    } else if ((cms && !strcmp(cms, "record_start")) || (tys && !strcmp(tys, "record_start"))) {
        fire_event(CARD_LINK_EV_CMD_RECORD_START);
    } else if ((cms && !strcmp(cms, "record_stop")) || (tys && !strcmp(tys, "record_stop"))) {
        fire_event(CARD_LINK_EV_CMD_RECORD_STOP);
    }

    cJSON_Delete(root);
}

// 字节流切分: 逐行交给 dispatch_line; lcd_frame / state_changed 等超大帧整行丢弃
static void rx_feed(const char *data, int len)
{
    static char line[LINK_LINE_BUF];
    static size_t llen = 0;
    static bool discarding = false;

    for (int i = 0; i < len; i++) {
        char c = data[i];
        if (discarding) {
            if (c == '\n') discarding = false;
            continue;
        }
        if (c == '\n') {
            if (llen > 0) {
                line[llen] = '\0';
                dispatch_line(line);
            }
            llen = 0;
            continue;
        }
        line[llen++] = c;
        // 超大位图帧与巨型状态广播无需在卡片侧解析, 按前缀整行丢弃。
        // 注意: 必须先把当前前缀补 '\0' 再做 strstr, 否则会越界读取残留字节。
        if (llen == 32) {
            line[llen] = '\0';
            if (strstr(line, "lcd_frame") || strstr(line, "state_changed")) {
                discarding = true;
                llen = 0;
                continue;
            }
        }
        if (llen >= LINK_LINE_BUF - 1) {
            ESP_LOGW(TAG, "收到超长未知消息, 整行丢弃");
            discarding = true;
            llen = 0;
        }
    }
}

// ---------------- WebSocket 事件 ----------------

static void ws_event_cb(void *args, esp_event_base_t base, int32_t event_id, void *event_data)
{
    (void)args;
    (void)base;
    esp_websocket_event_data_t *d = (esp_websocket_event_data_t *)event_data;

    switch (event_id) {
    case WEBSOCKET_EVENT_CONNECTED:
        s_connected = true;
        s_last_err[0] = '\0';
        ESP_LOGI(TAG, "WebSocket 已连接: %s", s_cur_uri);
        // 握手 + 首次真实状态同步
        {
            uint8_t mac[6] = {0};
            char mac_str[20] = "00:00:00:00:00:00";
            if (esp_read_mac(mac, ESP_MAC_WIFI_STA) == ESP_OK) {
                snprintf(mac_str, sizeof(mac_str), "%02X:%02X:%02X:%02X:%02X:%02X",
                         mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
            }
            char ssid[34] = {0};
            ble_prov_get_ssid_str(ssid, sizeof(ssid));
            tx_json_simple("{\"type\":\"hello\",\"fw\":\"ai-passport-ws+ndjson\",\"mac\":\"%s\",\"ssid\":\"%s\"}",
                           mac_str, ssid);
        }
        fire_event(CARD_LINK_EV_CONN_CHANGED);
        break;

    case WEBSOCKET_EVENT_DISCONNECTED:
        if (s_connected) {
            s_connected = false;
            ESP_LOGW(TAG, "WebSocket 连接已断开, 将自动重连");
            fire_event(CARD_LINK_EV_CONN_CHANGED);
        }
        break;

    case WEBSOCKET_EVENT_DATA:
        // 控制帧 (ping/pong/close) 的 data_len 为 0; 业务数据按字节流交给切分器,
        // 分片帧 (payload_offset/payload_len) 也会被自然拼接。
        if (d && d->data_len > 0 && d->data_ptr && d->op_code != 0x08) {
            rx_feed(d->data_ptr, d->data_len);
        }
        break;

    case WEBSOCKET_EVENT_ERROR:
        // 只记状态与内存, 不解析错误句柄内部结构(IDF 5.x 该结构变过好几次):
        // 具体的失败原因由 esp_websocket_client / esp-tls 自己的 ERROR 日志给出,
        // 那些日志的 tag 是 websocket_client / transport_ws / esp-tls。
        ESP_LOGE(TAG, "WebSocket 通信错误: uri=%s, 空闲堆=%u; 详见上面 websocket_client/esp-tls 的报错",
                 s_cur_uri, (unsigned)esp_get_free_heap_size());
        set_error("WebSocket 通信错误 (请检查域名解析、TLS 证书与设备令牌)");
        break;

    case WEBSOCKET_EVENT_CLOSED:
        if (s_connected) {
            s_connected = false;
            fire_event(CARD_LINK_EV_CONN_CHANGED);
        }
        break;

    default:
        break;
    }
}

// ---------------- 连接管理 ----------------

static bool build_uri(char *out, size_t out_len)
{
    if (!ble_prov_has_server()) return false;

    char host[64] = {0};
    char path[48] = {0};
    char token[64] = {0};
    if (ble_prov_get_server_host(host, sizeof(host)) != ESP_OK || host[0] == '\0') return false;

    uint16_t port = ble_prov_get_server_port();
    bool tls = ble_prov_get_server_tls();
    ble_prov_get_server_path(path, sizeof(path));
    ble_prov_get_server_token(token, sizeof(token));
    if (path[0] == '\0') snprintf(path, sizeof(path), "/ws/card");

    int n;
    if (token[0]) {
        n = snprintf(out, out_len, "%s://%s:%u%s?token=%s",
                     tls ? "wss" : "ws", host, (unsigned)port, path, token);
    } else {
        n = snprintf(out, out_len, "%s://%s:%u%s", tls ? "wss" : "ws", host, (unsigned)port, path);
    }
    return (n > 0 && n < (int)out_len);
}

static void ws_teardown(void)
{
    if (s_ws) {
        ESP_LOGW(TAG, "正在关闭旧的 WebSocket 连接");
        esp_websocket_client_stop(s_ws);
        esp_websocket_client_destroy(s_ws);
        s_ws = NULL;
    }
    s_connected = false;
    s_cur_uri[0] = '\0';
    if (s_tx_sb) xStreamBufferReset(s_tx_sb);
}

static esp_err_t ws_setup(const char *uri)
{
    snprintf(s_cur_uri, sizeof(s_cur_uri), "%s", uri);

    esp_websocket_client_config_t cfg = {0};
    cfg.uri = s_cur_uri;                     // 必须指向持久内存
    cfg.reconnect_timeout_ms = LINK_WS_RETRY_MS;
    cfg.network_timeout_ms = LINK_WS_TIMEOUT_MS;
    cfg.buffer_size = 2048;                  // 单次读取上限; 更大的帧会分多次 DATA 事件送达
    cfg.task_stack = 6144;
    cfg.task_prio = 5;
    cfg.disable_auto_reconnect = false;

    bool is_wss = (strncmp(uri, "wss://", 6) == 0);
    if (is_wss) {
        // 真实证书校验: 使用 ESP-IDF 内置根证书包 (本域名链路根为 DigiCert Global Root G2)
        cfg.crt_bundle_attach = esp_crt_bundle_attach;
    }

    s_ws = esp_websocket_client_init(&cfg);
    if (!s_ws) {
        set_error("WebSocket 客户端初始化失败 (内存不足?)");
        return ESP_ERR_NO_MEM;
    }

    // 注意: 该 API 名为 esp_websocket_register_events (没有 _client_ 中缀),
    // 写成 esp_websocket_client_register_events 会在编译期报未声明。
    esp_err_t err = esp_websocket_register_events(s_ws, WEBSOCKET_EVENT_ANY, ws_event_cb, NULL);
    if (err != ESP_OK) {
        set_error("注册 WebSocket 事件失败: %d", err);
        ws_teardown();
        return err;
    }

    ESP_LOGI(TAG, "正在连接工作台: %s", s_cur_uri);
    err = esp_websocket_client_start(s_ws);
    if (err != ESP_OK) {
        set_error("启动 WebSocket 连接失败: %d", err);
        ws_teardown();
        return err;
    }
    return ESP_OK;
}

// 发送任务: 从流缓冲取整行 NDJSON, 以文本帧发出; 同时负责心跳
static void link_tx_task(void *arg)
{
    (void)arg;
    char buf[1024];
    uint32_t last_hb = (uint32_t)pdTICKS_TO_MS(xTaskGetTickCount());

    while (s_task_alive) {
        size_t n = xStreamBufferReceive(s_tx_sb, buf, sizeof(buf), pdMS_TO_TICKS(200));
        if (n > 0) {
            if (s_connected && s_ws) {
                int sent = esp_websocket_client_send_text(s_ws, buf, (int)n, pdMS_TO_TICKS(5000));
                if (sent < 0) {
                    set_error("WebSocket 文本帧发送失败");
                }
            }
        }

        uint32_t now = (uint32_t)pdTICKS_TO_MS(xTaskGetTickCount());
        if (now - last_hb >= LINK_HEARTBEAT_MS) {
            last_hb = now;
            // 每 15 秒留一行带内存的心跳: 桥接连接失败时, "是没内存还是令牌不对"
            // 全靠这一行区分(串口日志在现场是唯一线索)。
            ESP_LOGI(TAG, "心跳: 已连接工作台=%d, 空闲堆=%u 字节",
                     (int)s_connected, (unsigned)esp_get_free_heap_size());
            tx_json_simple("{\"type\":\"ping\"}");
        }
    }
    vTaskDelete(NULL);
}

static void link_task(void *arg)
{
    (void)arg;
    char uri[LINK_URI_BUF] = {0};

    ESP_LOGI(TAG, "WebSocket 桥接客户端任务启动");

    while (s_task_alive) {
        if (!ble_prov_is_wifi_connected()) {
            s_connected = false;
            vTaskDelay(pdMS_TO_TICKS(500));
            continue;
        }
        if (!build_uri(uri, sizeof(uri))) {
            // 尚未配网下发工作台地址: 如实提示, 不伪造连接
            s_connected = false;
            vTaskDelay(pdMS_TO_TICKS(1000));
            continue;
        }

        // 地址变化 (重新配网 / 切换 wss 或 ws) 时重建连接
        if (strcmp(uri, s_cur_uri) != 0) {
            ws_teardown();
            if (ws_setup(uri) != ESP_OK) {
                vTaskDelay(pdMS_TO_TICKS(LINK_WS_RETRY_MS));
                continue;
            }
        }
        vTaskDelay(pdMS_TO_TICKS(1000));
    }

    ws_teardown();
    vTaskDelete(NULL);
}

// ---------------- 对外接口 ----------------

esp_err_t card_link_start(void)
{
    if (s_task_alive) return ESP_OK;

    // 桥接客户端起不来时, 胸卡会一直在"等待无线网络/正在连接工作台"之间徘徊,
    // 而现象与"服务端令牌不对"完全一样。所以这里必须把每一步的失败与当时的空闲堆
    // 都记下来 —— C3 无 PSRAM, Wi-Fi + NimBLE + LVGL 之后留给桥接的内存很紧。
    ESP_LOGI(TAG, "桥接客户端启动前: 空闲堆=%u 字节",
             (unsigned)esp_get_free_heap_size());

    if (!s_tx_sb) {
        s_tx_sb = xStreamBufferCreate(LINK_TX_SB_SIZE, 1);
        if (!s_tx_sb) {
            ESP_LOGE(TAG, "发送缓冲创建失败 (需要 %d 字节), 空闲堆=%u",
                     LINK_TX_SB_SIZE, (unsigned)esp_get_free_heap_size());
            return ESP_ERR_NO_MEM;
        }
    }
    if (!s_tx_mtx) s_tx_mtx = xSemaphoreCreateMutex();
    if (!s_sum_mtx) s_sum_mtx = xSemaphoreCreateMutex();
    if (!s_tx_mtx || !s_sum_mtx) {
        ESP_LOGE(TAG, "互斥量创建失败, 空闲堆=%u", (unsigned)esp_get_free_heap_size());
        return ESP_ERR_NO_MEM;
    }

    s_task_alive = true;
    if (xTaskCreate(link_task, "card_link", 4096, NULL, 4, &s_link_task) != pdPASS) {
        s_task_alive = false;
        ESP_LOGE(TAG, "连接任务创建失败 (栈 4096), 空闲堆=%u",
                 (unsigned)esp_get_free_heap_size());
        return ESP_ERR_NO_MEM;
    }
    if (xTaskCreate(link_tx_task, "card_tx", 3072, NULL, 5, &s_tx_task) != pdPASS) {
        ESP_LOGE(TAG, "发送任务创建失败 (栈 3072), 空闲堆=%u",
                 (unsigned)esp_get_free_heap_size());
    }
    ESP_LOGI(TAG, "WebSocket 桥接客户端已启动: 空闲堆=%u 字节",
             (unsigned)esp_get_free_heap_size());
    return ESP_OK;
}

bool card_link_is_connected(void)
{
    return s_connected;
}

const char *card_link_state_text(void)
{
    if (s_connected) return "已连接工作台";
    if (!ble_prov_is_wifi_connected()) return "等待无线网络";
    if (!ble_prov_has_server()) return "等待下发服务端";
    return "正在连接工作台";
}

uint32_t card_link_tx_lines(void) { return s_tx_lines; }
uint32_t card_link_rx_lines(void) { return s_rx_lines; }
uint32_t card_link_audio_frames(void) { return s_audio_frames; }
uint32_t card_link_audio_drops(void) { return s_audio_drops; }

const char *card_link_last_error(void) { return s_last_err; }

void card_link_clear_error(void)
{
    s_last_err[0] = '\0';
}

void card_link_set_event_cb(card_link_event_cb_t cb, void *user)
{
    s_ev_cb = cb;
    s_ev_user = user;
}

bool card_link_get_summary(card_link_summary_t *out)
{
    if (!out) return false;
    bool ok = false;
    if (s_sum_mtx && xSemaphoreTake(s_sum_mtx, pdMS_TO_TICKS(200)) == pdTRUE) {
        if (s_summary.valid) {
            *out = s_summary;
            ok = true;
        }
        xSemaphoreGive(s_sum_mtx);
    }
    return ok;
}
