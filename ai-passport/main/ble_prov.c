// main/ble_prov.c —— 飞书胸卡 BLE 蓝牙配网监听服务与 Wi-Fi 管理实现
#include "ble_prov.h"
#include "demo_radio.h"

#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_heap_caps.h"
#include "nvs_flash.h"
#include "nvs.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"

#include "host/ble_hs.h"
#include "host/ble_uuid.h"
#include "host/util/util.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"

#include <string.h>
#include <stdio.h>
#include <stdlib.h>

static const char *TAG = "ble_prov";
static const char *DEVICE_NAME = "FoloPassport";
static const char *NVS_NAMESPACE = "passport_wifi";

// 自定义配网服务 16-bit UUID
// Service: 0xFFF0, Write Char: 0xFFF1, Notify/Read Char: 0xFFF2
static const ble_uuid16_t gatt_svr_svc_uuid = BLE_UUID16_INIT(0xFFF0);
static const ble_uuid16_t gatt_svr_chr_write_uuid = BLE_UUID16_INIT(0xFFF1);
static const ble_uuid16_t gatt_svr_chr_notify_uuid = BLE_UUID16_INIT(0xFFF2);

static uint16_t s_notify_val_handle = 0;
static uint16_t s_conn_handle = BLE_HS_CONN_HANDLE_NONE;
static uint8_t  s_addr_type = 0;
static bool     s_ble_started = false;
static bool     s_ble_released = false;   // BLE 栈已释放(配网完成后让内存给桥接)
static bool     s_wifi_init = false;

static volatile ble_prov_state_t s_state = BLE_PROV_STATE_IDLE;
static char     s_ip_str[32] = {0};
static char     s_cur_ssid[34] = {0};
static char     s_cur_pwd[66] = {0};
static int      s_retry_cnt = 0;
static esp_netif_t *s_sta_netif = NULL;

// 工作台服务端地址: 由配网页随 Wi-Fi 凭据一并下发, 并持久化到 NVS。
// 严禁猜测/硬编码: 未下发时处于"未配置"态, 由上层如实提示"等待下发服务端"。
// 默认走 wss://<host>:443/ws/card，复用站点 HTTPS 证书，无需额外放行端口。
static char     s_server_host[64] = {0};
static uint16_t s_server_port = 443;
static bool     s_server_tls = true;
static char     s_server_path[48] = "/ws/card";
static char     s_server_token[64] = {0};

static int ble_prov_advertise(void);

// 从 JSON 文本里取 "key":"value" 的字符串值 (不引入额外解析依赖)
static void json_take_str(const char *input, const char *key, char *out, size_t max_out)
{
    out[0] = '\0';
    if (!input || !key) return;

    char pat[40];
    snprintf(pat, sizeof(pat), "\"%s\"", key);
    const char *p = strstr(input, pat);
    if (!p) return;
    p = strchr(p + strlen(pat), ':');
    if (!p) return;
    p = strchr(p, '"');
    if (!p) return;
    p++;
    const char *q = strchr(p, '"');
    if (!q || q <= p) return;

    size_t len = (size_t)(q - p);
    if (len >= max_out) len = max_out - 1;
    memcpy(out, p, len);
    out[len] = '\0';
}

// 解析配网报文中的工作台连接参数
static void parse_server_info(const char *input,
                              char *out_host, size_t max_host,
                              uint16_t *out_port, bool *out_tls,
                              char *out_path, size_t max_path,
                              char *out_token, size_t max_token)
{
    out_host[0] = '\0';
    out_path[0] = '\0';
    out_token[0] = '\0';
    if (!input) return;

    json_take_str(input, "host", out_host, max_host);
    if (out_host[0] == '\0') json_take_str(input, "server", out_host, max_host);
    json_take_str(input, "path", out_path, max_path);
    json_take_str(input, "token", out_token, max_token);

    const char *pt = strstr(input, "\"port\"");
    if (pt) {
        const char *p = strchr(pt, ':');
        if (p) {
            p++;
            while (*p == ' ') p++;
            long v = strtol(p, NULL, 10);
            if (v > 0 && v <= 65535) *out_port = (uint16_t)v;
        }
    }

    const char *tl = strstr(input, "\"tls\"");
    if (tl) {
        const char *p = strchr(tl, ':');
        if (p) {
            p++;
            while (*p == ' ') p++;
            *out_tls = (strncmp(p, "true", 4) == 0 || *p == '1');
            if (*out_tls) {
                // 走 TLS 时端口通常为 443; 若报文没给端口则兜底 443
                if (!pt || *out_port == 5566) *out_port = 443;
            }
        }
    }
}

// 解析凭据: 兼容 JSON 格式 {"ssid":"...","pwd":"..."} 与换行格式 SSID\nPWD
static void parse_credentials(const char *input, char *out_ssid, size_t max_ssid, char *out_pwd, size_t max_pwd) {
    out_ssid[0] = '\0';
    out_pwd[0] = '\0';
    if (!input || strlen(input) == 0) return;

    // 尝试解析 JSON 格式
    const char *s_tag = strstr(input, "\"ssid\":");
    if (!s_tag) s_tag = strstr(input, "\"ssid\" :");
    if (s_tag) {
        const char *p1 = strchr(s_tag, ':');
        if (p1) {
            p1 = strchr(p1, '"');
            if (p1) {
                p1++;
                const char *p2 = strchr(p1, '"');
                if (p2) {
                    size_t len = p2 - p1;
                    if (len >= max_ssid) len = max_ssid - 1;
                    strncpy(out_ssid, p1, len);
                    out_ssid[len] = '\0';
                }
            }
        }
        const char *pwd_tag = strstr(input, "\"pwd\":");
        if (!pwd_tag) pwd_tag = strstr(input, "\"password\":");
        if (!pwd_tag) pwd_tag = strstr(input, "\"pwd\" :");
        if (!pwd_tag) pwd_tag = strstr(input, "\"password\" :");
        if (pwd_tag) {
            const char *p1 = strchr(pwd_tag, ':');
            if (p1) {
                p1 = strchr(p1, '"');
                if (p1) {
                    p1++;
                    const char *p2 = strchr(p1, '"');
                    if (p2) {
                        size_t len = p2 - p1;
                        if (len >= max_pwd) len = max_pwd - 1;
                        strncpy(out_pwd, p1, len);
                        out_pwd[len] = '\0';
                    }
                }
            }
        }
        return;
    }

    // 尝试解析换行格式 (SSID\nPWD)
    const char *nl = strchr(input, '\n');
    if (nl) {
        size_t slen = nl - input;
        if (slen > 0 && input[slen - 1] == '\r') slen--;
        if (slen >= max_ssid) slen = max_ssid - 1;
        strncpy(out_ssid, input, slen);
        out_ssid[slen] = '\0';

        const char *pwd_start = nl + 1;
        size_t plen = strlen(pwd_start);
        if (plen > 0 && pwd_start[plen - 1] == '\n') plen--;
        if (plen > 0 && pwd_start[plen - 1] == '\r') plen--;
        if (plen >= max_pwd) plen = max_pwd - 1;
        strncpy(out_pwd, pwd_start, plen);
        out_pwd[plen] = '\0';
        return;
    }

    // 单行视为无密码 SSID
    strncpy(out_ssid, input, max_ssid - 1);
    out_ssid[max_ssid - 1] = '\0';
}

// 向连接的手机发送 GATT Notify 通知状态
static void send_notify_msg(const char *msg) {
    // BLE 栈释放后句柄已失效: 例如 Wi-Fi 断线重连会再次触发 IP 事件, 若此时还去
    // notify 就会访问已释放的内存。配网早已完成, 直接忽略即可。
    if (s_ble_released) return;
    if (s_conn_handle == BLE_HS_CONN_HANDLE_NONE || s_notify_val_handle == 0) return;
    struct os_mbuf *om = ble_hs_mbuf_from_flat(msg, strlen(msg));
    if (om) {
        ble_gatts_notify_custom(s_conn_handle, s_notify_val_handle, om);
    }
}

// 保存 Wi-Fi 凭证与工作台服务端地址到 NVS
static void save_wifi_to_nvs(const char *ssid, const char *pwd) {
    nvs_handle_t nvs_h;
    if (nvs_open(NVS_NAMESPACE, NVS_READWRITE, &nvs_h) == ESP_OK) {
        nvs_set_str(nvs_h, "ssid", ssid);
        nvs_set_str(nvs_h, "pwd", pwd);
        if (s_server_host[0]) {
            nvs_set_str(nvs_h, "srv_host", s_server_host);
            nvs_set_u16(nvs_h, "srv_port", s_server_port);
            nvs_set_u8(nvs_h, "srv_tls", s_server_tls ? 1 : 0);
            nvs_set_str(nvs_h, "srv_path", s_server_path);
            nvs_set_str(nvs_h, "srv_token", s_server_token);
        }
        nvs_commit(nvs_h);
        nvs_close(nvs_h);
        ESP_LOGI(TAG, "已保存到 NVS: SSID=%s, 工作台=%s:%u", ssid,
                 s_server_host[0] ? s_server_host : "(未下发)", (unsigned)s_server_port);
    }
}

// 从 NVS 读取历史工作台服务端地址
static void load_server_from_nvs(void) {
    nvs_handle_t nvs_h;
    if (nvs_open(NVS_NAMESPACE, NVS_READONLY, &nvs_h) != ESP_OK) return;
    size_t len = sizeof(s_server_host);
    if (nvs_get_str(nvs_h, "srv_host", s_server_host, &len) == ESP_OK && s_server_host[0]) {
        uint16_t p = 443;
        if (nvs_get_u16(nvs_h, "srv_port", &p) == ESP_OK && p != 0) s_server_port = p;
        uint8_t tls = 1;
        if (nvs_get_u8(nvs_h, "srv_tls", &tls) == ESP_OK) s_server_tls = (tls != 0);
        size_t plen = sizeof(s_server_path);
        if (nvs_get_str(nvs_h, "srv_path", s_server_path, &plen) != ESP_OK || s_server_path[0] == '\0') {
            snprintf(s_server_path, sizeof(s_server_path), "%s", "/ws/card");
        }
        size_t tlen = sizeof(s_server_token);
        if (nvs_get_str(nvs_h, "srv_token", s_server_token, &tlen) != ESP_OK) {
            s_server_token[0] = '\0';
        }
        ESP_LOGI(TAG, "从 NVS 恢复工作台地址: %s://%s:%u%s (令牌:%s)",
                 s_server_tls ? "wss" : "ws", s_server_host, (unsigned)s_server_port,
                 s_server_path, s_server_token[0] ? "有" : "无");
    }
    nvs_close(nvs_h);
}

// 从 NVS 读取历史 Wi-Fi 凭据
static bool load_wifi_from_nvs(char *ssid, size_t max_ssid, char *pwd, size_t max_pwd) {
    nvs_handle_t nvs_h;
    if (nvs_open(NVS_NAMESPACE, NVS_READONLY, &nvs_h) != ESP_OK) {
        return false;
    }
    size_t s_len = max_ssid;
    size_t p_len = max_pwd;
    esp_err_t err1 = nvs_get_str(nvs_h, "ssid", ssid, &s_len);
    esp_err_t err2 = nvs_get_str(nvs_h, "pwd", pwd, &p_len);
    nvs_close(nvs_h);
    return (err1 == ESP_OK && strlen(ssid) > 0 && err2 == ESP_OK);
}

// ---------------------------------------------------------------------------
// 配网专用任务
//
// ★ 重活绝不能放在 NimBLE 的 GATT 回调里直接做。
//   GATT 访问回调运行在 NimBLE host 任务上, 该任务栈由
//   CONFIG_BT_NIMBLE_HOST_TASK_STACK_SIZE 决定(未配置时默认 4096 字节),
//   而回调被调用时协议栈已经压了若干层帧。此时再执行
//   esp_netif_create_default_wifi_sta() + esp_wifi_init() + 事件注册这一整套
//   Wi-Fi 初始化(本身就需要 1.5KB 以上栈), 极易栈溢出 —— 现场表现正是
//   "手机显示凭证已写入, 但胸卡既不联网、也不回报状态", 因为设备已经重启,
//   而且重启后 NVS 里当然没有 Wi-Fi(凭证只在连上后才落盘)。
//
//   因此: 回调只把凭据塞进队列, 真正的 Wi-Fi 初始化/连接交给下面这个
//   带独立栈的任务来做。
// ---------------------------------------------------------------------------
typedef struct {
    char ssid[34];
    char pwd[66];
    bool release_ble;   // true = 只释放 BLE 栈, 不连网 (见 ble_prov_release_ble)
} prov_req_t;

static QueueHandle_t      s_prov_q;
static TaskHandle_t       s_prov_task;
static SemaphoreHandle_t  s_host_stopped;   // host 任务退出信号, 供 ble_teardown 等待
static esp_err_t          do_connect_wifi(const char *ssid, const char *pwd);

// 释放 BLE 协议栈, 把内存让给 wss/TLS 桥接。
//
// 蓝牙只服务于配网; 而 C3 无 PSRAM, 一次 wss(TLS) 握手要约 20KB, 配网完成后继续
// 占着 NimBLE 会让握手因内存不足失败(实测桥接启动后仅剩 3KB 空闲堆)。
// 释放后如需重新配网, 重启胸卡即可 —— 开机会重新起广播。
static void ble_teardown(void) {
    if (s_ble_released) return;

    ESP_LOGI(TAG, "为桥接释放 BLE 协议栈: 释放前空闲堆=%u 字节",
             (unsigned)esp_get_free_heap_size());
    ble_gap_adv_stop();

    const int rc = nimble_port_stop();
    if (rc != 0) {
        ESP_LOGW(TAG, "nimble_port_stop 返回 %d, BLE 未释放", rc);
        return;
    }
    // 必须等 host 任务真正退出再 deinit, 否则会释放仍在使用的内存。
    // 给个上限, 避免异常时把配网任务卡死。
    if (s_host_stopped &&
        xSemaphoreTake(s_host_stopped, pdMS_TO_TICKS(3000)) != pdTRUE) {
        ESP_LOGW(TAG, "等待 NimBLE host 任务退出超时, 跳过 deinit");
        return;
    }
    nimble_port_deinit();
    s_ble_started = false;
    s_ble_released = true;
    ESP_LOGI(TAG, "BLE 已释放: 空闲堆=%u 字节 (需要重新配网时请重启胸卡)",
             (unsigned)esp_get_free_heap_size());
}

static void prov_task(void *arg) {
    (void)arg;
    prov_req_t req;
    for (;;) {
        if (xQueueReceive(s_prov_q, &req, portMAX_DELAY) != pdTRUE) continue;

        if (req.release_ble) {
            // 延迟 8 秒再释放: 先让"配网成功"的 BLE 通知真正送到手机, 也给桥接第一次
            // 握手留出时间; 万一第一次因内存不足失败, 释放后它的自动重连就能成功。
            vTaskDelay(pdMS_TO_TICKS(8000));
            ble_teardown();
            continue;
        }

        if (req.ssid[0] == '\0') continue;
        ESP_LOGI(TAG, "配网任务开始连接路由器: SSID=%s (密码长度=%u)",
                 req.ssid, (unsigned)strlen(req.pwd));
        esp_err_t e = do_connect_wifi(req.ssid, req.pwd);
        if (e != ESP_OK) {
            ESP_LOGE(TAG, "启动 Wi-Fi 连接失败: %s", esp_err_to_name(e));
        }
    }
}

void ble_prov_release_ble(void) {
    if (!s_ble_started || s_ble_released) return;
    prov_req_t r = { 0 };
    r.release_ble = true;
    if (!s_prov_q || xQueueSend(s_prov_q, &r, 0) != pdTRUE) {
        ESP_LOGW(TAG, "释放 BLE 的请求未能入队 (配网任务忙)");
    }
}

// 把一套凭据排进配网任务。回调侧与本函数都可能调用(开机自连走这里)。
static void prov_enqueue(const char *ssid, const char *pwd) {
    if (!s_prov_q) {
        ESP_LOGE(TAG, "配网队列未就绪, 无法连接 %s", ssid ? ssid : "(null)");
        return;
    }
    prov_req_t req = { 0 };
    snprintf(req.ssid, sizeof(req.ssid), "%s", ssid ? ssid : "");
    snprintf(req.pwd, sizeof(req.pwd), "%s", pwd ? pwd : "");
    if (xQueueSend(s_prov_q, &req, 0) != pdTRUE) {
        ESP_LOGW(TAG, "配网任务正忙, 本次请求未排队 (SSID=%s)", req.ssid);
    }
}

// Wi-Fi 断开原因码 -> 可执行结论。这几个码覆盖了配网现场的绝大多数失败。
static const char *wifi_reason_hint(int reason) {
    switch (reason) {
    case 1:   return "未指定原因";
    case 2:   return "认证过期, 通常仍是密码不正确";
    case 15:  return "四次握手超时 —— 密码不正确(最常见)";
    case 200: return "收不到 AP 信标, 信号太弱或距离太远";
    case 201: return "没搜到这个 SSID —— 名称写错, 或这是 5GHz 网络(胸卡只支持 2.4GHz)";
    case 202: return "认证失败, 密码不正确";
    case 203: return "关联失败, 多为信号弱或 AP 连接数已满";
    case 204: return "握手超时, 密码或信号问题";
    case 205: return "AP 拒绝连接, 可能开了 MAC 白名单或已达上限";
    default:  return "见 IDF wifi_err_reason_t 对照表";
    }
}

// Wi-Fi 事件处理
static void wifi_event_handler(void *arg, esp_event_base_t event_base,
                               int32_t event_id, void *event_data) {
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        ESP_LOGI(TAG, "Wi-Fi STA 启动，尝试连接 %s...", s_cur_ssid);
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        const wifi_event_sta_disconnected_t *d =
            (const wifi_event_sta_disconnected_t *)event_data;
        const int reason = d ? (int)d->reason : -1;
        if (s_state == BLE_PROV_STATE_CONNECTING) {
            if (s_retry_cnt < 5) {
                s_retry_cnt++;
                ESP_LOGW(TAG, "Wi-Fi 第 %d/5 次重试; 上次失败 reason=%d (%s)",
                         s_retry_cnt, reason, wifi_reason_hint(reason));
                esp_wifi_connect();
            } else {
                s_state = BLE_PROV_STATE_FAILED;
                ESP_LOGE(TAG, "Wi-Fi 连接失败: reason=%d (%s); SSID=%s",
                         reason, wifi_reason_hint(reason), s_cur_ssid);
                char notify[64];
                snprintf(notify, sizeof(notify), "{\"status\":3,\"msg\":\"Connection failed\"}");
                send_notify_msg(notify);
            }
        }
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
        snprintf(s_ip_str, sizeof(s_ip_str), IPSTR, IP2STR(&event->ip_info.ip));
        s_state = BLE_PROV_STATE_CONNECTED;
        s_retry_cnt = 0;
        ESP_LOGI(TAG, "🎉 Wi-Fi 连接成功! 获取真实 IP: %s", s_ip_str);

        // 成功连接后持久化至 NVS
        save_wifi_to_nvs(s_cur_ssid, s_cur_pwd);

        char notify[96];
        snprintf(notify, sizeof(notify), "{\"status\":2,\"ip\":\"%s\",\"ssid\":\"%s\"}", s_ip_str, s_cur_ssid);
        send_notify_msg(notify);

        // 配网已完成: 稍后释放 BLE 协议栈, 把内存让给 wss/TLS 桥接(见 ble_teardown)。
        ble_prov_release_ble();
    }
}

// Wi-Fi 驱动初始化(只做一次)。
//
// ⚠ 两个硬性要求:
//   1. 每一步都要检查返回值。之前这一整套的返回值全被忽略, 而且无条件把 s_wifi_init
//      置成 true, 结果 esp_wifi_init 失败后 esp_wifi_start() 只回一个
//      ESP_ERR_WIFI_NOT_INIT, 现场看到的就只有"手机写完了但胸卡不联网", 完全看不出原因。
//   2. 必须在堆最充裕的时候调用(见 ble_prov_start 里的调用点)。C3 只有约 190KB 可用
//      动态 RAM, 而这个固件同时跑 NimBLE(MTU 512) + LVGL + I2S 音频 + 带 TLS 的
//      WebSocket 客户端; 等到配网时才懒加载 Wi-Fi 驱动, esp_wifi_init 极易因内存不足
//      失败。故意不在这里做"失败兜底重试"之类的花活 —— 失败就把真实错误码和堆数据打出来。
static esp_err_t wifi_driver_init_once(void) {
    if (s_wifi_init) return ESP_OK;

    ESP_LOGI(TAG, "Wi-Fi 初始化前: 空闲堆=%u 字节, 最大连续块=%u 字节",
             (unsigned)esp_get_free_heap_size(),
             (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_8BIT));

    if (!s_sta_netif) {
        s_sta_netif = esp_netif_create_default_wifi_sta();
        if (!s_sta_netif) {
            ESP_LOGE(TAG, "esp_netif_create_default_wifi_sta() 返回空");
            return ESP_FAIL;
        }
    }

    const wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    esp_err_t e = esp_wifi_init(&cfg);
    if (e != ESP_OK) {
        ESP_LOGE(TAG, "esp_wifi_init 失败: %s (空闲堆=%u 字节, 最大连续块=%u 字节)",
                 esp_err_to_name(e), (unsigned)esp_get_free_heap_size(),
                 (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_8BIT));
        return e;
    }

    e = esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                           &wifi_event_handler, NULL, NULL);
    if (e != ESP_OK) {
        ESP_LOGE(TAG, "注册 WIFI_EVENT 处理失败: %s", esp_err_to_name(e));
        return e;
    }
    e = esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                           &wifi_event_handler, NULL, NULL);
    if (e != ESP_OK) {
        ESP_LOGE(TAG, "注册 IP_EVENT 处理失败: %s", esp_err_to_name(e));
        return e;
    }
    e = esp_wifi_set_mode(WIFI_MODE_STA);
    if (e != ESP_OK) {
        ESP_LOGE(TAG, "esp_wifi_set_mode(STA) 失败: %s", esp_err_to_name(e));
        return e;
    }

    s_wifi_init = true;
    ESP_LOGI(TAG, "Wi-Fi 驱动初始化完成: 空闲堆=%u 字节",
             (unsigned)esp_get_free_heap_size());
    return ESP_OK;
}

// 启动 Wi-Fi 连接流程
static esp_err_t do_connect_wifi(const char *ssid, const char *pwd) {
    if (!ssid || strlen(ssid) == 0) return ESP_ERR_INVALID_ARG;

    char notify[64];
    snprintf(notify, sizeof(notify), "{\"status\":1,\"msg\":\"Connecting to %s...\"}", ssid);

    esp_err_t e = wifi_driver_init_once();
    if (e != ESP_OK) {
        // 必须回报手机: 否则配网页会一直停在"等待胸卡接入网络", 用户拿不到任何结论
        s_state = BLE_PROV_STATE_FAILED;
        send_notify_msg("{\"status\":3,\"msg\":\"Wi-Fi driver init failed\"}");
        return e;
    }

    s_state = BLE_PROV_STATE_CONNECTING;
    s_retry_cnt = 0;
    strncpy(s_cur_ssid, ssid, sizeof(s_cur_ssid) - 1);
    strncpy(s_cur_pwd, pwd, sizeof(s_cur_pwd) - 1);
    send_notify_msg(notify);

    wifi_config_t wifi_cfg = { 0 };
    strncpy((char *)wifi_cfg.sta.ssid, ssid, sizeof(wifi_cfg.sta.ssid) - 1);
    strncpy((char *)wifi_cfg.sta.password, pwd, sizeof(wifi_cfg.sta.password) - 1);
    wifi_cfg.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;

    // 停掉上一次的 STA, 换上新凭据再起: 两步的返回值都要看
    esp_err_t se = esp_wifi_stop();
    if (se != ESP_OK && se != ESP_ERR_WIFI_NOT_STARTED) {
        ESP_LOGW(TAG, "esp_wifi_stop 返回 %s (继续尝试设置新凭据)", esp_err_to_name(se));
    }
    se = esp_wifi_set_config(WIFI_IF_STA, &wifi_cfg);
    if (se != ESP_OK) {
        ESP_LOGE(TAG, "esp_wifi_set_config 失败: %s", esp_err_to_name(se));
        s_state = BLE_PROV_STATE_FAILED;
        send_notify_msg("{\"status\":3,\"msg\":\"set_config failed\"}");
        return se;
    }
    se = esp_wifi_start();
    if (se != ESP_OK) {
        ESP_LOGE(TAG, "esp_wifi_start 失败: %s (SSID=%s)", esp_err_to_name(se), ssid);
        s_state = BLE_PROV_STATE_FAILED;
        send_notify_msg("{\"status\":3,\"msg\":\"wifi start failed\"}");
        return se;
    }
    ESP_LOGI(TAG, "Wi-Fi 已启动, 等待 %s 的连接结果...", ssid);
    return ESP_OK;
}

// GATT 访问回调
static int gatt_svr_chr_access(uint16_t conn_handle, uint16_t attr_handle,
                               struct ble_gatt_access_ctxt *ctxt, void *arg) {
    uint16_t uuid16 = ble_uuid_u16(ctxt->chr->uuid);

    if (uuid16 == 0xFFF1) { // 写入 Wi-Fi 凭据
        if (ctxt->op == BLE_GATT_ACCESS_OP_WRITE_CHR) {
            // 配网报文含 Wi-Fi 凭据 + 工作台 wss 地址/路径/设备令牌, 需要足够缓冲
            char rx_buf[384] = {0};
            uint16_t len = OS_MBUF_PKTLEN(ctxt->om);
            if (len >= sizeof(rx_buf)) len = sizeof(rx_buf) - 1;
            ble_hs_mbuf_to_flat(ctxt->om, rx_buf, len, NULL);
            rx_buf[len] = '\0';

            ESP_LOGI(TAG, "收到手机下发的 Wi-Fi 凭证报文: %s", rx_buf);
            char ssid[34] = {0};
            char pwd[66] = {0};
            parse_credentials(rx_buf, ssid, sizeof(ssid), pwd, sizeof(pwd));

            // 同一报文内可附带工作台连接参数 (wss 地址/路径/设备令牌), 供卡片建立真实桥接通道
            char host[64] = {0};
            char path[48] = {0};
            char token[64] = {0};
            uint16_t port = s_server_port;
            bool tls = s_server_tls;
            parse_server_info(rx_buf, host, sizeof(host), &port, &tls,
                              path, sizeof(path), token, sizeof(token));
            if (host[0]) {
                strncpy(s_server_host, host, sizeof(s_server_host) - 1);
                s_server_host[sizeof(s_server_host) - 1] = '\0';
                s_server_port = port;
                s_server_tls = tls;
                if (path[0]) {
                    strncpy(s_server_path, path, sizeof(s_server_path) - 1);
                    s_server_path[sizeof(s_server_path) - 1] = '\0';
                } else {
                    snprintf(s_server_path, sizeof(s_server_path), "%s", "/ws/card");
                }
                strncpy(s_server_token, token, sizeof(s_server_token) - 1);
                s_server_token[sizeof(s_server_token) - 1] = '\0';
                ESP_LOGI(TAG, "已接收工作台地址: %s://%s:%u%s (令牌:%s)",
                         s_server_tls ? "wss" : "ws", s_server_host, (unsigned)s_server_port,
                         s_server_path, s_server_token[0] ? "有" : "无");
            }

            if (strlen(ssid) > 0) {
                // 只排队, 不在这里做 Wi-Fi 初始化: 本回调运行在 NimBLE host 任务上,
                // 该任务栈默认仅 4096 字节, 直接做 WiFi 初始化会栈溢出(见 prov_task 说明)。
                ESP_LOGI(TAG, "凭据已收到并入队: SSID=%s (密码长度=%u)", ssid,
                         (unsigned)strlen(pwd));
                prov_enqueue(ssid, pwd);
            } else {
                ESP_LOGW(TAG, "报文里没有解析出 SSID, 手机下发的原文: %s", rx_buf);
            }
            return 0;
        }
    } else if (uuid16 == 0xFFF2) { // 读取当前状态
        if (ctxt->op == BLE_GATT_ACCESS_OP_READ_CHR) {
            char rsp[96];
            snprintf(rsp, sizeof(rsp), "{\"status\":%d,\"ip\":\"%s\",\"ssid\":\"%s\"}",
                     (int)s_state, s_ip_str, s_cur_ssid);
            os_mbuf_append(ctxt->om, rsp, strlen(rsp));
            return 0;
        }
    }
    return BLE_ATT_ERR_UNLIKELY;
}

// 注册 GATT 服务定义
static const struct ble_gatt_svc_def gatt_svr_svcs[] = {
    {
        .type = BLE_GATT_SVC_TYPE_PRIMARY,
        .uuid = &gatt_svr_svc_uuid.u,
        .characteristics = (struct ble_gatt_chr_def[]) {
            {
                .uuid = &gatt_svr_chr_write_uuid.u,
                .access_cb = gatt_svr_chr_access,
                .flags = BLE_GATT_CHR_F_WRITE | BLE_GATT_CHR_F_WRITE_NO_RSP,
            },
            {
                .uuid = &gatt_svr_chr_notify_uuid.u,
                .access_cb = gatt_svr_chr_access,
                .flags = BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_NOTIFY,
                .val_handle = &s_notify_val_handle,
            },
            { 0 }
        },
    },
    { 0 }
};

static int ble_gap_event(struct ble_gap_event *event, void *arg) {
    switch (event->type) {
    case BLE_GAP_EVENT_CONNECT:
        if (event->connect.status == 0) {
            s_conn_handle = event->connect.conn_handle;
            ESP_LOGI(TAG, "手机已通过 BLE 成功连接胸卡 (conn_handle=%d)", s_conn_handle);
        } else {
            s_conn_handle = BLE_HS_CONN_HANDLE_NONE;
            ble_prov_advertise();
        }
        break;
    case BLE_GAP_EVENT_DISCONNECT:
        ESP_LOGI(TAG, "手机 BLE 断开连接，重新开启广播...");
        s_conn_handle = BLE_HS_CONN_HANDLE_NONE;
        ble_prov_advertise();
        break;
    case BLE_GAP_EVENT_ADV_COMPLETE:
        ble_prov_advertise();
        break;
    default:
        break;
    }
    return 0;
}

static int ble_prov_advertise(void) {
    // BLE 栈已释放(配网完成后为桥接腾内存): 任何残留的广播重启请求都必须挡掉,
    // 否则会访问已 deinit 的协议栈。
    if (s_ble_released) return -1;

    struct ble_hs_adv_fields fields = { 0 };
    fields.flags = BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP;
    fields.name = (const uint8_t *)DEVICE_NAME;
    fields.name_len = strlen(DEVICE_NAME);
    fields.name_is_complete = 1;

    static const ble_uuid16_t uuids16[] = { BLE_UUID16_INIT(0xFFF0) };
    fields.uuids16 = uuids16;
    fields.num_uuids16 = 1;
    fields.uuids16_is_complete = 1;

    int rc = ble_gap_adv_set_fields(&fields);
    if (rc != 0) {
        ESP_LOGE(TAG, "设置 BLE 广播字段失败: %d", rc);
        return rc;
    }

    struct ble_gap_adv_params params = { 0 };
    params.conn_mode = BLE_GAP_CONN_MODE_UND; // 可连接模式 (Connectable)
    params.disc_mode = BLE_GAP_DISC_MODE_GEN;
    rc = ble_gap_adv_start(s_addr_type, NULL, BLE_HS_FOREVER, &params, ble_gap_event, NULL);
    if (rc == 0) {
        ESP_LOGI(TAG, "BLE 配网广播已开启: %s (Service 0xFFF0)", DEVICE_NAME);
    }
    return rc;
}

static void on_sync(void) {
    ble_hs_util_ensure_addr(0);
    ble_hs_id_infer_auto(0, &s_addr_type);
    ble_prov_advertise();
}

static void host_task(void *arg) {
    (void)arg;
    nimble_port_run();
    // 先通知等待者, 再销毁任务: ble_teardown() 靠这个信号确认 host 任务已退出,
    // 否则 nimble_port_deinit() 可能释放仍在使用的内存。
    if (s_host_stopped) xSemaphoreGive(s_host_stopped);
    nimble_port_freertos_deinit();
}

esp_err_t ble_prov_start(void) {
    if (s_ble_started) return ESP_OK;

    demo_radio_nvs_prepare();
    demo_radio_network_prepare();

    // 0. 先建配网任务与队列: 后面所有 Wi-Fi 初始化都只能在它自己的栈上做
    //    (NimBLE host 任务栈只有 4096 字节, 装不下 WiFi 初始化的开销)。
    if (!s_prov_q) {
        s_prov_q = xQueueCreate(1, sizeof(prov_req_t));
    }
    if (!s_host_stopped) {
        // ble_teardown() 用它确认 NimBLE host 任务已退出
        s_host_stopped = xSemaphoreCreateBinary();
    }
    if (s_prov_q && !s_prov_task) {
        // 6144 字节: esp_netif_create_default_wifi_sta + esp_wifi_init + 事件注册
        // 这一套的实测栈开销在 2KB 上下, 留足冗余。
        if (xTaskCreate(prov_task, "prov_wifi", 6144, NULL, 5, &s_prov_task) != pdPASS) {
            ESP_LOGE(TAG, "配网任务创建失败");
            s_prov_task = NULL;
        }
    }
    if (!s_prov_q || !s_prov_task) {
        // 没有配网任务 = 手机写入凭证后谁也不会去连网, 必须硬失败而不是静默失灵
        ESP_LOGE(TAG, "配网任务/队列未就绪, 配网不可用");
        return ESP_FAIL;
    }

    // 1. 先把 Wi-Fi 驱动初始化好, 再起 NimBLE。
    //    ★ 顺序很重要: 这里是整个启动过程中堆最充裕的时刻。实测在配网时才懒加载
    //    Wi-Fi(即放在 NimBLE + LVGL + TLS WebSocket 客户端都起来之后), esp_wifi_init
    //    会失败, 而失败后的 esp_wifi_start() 只回一个 ESP_ERR_WIFI_NOT_INIT, 现场表现
    //    就是"手机显示凭证已写入, 但胸卡永远不联网"。
    //    失败不 return: BLE 配网仍需继续工作, 以便把真实失败原因回报给手机。
    if (wifi_driver_init_once() != ESP_OK) {
        ESP_LOGE(TAG, "Wi-Fi 驱动初始化失败 —— 配网将无法连接网络, 请对照上面的错误码与堆数据");
    }

    // 2. 初始化 NimBLE 栈并注册配网 GATT 服务
    nimble_port_init();
    ble_svc_gap_init();
    ble_svc_gatt_init();
    ble_svc_gap_device_name_set(DEVICE_NAME);

    ble_gatts_count_cfg(gatt_svr_svcs);
    ble_gatts_add_svcs(gatt_svr_svcs);

    ble_hs_cfg.sync_cb = on_sync;
    nimble_port_freertos_init(host_task);
    s_ble_started = true;

    // 2. 先恢复历史工作台地址, 再检查 NVS 中是否有保存的 Wi-Fi，有则自动后台自连
    load_server_from_nvs();

    char saved_ssid[34] = {0};
    char saved_pwd[66] = {0};
    if (load_wifi_from_nvs(saved_ssid, sizeof(saved_ssid), saved_pwd, sizeof(saved_pwd))) {
        ESP_LOGI(TAG, "从 NVS 恢复历史 Wi-Fi 凭证: SSID=%s，尝试后台自动连接...", saved_ssid);
        prov_enqueue(saved_ssid, saved_pwd);
    } else {
        ESP_LOGI(TAG, "NVS 中暂无已保存 Wi-Fi，等待手机 BLE/NFC 配网...");
    }

    return ESP_OK;
}

bool ble_prov_is_wifi_connected(void) {
    return (s_state == BLE_PROV_STATE_CONNECTED);
}

bool ble_prov_owns_wifi(void) {
    return s_wifi_init;
}

ble_prov_state_t ble_prov_get_state(void) {
    return s_state;
}

esp_err_t ble_prov_get_ip_str(char *buf, size_t max_len) {
    if (!buf || max_len == 0) return ESP_ERR_INVALID_ARG;
    if (s_state == BLE_PROV_STATE_CONNECTED && strlen(s_ip_str) > 0) {
        strncpy(buf, s_ip_str, max_len - 1);
        buf[max_len - 1] = '\0';
        return ESP_OK;
    }
    return ESP_ERR_NOT_FOUND;
}

esp_err_t ble_prov_get_ssid_str(char *buf, size_t max_len) {
    if (!buf || max_len == 0) return ESP_ERR_INVALID_ARG;
    if (strlen(s_cur_ssid) > 0) {
        strncpy(buf, s_cur_ssid, max_len - 1);
        buf[max_len - 1] = '\0';
        return ESP_OK;
    }
    return ESP_ERR_NOT_FOUND;
}

bool ble_prov_has_server(void) {
    return (s_server_host[0] != '\0' && s_server_port != 0);
}

esp_err_t ble_prov_get_server_host(char *buf, size_t max_len) {
    if (!buf || max_len == 0) return ESP_ERR_INVALID_ARG;
    if (!ble_prov_has_server()) {
        buf[0] = '\0';
        return ESP_ERR_NOT_FOUND;
    }
    strncpy(buf, s_server_host, max_len - 1);
    buf[max_len - 1] = '\0';
    return ESP_OK;
}

uint16_t ble_prov_get_server_port(void) {
    return s_server_port;
}

bool ble_prov_get_server_tls(void) {
    return s_server_tls;
}

esp_err_t ble_prov_get_server_path(char *buf, size_t max_len) {
    if (!buf || max_len == 0) return ESP_ERR_INVALID_ARG;
    const char *src = (s_server_path[0] != '\0') ? s_server_path : "/ws/card";
    strncpy(buf, src, max_len - 1);
    buf[max_len - 1] = '\0';
    return ESP_OK;
}

esp_err_t ble_prov_get_server_token(char *buf, size_t max_len) {
    if (!buf || max_len == 0) return ESP_ERR_INVALID_ARG;
    strncpy(buf, s_server_token, max_len - 1);
    buf[max_len - 1] = '\0';
    return ESP_OK;
}
