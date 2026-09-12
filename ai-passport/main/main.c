// main/main.c —— FoloToy AI Passport BSP 驱动参考示例:初始化 + 菜单 + 按键分发。
//
// 按键语义(全局统一):
//   上/下 短按   菜单中=移动选中项;演示页中=该页自定义
//   确定  短按   菜单中=进入选中项;演示页中=该页自定义
//   上键  长按/双击  演示页中=返回主功能菜单(由本文件统一拦截,与屏幕提示"长按上键 菜单"一致)
//   确定  长按   菜单中=直达飞书会议录音页
#include "bsp_i2c.h"
#include "bsp_display.h"
#include "bsp_button.h"
#include "bsp_audio.h"
#include "bsp_battery.h"
#include "bsp_pins.h"      // 错误日志里要打印 BSP_LCD_* 引脚号
#include "demo.h"
#include "ble_prov.h"
#include "card_link.h"
#include "ui_pixel.h"
#include "lvgl.h"
#include "esp_log.h"
#include "esp_sleep.h"

static const char *TAG = "main";

extern const lv_font_t font_chinese_14;

static const demo_entry_t DEMOS[] = {
    { "飞书会议", demo_meeting_enter, demo_meeting_exit, demo_meeting_key },
    { "按键测试", demo_button_enter,  demo_button_exit,  demo_button_key  },
    { "显示测试", demo_display_enter, demo_display_exit, demo_display_key },
    { "音频测试", demo_audio_enter,   demo_audio_exit,   demo_audio_key   },
    { "电池电量", demo_battery_enter, demo_battery_exit, demo_battery_key },
    { "无线网络", demo_wifi_enter,    demo_wifi_exit,    demo_wifi_key    },
    { "蓝牙广播", demo_ble_enter,     demo_ble_exit,     demo_ble_key     },
    { "低功耗",   demo_low_power_enter, demo_low_power_exit, demo_low_power_key },
};
#define DEMO_COUNT (sizeof(DEMOS) / sizeof(DEMOS[0]))

// 各外设初始化结果:失败的项在菜单里标 [不可用] 且不允许进入。
static bool s_ok[DEMO_COUNT];

static lv_obj_t *s_menu_scr;
static lv_obj_t *s_cards[DEMO_COUNT];
static lv_obj_t *s_rows[DEMO_COUNT];
static lv_obj_t *s_mascot;
static int  s_sel = 0;             // 当前选中项 (默认第1项: 飞书会议)
static int  s_active = -1;         // 当前所在演示页;-1 = 在菜单

static void menu_refresh(void) {
    for (size_t i = 0; i < DEMO_COUNT; i++) {
        lv_label_set_text_fmt(s_rows[i], "%s%s",
                              DEMOS[i].name,
                              s_ok[i] ? "" : " [不可用]");
        ui_pixel_set_selected(s_cards[i], (int)i == s_sel, s_ok[i]);
        lv_obj_set_style_text_color(s_rows[i],
            s_ok[i] ? lv_color_hex(UI_INK) : lv_color_hex(0x7A2020), 0);
    }
}

static void menu_build(void) {
    s_menu_scr = ui_pixel_screen_create("功能菜单");

    for (size_t i = 0; i < DEMO_COUNT; i++) {
        int x = 11 + (int)(i % 2) * 112;
        int y = 52 + (int)(i / 2) * 47;
        s_cards[i] = ui_pixel_panel_create(s_menu_scr, x, y, 102, 40, UI_PAPER);
        s_rows[i] = lv_label_create(s_cards[i]);
        lv_obj_set_style_text_font(s_rows[i], &font_chinese_14, 0);
        lv_obj_set_style_text_align(s_rows[i], LV_TEXT_ALIGN_CENTER, 0);
        lv_obj_center(s_rows[i]);
    }

    s_mascot = ui_pixel_mascot_create(s_menu_scr, 101, 242);

    // 底部操作提示
    lv_obj_t *hint = lv_label_create(s_menu_scr);
    lv_obj_set_style_text_font(hint, &font_chinese_14, 0);
    lv_obj_set_style_text_color(hint, lv_color_hex(0x334155), 0);
    lv_label_set_text(hint, "上下键 移动   OK 进入");
    lv_obj_align(hint, LV_ALIGN_BOTTOM_MID, 0, -8);

    menu_refresh();
    lv_screen_load(s_menu_scr);
}

static void enter_menu(void) {
    s_active = -1;
    s_sel = 0; // 默认选中【飞书会议】
    menu_build();
}

// 按键回调运行在 button 组件的任务里,操作 LVGL 必须加锁。
static void on_key(bsp_btn_t btn, bsp_btn_ev_t ev, void *user) {
    (void)user;

    // 每个按键事件都留痕。排查"某个键没反应"时,这条日志能直接把范围劈成两半:
    //   日志里没有这个键 -> 事件根本没产生(ADC 阈值不匹配, 或按键硬件未接通)
    //   日志里有但界面无反应 -> 事件到了, 问题在业务逻辑
    // ev: 0=按下 1=单击 2=双击 3=长按;  btn: 0=上 1=下 2=确定
    ESP_LOGI(TAG, "按键事件: btn=%d ev=%d (active=%d)", btn, ev, s_active);

    if (!bsp_lvgl_lock(500)) {
        // 以前这里是静默 return —— 丢事件却不留任何痕迹,排查时会被误判成"按键坏了"
        ESP_LOGW(TAG, "LVGL 加锁超时, 丢弃按键事件: btn=%d ev=%d", btn, ev);
        return;
    }
    if (s_active >= 0) {
        // 【返回菜单】主路径是上键长按/双击。这里额外把"确定键长按"也接成备用返回路径:
        // 一旦某个按键在硬件上失效(例如分压偏低被判成"无按键"),导航不会被彻底锁死,
        // 上键或确定键任一可用就能退出。确定键的短按/双击仍原样交给 demo 处理,不受影响。
        const int back_to_menu =
            (btn == BSP_BTN_UP && (ev == BSP_BTN_LONG || ev == BSP_BTN_DOUBLE)) ||
            (btn == BSP_BTN_OK && ev == BSP_BTN_LONG);
        if (back_to_menu) {
            ESP_LOGI(TAG, "返回主功能菜单: btn=%d, ev=%d", btn, ev);
            // 真实通知工作台: 卡片已返回功能菜单 (工作台同步切换为 MENU 画面)
            card_link_send_combo_menu();
            DEMOS[s_active].exit();
            enter_menu();
        } else {
            DEMOS[s_active].key(btn, ev);
        }
    } else {
        if (ev == BSP_BTN_CLICK) {
            if (btn == BSP_BTN_UP)   { s_sel = (s_sel + DEMO_COUNT - 1) % DEMO_COUNT; menu_refresh(); }
            if (btn == BSP_BTN_DOWN) { s_sel = (s_sel + 1) % DEMO_COUNT;              menu_refresh(); }
            if (btn == BSP_BTN_OK && s_ok[s_sel]) {
                s_active = s_sel;
                ui_pixel_mascot_jump(s_mascot);
                lv_obj_delete(s_menu_scr);
                s_menu_scr = NULL;
                s_mascot = NULL;
                DEMOS[s_active].enter();
            } else if (btn == BSP_BTN_UP || btn == BSP_BTN_DOWN) {
                ui_pixel_mascot_jump(s_mascot);
            }
        } else if (btn == BSP_BTN_OK && ev == BSP_BTN_LONG) {
            // 在菜单中长按确定键，直接返回飞书会议录音
            s_active = 0;
            lv_obj_delete(s_menu_scr);
            s_menu_scr = NULL;
            s_mascot = NULL;
            DEMOS[s_active].enter();
        }
    }
    bsp_lvgl_unlock();
}

void app_main(void) {
    ESP_LOGI(TAG, "FoloToy AI Passport BSP demo 启动");
    esp_sleep_wakeup_cause_t wakeup = esp_sleep_get_wakeup_cause();
    if (wakeup != ESP_SLEEP_WAKEUP_UNDEFINED) {
        ESP_LOGI(TAG, "休眠唤醒原因: %d", wakeup);
    }

    bsp_i2c_init();
    bsp_i2c_scan();

    // 屏幕是本 demo 的 UI 载体,失败就没有菜单可言
    if (bsp_display_init() != ESP_OK || !bsp_lvgl_init()) {
        ESP_LOGE(TAG, "显示/LVGL 初始化失败,demo 无法继续。"
                      "检查 SPI 接线(MOSI=%d SCLK=%d CS=%d DC=%d BL=%d)",
                 BSP_LCD_MOSI, BSP_LCD_SCLK, BSP_LCD_CS, BSP_LCD_DC, BSP_LCD_BL);
        return;
    }
    bsp_display_backlight(100);

    // 外设初始化与状态登记 (默认全可用)
    for (size_t i = 0; i < DEMO_COUNT; i++) {
        s_ok[i] = true;
    }
    s_ok[1] = (bsp_button_init(on_key, NULL) == ESP_OK);
    s_ok[3] = (bsp_audio_init() == ESP_OK);
    s_ok[4] = (bsp_battery_init() == ESP_OK);

    if (bsp_lvgl_lock(1000)) { 
        // 默认直接开机进入飞书会议智能页面
        s_active = 0;
        DEMOS[s_active].enter();
        bsp_lvgl_unlock(); 
    }

    // 启动后台 BLE 蓝牙配网监听服务 (广播名称 FoloPassport, Service 0xFFF0, 自动恢复 NVS 历史网络)
    ble_prov_start();

    // 启动真实 TCP/NDJSON 桥接客户端: 连上电脑工作台的 5566 端口,
    // 上行录音音频/电量/状态, 下行接收真实会议纪要与反向指令
    card_link_start();

    ESP_LOGI(TAG, "就绪: 飞书会议卡片直达模式与 BLE 配网监听已激活");
}
