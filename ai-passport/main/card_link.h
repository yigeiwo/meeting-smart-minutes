// main/card_link.h —— 飞书胸卡与电脑工作台 5566 端口的真实 TCP / NDJSON 桥接客户端
//
// 协议与 feishu_meeting_tool/card_bridge.py 严格一致，当前实现的消息如下:
//
//   卡片 -> 工作台 (每行一条 JSON, UTF-8, '\n' 结尾)
//     {"type":"hello","fw":"...","mac":"...","ssid":"..."}  连接建立后的真实握手
//     {"type":"ping"}                                  心跳 (15s)，工作台回 pong
//     {"type":"battery","soc":85}
//     {"type":"record_start"} / {"type":"record_stop"}  录音启停 (工作台据此落盘 wav 并跑 AI 提炼)
//     {"type":"combo_menu"}                            长按上键返回菜单
//     {"type":"audio","seq":N,"pcm":"<base64 16k/16bit/单声道>"}   真实录音音频上行
//
//   注: 卡片不发送 {"type":"button"} 原始按键消息 —— 工作台的按键处理已内含录音启停逻辑,
//       同时发送会导致重复触发; 卡片一律用 record_start/record_stop 这类语义消息驱动。
//
//   工作台 -> 卡片 (只保留语义消息，超大 lcd_frame 位图帧在接收侧按前缀丢弃)
//     {"event":"pong"}                                 心跳应答
//     {"event":"meeting_processed"|"summary",          真实纪要: 标题/云文档/待办/决议
//      "title":..,"doc_url":..,"todos":[{task,owner,priority}],"decisions":[..]}
//     {"event":"pipeline_result","ok":false,"msg":".."} 真实失败原因 (如实透传, 不伪造成功)
//     {"cmd":"record_start"|"record_stop"|"combo_menu"} 工作台反向控制卡片
//
#pragma once

#include "esp_err.h"
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define CARD_LINK_MAX_ITEMS   4
#define CARD_LINK_ITEM_LEN    56
#define CARD_LINK_TITLE_LEN   56
#define CARD_LINK_URL_LEN     128
#define CARD_LINK_ERR_LEN     72

// 最近一次从工作台真实收到的会议纪要要点。
// 严禁模拟: 只有真正解析到工作台下发的报文时 valid 才会为 true。
typedef struct {
    bool     valid;
    char     title[CARD_LINK_TITLE_LEN];
    char     doc_url[CARD_LINK_URL_LEN];
    char     todos[CARD_LINK_MAX_ITEMS][CARD_LINK_ITEM_LEN];
    uint8_t  todo_count;
    char     decisions[CARD_LINK_MAX_ITEMS][CARD_LINK_ITEM_LEN];
    uint8_t  decision_count;
    uint32_t recv_ms;          // 收到时刻 (系统毫秒)
} card_link_summary_t;

// 链路事件 (回调运行于 card_link 任务上下文)
typedef enum {
    CARD_LINK_EV_CONN_CHANGED = 0,   // TCP 连接状态变化
    CARD_LINK_EV_SUMMARY,            // 收到工作台真实纪要
    CARD_LINK_EV_PIPELINE_FAILED,    // 工作台如实回传的提炼失败
    CARD_LINK_EV_CMD_RECORD_START,   // 工作台要求开始录音
    CARD_LINK_EV_CMD_RECORD_STOP,    // 工作台要求停止录音
} card_link_event_t;

typedef void (*card_link_event_cb_t)(card_link_event_t ev, void *user);

/** 启动桥接后台任务 (幂等)。Wi-Fi 未连接时会自动等待并重试。 */
esp_err_t card_link_start(void);

/** 真实 TCP 是否已连上工作台 5566 端口 */
bool card_link_is_connected(void);

/** 链路状态文本，供屏幕显示 (真实状态，非占位符) */
const char *card_link_state_text(void);

/** 真实收发计数，用于屏幕上佐证链路确实在跑 */
uint32_t card_link_tx_lines(void);
uint32_t card_link_rx_lines(void);
uint32_t card_link_audio_frames(void);
uint32_t card_link_audio_drops(void);

/** 最近一次真实失败原因 (空串表示当前无错误) */
const char *card_link_last_error(void);

void card_link_set_event_cb(card_link_event_cb_t cb, void *user);

/** 清空一次性的失败提示 (例如界面已展示过) */
void card_link_clear_error(void);

/** 上行: 真实业务事件 */
void card_link_send_battery(int soc);
void card_link_send_record_start(void);
void card_link_send_record_stop(void);
void card_link_send_combo_menu(void);

/** 上行: 真实音频 (PCM 16bit 单声道)。
 *  返回 false 表示链路未就绪或发送缓冲已满，该帧被真实丢弃并计入 drops。 */
bool card_link_send_audio(const void *pcm, size_t bytes);

/** 取一份最近的真实纪要快照 (无则返回 false) */
bool card_link_get_summary(card_link_summary_t *out);

#ifdef __cplusplus
}
#endif
