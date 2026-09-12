// tests/test_button_filter.c
// 按键滤波逻辑测试。上键窗口 0~140mV, 空闲电平 3024mV(实测 raw 恒为满量程)。
// 用例里的坏读数形态取自实机抓包: 10ms 采样下 raw=0 只孤立出现 1 格。
#include <assert.h>
#include <stdio.h>
#include "bsp_button_filter.h"

#define IDLE_MV 3024
#define UP_LO   0
#define UP_HI   140

static int s_ok;

static void expect(bool cond, const char *what)
{
    if (!cond) {
        printf("FAIL: %s\n", what);
        s_ok = 0;
    } else {
        printf("ok  : %s\n", what);
    }
}

// ---- 1. 空闲不动: 从头到尾都不该认定按下, 也不该放行任何事件 ----
static void test_idle_never_asserts(void)
{
    bsp_btn_filter_t f;
    bsp_btn_filter_init(&f, UP_LO, UP_HI, BSP_BTN_FILTER_SAMPLES);

    int64_t t = 0;
    bool ever_asserted = false;
    bool ever_accepts = false;
    for (int i = 0; i < 600; i++) {           // 6 秒空闲
        bsp_btn_filter_feed(&f, IDLE_MV, t);
        if (bsp_btn_filter_asserted(&f)) ever_asserted = true;
        if (bsp_btn_filter_accepts_event(&f, t, BSP_BTN_EVENT_TOL_US)) ever_accepts = true;
        t += 10000;
    }
    expect(!ever_asserted, "空闲 6 秒从未认定按下");
    expect(!ever_accepts, "空闲 6 秒从未放行按键事件");
}

// ---- 2. 孤立单格坏读数: 这是实机故障形态, 必须被完全挡掉 ----
static void test_isolated_glitch_rejected(void)
{
    bsp_btn_filter_t f;
    bsp_btn_filter_init(&f, UP_LO, UP_HI, BSP_BTN_FILTER_SAMPLES);

    // 实机抓包: 每 2 秒出现 1~5 个孤立 raw=0 采样, 位置不固定
    const int glitch_at[] = { 7, 31, 58, 99, 120, 121 + 2, 167, 188 };
    const int n = (int)(sizeof(glitch_at) / sizeof(glitch_at[0]));
    int64_t t = 0;
    bool ever_asserted = false;
    for (int i = 0; i < 200; i++) {
        bool is_glitch = false;
        for (int k = 0; k < n; k++) {
            if (glitch_at[k] == i) is_glitch = true;
        }
        bsp_btn_filter_feed(&f, is_glitch ? 0 : IDLE_MV, t);
        if (bsp_btn_filter_asserted(&f)) ever_asserted = true;
        t += 10000;
    }
    expect(!ever_asserted, "8 个孤立坏读数全部被挡掉, 一次都没认定按下");
}

// ---- 3. 连续 2 格坏读数仍被挡掉(留裕量), 第 3 格才放行 ----
static void test_two_sample_glitch_rejected(void)
{
    bsp_btn_filter_t f;
    bsp_btn_filter_init(&f, UP_LO, UP_HI, BSP_BTN_FILTER_SAMPLES);

    bsp_btn_filter_feed(&f, 0, 0);
    bsp_btn_filter_feed(&f, 0, 10000);
    expect(!bsp_btn_filter_asserted(&f), "连续 2 格坏读数仍不算按下");

    bsp_btn_filter_feed(&f, 0, 20000);
    expect(bsp_btn_filter_asserted(&f), "第 3 格才认定为按下");
}

// ---- 4. 真人按键 300ms: 认定按下, 并按时长放行单击类事件 ----
static void test_real_press_accepted(void)
{
    bsp_btn_filter_t f;
    bsp_btn_filter_init(&f, UP_LO, UP_HI, BSP_BTN_FILTER_SAMPLES);

    int64_t t = 0;
    for (int i = 0; i < 30; i++) {            // 按住 300ms
        bsp_btn_filter_feed(&f, 0, t);
        t += 10000;
    }
    expect(bsp_btn_filter_asserted(&f), "真人按住 300ms 认定为按下");

    bsp_btn_filter_feed(&f, IDLE_MV, t);      // 松开
    t += 10000;
    expect(!bsp_btn_filter_asserted(&f), "松开后不再认定按下");
    expect(bsp_btn_filter_accepts_event(&f, t, BSP_BTN_EVENT_TOL_US),
           "松开瞬间到达的单击事件被放行");
    expect(bsp_btn_filter_accepts_event(&f, t + 200000, BSP_BTN_EVENT_TOL_US),
           "松开 200ms 后到达的双击事件仍被放行");
    expect(!bsp_btn_filter_accepts_event(&f, t + 900000, BSP_BTN_EVENT_TOL_US),
           "松开 900ms 后才到达的事件被挡掉");
}

// ---- 5. 从未确认按下时, 任何事件都不放行(上电首个采样前的保护) ----
static void test_no_event_before_first_press(void)
{
    bsp_btn_filter_t f;
    bsp_btn_filter_init(&f, UP_LO, UP_HI, BSP_BTN_FILTER_SAMPLES);
    expect(!bsp_btn_filter_accepts_event(&f, 0, BSP_BTN_EVENT_TOL_US),
           "未发生确认按下前事件一律不放行");
    expect(!bsp_btn_filter_asserted(&f), "初始状态为未按下");
}

int main(void)
{
    s_ok = 1;
    test_idle_never_asserts();
    test_isolated_glitch_rejected();
    test_two_sample_glitch_rejected();
    test_real_press_accepted();
    test_no_event_before_first_press();
    printf(s_ok ? "\nALL PASS\n" : "\nSOME FAILED\n");
    return s_ok ? 0 : 1;
}
