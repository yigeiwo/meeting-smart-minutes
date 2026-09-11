// main/demo_meeting.c - Feishu Meeting Smart Minutes Suite
// Full support for:
// 1. Crystal-clear typography without font glyph corruption
// 2. Real-time battery indicator (CW2017 SOC% & Voltage)
// 3. Pairing Link & Web Console Connection Board
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
static lv_obj_t   *s_bat_bar = NULL;
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
    "1. Complete Q3 Product Architecture Review",
    "2. Deploy Feishu Smart Minutes Bridge to AWS",
    "3. Sync Meeting Action Items to Feishu Base",
    "4. AI Passport Hardware NFC & BT Testing"
};
#define TODO_COUNT 4

static void update_battery(void) {
    if (!s_bat_label) return;
    int soc = bsp_battery_soc();
    int mv  = bsp_battery_mv();

    if (soc < 0) {
        lv_label_set_text(s_bat_label, "BAT: USB PWR");
        lv_obj_set_style_text_color(s_bat_label, lv_color_hex(0x38BDF8), 0);
    } else {
        lv_label_set_text_fmt(s_bat_label, "BAT: %d%% (%dmV)", soc, mv > 0 ? mv : 4150);
        if (soc < 20) {
            lv_obj_set_style_text_color(s_bat_label, lv_color_hex(0xEF4444), 0);
        } else {
            lv_obj_set_style_text_color(s_bat_label, lv_color_hex(0x10B981), 0);
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

    // Tab Header
    if (s_tab_label) {
        if (s_cur_view == VIEW_RECORDER) {
            lv_label_set_text(s_tab_label, "[1/3] MEETING RECORDER");
        } else if (s_cur_view == VIEW_PAIRING_LINK) {
            lv_label_set_text(s_tab_label, "[2/3] PAIRING & SERVER LINK");
        } else if (s_cur_view == VIEW_TODOS) {
            lv_label_set_text_fmt(s_tab_label, "[3/3] ACTION TODOS (%d/%d)", s_todo_page + 1, TODO_COUNT);
        }
    }

    if (s_cur_view == VIEW_RECORDER) {
        // Mode 1: Meeting Recorder
        if (s_state == MEETING_IDLE) {
            if (s_status_badge) {
                lv_label_set_text(s_status_badge, "● IDLE [READY]");
                lv_obj_set_style_text_color(s_status_badge, lv_color_hex(0x059669), 0);
            }
            if (s_timer_label) lv_label_set_text(s_timer_label, "00:00:00");
            if (s_content_label) {
                lv_label_set_text(s_content_label,
                    "Feishu Smart Minutes\n"
                    "Press OK to Record Voice\n"
                    "Audio -> Realtime AI Minutes");
            }
            if (s_hint_label) lv_label_set_text(s_hint_label, "OK: Start Rec   UP/DN: Switch Tab");
        } else if (s_state == MEETING_RECORDING) {
            if (s_status_badge) {
                lv_label_set_text(s_status_badge, "● REC [LIVE]");
                lv_obj_set_style_text_color(s_status_badge, lv_color_hex(0xDC2626), 0);
            }
            if (s_timer_label) {
                uint32_t m = s_rec_seconds / 60;
                uint32_t s = s_rec_seconds % 60;
                lv_label_set_text_fmt(s_timer_label, "00:%02lu:%02lu", (unsigned long)m, (unsigned long)s);
            }
            if (s_content_label) {
                lv_label_set_text(s_content_label,
                    "Recording via ES8311 I2S\n"
                    "Capturing Discussion Audio\n"
                    "Streaming to Local Gateway...");
            }
            if (s_hint_label) lv_label_set_text(s_hint_label, "OK: Finish & Summarize");
        } else if (s_state == MEETING_PROCESSING) {
            if (s_status_badge) {
                lv_label_set_text(s_status_badge, "⚡ AI PROCESSING...");
                lv_obj_set_style_text_color(s_status_badge, lv_color_hex(0xD97706), 0);
            }
            if (s_content_label) {
                lv_label_set_text(s_content_label,
                    "Extracting Key Decisions...\n"
                    "Generating Meeting Minutes\n"
                    "Syncing to Feishu Docs...");
            }
            if (s_hint_label) lv_label_set_text(s_hint_label, "Please wait...");
        } else if (s_state == MEETING_COMPLETED) {
            if (s_status_badge) {
                lv_label_set_text(s_status_badge, "✔ SUMMARIZED");
                lv_obj_set_style_text_color(s_status_badge, lv_color_hex(0x2563EB), 0);
            }
            if (s_content_label) {
                lv_label_set_text(s_content_label,
                    "Meeting Minutes Generated!\n"
                    "Pushed to Feishu / DingTalk\n"
                    "Press DN for Action Items");
            }
            if (s_hint_label) lv_label_set_text(s_hint_label, "OK: New Rec   DN: View Todos");
        }
    } else if (s_cur_view == VIEW_PAIRING_LINK) {
        // Mode 2: Pairing & Connection Link
        if (s_status_badge) {
            lv_label_set_text(s_status_badge, "🔗 PAIRING LINK & WEB");
            lv_obj_set_style_text_color(s_status_badge, lv_color_hex(0x2563EB), 0);
        }
        if (s_timer_label) lv_label_set_text(s_timer_label, "PIN: 8826");
        if (s_content_label) {
            lv_label_set_text_fmt(s_content_label,
                "Web: http://127.0.0.1:8000\n"
                "TCP: 5566 (NDJSON Stream)\n"
                "MAC: %s\n"
                "Status: CONNECTED / READY", s_mac_str);
        }
        if (s_hint_label) lv_label_set_text(s_hint_label, "Open Web Browser to Pair");
    } else if (s_cur_view == VIEW_TODOS) {
        // Mode 3: Action Todos
        if (s_status_badge) {
            lv_label_set_text(s_status_badge, "📋 ACTION TODOS");
            lv_obj_set_style_text_color(s_status_badge, lv_color_hex(0x7C3AED), 0);
        }
        if (s_timer_label) lv_label_set_text(s_timer_label, "Feishu Task Sync");
        if (s_content_label) {
            int idx = s_todo_page % TODO_COUNT;
            lv_label_set_text_fmt(s_content_label,
                "Item %d of %d:\n%s", idx + 1, TODO_COUNT, s_sample_todos[idx]);
        }
        if (s_hint_label) lv_label_set_text(s_hint_label, "UP/DN: Next Item   OK: Done");
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
        ESP_LOGE(TAG, "Audio buffer alloc failed");
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

    // Retrieve real MAC address
    uint8_t mac[6] = {0};
    if (esp_read_mac(mac, ESP_MAC_WIFI_STA) == ESP_OK) {
        snprintf(s_mac_str, sizeof(s_mac_str), "%02X:%02X:%02X:%02X:%02X:%02X",
                 mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    }

    // Top Battery bar
    s_bat_label = lv_label_create(s_scr);
    lv_obj_set_style_text_font(s_bat_label, &lv_font_montserrat_14, 0);
    lv_obj_align(s_bat_label, LV_ALIGN_TOP_LEFT, 16, 26);
    update_battery();

    // Main Card Panel
    lv_obj_t *panel = ui_pixel_panel_create(s_scr, 16, 48, 208, 192, UI_PAPER);

    s_tab_label = lv_label_create(panel);
    lv_obj_set_style_text_font(s_tab_label, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(s_tab_label, lv_color_hex(0x64748B), 0);
    lv_obj_align(s_tab_label, LV_ALIGN_TOP_MID, 0, 4);

    s_status_badge = lv_label_create(panel);
    lv_obj_set_style_text_font(s_status_badge, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(s_status_badge, lv_color_hex(0x059669), 0);
    lv_obj_align(s_status_badge, LV_ALIGN_TOP_MID, 0, 22);

    s_timer_label = lv_label_create(panel);
    lv_obj_set_style_text_font(s_timer_label, &lv_font_montserrat_20, 0);
    lv_obj_set_style_text_color(s_timer_label, lv_color_hex(UI_INK), 0);
    lv_obj_align(s_timer_label, LV_ALIGN_TOP_MID, 0, 44);

    s_content_label = lv_label_create(panel);
    lv_obj_set_style_text_font(s_content_label, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(s_content_label, lv_color_hex(UI_INK), 0);
    lv_obj_set_style_text_align(s_content_label, LV_TEXT_ALIGN_CENTER, 0);
    lv_label_set_long_mode(s_content_label, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(s_content_label, 192);
    lv_obj_align(s_content_label, LV_ALIGN_CENTER, 0, 24);

    s_hint_label = lv_label_create(panel);
    lv_obj_set_style_text_font(s_hint_label, &lv_font_montserrat_14, 0);
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
            // Switch back to recorder
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
