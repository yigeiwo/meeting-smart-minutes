// components/bsp/src/bsp_button.c
// 移植自 trae_card/components/platform/platform_esp32/src/btn_iot_button.c
#include "bsp_button.h"
#include "bsp_button_filter.h"
#include "bsp_pins.h"
#include "iot_button.h"
#include "button_adc.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_timer.h"

static const char *TAG = "bsp_btn";

static const uint16_t BTN_MV[BSP_BTN_COUNT][2] = BSP_BTN_MV_TABLE;

static button_handle_t s_btn[BSP_BTN_COUNT];
static bsp_btn_cb_t    s_cb;
static void           *s_user;

// ADC1 是 unit 级独占资源:iot_button 与电压显示必须共用同一个 oneshot 句柄。
// 谁第二个调 adc_oneshot_new_unit() 谁就拿到 "adc1 is already in use"。
static adc_oneshot_unit_handle_t s_adc;
static adc_cali_handle_t         s_cali;

// 按键滤波状态:由下面的独立采样器更新, 事件出口据此门控。
// 采样器同时缓存最近一次电压用给 Button 页, 页面不再自己开一路 ADC 读者。
static bsp_btn_filter_t  s_filt[BSP_BTN_COUNT];
static volatile int      s_last_mv = -1;

// 电压读取的衰减档必须与 button 组件内部的 ADC_BUTTON_ATTEN 一致 —— 通道只被配置一次
// (由组件在 iot_button_new_adc_device() 里下发),两边对不上会让读数与按键阈值错位。
// managed_components/espressif__button/button_adc.c:26 在 C3 上取 ADC_ATTEN_DB_6+1。
#define BSP_BTN_ATTEN  ADC_ATTEN_DB_12       // 量程约 0~3100mV,覆盖松开态

// 每个按键把"哪个键"随回调带回来。button 组件的回调签名固定,故用 usr_data 传索引。
//
// 出口门控: 按键组件不判断 ADC 采样的连续性, 单格坏读数会被它当成一次按下。
// 本板空闲电平在 12dB 量程上限(实测 raw 恒为 4095), 该工作点会偶发 raw=0 的坏读数,
// 折算成 0mV 恰好落进上键窗口(0~140mV) —— 结果就是上键自发乱按、页面自己跳回主菜单,
// 而下键/确定键(180~430 / 460~1200mV)离 0 很远, 完全不受影响。
// 这里用独立采样器的"连续 3 格同窗口"结论做闸门: 没有真实按下就不放行。
// 真人按键 ≥100ms(≥10 格), 与 3 格门限有 3 倍以上裕量。完整推导见 bsp_button_filter.h。
static void on_event(void *arg, void *usr_data, bsp_btn_ev_t ev) {
    (void)arg;
    if (!s_cb) return;

    const int i = (int)(intptr_t)usr_data;
    if (i >= 0 && i < BSP_BTN_COUNT) {
        const int64_t now = esp_timer_get_time();
        if (!bsp_btn_filter_accepts_event(&s_filt[i], now, BSP_BTN_EVENT_TOL_US)) {
            // 每秒最多提示一次, 避免坏读数频繁时刷屏
            static int64_t s_last_warn_us;
            if (now - s_last_warn_us > 1000000) {
                s_last_warn_us = now;
                ESP_LOGW(TAG, "丢弃按键 %d 的坏读数事件 (ev=%d): 未经连续采样确认", i, ev);
            }
            return;
        }
    }
    s_cb((bsp_btn_t)i, ev, s_user);
}
static void cb_press (void *a, void *u) { on_event(a, u, BSP_BTN_PRESS);  }
static void cb_click (void *a, void *u) { on_event(a, u, BSP_BTN_CLICK);  }
static void cb_double(void *a, void *u) { on_event(a, u, BSP_BTN_DOUBLE); }
static void cb_long  (void *a, void *u) { on_event(a, u, BSP_BTN_LONG);   }

// ---------------------------------------------------------------------------
// 独立采样器: 10ms 一格, 既是滤波器的事实来源, 也是 Button 页的电压来源
//
// 为什么必须有这一层(实机实测结论):
//   · 空闲时引脚被外部 10k 上拉到 3.3V, 已到 ADC 12dB 量程上限, 实测 raw 恒为 4095。
//   · 该工作点上 ADC 会偶发返回 raw=0 的坏读数; 10ms 采样实测【从不连续出现 2 格】。
//   · 引脚一旦配置成 ADC 输入, 数字输入通路即被关闭 —— 此时 gpio_get_level(GPIO0) 恒为 0,
//     与 ADC 读到的 4095 相互矛盾, 所以【不能】用数字电平当交叉判据(已实测排除)。
//   · 因此判定"真实按下"只能靠 ADC 的连续性: 连续 3 格同窗口(30ms)才算按下。
// ---------------------------------------------------------------------------
#define BSP_BTN_SAMPLE_PERIOD_US  (10 * 1000)

// 诊断波形: 每 2 秒打印一行 10ms/格的分类波形(U/D/O=三键窗口, -=窗口间空隙, .=松开)。
// 排查"某个键自发乱跳"时改成 1 即可, 与采样共用同一次读取, 不额外增加 ADC 读者。
#define BSP_BTN_DIAG  0

static esp_timer_handle_t s_sample_timer;

static void btn_sample_cb(void *arg) {
    (void)arg;
    if (!s_adc || !s_cali) return;

    int raw = 0, mv = 0;
    if (adc_oneshot_read(s_adc, BSP_BTN_ADC_CHANNEL, &raw) != ESP_OK) return;
    if (adc_cali_raw_to_voltage(s_cali, raw, &mv) != ESP_OK) return;
    s_last_mv = mv;

    const int64_t now = esp_timer_get_time();
    for (int i = 0; i < BSP_BTN_COUNT; i++) {
        bsp_btn_filter_feed(&s_filt[i], mv, now);
    }

#if BSP_BTN_DIAG
    static char tr[201];
    static int  idx = 0, sec = 0, up_cnt = 0, up_run = 0, up_maxrun = 0;
    char c = '.';
    for (int i = 0; i < BSP_BTN_COUNT; i++) {
        if (mv >= BTN_MV[i][0] && mv <= BTN_MV[i][1]) c = "UDO"[i];
    }
    if (c == 'U') { up_cnt++; if (++up_run > up_maxrun) up_maxrun = up_run; }
    else          { up_run = 0; }
    tr[idx++] = c;
    if (idx >= 200) {
        tr[idx] = '\0';
        ESP_LOGI(TAG, "[%02ds] %s", sec, &tr[0]);
        ESP_LOGI(TAG, "       %s", &tr[100]);
        ESP_LOGI(TAG, "       U格%d 最长连续%d格(%dms) 当前mv=%d", up_cnt, up_maxrun,
                 up_maxrun * 10, mv);
        idx = 0; sec += 2; up_cnt = up_maxrun = up_run = 0;
    }
#endif
}

esp_err_t bsp_button_init(bsp_btn_cb_t cb, void *user) {
    s_cb = cb; s_user = user;

    for (int i = 0; i < BSP_BTN_COUNT; i++) {
        bsp_btn_filter_init(&s_filt[i], BTN_MV[i][0], BTN_MV[i][1], BSP_BTN_FILTER_SAMPLES);
    }

    // 启用 GPIO0 弱上拉与禁用下拉，防止引脚在外部上拉偏弱或悬空时跌入 0V 导致误触
    const esp_err_t pe = gpio_set_pull_mode(GPIO_NUM_0, GPIO_PULLUP_ONLY);
    if (pe != ESP_OK) {
        ESP_LOGE(TAG, "GPIO0 上拉配置失败 (%s)", esp_err_to_name(pe));
        return pe;
    }

    // 先由 BSP 建 unit,再把句柄交给 button 组件(button_adc.h:adc_handle 非 NULL 即复用),
    // 这样采样器与组件读的是同一路 ADC。
    const adc_oneshot_unit_init_cfg_t ucfg = { .unit_id = BSP_BTN_ADC_UNIT };
    esp_err_t ae = adc_oneshot_new_unit(&ucfg, &s_adc);
    if (ae != ESP_OK) {
        ESP_LOGE(TAG, "ADC unit 创建失败 (%s)", esp_err_to_name(ae));
        s_adc = NULL;
        return ae;
    }

    for (int i = 0; i < BSP_BTN_COUNT; i++) {
        const button_adc_config_t ac = {
            .adc_handle   = &s_adc,          // 复用上面这一个,别让组件自建
            .unit_id      = BSP_BTN_ADC_UNIT,
            .adc_channel  = BSP_BTN_ADC_CHANNEL,
            .button_index = i,
            .min          = BTN_MV[i][0],
            .max          = BTN_MV[i][1],
        };
        const button_config_t bc = {
            .long_press_time = 500,
            .short_press_time = 180,
        };
        esp_err_t e = iot_button_new_adc_device(&bc, &ac, &s_btn[i]);
        if (e != ESP_OK || !s_btn[i]) {
            ESP_LOGE(TAG, "按键 %d 创建失败 (%s) —— 检查 GPIO%d 的 ADC 配置与分压电阻",
                     i, esp_err_to_name(e), BSP_BTN_ADC_CHANNEL);
            return e == ESP_OK ? ESP_FAIL : e;
        }
        void *idx = (void *)(intptr_t)i;
        iot_button_register_cb(s_btn[i], BUTTON_PRESS_DOWN,      NULL, cb_press,  idx);
        iot_button_register_cb(s_btn[i], BUTTON_SINGLE_CLICK,    NULL, cb_click,  idx);
        iot_button_register_cb(s_btn[i], BUTTON_DOUBLE_CLICK,    NULL, cb_double, idx);
        iot_button_register_cb(s_btn[i], BUTTON_LONG_PRESS_START,NULL, cb_long,   idx);
        iot_button_register_cb(s_btn[i], BUTTON_LONG_PRESS_UP,   NULL, cb_long,   idx);
    }

    // 通道已由组件配置好,这里只补一份校准句柄给采样器用。
    const adc_cali_curve_fitting_config_t cal = {
        .unit_id  = BSP_BTN_ADC_UNIT,
        .chan     = BSP_BTN_ADC_CHANNEL,
        .atten    = BSP_BTN_ATTEN,
        .bitwidth = ADC_BITWIDTH_DEFAULT,
    };
    if (adc_cali_create_scheme_curve_fitting(&cal, &s_cali) != ESP_OK) {
        // 采样器没有电压就无法判键, 必须硬失败: 否则所有按键都会被门控挡掉,
        // 表现为"整块板子按键全失灵", 比直接报错更难排查。
        ESP_LOGE(TAG, "ADC 校准创建失败, 按键无法判键");
        return ESP_FAIL;
    }

    const esp_timer_create_args_t sc = { .callback = btn_sample_cb, .name = "btn_sample" };
    esp_err_t te = esp_timer_create(&sc, &s_sample_timer);
    if (te == ESP_OK) {
        te = esp_timer_start_periodic(s_sample_timer, BSP_BTN_SAMPLE_PERIOD_US);
    }
    if (te != ESP_OK) {
        ESP_LOGE(TAG, "按键采样定时器启动失败 (%s), 按键事件将被全部门控挡掉",
                 esp_err_to_name(te));
        return te;
    }

    ESP_LOGI(TAG, "按键就绪:ADC1_CH%d 三键分压, %dms 采样 + 连续%d格滤波",
             BSP_BTN_ADC_CHANNEL, BSP_BTN_SAMPLE_PERIOD_US / 1000, BSP_BTN_FILTER_SAMPLES);
    return ESP_OK;
}

int bsp_button_read_mv(void) {
    // 直接返回采样器缓存的值: 既保证与判键用的是同一份数据, 也避免再开一路 ADC 读者
    // (额外读者会与按键组件抢同一次转换, 正是坏读数变多的来源之一)。
    if (!s_adc || !s_cali) return -1;
    return s_last_mv;
}
