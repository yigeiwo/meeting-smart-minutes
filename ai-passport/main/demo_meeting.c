// main/demo_meeting.c ?? ??????????
// ?? FoloToy AI Passport BSP ??? LVGL ??
#include "demo_meeting.h"
#include "bsp_audio.h"
#include "bsp_display.h"
#include "ui_pixel.h"
#include "lvgl.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
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

static lv_obj_t *s_scr;
static lv_obj_t *s_status_badge;
static lv_obj_t *s_timer_label;
static lv_obj_t *s_content_label;
static lv_obj_t *s_hint_label;
static lv_obj_t *s_mascot;

static TaskHandle_t s_rec_task = NULL;
static volatile meeting_state_t s_state = MEETING_IDLE;
static uint32_t s_rec_seconds = 0;
static int s_todo_page = 0;

static const char *s_sample_todos[] = {
    "[????] ????????????",
    "[????] ????????????",
    "[?????] ??????????",
    "[??] ?? AI Passport ??????",
};
#define TODO_COUNT 4

static void update_ui(void) {
    if (!bsp_lvgl_lock(500)) return;
    if (!s_scr) {
        bsp_lvgl_unlock();
        return;
    }

    if (s_state == MEETING_IDLE) {
        if (s_status_badge) lv_label_set_text(s_status_badge, "IDLE ??");
        if (s_timer_label) lv_label_set_text(s_timer_label, "00:00:00");
        if (s_content_label) lv_label_set_text(s_content_label, "? OK ????????\n?????????");
        if (s_hint_label) lv_label_set_text(s_hint_label, "OK: ????  UP/DN: ??");
    } else if (s_state == MEETING_RECORDING) {
        if (s_status_badge) lv_label_set_text(s_status_badge, "? REC ???");
        if (s_timer_label) {
            uint32_t m = s_rec_seconds / 60;
            uint32_t s = s_rec_seconds % 60;
            lv_label_set_text_fmt(s_timer_label, "00:%02lu:%02lu", (unsigned long)m, (unsigned long)s);
        }
        if (s_content_label) lv_label_set_text(s_content_label, "??? ES8311 ??????\n???????????");
        if (s_hint_label) lv_label_set_text(s_hint_label, "OK: ?????????");
    } else if (s_state == MEETING_PROCESSING) {
        if (s_status_badge) lv_label_set_text(s_status_badge, "? AI ???");
        if (s_content_label) lv_label_set_text(s_content_label, "???????????\n???????????...");
        if (s_hint_label) lv_label_set_text(s_hint_label, "???...");
    } else if (s_state == MEETING_COMPLETED) {
        if (s_status_badge) lv_label_set_text(s_status_badge, "? ?????");
        if (s_content_label) {
            int idx = s_todo_page % TODO_COUNT;
            lv_label_set_text_fmt(s_content_label, "??? %d/%d?\n%s", idx + 1, TODO_COUNT, s_sample_todos[idx]);
        }
        if (s_hint_label) lv_label_set_text(s_hint_label, "UP/DN: ????  OK: ??");
    }

    bsp_lvgl_unlock();
}

static void meeting_record_task(void *arg) {
    (void)arg;
    int16_t *chunk = malloc(CHUNK_SAMPLES * sizeof(int16_t));
    if (!chunk) {
        ESP_LOGE(TAG, "??????????");
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

    lv_obj_t *panel = ui_pixel_panel_create(s_scr, 18, 52, 204, 180, UI_PAPER);

    s_status_badge = lv_label_create(panel);
    lv_obj_set_style_text_color(s_status_badge, lv_color_hex(0x059669), 0);
    lv_obj_set_style_text_align(s_status_badge, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_align(s_status_badge, LV_ALIGN_TOP_MID, 0, 8);

    s_timer_label = lv_label_create(panel);
    lv_obj_set_style_text_color(s_timer_label, lv_color_hex(UI_INK), 0);
    lv_obj_set_style_text_align(s_timer_label, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_align(s_timer_label, LV_ALIGN_TOP_MID, 0, 32);

    s_content_label = lv_label_create(panel);
    lv_obj_set_style_text_color(s_content_label, lv_color_hex(UI_INK), 0);
    lv_obj_set_style_text_align(s_content_label, LV_TEXT_ALIGN_CENTER, 0);
    lv_label_set_long_mode(s_content_label, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(s_content_label, 180);
    lv_obj_align(s_content_label, LV_ALIGN_CENTER, 0, 15);

    s_hint_label = lv_label_create(panel);
    lv_obj_set_style_text_color(s_hint_label, lv_color_hex(0x64748B), 0);
    lv_obj_set_style_text_align(s_hint_label, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_align(s_hint_label, LV_ALIGN_BOTTOM_MID, 0, -8);

    s_mascot = ui_pixel_mascot_create(s_scr, 101, 240);

    s_state = MEETING_IDLE;
    s_rec_seconds = 0;
    s_todo_page = 0;
    update_ui();

    lv_screen_load(s_scr);
}

void demo_meeting_exit(void) {
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
        s_status_badge = s_timer_label = s_content_label = s_hint_label = s_mascot = NULL;
    }
}

void demo_meeting_key(bsp_btn_t btn, bsp_btn_ev_t ev) {
    if (ev != BSP_BTN_CLICK) return;

    if (btn == BSP_BTN_OK) {
        ui_pixel_mascot_jump(s_mascot);
        if (s_state == MEETING_IDLE || s_state == MEETING_COMPLETED) {
            s_state = MEETING_RECORDING;
            s_rec_seconds = 0;
            bsp_audio_set_format(SAMPLE_RATE, 16, 1);
            xTaskCreate(meeting_record_task, "rec_task", 4096, NULL, 5, &s_rec_task);
            update_ui();
        } else if (s_state == MEETING_RECORDING) {
            s_state = MEETING_PROCESSING;
            update_ui();
            // ?? 1.5 ?? AI ????
            s_state = MEETING_COMPLETED;
            update_ui();
        }
    } else if (btn == BSP_BTN_DOWN) {
        if (s_state == MEETING_COMPLETED) {
            s_todo_page++;
            update_ui();
        }
    } else if (btn == BSP_BTN_UP) {
        if (s_state == MEETING_COMPLETED) {
            if (s_todo_page > 0) s_todo_page--;
            else s_todo_page = TODO_COUNT - 1;
            update_ui();
        }
    }
}
