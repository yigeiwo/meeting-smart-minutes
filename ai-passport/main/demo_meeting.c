// main/demo_meeting.c - 飞书会议智能妙记专属胸卡固件 (100% 中文原生字库)
#include "demo_meeting.h"
#include "bsp_audio.h"
#include "bsp_display.h"
#include "bsp_battery.h"
#include "ui_pixel.h"
#include "lvgl.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

static const char *TAG = "demo_meeting";

// 引用原生中文高质量点阵矢量字库 (14px 黑体)
extern const lv_font_t font_chinese_14;

#define SAMPLE_RATE     16000
#define CHUNK_SAMPLES   512

typedef enum {
    MEETING_IDLE = 0,
    MEETING_RECORDING,
    MEETING_PROCESSING,
    MEETING_COMPLETED
} meeting_state_t;

typedef enum {
    VIEW_RECORDER = 0,
    VIEW_PAIRING_LINK,
    VIEW_TODOS,
    VIEW_COUNT
} meeting_view_t;

static lv_obj_t   *s_scr = NULL;
static lv_obj_t   *s_bat_label = NULL;
static lv_obj_t   *s_tab_label = NULL;
static lv_obj_t   *s_status_badge = NULL;
static lv_obj_t   *s_timer_label = NULL;
static lv_obj_t   *s_content_label = NULL;
static lv_obj_t   *s_hint_label = NULL;
static lv_obj_t   *s_mascot = NULL;
static lv_timer_t *s_tick_timer = NULL;

static TaskHandle_t s_rec_task = NULL;
static volatile meeting_state_t s_state = MEETING_IDLE;
static volatile meeting_view_t  s_cur_view = VIEW_RECORDER;
static uint32_t s_rec_seconds = 0;
static int s_todo_page = 0;
static char s_mac_str[24] = "4C:11:AE:30:DE:3C";

static const char *s_sample_todos[] = {
    "1. 落实技术架构方案评审决议",
    "2. 部署飞书智能妙记云端桥接服务",
    "3. 同步会议待办至飞书多维表格",
    "4. 完成胸卡硬件 NFC 与蓝牙测试"
};
#define TODO_COUNT 4

static void update_battery(void) {
    if (!s_bat_label) return;
    int soc = bsp_battery_soc();
    int mv  = bsp_battery_mv();

    if (soc < 0) {
        lv_label_set_text(s_bat_label, "电量: USB供电中");
        lv_obj_set_style_text_color(s_bat_label, lv_color_hex(0x0284C7), 0);
    } else {
        lv_label_set_text_fmt(s_bat_label, "电量: %d%% (%dmV)", soc, mv > 0 ? mv : 4150);
        if (soc < 20) {
            lv_obj_set_style_text_color(s_bat_label, lv_color_hex(0xDC2626), 0);
        } else {
            lv_obj_set_style_text_color(s_bat_label, lv_color_hex(0x16A34A), 0);
        }
    }
}

static void update_ui(void) {
    if (!bsp_lvgl_lock(500)) return;
    if (!s_scr) {
        bsp_lvgl_unlock();
        return;
    }

    update_battery();

    // 顶部当前看板标签
    if (s_tab_label) {
        if (s_cur_view == VIEW_RECORDER) {
            lv_label_set_text(s_tab_label, "[1/3] 飞书会议录音与纪要");
        } else if (s_cur_view == VIEW_PAIRING_LINK) {
            lv_label_set_text(s_tab_label, "[2/3] 配对连接与网络看板");
        } else if (s_cur_view == VIEW_TODOS) {
            lv_label_set_text_fmt(s_tab_label, "[3/3] 待办事项落实 (%d/%d)", s_todo_page + 1, TODO_COUNT);
        }
    }

    if (s_cur_view == VIEW_RECORDER) {
        // 看板 1: 会议录音与纪要提炼
        if (s_state == MEETING_IDLE) {
            if (s_status_badge) {
                lv_label_set_text(s_status_badge, "[待命] 系统就绪");
                lv_obj_set_style_text_color(s_status_badge, lv_color_hex(0x059669), 0);
            }
            if (s_timer_label) lv_label_set_text(s_timer_label, "00:00:00");
            if (s_content_label) {
                lv_label_set_text(s_content_label,
                    "飞书会议智能妙记\n"
                    "请按 OK 键开始会议录音\n"
                    "音频实时转写与 AI 总结");
            }
            if (s_hint_label) lv_label_set_text(s_hint_label, "OK: 开始录音   上下键: 翻页");
        } else if (s_state == MEETING_RECORDING) {
            if (s_status_badge) {
                lv_label_set_text(s_status_badge, "[录音] 正在采集音频");
                lv_obj_set_style_text_color(s_status_badge, lv_color_hex(0xDC2626), 0);
            }
            if (s_timer_label) {
                uint32_t m = s_rec_seconds / 60;
                uint32_t s = s_rec_seconds % 60;
                lv_label_set_text_fmt(s_timer_label, "00:%02lu:%02lu", (unsigned long)m, (unsigned long)s);
            }
            if (s_content_label) {
                lv_label_set_text(s_content_label,
                    "正在通过麦克风录音\n"
                    "实时采集会议讨论音频\n"
                    "流式推送至智能妙记网关...");
            }
            if (s_hint_label) lv_label_set_text(s_hint_label, "OK: 结束录音并生成纪要");
        } else if (s_state == MEETING_PROCESSING) {
            if (s_status_badge) {
                lv_label_set_text(s_status_badge, "[提炼] AI 智能处理中");
                lv_obj_set_style_text_color(s_status_badge, lv_color_hex(0xD97706), 0);
            }
            if (s_timer_label) lv_label_set_text(s_timer_label, "AI SYNC");
            if (s_content_label) {
                lv_label_set_text(s_content_label,
                    "AI 正在提炼核心决议...\n"
                    "生成结构化会议纪要\n"
                    "正在同步至飞书云文档...");
            }
            if (s_hint_label) lv_label_set_text(s_hint_label, "请稍候...");
        } else if (s_state == MEETING_COMPLETED) {
            if (s_status_badge) {
                lv_label_set_text(s_status_badge, "[完成] 纪要已同步");
                lv_obj_set_style_text_color(s_status_badge, lv_color_hex(0x2563EB), 0);
            }
            if (s_timer_label) lv_label_set_text(s_timer_label, "DONE");
            if (s_content_label) {
                lv_label_set_text(s_content_label,
                    "会议纪要已自动生成！\n"
                    "已推送到飞书与机器人群\n"
                    "按向下键查看待办清单");
            }
            if (s_hint_label) lv_label_set_text(s_hint_label, "OK: 重新录音   向下键: 待办");
        }
    } else if (s_cur_view == VIEW_PAIRING_LINK) {
        // 看板 2: 配对连接与网络服务器看板
        if (s_status_badge) {
            lv_label_set_text(s_status_badge, "[连接] 配对网络已就绪");
            lv_obj_set_style_text_color(s_status_badge, lv_color_hex(0x2563EB), 0);
        }
        if (s_timer_label) lv_label_set_text(s_timer_label, "PIN: 8826");
        if (s_content_label) {
            lv_label_set_text_fmt(s_content_label,
                "控制台: http://127.0.0.1:8000\n"
                "端口: TCP 5566 (硬件流)\n"
                "设备: %s\n"
                "状态: 在线 / 已连接", s_mac_str);
        }
        if (s_hint_label) lv_label_set_text(s_hint_label, "请在电脑浏览器打开控制台配对");
    } else if (s_cur_view == VIEW_TODOS) {
        // 看板 3: 待办事项落实
        if (s_status_badge) {
            lv_label_set_text(s_status_badge, "[待办] 核心行动项清单");
            lv_obj_set_style_text_color(s_status_badge, lv_color_hex(0x7C3AED), 0);
        }
        if (s_timer_label) lv_label_set_text(s_timer_label, "FEISHU TASK");
        if (s_content_label) {
            int idx = s_todo_page % TODO_COUNT;
            lv_label_set_text_fmt(s_content_label,
                "待办 %d/%d:\n%s", idx + 1, TODO_COUNT, s_sample_todos[idx]);
        }
        if (s_hint_label) lv_label_set_text(s_hint_label, "上下键: 切换待办   OK: 确认");
    }

    bsp_lvgl_unlock();
}

static void on_tick_timer(lv_timer_t *timer) {
    (void)timer;
    update_ui();
}

static void meeting_record_task(void *arg) {
    (void)arg;
    int16_t *chunk = malloc(CHUNK_SAMPLES * sizeof(int16_t));
    if (!chunk) {
        ESP_LOGE(TAG, "音频内存分配失败");
        vTaskDelete(NULL);
        return;
    }

    uint32_t ticks = 0;
    while (s_state == MEETING_RECORDING) {
        bsp_audio_read(chunk, CHUNK_SAMPLES * sizeof(int16_t));
        ticks++;
        if (ticks >= (SAMPLE_RATE / CHUNK_SAMPLES)) {
            ticks = 0;
            s_rec_seconds++;
            update_ui();
        }
    }

    free(chunk);
    vTaskDelete(NULL);
}

void demo_meeting_enter(void) {
    s_scr = ui_pixel_screen_create("FEISHU");

    // 读取芯片硬件 STA MAC 地址
    uint8_t mac[6] = {0};
    if (esp_read_mac(mac, ESP_MAC_WIFI_STA) == ESP_OK) {
        snprintf(s_mac_str, sizeof(s_mac_str), "%02X:%02X:%02X:%02X:%02X:%02X",
                 mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    }

    // 顶部电量显示 (使用原生中文 14px 字体)
    s_bat_label = lv_label_create(s_scr);
    lv_obj_set_style_text_font(s_bat_label, &font_chinese_14, 0);
    lv_obj_align(s_bat_label, LV_ALIGN_TOP_LEFT, 16, 26);
    update_battery();

    // 主内容面板卡片
    lv_obj_t *panel = ui_pixel_panel_create(s_scr, 16, 48, 208, 192, UI_PAPER);

    // 顶部当前看板标签
    s_tab_label = lv_label_create(panel);
    lv_obj_set_style_text_font(s_tab_label, &font_chinese_14, 0);
    lv_obj_set_style_text_color(s_tab_label, lv_color_hex(0x64748B), 0);
    lv_obj_align(s_tab_label, LV_ALIGN_TOP_MID, 0, 4);

    // 状态徽章
    s_status_badge = lv_label_create(panel);
    lv_obj_set_style_text_font(s_status_badge, &font_chinese_14, 0);
    lv_obj_set_style_text_color(s_status_badge, lv_color_hex(0x059669), 0);
    lv_obj_align(s_status_badge, LV_ALIGN_TOP_MID, 0, 24);

    // 计时器 / 配对码大字体标签 (使用 Montserrat 20 英文/数字)
    s_timer_label = lv_label_create(panel);
    lv_obj_set_style_text_font(s_timer_label, &lv_font_montserrat_20, 0);
    lv_obj_set_style_text_color(s_timer_label, lv_color_hex(UI_INK), 0);
    lv_obj_align(s_timer_label, LV_ALIGN_TOP_MID, 0, 46);

    // 核心内容说明 (使用原生中文 14px 字体，自动换行)
    s_content_label = lv_label_create(panel);
    lv_obj_set_style_text_font(s_content_label, &font_chinese_14, 0);
    lv_obj_set_style_text_color(s_content_label, lv_color_hex(UI_INK), 0);
    lv_obj_set_style_text_align(s_content_label, LV_TEXT_ALIGN_CENTER, 0);
    lv_label_set_long_mode(s_content_label, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(s_content_label, 196);
    lv_obj_align(s_content_label, LV_ALIGN_CENTER, 0, 24);

    // 底部按键提示 (使用原生中文 14px 字体)
    s_hint_label = lv_label_create(panel);
    lv_obj_set_style_text_font(s_hint_label, &font_chinese_14, 0);
    lv_obj_set_style_text_color(s_hint_label, lv_color_hex(0x64748B), 0);
    lv_obj_align(s_hint_label, LV_ALIGN_BOTTOM_MID, 0, -4);

    s_mascot = ui_pixel_mascot_create(s_scr, 101, 246);

    s_state = MEETING_IDLE;
    s_cur_view = VIEW_RECORDER;
    s_rec_seconds = 0;
    s_todo_page = 0;

    update_ui();

    s_tick_timer = lv_timer_create(on_tick_timer, 1000, NULL);
    lv_screen_load(s_scr);
}

void demo_meeting_exit(void) {
    if (s_tick_timer) {
        lv_timer_delete(s_tick_timer);
        s_tick_timer = NULL;
    }
    if (s_state == MEETING_RECORDING) {
        s_state = MEETING_IDLE;
    }
    if (s_rec_task) {
        vTaskDelete(s_rec_task);
        s_rec_task = NULL;
    }
    if (s_scr) {
        lv_obj_delete(s_scr);
        s_scr = NULL;
        s_bat_label = s_tab_label = s_status_badge = s_timer_label = s_content_label = s_hint_label = s_mascot = NULL;
    }
}

void demo_meeting_key(bsp_btn_t btn, bsp_btn_ev_t ev) {
    if (ev != BSP_BTN_CLICK) return;

    // 强力消抖与按键切换限速 (至少间隔 400ms)，彻底解决由于 ADC 浮空抖动导致的屏幕自激反复切换
    static uint32_t s_last_key_tick = 0;
    uint32_t now_tick = (uint32_t)pdTICKS_TO_MS(xTaskGetTickCount());
    if (now_tick - s_last_key_tick < 400) {
        return;
    }
    s_last_key_tick = now_tick;

    ESP_LOGI(TAG, "按键生效: btn=%d, 当前看板=%d", btn, s_cur_view);

    if (btn == BSP_BTN_OK) {
        ui_pixel_mascot_jump(s_mascot);
        if (s_cur_view == VIEW_RECORDER) {
            if (s_state == MEETING_IDLE || s_state == MEETING_COMPLETED) {
                s_state = MEETING_RECORDING;
                s_rec_seconds = 0;
                bsp_audio_set_format(SAMPLE_RATE, 16, 1);
                xTaskCreate(meeting_record_task, "rec_task", 4096, NULL, 5, &s_rec_task);
                update_ui();
            } else if (s_state == MEETING_RECORDING) {
                s_state = MEETING_PROCESSING;
                update_ui();
                s_state = MEETING_COMPLETED;
                update_ui();
            }
        } else if (s_cur_view == VIEW_PAIRING_LINK) {
            s_cur_view = VIEW_RECORDER;
            update_ui();
        } else if (s_cur_view == VIEW_TODOS) {
            s_todo_page = (s_todo_page + 1) % TODO_COUNT;
            update_ui();
        }
    } else if (btn == BSP_BTN_DOWN) {
        s_cur_view = (s_cur_view + 1) % VIEW_COUNT;
        update_ui();
    } else if (btn == BSP_BTN_UP) {
        if (s_cur_view > 0) s_cur_view--;
        else s_cur_view = VIEW_COUNT - 1;
        update_ui();
    }
}
