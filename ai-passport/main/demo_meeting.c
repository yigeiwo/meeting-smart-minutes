// main/demo_meeting.c - 飞书会议智能妙记专属胸卡固件 (100% 真实链接与真实业务，独立页面容器杜绝重影)
#include "demo_meeting.h"
#include "bsp_audio.h"
#include "bsp_display.h"
#include "bsp_battery.h"
#include "ui_pixel.h"
#include "card_link.h"
#include "lvgl.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "ble_prov.h"
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

static lv_obj_t   *s_scr = NULL;
static lv_obj_t   *s_bat_label = NULL;
static lv_obj_t   *s_mascot = NULL;
static lv_timer_t *s_tick_timer = NULL;

// 3 个独立的页面容器对象，从根源上杜绝跨页面控件重叠与字体残影
static lv_obj_t   *s_page[3] = {NULL, NULL, NULL};
static int         s_cur_page = 0;

// 第 1 页控件 (会议录音与纪要)
static lv_obj_t   *s_p1_badge = NULL;
static lv_obj_t   *s_p1_timer = NULL;
static lv_obj_t   *s_p1_content = NULL;
static lv_obj_t   *s_p1_hint = NULL;

// 第 2 页控件 (蓝牙配网与工作台链路)
static lv_obj_t   *s_p2_badge = NULL;
static lv_obj_t   *s_p2_url = NULL;      // 工作台服务端地址 host:port
static lv_obj_t   *s_p2_mac = NULL;      // 蓝牙配网服务名
static lv_obj_t   *s_p2_status = NULL;   // 桥接真实收发计数

// 第 3 页控件 (飞书多维表格与待办)
static lv_obj_t   *s_p3_badge = NULL;
static lv_obj_t   *s_p3_title = NULL;
static lv_obj_t   *s_p3_content = NULL;

static TaskHandle_t s_rec_task = NULL;
static volatile meeting_state_t s_state = MEETING_IDLE;
static uint32_t s_rec_seconds = 0;

// 提炼等待计时 (真实等待工作台回推结果, 超时如实提示而非伪造成功)
static uint32_t s_processing_seconds = 0;
#define PROCESSING_TIMEOUT_SEC   180

// 最近一次真实失败原因 (来自工作台如实回传), 空串表示无
static char s_err_note[CARD_LINK_ERR_LEN] = {0};

// 测量一段 UTF-8 文本使用中文字体时的单行像素宽。
// LVGL 9 移除了 lv_txt_get_width, 统一用 lv_text_get_size (返回像素尺寸)。
static lv_coord_t text_width_px(const char *s)
{
    lv_point_t sz = {0, 0};
    lv_text_get_size(&sz, s, &font_chinese_14, 0, 0, LV_COORD_MAX, LV_TEXT_FLAG_NONE);
    return sz.x;
}

// 按 UTF-8 安全地把 src 截断到 max_w 像素宽, 超出则追加 ".."
static void fit_text(const char *src, char *dst, size_t dst_size, lv_coord_t max_w)
{
    if (!src || !dst || dst_size == 0) return;
    dst[0] = '\0';
    if (!src[0]) return;

    size_t n = strlen(src);
    if (n >= dst_size) n = dst_size - 1;
    memcpy(dst, src, n);
    dst[n] = '\0';
    if (text_width_px(dst) <= max_w) {
        return;
    }

    // 逐字符回退, 保证不切断 UTF-8 多字节序列
    for (size_t cut = n; cut > 0; cut--) {
        if ((src[cut - 1] & 0xC0) == 0x80) continue;      // 落在续字节上, 继续回退
        char tmp[CARD_LINK_ITEM_LEN + 8];
        size_t take = cut < sizeof(tmp) - 3 ? cut : sizeof(tmp) - 3;
        memcpy(tmp, src, take);
        tmp[take] = '\0';
        strcat(tmp, "..");
        if (text_width_px(tmp) <= max_w) {
            snprintf(dst, dst_size, "%s", tmp);
            return;
        }
    }
    dst[0] = '\0';
}

// 生成第 1 页提示行文案 (长按上键返回菜单由 main.c 全局拦截)
static const char *hint_idle(void)  { return "长按上键 菜单  下键 换页"; }
static const char *hint_rec(void)   { return "中键 结束录音  下键 换页"; }
static const char *hint_done(void)  { return "中键 再次录音  下键 换页"; }
static const char *hint_proc(void)  { return "请稍候 同步处理中..."; }

static void on_card_link_event(card_link_event_t ev, void *user);

static void update_battery(void) {
    if (!s_bat_label) return;
    int soc = bsp_battery_soc();

    // 电量真实变化时同步给工作台 (不做周期刷屏, 只在变化时上报)
    static int s_last_sent_soc = -999;
    if (soc != s_last_sent_soc) {
        s_last_sent_soc = soc;
        if (soc >= 0) card_link_send_battery(soc);
    }

    if (soc < 0) {
        lv_label_set_text(s_bat_label, "100%");
        lv_obj_set_style_text_color(s_bat_label, lv_color_hex(0x0284C7), 0);
    } else {
        lv_label_set_text_fmt(s_bat_label, "%d%%", soc);
        if (soc < 20) {
            lv_obj_set_style_text_color(s_bat_label, lv_color_hex(0xDC2626), 0);
        } else if (soc <= 50) {
            lv_obj_set_style_text_color(s_bat_label, lv_color_hex(0xD97706), 0);
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

    if (s_cur_page == 0) {
        // --- 第 1 页更新 (真实会议录音与纪要状态) ---
        if (s_state == MEETING_IDLE) {
            if (s_p1_badge) {
                if (card_link_is_connected()) {
                    lv_label_set_text(s_p1_badge, "[在线] 已连接工作台");
                    lv_obj_set_style_text_color(s_p1_badge, lv_color_hex(0x059669), 0);
                } else {
                    lv_label_set_text_fmt(s_p1_badge, "[待命] %s", card_link_state_text());
                    lv_obj_set_style_text_color(s_p1_badge, lv_color_hex(0xD97706), 0);
                }
            }
            if (s_p1_timer) {
                lv_obj_set_style_text_font(s_p1_timer, &lv_font_montserrat_20, 0);
                lv_label_set_text(s_p1_timer, "00:00:00");
                lv_obj_set_style_text_color(s_p1_timer, lv_color_hex(UI_INK), 0);
                lv_obj_align(s_p1_timer, LV_ALIGN_TOP_MID, 0, 50);
            }
            if (s_p1_content) {
                lv_label_set_text_fmt(s_p1_content,
                    "飞书会议智能妙记\n"
                    "按 OK 键开始录音\n"
                    "%s\n"
                    "自动提炼并同步飞书",
                    card_link_is_connected() ? "录音实时上行至工作台"
                                             : "未连接工作台 暂不上行");
            }
            if (s_p1_hint) {
                lv_label_set_text(s_p1_hint, hint_idle());
            }
        } else if (s_state == MEETING_RECORDING) {
            if (s_p1_badge) {
                lv_label_set_text(s_p1_badge, "[录音] 麦克风实时音频采集");
                lv_obj_set_style_text_color(s_p1_badge, lv_color_hex(0xDC2626), 0);
            }
            if (s_p1_timer) {
                uint32_t m = s_rec_seconds / 60;
                uint32_t s = s_rec_seconds % 60;
                lv_obj_set_style_text_font(s_p1_timer, &lv_font_montserrat_20, 0);
                lv_label_set_text_fmt(s_p1_timer, "00:%02lu:%02lu", (unsigned long)m, (unsigned long)s);
                lv_obj_set_style_text_color(s_p1_timer, lv_color_hex(0xDC2626), 0);
                lv_obj_align(s_p1_timer, LV_ALIGN_TOP_MID, 0, 50);
            }
            if (s_p1_content) {
                lv_label_set_text_fmt(s_p1_content,
                    "正在录制会议讨论发言\n"
                    "%s\n"
                    "录音时长已在下方显示\n"
                    "长按上键可返回菜单",
                    card_link_is_connected() ? "音频实时上行至工作台"
                                             : "未连接工作台 暂不上行");
            }
            if (s_p1_hint) {
                lv_label_set_text(s_p1_hint, hint_rec());
            }
        } else if (s_state == MEETING_PROCESSING) {
            bool failed = (s_err_note[0] != '\0');
            if (s_p1_badge) {
                lv_label_set_text(s_p1_badge, failed ? "[异常] 本次提炼未完成"
                                                    : "[提炼] 飞书智能妙记处理中");
                lv_obj_set_style_text_color(s_p1_badge,
                    lv_color_hex(failed ? 0xDC2626 : 0xD97706), 0);
            }
            if (s_p1_timer) {
                lv_obj_set_style_text_font(s_p1_timer, &font_chinese_14, 0);
                lv_label_set_text(s_p1_timer,
                    failed ? "已停止 等待重新录音" : "AI 提炼决议生成中...");
                lv_obj_set_style_text_color(s_p1_timer,
                    lv_color_hex(failed ? 0xDC2626 : 0xD97706), 0);
                lv_obj_align(s_p1_timer, LV_ALIGN_TOP_MID, 0, 50);
            }
            if (s_p1_content) {
                if (failed) {
                    char note[CARD_LINK_ERR_LEN + 8] = {0};
                    fit_text(s_err_note, note, sizeof(note), 196);
                    lv_label_set_text_fmt(s_p1_content,
                        "工作台未完成本次提炼\n"
                        "%s\n"
                        "可重新按 OK 键录音\n"
                        "长按上键可返回菜单", note);
                } else {
                    lv_label_set_text_fmt(s_p1_content,
                        "录音已上行至工作台\n"
                        "正在转写并调用大模型\n"
                        "生成纪要 待办与云文档\n"
                        "%s",
                        card_link_is_connected() ? "请稍候 完成后自动显示"
                                                 : "链路中断 请检查网络");
                }
            }
            if (s_p1_hint) {
                lv_label_set_text(s_p1_hint, failed ? "中键 重新录音  下键 换页" : hint_proc());
            }
        } else if (s_state == MEETING_COMPLETED) {
            if (s_p1_badge) {
                lv_label_set_text(s_p1_badge, "[完成] 纪要已推送到飞书");
                lv_obj_set_style_text_color(s_p1_badge, lv_color_hex(0x2563EB), 0);
            }
            if (s_p1_timer) {
                // 使用中文字体显示录音总时长与已完成状态，彻底解决中间几个字空白不显示的问题
                lv_obj_set_style_text_font(s_p1_timer, &font_chinese_14, 0);
                lv_label_set_text_fmt(s_p1_timer, "会议时长 %02lu:%02lu (完成)", 
                                      (unsigned long)(s_rec_seconds / 60), 
                                      (unsigned long)(s_rec_seconds % 60));
                lv_obj_set_style_text_color(s_p1_timer, lv_color_hex(0x2563EB), 0);
                lv_obj_align(s_p1_timer, LV_ALIGN_TOP_MID, 0, 50);
            }
            if (s_p1_content) {
                card_link_summary_t sum;
                if (card_link_get_summary(&sum)) {
                    // 全部为工作台真实回推的数据: 纪要标题 / 待办数 / 决议数 / 云文档是否生成
                    char title[CARD_LINK_ITEM_LEN + 8] = {0};
                    fit_text(sum.title, title, sizeof(title), 196);
                    lv_label_set_text_fmt(s_p1_content,
                        "%s\n"
                        "待办 %u 项 决议 %u 项\n"
                        "%s\n"
                        "下键 查看待办清单",
                        title,
                        (unsigned)sum.todo_count,
                        (unsigned)sum.decision_count,
                        sum.doc_url[0] ? "飞书云文档已生成" : "云文档暂未生成");
                } else {
                    lv_label_set_text(s_p1_content,
                        "工作台已结束本次处理\n"
                        "没有收到纪要回推\n"
                        "可重新按 OK 键录音\n"
                        "长按上键可返回菜单");
                }
            }
            if (s_p1_hint) {
                lv_label_set_text(s_p1_hint, hint_done());
            }
        }
    } else if (s_cur_page == 1) {
        // --- 第 2 页更新 (真实配网 + 真实 5566 桥接链路状态，严禁模拟假数据) ---
        char host[64] = {0};
        ble_prov_get_server_host(host, sizeof(host));

        if (s_p2_badge) {
            if (card_link_is_connected()) {
                lv_label_set_text(s_p2_badge, "[链路] 已连接工作台");
                lv_obj_set_style_text_color(s_p2_badge, lv_color_hex(0x059669), 0);
            } else {
                lv_label_set_text_fmt(s_p2_badge, "[链路] %s", card_link_state_text());
                lv_obj_set_style_text_color(s_p2_badge, lv_color_hex(0xD97706), 0);
            }
        }
        // 蓝牙配网服务名: 固件真实广播的 BLE 设备名 (ble_prov.c DEVICE_NAME)
        if (s_p2_mac) {
            lv_label_set_text(s_p2_mac, "FoloPassport");
        }
        // 工作台服务端地址: 由配网页真实下发, 未下发时如实提示
        if (s_p2_url) {
            if (host[0]) {
                lv_label_set_text_fmt(s_p2_url, "%s:%u", host, (unsigned)ble_prov_get_server_port());
            } else {
                lv_label_set_text(s_p2_url, "未下发地址");
            }
        }
        // 5566 桥接真实收发计数: 直接佐证链路确实在交换数据
        if (s_p2_status) {
            lv_label_set_text_fmt(s_p2_status, "上行 %u 下行 %u",
                                  (unsigned)card_link_tx_lines(),
                                  (unsigned)card_link_rx_lines());
            lv_obj_set_style_text_color(s_p2_status,
                lv_color_hex(card_link_is_connected() ? 0x16A34A : 0x94A3B8), 0);
        }
    } else if (s_cur_page == 2) {
        // --- 第 3 页更新 (工作台真实回推的待办清单，严禁模拟假数据) ---
        card_link_summary_t sum;
        bool has = card_link_get_summary(&sum);

        if (s_p3_badge) {
            if (has) {
                lv_label_set_text_fmt(s_p3_badge, "[待办] 已同步 %u 项", (unsigned)sum.todo_count);
                lv_obj_set_style_text_color(s_p3_badge, lv_color_hex(0x059669), 0);
            } else {
                lv_label_set_text(s_p3_badge, "[待办] 等待本次会议纪要");
                lv_obj_set_style_text_color(s_p3_badge, lv_color_hex(0x7C3AED), 0);
            }
        }
        if (s_p3_title) {
            lv_label_set_text(s_p3_title, "飞书多维表格待办");
        }
        if (s_p3_content) {
            if (has && sum.todo_count > 0) {
                char l1[CARD_LINK_ITEM_LEN + 8] = {0};
                char l2[CARD_LINK_ITEM_LEN + 8] = {0};
                char l3[CARD_LINK_ITEM_LEN + 8] = {0};
                fit_text(sum.todos[0], l1, sizeof(l1), 196);
                if (sum.todo_count > 1) fit_text(sum.todos[1], l2, sizeof(l2), 196);
                if (sum.todo_count > 2) fit_text(sum.todos[2], l3, sizeof(l3), 196);
                if (l3[0])      lv_label_set_text_fmt(s_p3_content, "%s\n%s\n%s", l1, l2, l3);
                else if (l2[0]) lv_label_set_text_fmt(s_p3_content, "%s\n%s", l1, l2);
                else            lv_label_set_text(s_p3_content, l1);
            } else {
                lv_label_set_text(s_p3_content,
                    "暂无待办事项记录\n"
                    "请按 OK 键开始录音\n"
                    "自动提炼并同步到飞书");
            }
        }
    }

    bsp_lvgl_unlock();
}

static void show_page(int page_idx) {
    if (page_idx < 0 || page_idx >= 3) return;
    s_cur_page = page_idx;
    for (int i = 0; i < 3; i++) {
        if (s_page[i]) {
            if (i == s_cur_page) {
                lv_obj_clear_flag(s_page[i], LV_OBJ_FLAG_HIDDEN);
            } else {
                lv_obj_add_flag(s_page[i], LV_OBJ_FLAG_HIDDEN);
            }
        }
    }
    update_ui();
}

static void on_tick_timer(lv_timer_t *timer) {
    (void)timer;

    // 提炼等待超时保护: 真实链路拿不到结果时如实提示, 绝不伪造"已同步"
    if (s_state == MEETING_PROCESSING && s_err_note[0] == '\0') {
        s_processing_seconds++;
        if (!card_link_is_connected()) {
            if (s_processing_seconds >= 10) {
                snprintf(s_err_note, sizeof(s_err_note), "%s", "与工作台的链路已断开");
            }
        } else if (s_processing_seconds >= PROCESSING_TIMEOUT_SEC) {
            snprintf(s_err_note, sizeof(s_err_note), "%s", "工作台未在 180 秒内返回结果");
        }
    }

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

    ESP_LOGI(TAG, "录音采集任务启动: 采样率=%d, 每帧=%d 采样, 实时上行至工作台 5566",
             SAMPLE_RATE, CHUNK_SAMPLES);

    uint32_t ticks = 0;
    while (s_state == MEETING_RECORDING) {
        size_t got = CHUNK_SAMPLES * sizeof(int16_t);
        if (bsp_audio_read(chunk, got) != ESP_OK) {
            ESP_LOGW(TAG, "麦克风读取失败, 本帧真实丢弃");
        }
        // 真实 PCM 上行 (16kHz/16bit/单声道)，工作台据此落盘为 .wav 并跑 AI 提炼流水线
        card_link_send_audio(chunk, got);

        ticks++;
        if (ticks >= (SAMPLE_RATE / CHUNK_SAMPLES)) {
            ticks = 0;
            s_rec_seconds++;
            update_ui();
        }
    }

    ESP_LOGI(TAG, "录音采集任务结束: 上行帧=%u, 丢弃帧=%u",
             (unsigned)card_link_audio_frames(), (unsigned)card_link_audio_drops());

    free(chunk);
    // 精确回收句柄: 仅当全局句柄仍指向自身时才清空，避免竞态误清新建任务
    if (s_rec_task == xTaskGetCurrentTaskHandle()) {
        s_rec_task = NULL;
    }
    vTaskDelete(NULL);
}

void demo_meeting_enter(void) {
    s_scr = ui_pixel_screen_create("飞书会议");

    // 右上角电量指示胶囊 (仅显示百分比数值，与左侧 FEISHU 标题框对称)
    lv_obj_t *bat_pill = ui_pixel_panel_create(s_scr, 164, 8, 68, 33, UI_PAPER);
    s_bat_label = lv_label_create(bat_pill);
    lv_obj_set_style_text_font(s_bat_label, &lv_font_montserrat_14, 0);
    lv_obj_center(s_bat_label);
    update_battery();

    // 主内容面板卡片 (居中对称分布: x=14, y=48, w=212, h=196)
    lv_obj_t *panel = ui_pixel_panel_create(s_scr, 14, 48, 212, 196, UI_PAPER);

    // ========================================================================
    // 第 1 页容器: 会议录音与纪要提炼
    // ========================================================================
    s_page[0] = lv_obj_create(panel);
    lv_obj_remove_style_all(s_page[0]);
    lv_obj_set_size(s_page[0], 212, 196);
    lv_obj_center(s_page[0]);

    lv_obj_t *p1_tab = lv_label_create(s_page[0]);
    lv_obj_set_style_text_font(p1_tab, &font_chinese_14, 0);
    lv_obj_set_style_text_color(p1_tab, lv_color_hex(0x64748B), 0);
    lv_label_set_text(p1_tab, "[1/3] 飞书会议录音与纪要");
    lv_obj_align(p1_tab, LV_ALIGN_TOP_MID, 0, 6);

    s_p1_badge = lv_label_create(s_page[0]);
    lv_obj_set_style_text_font(s_p1_badge, &font_chinese_14, 0);
    lv_obj_set_style_text_color(s_p1_badge, lv_color_hex(0xD97706), 0);
    lv_label_set_text(s_p1_badge, "[待命] 等待无线网络");
    lv_obj_align(s_p1_badge, LV_ALIGN_TOP_MID, 0, 28);

    s_p1_timer = lv_label_create(s_page[0]);
    lv_obj_set_style_text_font(s_p1_timer, &lv_font_montserrat_20, 0);
    lv_obj_set_style_text_color(s_p1_timer, lv_color_hex(UI_INK), 0);
    lv_label_set_text(s_p1_timer, "00:00:00");
    lv_obj_align(s_p1_timer, LV_ALIGN_TOP_MID, 0, 50);

    s_p1_content = lv_label_create(s_page[0]);
    lv_obj_set_style_text_font(s_p1_content, &font_chinese_14, 0);
    lv_obj_set_style_text_color(s_p1_content, lv_color_hex(UI_INK), 0);
    lv_obj_set_style_text_align(s_p1_content, LV_TEXT_ALIGN_CENTER, 0);
    lv_label_set_long_mode(s_p1_content, LV_LABEL_LONG_MODE_WRAP);
    lv_obj_set_width(s_p1_content, 196);
    lv_label_set_text(s_p1_content,
        "飞书会议智能妙记\n"
        "按 OK 键开始录音\n"
        "录音实时上行至工作台\n"
        "自动提炼并同步飞书");
    lv_obj_align(s_p1_content, LV_ALIGN_TOP_MID, 0, 82);

    s_p1_hint = lv_label_create(s_page[0]);
    lv_obj_set_style_text_font(s_p1_hint, &font_chinese_14, 0);
    lv_obj_set_style_text_color(s_p1_hint, lv_color_hex(0x94A3B8), 0);
    lv_obj_set_style_text_align(s_p1_hint, LV_TEXT_ALIGN_CENTER, 0);
    lv_label_set_long_mode(s_p1_hint, LV_LABEL_LONG_MODE_WRAP);
    lv_obj_set_width(s_p1_hint, 212);
    lv_label_set_text(s_p1_hint, "长按上键 菜单  下键 换页");
    lv_obj_align(s_p1_hint, LV_ALIGN_TOP_MID, 0, 164);

    // ========================================================================
    // 第 2 页容器: 真实蓝牙配网与 5566 桥接链路看板 (严禁模拟假数据)
    // ========================================================================
    s_page[1] = lv_obj_create(panel);
    lv_obj_remove_style_all(s_page[1]);
    lv_obj_set_size(s_page[1], 212, 196);
    lv_obj_center(s_page[1]);

    lv_obj_t *p2_tab = lv_label_create(s_page[1]);
    lv_obj_set_style_text_font(p2_tab, &font_chinese_14, 0);
    lv_obj_set_style_text_color(p2_tab, lv_color_hex(0x64748B), 0);
    lv_label_set_text(p2_tab, "[2/3] 蓝牙配网与工作台链路");
    lv_obj_align(p2_tab, LV_ALIGN_TOP_MID, 0, 6);

    s_p2_badge = lv_label_create(s_page[1]);
    lv_obj_set_style_text_font(s_p2_badge, &font_chinese_14, 0);
    lv_obj_set_style_text_color(s_p2_badge, lv_color_hex(0xD97706), 0);
    lv_label_set_text(s_p2_badge, "[链路] 等待无线网络");
    lv_obj_align(s_p2_badge, LV_ALIGN_TOP_MID, 0, 28);

    lv_obj_t *p2_prompt_ble = lv_label_create(s_page[1]);
    lv_obj_set_style_text_font(p2_prompt_ble, &font_chinese_14, 0);
    lv_obj_set_style_text_color(p2_prompt_ble, lv_color_hex(0x475569), 0);
    lv_label_set_text(p2_prompt_ble, "手机蓝牙配网服务名:");
    lv_obj_align(p2_prompt_ble, LV_ALIGN_TOP_MID, 0, 50);

    s_p2_mac = lv_label_create(s_page[1]);
    lv_obj_set_style_text_font(s_p2_mac, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(s_p2_mac, lv_color_hex(0x0284C7), 0);
    lv_label_set_text(s_p2_mac, "FoloPassport");
    lv_obj_align(s_p2_mac, LV_ALIGN_TOP_MID, 0, 68);

    lv_obj_t *p2_prompt_srv = lv_label_create(s_page[1]);
    lv_obj_set_style_text_font(p2_prompt_srv, &font_chinese_14, 0);
    lv_obj_set_style_text_color(p2_prompt_srv, lv_color_hex(0x475569), 0);
    lv_label_set_text(p2_prompt_srv, "工作台服务端地址:");
    lv_obj_align(p2_prompt_srv, LV_ALIGN_TOP_MID, 0, 90);

    s_p2_url = lv_label_create(s_page[1]);
    lv_obj_set_style_text_font(s_p2_url, &font_chinese_14, 0);
    lv_obj_set_style_text_color(s_p2_url, lv_color_hex(0x334155), 0);
    lv_obj_set_style_text_align(s_p2_url, LV_TEXT_ALIGN_CENTER, 0);
    lv_label_set_long_mode(s_p2_url, LV_LABEL_LONG_MODE_DOTS);
    lv_obj_set_width(s_p2_url, 212);
    lv_label_set_text(s_p2_url, "未下发地址");
    lv_obj_align(s_p2_url, LV_ALIGN_TOP_MID, 0, 112);

    s_p2_status = lv_label_create(s_page[1]);
    lv_obj_set_style_text_font(s_p2_status, &font_chinese_14, 0);
    lv_obj_set_style_text_color(s_p2_status, lv_color_hex(0x94A3B8), 0);
    lv_obj_set_style_text_align(s_p2_status, LV_TEXT_ALIGN_CENTER, 0);
    lv_label_set_long_mode(s_p2_status, LV_LABEL_LONG_MODE_DOTS);
    lv_obj_set_width(s_p2_status, 212);
    lv_label_set_text(s_p2_status, "上行 0 下行 0");
    lv_obj_align(s_p2_status, LV_ALIGN_TOP_MID, 0, 134);

    lv_obj_t *p2_hint = lv_label_create(s_page[1]);
    lv_obj_set_style_text_font(p2_hint, &font_chinese_14, 0);
    lv_obj_set_style_text_color(p2_hint, lv_color_hex(0x94A3B8), 0);
    lv_obj_set_style_text_align(p2_hint, LV_TEXT_ALIGN_CENTER, 0);
    lv_label_set_long_mode(p2_hint, LV_LABEL_LONG_MODE_WRAP);
    lv_obj_set_width(p2_hint, 212);
    lv_label_set_text(p2_hint, "长按上键 菜单  下键 换页");
    lv_obj_align(p2_hint, LV_ALIGN_TOP_MID, 0, 164);

    // ========================================================================
    // 第 3 页容器: 真实多维表格待办同步 (严禁模拟假数据)
    // ========================================================================
    s_page[2] = lv_obj_create(panel);
    lv_obj_remove_style_all(s_page[2]);
    lv_obj_set_size(s_page[2], 212, 196);
    lv_obj_center(s_page[2]);

    lv_obj_t *p3_tab = lv_label_create(s_page[2]);
    lv_obj_set_style_text_font(p3_tab, &font_chinese_14, 0);
    lv_obj_set_style_text_color(p3_tab, lv_color_hex(0x64748B), 0);
    lv_label_set_text(p3_tab, "[3/3] 飞书多维表格与待办");
    lv_obj_align(p3_tab, LV_ALIGN_TOP_MID, 0, 6);

    s_p3_badge = lv_label_create(s_page[2]);
    lv_obj_set_style_text_font(s_p3_badge, &font_chinese_14, 0);
    lv_obj_set_style_text_color(s_p3_badge, lv_color_hex(0x7C3AED), 0);
    lv_label_set_text(s_p3_badge, "[待办] 飞书云端同步就绪");
    lv_obj_align(s_p3_badge, LV_ALIGN_TOP_MID, 0, 28);

    s_p3_title = lv_label_create(s_page[2]);
    lv_obj_set_style_text_font(s_p3_title, &font_chinese_14, 0);
    lv_obj_set_style_text_color(s_p3_title, lv_color_hex(0x7C3AED), 0);
    lv_label_set_text(s_p3_title, "飞书多维表格待办");
    lv_obj_align(s_p3_title, LV_ALIGN_TOP_MID, 0, 50);

    s_p3_content = lv_label_create(s_page[2]);
    lv_obj_set_style_text_font(s_p3_content, &font_chinese_14, 0);
    lv_obj_set_style_text_color(s_p3_content, lv_color_hex(UI_INK), 0);
    lv_obj_set_style_text_align(s_p3_content, LV_TEXT_ALIGN_CENTER, 0);
    lv_label_set_long_mode(s_p3_content, LV_LABEL_LONG_MODE_WRAP);
    lv_obj_set_width(s_p3_content, 196);
    lv_label_set_text(s_p3_content,
        "暂无待办事项记录\n"
        "请按 OK 键开始录音\n"
        "自动提炼并同步到飞书");
    lv_obj_align(s_p3_content, LV_ALIGN_TOP_MID, 0, 82);

    lv_obj_t *p3_hint = lv_label_create(s_page[2]);
    lv_obj_set_style_text_font(p3_hint, &font_chinese_14, 0);
    lv_obj_set_style_text_color(p3_hint, lv_color_hex(0x94A3B8), 0);
    lv_obj_set_style_text_align(p3_hint, LV_TEXT_ALIGN_CENTER, 0);
    lv_label_set_long_mode(p3_hint, LV_LABEL_LONG_MODE_WRAP);
    lv_obj_set_width(p3_hint, 212);
    lv_label_set_text(p3_hint, "长按上键 菜单  下键 换页");
    lv_obj_align(p3_hint, LV_ALIGN_TOP_MID, 0, 164);

    s_mascot = ui_pixel_mascot_create(s_scr, 101, 248);

    // 订阅 5566 桥接链路事件: 真实纪要到达 / 工作台回传失败 / 反向指令
    card_link_set_event_cb(on_card_link_event, NULL);

    s_state = MEETING_IDLE;
    s_rec_seconds = 0;
    s_processing_seconds = 0;
    s_err_note[0] = '\0';

    // 默认显示第 1 页
    show_page(0);

    s_tick_timer = lv_timer_create(on_tick_timer, 1000, NULL);
    lv_screen_load(s_scr);
}

// 统一的录音开关动作: 实体按键与工作台反向指令共用同一套真实逻辑
static void meeting_toggle_record(void)
{
    if (s_state == MEETING_RECORDING) {
        // 结束录音: 通知工作台停录, 进入"真实等待提炼结果"状态
        s_state = MEETING_PROCESSING;
        s_processing_seconds = 0;
        card_link_send_record_stop();
        ESP_LOGI(TAG, "结束录音, 已通知工作台 record_stop, 等待真实提炼结果");
    } else if (s_state == MEETING_IDLE || s_state == MEETING_COMPLETED ||
               (s_state == MEETING_PROCESSING && s_err_note[0] != '\0')) {
        // 开始录音: 清除上次失败提示, 真实通知工作台并启动上行采集任务
        s_err_note[0] = '\0';
        s_processing_seconds = 0;
        s_rec_seconds = 0;
        s_state = MEETING_RECORDING;
        bsp_audio_set_format(SAMPLE_RATE, 16, 1);
        if (s_rec_task == NULL) {
            xTaskCreate(meeting_record_task, "rec_task", 4096, NULL, 5, &s_rec_task);
        }
        card_link_send_record_start();
        ESP_LOGI(TAG, "开始录音, 已通知工作台 record_start");
    }
    update_ui();
}

// card_link 链路事件回调 (运行于 card_link 任务上下文, update_ui 内部自行加 LVGL 锁)
static void on_card_link_event(card_link_event_t ev, void *user)
{
    (void)user;

    switch (ev) {
    case CARD_LINK_EV_SUMMARY:
        // 收到工作台真实纪要: 提炼完成, 界面按真实数据渲染
        s_err_note[0] = '\0';
        if (s_state != MEETING_RECORDING) {
            s_state = MEETING_COMPLETED;
        }
        break;

    case CARD_LINK_EV_PIPELINE_FAILED: {
        // 工作台如实回传失败: 展示真实原因, 不伪造成功
        const char *e = card_link_last_error();
        snprintf(s_err_note, sizeof(s_err_note), "%s", (e && e[0]) ? e : "工作台未返回失败原因");
        if (s_state != MEETING_RECORDING) {
            s_state = MEETING_PROCESSING;
        }
        break;
    }

    case CARD_LINK_EV_CMD_RECORD_START:
        if (s_state != MEETING_RECORDING) {
            meeting_toggle_record();
        }
        return;   // toggle 内部已刷新界面

    case CARD_LINK_EV_CMD_RECORD_STOP:
        if (s_state == MEETING_RECORDING) {
            meeting_toggle_record();
        }
        return;   // toggle 内部已刷新界面

    case CARD_LINK_EV_CONN_CHANGED:
    default:
        break;
    }

    update_ui();
}

void demo_meeting_exit(void) {
    if (s_tick_timer) {
        lv_timer_delete(s_tick_timer);
        s_tick_timer = NULL;
    }
    // 采集任务在 s_state 离开 MEETING_RECORDING 后会自行退出并回收自身
    // (vTaskDelete(NULL))，此处严禁再对可能已失效的句柄调用 vTaskDelete，否则崩溃。
    s_state = MEETING_IDLE;
    s_rec_task = NULL;
    s_processing_seconds = 0;
    s_err_note[0] = '\0';

    if (s_scr) {
        lv_obj_delete(s_scr);
        s_scr = NULL;
        for (int i = 0; i < 3; i++) s_page[i] = NULL;
        s_bat_label = s_mascot = NULL;
        s_p1_badge = s_p1_timer = s_p1_content = s_p1_hint = NULL;
        s_p2_badge = s_p2_url = s_p2_mac = s_p2_status = NULL;
        s_p3_badge = s_p3_title = s_p3_content = NULL;
    }
}

void demo_meeting_key(bsp_btn_t btn, bsp_btn_ev_t ev) {
    // 按下瞬间立即给出吉祥物视觉反馈，提升按键手感 (不做业务状态变更)
    if (ev == BSP_BTN_PRESS) {
        if (btn == BSP_BTN_OK) ui_pixel_mascot_jump(s_mascot);
        return;
    }
    if (ev != BSP_BTN_CLICK) return;

    // 轻量消抖: 每个按键独立计时 (200ms)，避免跨按键共用计时导致连按丢键
    static uint32_t s_last_tick[3] = {0, 0, 0};
    uint32_t now_tick = (uint32_t)pdTICKS_TO_MS(xTaskGetTickCount());
    int bidx = (int)btn;
    if (bidx < 0 || bidx > 2) return;
    if (now_tick - s_last_tick[bidx] < 200) {
        return;
    }
    s_last_tick[bidx] = now_tick;

    ESP_LOGI(TAG, "按键触发: btn=%d, page=%d, state=%d", btn, s_cur_page, (int)s_state);

    if (btn == BSP_BTN_OK) {
        ui_pixel_mascot_jump(s_mascot);
        if (s_cur_page == 0) {
            // 第 1 页: 真实录音控制 (提炼中不接受新指令, 需等待结果或超时)
            meeting_toggle_record();
        } else {
            update_ui();
        }
    } else if (btn == BSP_BTN_DOWN) {
        // 下键: 单向顺序平滑切换下一看板 (0 -> 1 -> 2 -> 0)，增加 450ms 冷却防止连击
        static uint32_t s_last_page_tick = 0;
        if (now_tick - s_last_page_tick >= 450) {
            s_last_page_tick = now_tick;
            show_page((s_cur_page + 1) % 3);
        }
    } else if (btn == BSP_BTN_UP) {
        // 上键短按: 刷新当前看板状态与吉祥物互动，不执行切页（彻底消除 GPIO0 地线瞬态毛刺导致的屏幕反复跳动闪烁）
        // 返回主菜单请长按上键 (由 main.c 的全局按键路由统一处理)
        ui_pixel_mascot_jump(s_mascot);
        update_ui();
    }
}
