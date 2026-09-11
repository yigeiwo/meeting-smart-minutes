// main/ble_prov.c —— 飞书胸卡 BLE 蓝牙配网监听服务与 Wi-Fi 管理实现
#include "ble_prov.h"
#include "demo_radio.h"

#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "nvs_flash.h"
#include "nvs.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
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

// Wi-Fi 事件处理
static void wifi_event_handler(void *arg, esp_event_base_t event_base,
                               int32_t event_id, void *event_data) {
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        ESP_LOGI(TAG, "Wi-Fi STA 启动，尝试连接 %s...", s_cur_ssid);
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        if (s_state == BLE_PROV_STATE_CONNECTING) {
            if (s_retry_cnt < 5) {
                s_retry_cnt++;
                ESP_LOGW(TAG, "Wi-Fi 连接重试 (%d/5)...", s_retry_cnt);
                esp_wifi_connect();
            } else {
                s_state = BLE_PROV_STATE_FAILED;
                ESP_LOGE(TAG, "Wi-Fi 连接失败，请检查密码或信号");
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
    }
}

// 启动 Wi-Fi 连接流程
static esp_err_t do_connect_wifi(const char *ssid, const char *pwd) {
    if (!ssid || strlen(ssid) == 0) return ESP_ERR_INVALID_ARG;

    s_state = BLE_PROV_STATE_CONNECTING;
    s_retry_cnt = 0;
    strncpy(s_cur_ssid, ssid, sizeof(s_cur_ssid) - 1);
    strncpy(s_cur_pwd, pwd, sizeof(s_cur_pwd) - 1);

    char notify[64];
    snprintf(notify, sizeof(notify), "{\"status\":1,\"msg\":\"Connecting to %s...\"}", ssid);
    send_notify_msg(notify);

    if (!s_wifi_init) {
        s_sta_netif = esp_netif_create_default_wifi_sta();
        wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
        esp_wifi_init(&cfg);
        esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, NULL);
        esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL, NULL);
        esp_wifi_set_mode(WIFI_MODE_STA);
        s_wifi_init = true;
    }

    wifi_config_t wifi_cfg = { 0 };
    strncpy((char *)wifi_cfg.sta.ssid, ssid, sizeof(wifi_cfg.sta.ssid) - 1);
    strncpy((char *)wifi_cfg.sta.password, pwd, sizeof(wifi_cfg.sta.password) - 1);
    wifi_cfg.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;

    esp_wifi_stop();
    esp_wifi_set_config(WIFI_IF_STA, &wifi_cfg);
    return esp_wifi_start();
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
                ESP_LOGI(TAG, "准备连接 Wi-Fi: SSID=%s", ssid);
                do_connect_wifi(ssid, pwd);
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
    nimble_port_run();
    nimble_port_freertos_deinit();
}

esp_err_t ble_prov_start(void) {
    if (s_ble_started) return ESP_OK;

    demo_radio_nvs_prepare();
    demo_radio_network_prepare();

    // 1. 初始化 NimBLE 栈并注册配网 GATT 服务
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
        do_connect_wifi(saved_ssid, saved_pwd);
    } else {
        ESP_LOGI(TAG, "NVS 中暂无已保存 Wi-Fi，等待手机 BLE/NFC 配网...");
    }

    return ESP_OK;
}

bool ble_prov_is_wifi_connected(void) {
    return (s_state == BLE_PROV_STATE_CONNECTED);
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
