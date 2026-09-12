// components/bsp/include/bsp_button_filter.h
// 按键电平滤波 —— 纯逻辑, 不依赖任何硬件, 可在主机上直接跑测试。
//
// 存在的原因(实机实测结论, 见 docs/hardware-design/AI_HARDWARE_DEVELOPMENT_GUIDE):
//   ESP32-C3 的 ADC 在本板的空闲工作点上会偶发返回 raw=0 的坏读数
//   (空闲时引脚被 10k 上拉到 3.3V, 已到 12dB 量程上限, 实测 raw 恒为 4095=满量程)。
//   实测这些坏读数的形态是【孤立的单格】: 10ms 采样下从不连续出现 2 格以上。
//   而按键组件对 ADC 电平不做多格一致性判断, 单格坏读数就会被当成"按下",
//   落进上键窗口(0~140mV)后表现为: 上键自发乱按 → 页面自己跳回主菜单。
//
// 对策: 只有连续 need 格(默认 3 格 = 30ms)都落在同一窗口, 才认为按键真的被按下;
//   随后该键真人在按的持续时间都在 100ms 以上(≥10 格), 留出 3 倍以上裕量。
#pragma once

#include <stdbool.h>
#include <stdint.h>

// 认定为"真实按下"所需的连续采样格数。3 格 = 30ms。
#define BSP_BTN_FILTER_SAMPLES 3

typedef struct {
    int     lo;          // 识别窗口下限 mV (闭区间)
    int     hi;          // 识别窗口上限 mV (闭区间)
    int     need;        // 需要连续多少格
    int     run;         // 当前已连续落在窗口内的格数
    bool    asserted;    // 滤波后的按键电平
    int64_t last_ok_us;  // 最近一次"确认按下"的时刻, 0 表示尚未发生过
} bsp_btn_filter_t;

// 已确认按下后, 允许按键事件迟到的时长: 松开瞬间产生的单击、长按抬起、
// 双击的第二下, 都会在这个窗口内到达。
#define BSP_BTN_EVENT_TOL_US  (800 * 1000)

static inline void bsp_btn_filter_init(bsp_btn_filter_t *f, int lo, int hi, int need)
{
    f->lo = lo;
    f->hi = hi;
    f->need = need;
    f->run = 0;
    f->asserted = false;
    f->last_ok_us = 0;
}

// 送入一格采样值(mV)。坏读数会让 run 立刻归零, 因此永远凑不满 need 格。
static inline void bsp_btn_filter_feed(bsp_btn_filter_t *f, int mv, int64_t now_us)
{
    if (mv >= f->lo && mv <= f->hi) {
        if (f->run < f->need) {
            f->run++;
        }
        if (f->run >= f->need) {
            f->asserted = true;
            f->last_ok_us = now_us;
        }
    } else {
        f->run = 0;
        f->asserted = false;
    }
}

static inline bool bsp_btn_filter_asserted(const bsp_btn_filter_t *f)
{
    return f->asserted;
}

// 按键组件报来的事件是否可信: 必须有一次"确认按下"发生在 tol_us 之内。
// 坏读数从来不会推进 last_ok_us, 所以它的派生事件会被这里挡掉。
static inline bool bsp_btn_filter_accepts_event(const bsp_btn_filter_t *f,
                                                int64_t now_us, int64_t tol_us)
{
    if (f->last_ok_us == 0) {
        return false;
    }
    const int64_t dt = now_us - f->last_ok_us;
    return dt >= 0 && dt <= tol_us;
}
