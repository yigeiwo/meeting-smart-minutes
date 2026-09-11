// main/ble_prov.h —— 飞书胸卡 BLE 蓝牙配网监听服务与 Wi-Fi 管理
#pragma once

#include "esp_err.h"
#include <stdbool.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    BLE_PROV_STATE_IDLE = 0,       // 蓝牙广播中，等待配网凭据
    BLE_PROV_STATE_CONNECTING,     // 正在尝试连接 Wi-Fi 路由器
    BLE_PROV_STATE_CONNECTED,      // 已成功连接 Wi-Fi 并获取局域网 IP
    BLE_PROV_STATE_FAILED,         // 连接失败（密码错误或信号差）
} ble_prov_state_t;

/**
 * @brief 初始化并启动后台 BLE 配网服务及 Wi-Fi STA 管理
 */
esp_err_t ble_prov_start(void);

/**
 * @brief 当前是否已成功连接到 Wi-Fi 局域网
 */
bool ble_prov_is_wifi_connected(void);

/**
 * @brief 获取当前配网与网络状态
 */
ble_prov_state_t ble_prov_get_state(void);

/**
 * @brief 获取当前设备的局域网真实 IP 字符串 (如 "192.168.0.105")
 */
esp_err_t ble_prov_get_ip_str(char *buf, size_t max_len);

/**
 * @brief 获取当前连接或保存的 Wi-Fi SSID
 */
esp_err_t ble_prov_get_ssid_str(char *buf, size_t max_len);

#ifdef __cplusplus
}
#endif
