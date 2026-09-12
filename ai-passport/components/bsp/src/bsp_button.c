// components/bsp/src/bsp_button.c
// 移植自 trae_card/components/platform/platform_esp32/src/btn_iot_button.c
#include "bsp_button.h"
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

// ADC1 是 unit 级独占资源:iot_button 与 bsp_button_read_mv() 必须共用同一个 oneshot
// 句柄。谁第二个调 adc_oneshot_new_unit() 谁就拿到 "adc1 is already in use"。
static adc_oneshot_unit_handle_t s_adc;
static adc_cali_handle_t         s_cali;

// 电压读取的衰减档必须与 button 组件内部的 ADC_BUTTON_ATTEN 一致 —— 通道只被配置一次
// (由组件在 iot_button_new_adc_device() 里下发),两边对不上会让读数与按键阈值错位。
// managed_components/espressif__button/button_adc.c:26 在 C3 上取 ADC_ATTEN_DB_6+1。
#define BSP_BTN_ATTEN  ADC_ATTEN_DB_12       // 量程约 0~3100mV,覆盖松开态

// 每个按键把"哪个键"随回调带回来。button 组件的回调签名固定,故用 usr_data 传索引。
static void on_event(void *arg, void *usr_data, bsp_btn_ev_t ev) {
    (void)arg;
    if (!s_cb) return;
    s_cb((bsp_btn_t)(intptr_t)usr_data, ev, s_user);
}
static void cb_press (void *a, void *u) { on_event(a, u, BSP_BTN_PRESS);  }
static void cb_click (void *a, void *u) { on_event(a, u, BSP_BTN_CLICK);  }
static void cb_double(void *a, void *u) { on_event(a, u, BSP_BTN_DOUBLE); }
static void cb_long  (void *a, void *u) { on_event(a, u, BSP_BTN_LONG);   }

// ---------------------------------------------------------------------------
// 分压诊断
//
// 实测结论(已用 gpio_get_level 交叉验证过):
//   引脚一旦配置成 ADC 输入, 数字输入通路就被关闭 —— gpio_get_level(GPIO0) 恒为 0,
//   而此时 ADC 稳定读到 raw=4095(满量程, 约 3024mV)。所以数字电平【不能】当判据,
//   判断"按键是不是被真实按下"只能看 ADC 的 raw。
//
// 本诊断以 10ms 为一格, 记录 raw 落在各电压窗口的情况, 每 2 秒打印一行分类波形:
//   U=上键窗口(0~140mV)  D=下键窗口(180~430)  O=确定窗口(460~1200)
//   -=窗口之间的空隙      .=松开(>1200)        !=读失败
// 并统计【最长连续 U 格数】: 真人按键会连续压住几十格, 偶发坏读数通常只占 1 格,
// 这个数字决定了滤波窗口该取多长。
// 不需要时把 BSP_BTN_DIAG 改成 0, 定时器与代码一起被裁掉, 零开销。
// ---------------------------------------------------------------------------
#define BSP_BTN_DIAG  1

#if BSP_BTN_DIAG
static char s_diag_tr[201];      // 200 格 x 10ms = 2 秒

static void btn_diag_cb(void *arg) {
    (void)arg;
    static int idx = 0, sec = 0;
    static int u_cnt = 0, u_run = 0, u_maxrun = 0;
    static int u_raw_min = 99999, u_raw_max = -1;
    static int raw_min = 99999, raw_max = -1, fails = 0;

    int raw = 0, mv = -1;
    esp_err_t e = s_adc ? adc_oneshot_read(s_adc, BSP_BTN_ADC_CHANNEL, &raw) : ESP_FAIL;
    if (e == ESP_OK && s_cali) {
        if (adc_cali_raw_to_voltage(s_cali, raw, &mv) != ESP_OK) mv = -1;
    } else {
        mv = -1;
    }

    char c;
    if (mv < 0) {
        c = '!'; fails++;
        if (u_run > u_maxrun) u_maxrun = u_run;
        u_run = 0;
    } else {
        if (raw < raw_min) raw_min = raw;
        if (raw > raw_max) raw_max = raw;
        if (mv <= 140) {                       // 上键窗口
            c = 'U'; u_cnt++; u_run++;
            if (u_run > u_maxrun) u_maxrun = u_run;
            if (raw < u_raw_min) u_raw_min = raw;
            if (raw > u_raw_max) u_raw_max = raw;
        } else {
            if (u_run > u_maxrun) u_maxrun = u_run;
            u_run = 0;
            if (mv < 180)        c = '-';
            else if (mv <= 430)  c = 'D';
            else if (mv < 460)   c = '-';
            else if (mv <= 1200) c = 'O';
            else                 c = '.';
        }
    }
    if (idx < 200) s_diag_tr[idx++] = c;

    if (idx >= 200) {
        s_diag_tr[idx] = '\0';
        ESP_LOGI(TAG, "[%02ds] %s", sec, &s_diag_tr[0]);
        ESP_LOGI(TAG, "       %s", &s_diag_tr[100]);
        ESP_LOGI(TAG, "       U格%d 最长连续%d格(%dms) U内raw=%d~%d | 全程raw=%d~%d 读失败%d",
                 u_cnt, u_maxrun, u_maxrun * 10,
                 u_raw_min > 99999 ? -1 : u_raw_min, u_raw_max,
                 raw_min > 99999 ? -1 : raw_min, raw_max, fails);
        idx = 0; sec += 2;
        u_cnt = u_run = u_maxrun = 0; fails = 0;
        u_raw_min = 99999; u_raw_max = -1;
        raw_min = 99999; raw_max = -1;
    }
}
#endif

esp_err_t bsp_button_init(bsp_btn_cb_t cb, void *user) {
    s_cb = cb; s_user = user;

    // 启用 GPIO0 弱上拉与禁用下拉，防止引脚在外部上拉偏弱或悬空时跌入 0V 导致误触
    const esp_err_t pe = gpio_set_pull_mode(GPIO_NUM_0, GPIO_PULLUP_ONLY);
    ESP_LOGI(TAG, "GPIO0 弱上拉设置: %s; 此刻数字电平=%d", esp_err_to_name(pe),
             gpio_get_level(GPIO_NUM_0));

    // 先由 BSP 建 unit,再把句柄交给 button 组件(button_adc.h:adc_handle 非 NULL 即复用),
    // 这样本文件的 bsp_button_read_mv() 也能读同一路 ADC。
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

    // 交叉验证: ADC 通道被组件配置后, GPIO0 的数字输入通路是否还读得到真实电平。
    // 若这里读到 0 而外部是 10k 上拉, 说明模拟配置关掉了数字输入, 之后的波形诊断作废。
    ESP_LOGI(TAG, "ADC 通道配置后 GPIO0 数字电平=%d (应为 1)", gpio_get_level(GPIO_NUM_0));

    // 通道已由组件配置好,这里只补一份校准句柄给 bsp_button_read_mv() 用。
    // 失败不致命:按键照常工作,只是读不出电压(标定分压电阻时才需要)。
    const adc_cali_curve_fitting_config_t cal = {
        .unit_id  = BSP_BTN_ADC_UNIT,
        .chan     = BSP_BTN_ADC_CHANNEL,
        .atten    = BSP_BTN_ATTEN,
        .bitwidth = ADC_BITWIDTH_DEFAULT,
    };
    if (adc_cali_create_scheme_curve_fitting(&cal, &s_cali) != ESP_OK) {
        ESP_LOGW(TAG, "ADC 校准创建失败,Button 页将无法显示电压");
        s_cali = NULL;
    }

#if BSP_BTN_DIAG
    // 必须等校准句柄建好后再启动采样,否则 ADC 快照一直取不到值
    const esp_timer_create_args_t vt = { .callback = btn_diag_cb, .name = "btn_diag" };
    esp_timer_handle_t vth = NULL;
    if (esp_timer_create(&vt, &vth) == ESP_OK) {
        esp_timer_start_periodic(vth, 10 * 1000);   // 10ms 一格, 2 秒打印一行波形
    } else {
        ESP_LOGW(TAG, "诊断定时器创建失败, 波形不可用");
    }
#endif

    ESP_LOGI(TAG, "按键就绪:ADC1_CH%d 三键分压", BSP_BTN_ADC_CHANNEL);
    return ESP_OK;
}

int bsp_button_read_mv(void) {
    // 读的是 bsp_button_init() 建好、并与 iot_button 共用的那一路 ADC。
    // 单次采样与组件的按键轮询互不干扰(oneshot 内部自带锁)。
    if (!s_adc || !s_cali) return -1;

    int raw = 0, mv = 0;
    if (adc_oneshot_read(s_adc, BSP_BTN_ADC_CHANNEL, &raw) != ESP_OK) return -1;
    if (adc_cali_raw_to_voltage(s_cali, raw, &mv) != ESP_OK) return -1;
    return mv;
}
