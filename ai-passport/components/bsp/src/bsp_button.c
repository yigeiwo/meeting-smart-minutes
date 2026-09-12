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
// ⚠ 排查"某个键自发乱跳"时, 不要用周期性 adc_oneshot_read 去观测 —— 那会在按键
//   组件之外再开一路读者, 把嫌疑和观测手段混在一起。这里改为【只读数字电平】
//   (gpio_get_level 不经过 ADC), 20ms 一格记录 GPIO0 的原始波形, 每 2 秒打印一行;
//   同时用 1Hz 的 ADC 采样给出 raw/mV 做参照。两类证据对照即可判定:
//     数字波形也是一串低电平 -> 引脚被真实拉低 (硬件通路问题)
//     数字波形恒为高, 却有按键事件 -> ADC 读数被污染 (驱动/衰减配置问题)
// 不需要时把 BSP_BTN_DIAG 改成 0, 定时器与代码一起被裁掉, 零开销。
// ---------------------------------------------------------------------------
#define BSP_BTN_DIAG  1

#if BSP_BTN_DIAG
// 100 格 x 20ms = 2 秒一行
static void btn_diag_cb(void *arg) {
    (void)arg;
    static char tr[101];
    static int  idx = 0, hi = 0, lo = 0, jumps = 0, last = -1;
    static int  sec = 0;

    const int lv = gpio_get_level(GPIO_NUM_0);
    tr[idx++] = lv ? '1' : '0';
    if (lv) hi++; else lo++;
    if (last >= 0 && lv != last) jumps++;
    last = lv;

    if (idx >= 100) {
        tr[idx] = '\0';
        int raw = 0, mv = -1;
        if (s_adc && s_cali) {
            if (adc_oneshot_read(s_adc, BSP_BTN_ADC_CHANNEL, &raw) == ESP_OK) {
                if (adc_cali_raw_to_voltage(s_cali, raw, &mv) != ESP_OK) mv = -2;
            } else {
                mv = -3;
            }
        }
        ESP_LOGI(TAG, "[%02ds] GPIO0 波形(20ms/格, 1=高 0=低): %s", sec, tr);
        ESP_LOGI(TAG, "      高%d 低%d 跳变%d | ADC快照 raw=%d mv=%d", hi, lo, jumps, raw, mv);
        idx = 0; hi = lo = jumps = 0; sec += 2;
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
        esp_timer_start_periodic(vth, 20 * 1000);   // 20ms 一格, 2 秒打印一行波形
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
